import random
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from utils.image_generator import criar_imagem_texto
from database.db_manager import UserManager
from cogs.config_cog import get_config


# Estado do gerador por guild — evita compartilhamento entre servidores
def _estado_padrao() -> dict:
    return {
        "ativo":       True,
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
        self.contador_economia_loop.start()
        self.verificar_expiracao_loop.start()

    def cog_unload(self):
        self.contador_economia_loop.cancel()
        self.verificar_expiracao_loop.cancel()

    def _estado(self, guild_id: int) -> dict:
        """Retorna o estado do gerador para a guild, criando se não existir."""
        if guild_id not in self.guilds_estado:
            self.guilds_estado[guild_id] = _estado_padrao()
        return self.guilds_estado[guild_id]

    def gerar_texto_aleatorio(self):
        base     = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        acentos  = {"A": "ÁÃÂÀ", "E": "ÉÊÈ", "I": "ÍÎÌ", "O": "ÓÕÔÒ", "U": "ÚÛÙ"}
        especiais = "!@#$%&*()"

        def gerar_com_acentos(tamanho):
            texto = ""
            for _ in range(tamanho):
                letra = random.choice(base)
                if letra in acentos and random.random() < 0.5:
                    letra = random.choice(acentos[letra])
                elif random.random() < 0.2:
                    letra = random.choice(especiais)
                texto += letra
            return texto

        tipos = [
            {"nivel": "fácil",      "chance": 0.50, "tamanho": 5,  "recompensa": 3,  "caracteres": base},
            {"nivel": "médio",      "chance": 0.40, "tamanho": 12, "recompensa": 5,  "caracteres": base},
            {"nivel": "difícil",    "chance": 0.09, "tamanho": 25, "recompensa": 10, "caracteres": base},
            {"nivel": "impossível", "chance": 0.01, "tamanho": 25, "recompensa": 50, "caracteres": "custom_acentuado"},
        ]

        escolha = random.choices(tipos, weights=[t["chance"] for t in tipos])[0]
        texto   = (
            gerar_com_acentos(escolha["tamanho"])
            if escolha["caracteres"] == "custom_acentuado"
            else "".join(random.choice(escolha["caracteres"]) for _ in range(escolha["tamanho"]))
        )
        return texto, escolha["recompensa"], escolha["nivel"]

    # ──────────────────────────────────────────
    #  Loops
    # ──────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def contador_economia_loop(self):
        await self.bot.wait_until_ready()

        for guild in self.bot.guilds:
            estado   = self._estado(guild.id)
            canal_id = get_config(guild.id, "canal_economia")

            if not canal_id or not estado["ativo"]:
                continue

            agora = datetime.now(timezone.utc)

            # Respeita cooldown de 1h se o último desafio expirou sem resposta
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

            file_image = criar_imagem_texto(texto)
            await channel.send(
                f"**Desafio de digitação!** (Nível: {nivel})\n"
                f"Digite exatamente o texto para ganhar {recompensa} flingers! Você tem **5 minutos**.\n",
                file=file_image
            )

    @tasks.loop(seconds=30)
    async def verificar_expiracao_loop(self):
        """Verifica se algum desafio ativo expirou sem resposta (5 minutos)."""
        await self.bot.wait_until_ready()
        agora = datetime.now(timezone.utc)

        for guild_id, estado in self.guilds_estado.items():
            if not estado["can_win"] or estado["posted_at"] is None:
                continue

            decorrido = (agora - estado["posted_at"]).total_seconds()
            if decorrido < 300:
                continue

            # Expirou
            canal_id = get_config(guild_id, "canal_economia")
            estado["can_win"]      = False
            estado["texto"]        = None
            estado["reward"]       = 0
            estado["posted_at"]    = None
            estado["cooldown_ate"] = agora + timedelta(hours=1)

            if canal_id:
                channel = self.bot.get_channel(canal_id)
                if channel:
                    await channel.send(
                        "⏰ Tempo esgotado! Ninguém digitou o texto. O próximo desafio será em **1 hora**."
                    )
            print(f"[Economy] Desafio expirado na guild {guild_id}. Próximo em 1 hora.")

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

    @app_commands.command(name="topflingers", description="Veja os usuários com mais flingers")
    async def top_flingers(self, interaction: discord.Interaction):
        conn = sqlite3.connect("usuarios.db")
        c    = conn.cursor()
        c.execute("""
            SELECT nome, discriminator, flingers
            FROM usuarios
            WHERE guild_id = ?
            ORDER BY flingers DESC
            LIMIT 10
        """, (interaction.guild_id,))
        top = c.fetchall()
        conn.close()

        if not top:
            await interaction.response.send_message("⚠️ Ninguém possui flingers ainda!", ephemeral=True)
            return

        embed = discord.Embed(title="💰 Top 10 Usuários com mais Flingers", color=discord.Color.gold())
        for i, (nome, discriminator, flingers) in enumerate(top, start=1):
            embed.add_field(
                name=f"#{i} - {nome}#{discriminator}",
                value=f"💵 {flingers} flingers",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="trabalhar", description="Trabalhe e ganhe de 1 a 5 flingers (10min de cooldown)")
    async def trabalhar(self, interaction: discord.Interaction):
        user_id  = interaction.user.id
        guild_id = interaction.guild_id
        agora    = datetime.now(timezone.utc)
        anterior = self.ultimo_trabalho.get((user_id, guild_id))

        if anterior and (agora - anterior).total_seconds() < 600:
            restante = 600 - int((agora - anterior).total_seconds())
            minutos  = restante // 60
            segundos = restante % 60
            return await interaction.response.send_message(
                f"⏳ Você está cansado! Tente novamente em {minutos}m{segundos}s.", ephemeral=True
            )

        ganho = random.randint(1, 5)
        UserManager.adicionar_flingers(user_id, guild_id, ganho)
        self.ultimo_trabalho[(user_id, guild_id)] = agora

        await interaction.response.send_message(
            f"🛠️ {interaction.user.mention}, você trabalhou duro e ganhou **{ganho} flingers!**"
        )

    @app_commands.command(name="gerador", description="Habilita ou desabilita o gerador de texto aleatório")
    async def toggle_gerador(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Você precisa ter permissão de administrador para usar este comando!", ephemeral=True
            )
            return

        estado = self._estado(interaction.guild_id)
        estado["ativo"] = not estado["ativo"]

        if estado["ativo"]:
            await interaction.response.send_message(
                "✅ **Gerador de texto aleatório habilitado!**\n"
                "Um novo desafio será postado a cada 30 minutos.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "⏸️ **Gerador de texto aleatório desabilitado!**\n"
                "Use o comando novamente para reativar.", ephemeral=True
            )

    @app_commands.command(name="status_gerador", description="Mostra o status atual do gerador de texto")
    async def status_gerador(self, interaction: discord.Interaction):
        agora  = datetime.now(timezone.utc)
        estado = self._estado(interaction.guild_id)
        status = "🟢 **ATIVO**" if estado["ativo"] else "🔴 **DESABILITADO**"

        embed = discord.Embed(
            title="📊 Status do Gerador de Texto",
            color=discord.Color.green() if estado["ativo"] else discord.Color.red()
        )
        embed.add_field(name="Status",    value=status,              inline=True)
        embed.add_field(name="Intervalo", value="A cada 30 minutos", inline=True)

        if estado["can_win"] and estado["posted_at"]:
            restante = 300 - int((agora - estado["posted_at"]).total_seconds())
            embed.add_field(name="Desafio ativo", value=f"⏳ Expira em {max(restante, 0)}s", inline=False)
        elif estado["cooldown_ate"] and agora < estado["cooldown_ate"]:
            restante = int((estado["cooldown_ate"] - agora).total_seconds())
            minutos  = restante // 60
            segundos = restante % 60
            embed.add_field(name="Cooldown ativo", value=f"⏳ Próximo desafio em {minutos}m{segundos}s", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="dar_flingers", description="[Admin] Adiciona flingers a um usuário.")
    @app_commands.describe(usuario="Usuário que receberá os flingers", quantidade="Quantidade a adicionar")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_add_flingers(self, interaction: discord.Interaction, usuario: discord.Member, quantidade: int):
        if quantidade <= 0:
            await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
            return

        conn = sqlite3.connect("usuarios.db")
        c    = conn.cursor()
        c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (usuario.id, interaction.guild_id))
        row = c.fetchone()
        if not row:
            conn.close()
            await interaction.response.send_message(f"❌ {usuario.mention} não está registrado neste servidor.", ephemeral=True)
            return

        c.execute(
            "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
            (quantidade, usuario.id, interaction.guild_id)
        )
        conn.commit()
        novo_saldo = row[0] + quantidade
        conn.close()

        embed = discord.Embed(title="💰 Flingers adicionados", color=discord.Color.green())
        embed.add_field(name="Usuário",        value=usuario.mention,   inline=True)
        embed.add_field(name="Adicionado",     value=f"+{quantidade} 💵", inline=True)
        embed.add_field(name="Novo saldo",     value=f"{novo_saldo} 💵", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Economy] Admin {interaction.user} adicionou {quantidade} flingers para {usuario} na guild {interaction.guild_id}.")

    @app_commands.command(name="tirar_flingers", description="[Admin] Remove flingers de um usuário.")
    @app_commands.describe(usuario="Usuário que perderá os flingers", quantidade="Quantidade a remover")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_remove_flingers(self, interaction: discord.Interaction, usuario: discord.Member, quantidade: int):
        if quantidade <= 0:
            await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
            return

        conn = sqlite3.connect("usuarios.db")
        c    = conn.cursor()
        c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (usuario.id, interaction.guild_id))
        row = c.fetchone()
        if not row:
            conn.close()
            await interaction.response.send_message(f"❌ {usuario.mention} não está registrado neste servidor.", ephemeral=True)
            return

        novo_saldo = max(0, row[0] - quantidade)
        c.execute(
            "UPDATE usuarios SET flingers = ? WHERE id = ? AND guild_id = ?",
            (novo_saldo, usuario.id, interaction.guild_id)
        )
        conn.commit()
        conn.close()

        removido = row[0] - novo_saldo
        embed = discord.Embed(title="💸 Flingers removidos", color=discord.Color.red())
        embed.add_field(name="Usuário",    value=usuario.mention,    inline=True)
        embed.add_field(name="Removido",   value=f"-{removido} 💵",  inline=True)
        embed.add_field(name="Novo saldo", value=f"{novo_saldo} 💵", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Economy] Admin {interaction.user} removeu {removido} flingers de {usuario} na guild {interaction.guild_id}.")


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))