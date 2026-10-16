import random
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from utils.image_generator import criar_imagem_texto
from database.db_manager import UserManager
from cogs.config_cog import get_config, modulo_ativo

DB_PATH = "usuarios.db"

# Caracteres usados nos níveis "normais" (fácil, médio, difícil)
GERADOR_CARACTERES_BASE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
GERADOR_CARACTERES_ESPECIAIS = "!@#$%&*()"
GERADOR_ACENTOS = {
    "A": "ÁÃÂÀ",
    "E": "ÉÊÈ",
    "I": "ÍÎÌ",
    "O": "ÓÕÔÒ",
    "U": "ÚÛÙ",
}

GERADOR_CHANCE_ACENTO = 0.5
GERADOR_CHANCE_ESPECIAL = 0.2

# Níveis de dificuldade do desafio:
#   nivel       -> nome exibido no anúncio do desafio
#   chance      -> peso relativo de sorteio (não precisa somar 1.0)
#   tamanho     -> quantidade de caracteres do texto gerado
#   recompensa  -> flingers ganhos ao acertar
#   caracteres  -> "custom_acentuado" usa GERADOR_ACENTOS/ESPECIAIS, ou string com os caracteres permitidos
GERADOR_NIVEIS = [
    {"nivel": "fácil",      "chance": 0.50, "tamanho": 5,  "recompensa": 25,   "caracteres": GERADOR_CARACTERES_BASE},
    {"nivel": "médio",      "chance": 0.30, "tamanho": 12, "recompensa": 50,  "caracteres": GERADOR_CARACTERES_BASE},
    {"nivel": "difícil",    "chance": 0.15, "tamanho": 25, "recompensa": 75,  "caracteres": GERADOR_CARACTERES_BASE},
    {"nivel": "impossível", "chance": 0.05, "tamanho": 25, "recompensa": 150, "caracteres": "custom_acentuado"},
]

# Intervalo (em segundos) entre cada rodada do gerador: (mínimo, máximo)
GERADOR_INTERVALO_SEGUNDOS = (60 * 60, 90 * 60)

# Tempo (em segundos) que os jogadores têm para responder ao desafio
GERADOR_TEMPO_RESPOSTA_SEGUNDOS = 900

# Cooldown (em horas) aplicado quando um desafio expira sem resposta
GERADOR_COOLDOWN_EXPIRACAO_HORAS = 2

# ══════════════════════════════════════════════════════════════════
#  FIM DA CONFIGURAÇÃO DO GERADOR
# ══════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO DO ROUBO
# ══════════════════════════════════════════════════════════════════
ROUBO_CHANCE_SUCESSO      = 79     # 1–79  -> sucesso
ROUBO_CHANCE_FALHA        = 20     # 80–99 -> falha  (o 1% restante é prisão)
ROUBO_VALOR_MINIMO        = 50     # mínimo roubado num sucesso
ROUBO_PERCENTUAL_MAXIMO   = 0.10   # máximo = 10% da carteira da vítima
ROUBO_PENALIDADE_FALHA    = 100    # flingers perdidos ao falhar (pode ficar negativo)
ROUBO_PERCENTUAL_PRISAO   = 0.10   # % de carteira + banco perdido ao ser preso
ROUBO_COOLDOWN_SEGUNDOS   = 3600    # cooldown entre tentativas do mesmo usuário

# Chance (0.0–1.0) da mensagem de /sacar aparecer para todo mundo no chat
SACAR_CHANCE_PUBLICO = 0.20

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

@contextmanager
def transacao():
    """
    Abre uma transação exclusiva no SQLite (BEGIN IMMEDIATE).
    Garante que leitura + escrita de saldos aconteçam de forma atômica,
    evitando que dois comandos simultâneos (ex.: dois roubos na mesma vítima)
    gastem o mesmo dinheiro. Faz COMMIT ao sair, ROLLBACK se der exceção.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.isolation_level = None  # controle manual da transação
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _ler_quantidade(texto: str):
    """Aceita um número inteiro positivo ou 'tudo'. Retorna int, 'tudo' ou None."""
    texto = texto.strip().lower()
    if texto in ("tudo", "all", "t"):
        return "tudo"
    try:
        valor = int(texto)
    except ValueError:
        return None
    return valor if valor > 0 else None


# Estado do gerador por guild — evita compartilhamento entre servidores
# (o liga/desliga em si é controlado pelo módulo "gerador" via /modulo)
def _estado_padrao() -> dict:
    return {
        "can_win":     False,
        "texto":       None,
        "reward":      0,
        "posted_at":   None,
        "cooldown_ate": None,
    }

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot            = bot
        self.guilds_estado: dict[int, dict] = {}  # guild_id -> estado
        self.ultimo_trabalho = {}
        self.ultimo_roubo = {}  # (user_id, guild_id) -> datetime
        self._loop_task: asyncio.Task | None = None
        self.verificar_expiracao_loop.start()

    def cog_unload(self):
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        self.verificar_expiracao_loop.cancel()

    def _estado(self, guild_id: int) -> dict:
        """Retorna o estado do gerador para a guild, criando se não existir."""
        if guild_id not in self.guilds_estado:
            self.guilds_estado[guild_id] = _estado_padrao()
        return self.guilds_estado[guild_id]

    def gerar_texto_aleatorio(self):
        def gerar_com_acentos(tamanho):
            texto = ""
            for _ in range(tamanho):
                letra = random.choice(GERADOR_CARACTERES_BASE)
                if letra in GERADOR_ACENTOS and random.random() < GERADOR_CHANCE_ACENTO:
                    letra = random.choice(GERADOR_ACENTOS[letra])
                elif random.random() < GERADOR_CHANCE_ESPECIAL:
                    letra = random.choice(GERADOR_CARACTERES_ESPECIAIS)
                texto += letra
            return texto

        escolha = random.choices(GERADOR_NIVEIS, weights=[t["chance"] for t in GERADOR_NIVEIS])[0]
        texto   = (
            gerar_com_acentos(escolha["tamanho"])
            if escolha["caracteres"] == "custom_acentuado"
            else "".join(random.choice(escolha["caracteres"]) for _ in range(escolha["tamanho"]))
        )
        return texto, escolha["recompensa"], escolha["nivel"]

    # ──────────────────────────────────────────
    #  Loops
    # ──────────────────────────────────────────

    async def contador_economia_loop(self):
        """Loop principal do gerador — intervalo aleatório entre cada rodada."""
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(random.randint(*GERADOR_INTERVALO_SEGUNDOS))

            for guild in self.bot.guilds:
                estado   = self._estado(guild.id)
                canal_id = get_config(guild.id, "canal_economia")

                if not canal_id or not modulo_ativo(guild.id, "gerador"):
                    continue

                agora = datetime.now(timezone.utc)

                # Respeita cooldown se o último desafio expirou sem resposta
                if estado["cooldown_ate"] and agora < estado["cooldown_ate"]:
                    continue

                # Não posta se já há desafio ativo nesta guild
                if estado["can_win"]:
                    continue

                channel = self.bot.get_channel(canal_id)
                if not channel:
                    continue

                texto, recompensa, nivel = self.gerar_texto_aleatorio()
                estado["texto"]       = texto
                estado["reward"]      = recompensa
                estado["can_win"]     = True
                estado["posted_at"]   = agora
                estado["cooldown_ate"] = None

                minutos_resposta = GERADOR_TEMPO_RESPOSTA_SEGUNDOS // 60

                file_image = criar_imagem_texto(texto)
                await channel.send(
                    f"**Desafio de digitação!** (Nível: {nivel})\n"
                    f"Digite exatamente o texto para ganhar {recompensa} flingers! Você tem **{minutos_resposta} minutos**.\n",
                    file=file_image
                )

    @commands.Cog.listener()
    async def on_ready(self):
        """Inicia o loop do gerador após o bot estar pronto."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.ensure_future(self.contador_economia_loop())

    @tasks.loop(seconds=30)
    async def verificar_expiracao_loop(self):
        """Verifica se algum desafio ativo expirou sem resposta."""
        await self.bot.wait_until_ready()
        agora = datetime.now(timezone.utc)

        for guild_id, estado in self.guilds_estado.items():
            if not estado["can_win"] or estado["posted_at"] is None:
                continue

            decorrido = (agora - estado["posted_at"]).total_seconds()
            if decorrido < GERADOR_TEMPO_RESPOSTA_SEGUNDOS:
                continue

            # Expirou
            canal_id = get_config(guild_id, "canal_economia")
            estado["can_win"]      = False
            estado["texto"]        = None
            estado["reward"]       = 0
            estado["posted_at"]    = None
            estado["cooldown_ate"] = agora + timedelta(hours=GERADOR_COOLDOWN_EXPIRACAO_HORAS)

            if canal_id:
                channel = self.bot.get_channel(canal_id)
                if channel:
                    await channel.send(
                        f"⏰ Tempo esgotado! Ninguém digitou o texto. O próximo desafio será em **{GERADOR_COOLDOWN_EXPIRACAO_HORAS} hora(s)**."
                    )
            print(f"[Economy] Desafio expirado na guild {guild_id}. Próximo em {GERADOR_COOLDOWN_EXPIRACAO_HORAS} hora(s).")

    # ──────────────────────────────────────────
    #  Listener
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        canal_id = get_config(message.guild.id, "canal_economia")
        if not canal_id or message.channel.id != canal_id:
            return

        estado = self._estado(message.guild.id)

        if estado["can_win"] and estado["texto"] is not None:
            if message.content.strip().lower() == estado["texto"].lower():
                reward = estado["reward"]
                estado["can_win"]    = False
                estado["texto"]      = None
                estado["reward"]     = 0
                estado["posted_at"]  = None
                estado["cooldown_ate"] = None

                UserManager.adicionar_flingers(message.author.id, message.guild.id, reward)
                await message.channel.send(
                    f"🎉 {message.author.mention} digitou primeiro o texto correto e ganhou {reward} flingers!"
                )

    # ──────────────────────────────────────────
    #  Slash commands
    # ──────────────────────────────────────────

    @app_commands.command(name="topflingers", description="Veja os usuários com mais flingers (carteira + banco)")
    async def top_flingers(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
            SELECT nome, discriminator, flingers, banco, (flingers + banco) AS total
            FROM usuarios
            WHERE guild_id = ?
            ORDER BY total DESC
            LIMIT 10
            """, (interaction.guild_id,))
            top = c.fetchall()

        if not top or top[0][4] <= 0:
            await interaction.response.send_message("⚠️ Ninguém possui flingers ainda!", ephemeral=True)
            return

        embed = discord.Embed(title="💰 Top 10 Usuários com mais Flingers", color=discord.Color.gold())
        for i, (nome, discriminator, carteira, banco, total) in enumerate(top, start=1):
            embed.add_field(
                name=f"#{i} - {nome}#{discriminator}",
                value=f"💵 {total} flingers (👛 {carteira} • 🏦 {banco})",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="saldo", description="Veja o que você tem na carteira e no banco")
    async def saldo(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT flingers, banco FROM usuarios WHERE id = ? AND guild_id = ?",
                (interaction.user.id, interaction.guild_id)
            ).fetchone()

        if not row:
            await interaction.response.send_message("❌ Você não está registrado neste servidor.", ephemeral=True)
            return

        carteira, banco = row
        embed = discord.Embed(title="💼 Seu saldo", color=discord.Color.blurple())
        embed.add_field(name="👛 Carteira (roubável)", value=f"{carteira} flingers", inline=True)
        embed.add_field(name="🏦 Banco (protegido)",   value=f"{banco} flingers",    inline=True)
        embed.add_field(name="Total",                  value=f"{carteira + banco} flingers", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="depositar", description="Guarde flingers da carteira no banco (o banco não pode ser roubado)")
    @app_commands.describe(quantidade="Quantidade a depositar, ou 'tudo'")
    async def depositar(self, interaction: discord.Interaction, quantidade: str):
        pedido = _ler_quantidade(quantidade)
        if pedido is None:
            await interaction.response.send_message(
                "❌ Informe um número maior que zero ou `tudo`.", ephemeral=True
            )
            return

        erro = None
        with transacao() as conn:
            row = conn.execute(
                "SELECT flingers, banco FROM usuarios WHERE id = ? AND guild_id = ?",
                (interaction.user.id, interaction.guild_id)
            ).fetchone()

            if not row:
                erro = "❌ Você não está registrado neste servidor."
            else:
                carteira, banco = row
                valor = carteira if pedido == "tudo" else pedido

                if carteira <= 0:
                    erro = "❌ Você não tem flingers na carteira para depositar."
                elif valor > carteira:
                    erro = f"❌ Você só tem **{carteira}** flingers na carteira."
                else:
                    conn.execute(
                        "UPDATE usuarios SET flingers = flingers - ?, banco = banco + ? "
                        "WHERE id = ? AND guild_id = ?",
                        (valor, valor, interaction.user.id, interaction.guild_id)
                    )
                    carteira -= valor
                    banco += valor

        if erro:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        await interaction.response.send_message(
            f"🏦 Você depositou **{valor}** flingers.\n"
            f"👛 Carteira: **{carteira}** • 🏦 Banco: **{banco}**",
            ephemeral=True
        )

    @app_commands.command(name="sacar", description="Retire flingers do banco para a carteira")
    @app_commands.describe(quantidade="Quantidade a sacar, ou 'tudo'")
    async def sacar(self, interaction: discord.Interaction, quantidade: str):
        pedido = _ler_quantidade(quantidade)
        if pedido is None:
            await interaction.response.send_message(
                "❌ Informe um número maior que zero ou `tudo`.", ephemeral=True
            )
            return

        erro = None
        with transacao() as conn:
            row = conn.execute(
                "SELECT flingers, banco FROM usuarios WHERE id = ? AND guild_id = ?",
                (interaction.user.id, interaction.guild_id)
            ).fetchone()

            if not row:
                erro = "❌ Você não está registrado neste servidor."
            else:
                carteira, banco = row
                valor = banco if pedido == "tudo" else pedido

                if banco <= 0:
                    erro = "❌ Você não tem flingers no banco para sacar."
                elif valor > banco:
                    erro = f"❌ Você só tem **{banco}** flingers no banco."
                else:
                    conn.execute(
                        "UPDATE usuarios SET banco = banco - ?, flingers = flingers + ? "
                        "WHERE id = ? AND guild_id = ?",
                        (valor, valor, interaction.user.id, interaction.guild_id)
                    )
                    carteira += valor
                    banco -= valor

        if erro:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        # Por padrão só quem sacou vê. Com SACAR_CHANCE_PUBLICO de chance, todo mundo vê
        # (sem revelar o saldo do banco, só o que foi sacado e o que ficou solto na carteira).
        if random.random() < SACAR_CHANCE_PUBLICO:
            await interaction.response.send_message(
                f"👀 {interaction.user.mention} foi ao banco e sacou **{valor}** flingers! "
                f"Agora está com **{carteira}** flingers soltos na carteira... 🥷"
            )
        else:
            await interaction.response.send_message(
                f"👛 Você sacou **{valor}** flingers. Lembre: dinheiro na carteira pode ser roubado!\n"
                f"👛 Carteira: **{carteira}** • 🏦 Banco: **{banco}**",
                ephemeral=True
            )

    @app_commands.command(name="roubar", description="Tente roubar flingers da carteira de outro usuário")
    @app_commands.describe(vitima="Quem você quer roubar")
    async def roubar(self, interaction: discord.Interaction, vitima: discord.Member):
        ladrao   = interaction.user
        guild_id = interaction.guild_id
        agora    = datetime.now(timezone.utc)

        if vitima.bot:
            await interaction.response.send_message("❌ Você não pode roubar um bot.", ephemeral=True)
            return
        if vitima.id == ladrao.id:
            await interaction.response.send_message("❌ Você não pode roubar a si mesmo.", ephemeral=True)
            return

        # Cooldown entre tentativas
        anterior = self.ultimo_roubo.get((ladrao.id, guild_id))
        if anterior:
            decorrido = (agora - anterior).total_seconds()
            if decorrido < ROUBO_COOLDOWN_SEGUNDOS:
                restante = int(ROUBO_COOLDOWN_SEGUNDOS - decorrido)
                await interaction.response.send_message(
                    f"🕵️ A polícia está de olho! Tente novamente em {restante // 60}m{restante % 60}s.",
                    ephemeral=True
                )
                return

        erro      = None
        resultado = None   # "sucesso" | "falha" | "preso"
        detalhes  = {}

        with transacao() as conn:
            l = conn.execute(
                "SELECT flingers, banco FROM usuarios WHERE id = ? AND guild_id = ?",
                (ladrao.id, guild_id)
            ).fetchone()
            v = conn.execute(
                "SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?",
                (vitima.id, guild_id)
            ).fetchone()

            if not l:
                erro = "❌ Você não está registrado neste servidor."
            elif not v:
                erro = f"❌ {vitima.mention} não está registrado neste servidor."
            elif v[0] <= 0:
                erro = f"❌ {vitima.mention} não tem nada na carteira para roubar."
            else:
                carteira_ladrao, banco_ladrao = l
                carteira_vitima = v[0]

                sorteio = random.randint(1, 100)

                if sorteio <= ROUBO_CHANCE_SUCESSO:
                    # Sucesso: entre 50 e 10% da carteira (mínimo de teto = 50).
                    # Se a carteira tiver menos de 50, rouba tudo que tiver.
                    limite = max(ROUBO_VALOR_MINIMO, int(carteira_vitima * ROUBO_PERCENTUAL_MAXIMO))
                    valor  = min(carteira_vitima, random.randint(ROUBO_VALOR_MINIMO, limite))

                    conn.execute(
                        "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ?",
                        (valor, vitima.id, guild_id)
                    )
                    conn.execute(
                        "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                        (valor, ladrao.id, guild_id)
                    )
                    resultado = "sucesso"
                    detalhes  = {"valor": valor}

                elif sorteio <= ROUBO_CHANCE_SUCESSO + ROUBO_CHANCE_FALHA:
                    # Falha: perde 100, pode ficar negativo (devendo)
                    conn.execute(
                        "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ?",
                        (ROUBO_PENALIDADE_FALHA, ladrao.id, guild_id)
                    )
                    resultado = "falha"
                    detalhes  = {"novo_saldo": carteira_ladrao - ROUBO_PENALIDADE_FALHA}

                else:
                    # Preso: perde 10% de tudo (carteira positiva + banco)
                    multa_carteira = int(max(carteira_ladrao, 0) * ROUBO_PERCENTUAL_PRISAO)
                    multa_banco    = int(banco_ladrao * ROUBO_PERCENTUAL_PRISAO)

                    conn.execute(
                        "UPDATE usuarios SET flingers = flingers - ?, banco = banco - ? "
                        "WHERE id = ? AND guild_id = ?",
                        (multa_carteira, multa_banco, ladrao.id, guild_id)
                    )
                    resultado = "preso"
                    detalhes  = {"multa": multa_carteira + multa_banco}

        if erro:
            await interaction.response.send_message(erro, ephemeral=True)
            return

        # Só conta cooldown se a tentativa realmente aconteceu
        self.ultimo_roubo[(ladrao.id, guild_id)] = agora

        if resultado == "sucesso":
            await interaction.response.send_message(
                f"🥷 {ladrao.mention} roubou **{detalhes['valor']} flingers** de {vitima.mention}! "
                f"Dica: guarde seu dinheiro no banco com `/depositar`."
            )
        elif resultado == "falha":
            saldo = detalhes["novo_saldo"]
            extra = f" Agora você está devendo **{abs(saldo)}** flingers." if saldo < 0 else ""
            await interaction.response.send_message(
                f"💥 {ladrao.mention} tentou roubar {vitima.mention}, mas deu tudo errado e perdeu "
                f"**{ROUBO_PENALIDADE_FALHA} flingers**!{extra}"
            )
        else:
            await interaction.response.send_message(
                f"🚔 {ladrao.mention} foi **preso** tentando roubar {vitima.mention}! "
                f"Pagou **{detalhes['multa']} flingers** em multa e fiança."
            )

    @app_commands.command(name="trabalhar", description="Trabalhe e ganhe flingers (10min de cooldown)")
    async def trabalhar(self, interaction: discord.Interaction):
        user_id  = interaction.user.id
        guild_id = interaction.guild_id
        agora    = datetime.now(timezone.utc)
        anterior = self.ultimo_trabalho.get((user_id, guild_id))

        cooldown_aplicado = self.ultimo_trabalho.get((user_id, guild_id, "cooldown"), 600)
        if anterior and (agora - anterior).total_seconds() < cooldown_aplicado:
            restante = cooldown_aplicado - int((agora - anterior).total_seconds())
            minutos  = restante // 60
            segundos = restante % 60
            return await interaction.response.send_message(
                f"⏳ Você está cansado! Tente novamente em {minutos}m{segundos}s.", ephemeral=True
            )

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT nivel FROM usuarios WHERE id = ? AND guild_id = ?", (user_id, guild_id))
            row = c.fetchone()
        nivel = row[0] if row else 1

        faixa  = (nivel - 1) // 5
        minimo = 1 + faixa * 3
        maximo = 5 + faixa * 3

        ganho = random.randint(minimo, maximo)

        hora_extra = random.random() < 0.10
        if hora_extra:
            ganho *= 5

        UserManager.adicionar_flingers(user_id, guild_id, ganho)
        self.ultimo_trabalho[(user_id, guild_id)] = agora
        cooldown = 1200 if hora_extra else 600
        self.ultimo_trabalho[(user_id, guild_id, "cooldown")] = cooldown

        if hora_extra:
            await interaction.response.send_message(
                f"⚡ {interaction.user.mention}, você fez **hora extra** e ganhou **{ganho} flingers!** "
                f"(5x bônus!) Mas está exausto... descanse por **20 minutos**."
            )
        else:
            await interaction.response.send_message(
                f"🛠️ {interaction.user.mention}, você trabalhou duro e ganhou **{ganho} flingers!**"
            )

    @app_commands.command(name="status_gerador", description="Mostra o status atual do gerador de texto")
    async def status_gerador(self, interaction: discord.Interaction):
        agora  = datetime.now(timezone.utc)
        estado = self._estado(interaction.guild_id)
        ativo  = modulo_ativo(interaction.guild_id, "gerador")
        status = "🟢 **ATIVO**" if ativo else "🔴 **DESABILITADO**"

        minutos_min = GERADOR_INTERVALO_SEGUNDOS[0] // 60
        minutos_max = GERADOR_INTERVALO_SEGUNDOS[1] // 60

        embed = discord.Embed(
            title="📊 Status do Gerador de Texto",
            color=discord.Color.green() if ativo else discord.Color.red()
        )
        embed.add_field(name="Status",    value=status,              inline=True)
        embed.add_field(name="Intervalo", value=f"A cada {minutos_min}–{minutos_max} minutos", inline=True)

        if estado["can_win"] and estado["posted_at"]:
            restante = GERADOR_TEMPO_RESPOSTA_SEGUNDOS - int((agora - estado["posted_at"]).total_seconds())
            embed.add_field(name="Desafio ativo", value=f"⏳ Expira em {max(restante, 0)}s", inline=False)
        elif estado["cooldown_ate"] and agora < estado["cooldown_ate"]:
            restante = int((estado["cooldown_ate"] - agora).total_seconds())
            minutos  = restante // 60
            segundos = restante % 60
            embed.add_field(name="Cooldown ativo", value=f"⏳ Próximo desafio em {minutos}m{segundos}s", inline=False)

        embed.set_footer(text="Use /modulo para ativar ou desativar.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="admin_add_flingers", description="[Admin] Adiciona flingers a um usuário.")
    @app_commands.describe(usuario="Usuário que receberá os flingers", quantidade="Quantidade a adicionar")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_add_flingers(self, interaction: discord.Interaction, usuario: discord.Member, quantidade: int):
        if quantidade <= 0:
            await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (usuario.id, interaction.guild_id))
            row = c.fetchone()
            if not row:
                await interaction.response.send_message(f"❌ {usuario.mention} não está registrado neste servidor.", ephemeral=True)
                return

            c.execute(
            "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
            (quantidade, usuario.id, interaction.guild_id)
            )
            conn.commit()
            novo_saldo = row[0] + quantidade

        embed = discord.Embed(title="💰 Flingers adicionados", color=discord.Color.green())
        embed.add_field(name="Usuário",        value=usuario.mention,   inline=True)
        embed.add_field(name="Adicionado",     value=f"+{quantidade} 💵", inline=True)
        embed.add_field(name="Novo saldo (carteira)", value=f"{novo_saldo} 💵", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Economy] Admin {interaction.user} adicionou {quantidade} flingers para {usuario} na guild {interaction.guild_id}.")

    @app_commands.command(name="admin_remove_flingers", description="[Admin] Remove flingers de um usuário.")
    @app_commands.describe(usuario="Usuário que perderá os flingers", quantidade="Quantidade a remover")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_remove_flingers(self, interaction: discord.Interaction, usuario: discord.Member, quantidade: int):
        if quantidade <= 0:
            await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
            return

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (usuario.id, interaction.guild_id))
            row = c.fetchone()
            if not row:
                await interaction.response.send_message(f"❌ {usuario.mention} não está registrado neste servidor.", ephemeral=True)
                return

            novo_saldo = max(0, row[0] - quantidade)
            c.execute(
            "UPDATE usuarios SET flingers = ? WHERE id = ? AND guild_id = ?",
            (novo_saldo, usuario.id, interaction.guild_id)
            )
            conn.commit()

        removido = row[0] - novo_saldo
        embed = discord.Embed(title="💸 Flingers removidos", color=discord.Color.red())
        embed.add_field(name="Usuário",    value=usuario.mention,    inline=True)
        embed.add_field(name="Removido",   value=f"-{removido} 💵",  inline=True)
        embed.add_field(name="Novo saldo (carteira)", value=f"{novo_saldo} 💵", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Economy] Admin {interaction.user} removeu {removido} flingers de {usuario} na guild {interaction.guild_id}.")


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))