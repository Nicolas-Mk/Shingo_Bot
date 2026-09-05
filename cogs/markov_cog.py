"""
MarkovCog — Geração de frases por Cadeia de Markov.

Melhorias v2:
  - Limpeza de texto aprimorada (pontuação separada dos tokens)
  - Anti-plágio: descarta frases idênticas a mensagens já vistas
  - Anti-loop: detecta e interrompe ciclos na geração
  - Geração por início aleatório (não só do token INICIO)
  - Novo comando /markov_treinar para importar histórico do Discord via API

Melhorias v3:
  - Rastreamento de origem: cada transição do modelo agora sabe de qual
    mensagem original ela veio.
  - Anti-colagem-única: a geração não pode "andar" mais que N tokens
    seguidos dentro da mesma mensagem de origem — ao atingir o limite,
    é forçada a pular para uma transição vinda de outra fonte.
  - Colagem mínima obrigatória: frases com mais de LIMIAR_PALAVRAS_COLAGEM
    palavras só são aceitas se tiverem contribuição de pelo menos
    MIN_FONTES_COLAGEM mensagens diferentes (evita reproduzir quase
    verbatim uma única frase de um usuário).
"""

import re
import random
import sqlite3
import unicodedata
from collections import defaultdict

import discord
from discord.ext import commands
from discord import app_commands

from cogs.config_cog import modulo_ativo


# ──────────────────────────────────────────────────────────────────
#  CONFIGURAÇÕES
# ──────────────────────────────────────────────────────────────────

ORDEM              = 2      # 2 = mais criativo / 3 = mais coerente
TRIGGER_CHANCE     = 0.01   # 1% de chance por mensagem de o bot falar sozinho
MIN_MSGS_TREINO    = 100    # Mínimo de mensagens antes de gerar frases
MAX_PALAVRAS       = 30     # Tamanho máximo da frase gerada
MIN_PALAVRAS       = 5      # Tamanho mínimo aceitável
HISTORICO_LIMITE   = 3000   # Mensagens do banco carregadas na inicialização
                             # (reduzido de 100k p/ manter o modelo mais "afiado"
                             #  com o vocabulário/estilo recente do servidor)
TENTATIVAS_GERACAO = 6      # Quantas vezes tenta gerar antes de desistir
INICIO_ALEATORIO_CHANCE = 0.4   # 40% das vezes começa por um bigrama aleatório
                                # em vez do token __INICIO__ (mais variedade)

# ── Anti-colagem-única (v3.1: parar em vez de forçar salto) ─────
# Em vez de forçar uma troca de fonte no meio da frase (o que gerava
# saltos de assunto sem sentido), a cadeia sempre escolhe a transição
# mais natural. Só interrompemos a geração se ela ficar "colada" demais
# numa ÚNICA mensagem de origem — sinal de que está reproduzindo aquela
# frase quase inteira. Isso encurta a frase em vez de deformá-la.
MAX_TRECHO_MESMA_FONTE = 7   # nº máx. de palavras seguidas vindas da mesma
                              # mensagem de origem antes de parar a geração

_INICIO = "__INICIO__"
_FIM    = "__FIM__"

# ── Regex de limpeza ─────────────────────────────────────────────
# Remove: menções, canais, emojis animados/estáticos, links, comandos slash/prefixo
_RE_REMOVER = re.compile(
    r"(<@!?\d+>|<#\d+>|<a?:\w+:\d+>|https?://\S+|discord\.gg/\S+|[/!?]\S+)",
    re.IGNORECASE,
)
# Separa pontuação dos tokens: "oi," → "oi ,"  |  "né?" → "né ?"
_RE_PONTUACAO = re.compile(r"([.,!?;:\"'()\[\]{}…])")


# ──────────────────────────────────────────────────────────────────
#  MODELO
# ──────────────────────────────────────────────────────────────────

class ModeloMarkov:
    """
    Cadeia de Markov de ordem N com anti-plágio, anti-loop e anti-colagem-única.

    Estrutura interna do modelo:
        {estado_tuple: {token: {msg_id: contagem}}}

    Guardar a contagem por `msg_id` (em vez de só um total) permite saber,
    a qualquer momento da geração, de qual mensagem original cada palavra
    escolhida "puxou" — e assim forçar que a frase final seja uma colagem
    de várias mensagens, não uma cópia quase literal de uma só.
    """

    def __init__(self, ordem: int = 2):
        self.ordem = ordem
        self.modelo: dict[tuple, dict[str, dict[int, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self.total_treinadas = 0
        # Conjunto normalizado das mensagens vistas — usado para anti-plágio
        self._frases_vistas: set[str] = set()
        # Contador incremental de ids de mensagem (só precisa ser único
        # dentro desta instância do modelo)
        self._proximo_id = 0

    # ── limpeza ──────────────────────────────────────────────────

    @staticmethod
    def _normalizar(texto: str) -> str:
        """Versão normalizada para comparação (sem acentos, minúsculo, sem espaços extras)."""
        nfkd = unicodedata.normalize("NFKD", texto)
        sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", sem_acento.lower()).strip()

    @staticmethod
    def _limpar(texto: str) -> str:
        """Prepara o texto para tokenização."""
        texto = _RE_REMOVER.sub(" ", texto)
        texto = _RE_PONTUACAO.sub(r" \1 ", texto)   # separa pontuação
        texto = re.sub(r"\s+", " ", texto)           # normaliza espaços
        return texto.strip()

    # ── treinamento ──────────────────────────────────────────────

    def treinar(self, texto: str):
        texto_limpo = self._limpar(texto)
        if not texto_limpo:
            return
        palavras = texto_limpo.lower().split()
        if len(palavras) < 2:
            return

        # Registra frase original normalizada para anti-plágio
        self._frases_vistas.add(self._normalizar(texto))

        # Cada mensagem treinada ganha um id único, usado para rastrear
        # de onde cada transição do modelo veio.
        msg_id = self._proximo_id
        self._proximo_id += 1

        seq = [_INICIO] * self.ordem + palavras + [_FIM]
        for i in range(len(seq) - self.ordem):
            estado  = tuple(seq[i : i + self.ordem])
            proxima = seq[i + self.ordem]
            self.modelo[estado][proxima][msg_id] += 1

        self.total_treinadas += 1

    def treinar_lote(self, mensagens: list[str]):
        for m in mensagens:
            self.treinar(m)

    # ── geração ──────────────────────────────────────────────────

    def _escolher_estado_inicial(self) -> tuple:
        """
        Com INICIO_ALEATORIO_CHANCE de probabilidade, escolhe um bigrama
        que começa uma frase (não usa __INICIO__); caso contrário, usa o
        token padrão de início. Isso aumenta a variedade das frases.
        """
        estado_padrao = tuple([_INICIO] * self.ordem)

        if random.random() > INICIO_ALEATORIO_CHANCE:
            if estado_padrao in self.modelo:
                return estado_padrao

        # Filtra estados que não contêm tokens internos para servir de início
        estados_validos = [
            e for e in self.modelo
            if _INICIO not in e and _FIM not in e
        ]
        if estados_validos:
            return random.choice(estados_validos)

        return estado_padrao

    def _escolher_proximo(
        self, candidatos: dict[str, dict[int, int]]
    ) -> tuple[str, int]:
        """
        Escolhe o próximo token de forma puramente proporcional à frequência
        real das transições (sem forçar troca de fonte — isso é o que
        mantém a frase coerente). Também sorteia, dentre as mensagens que
        contribuíram para o token escolhido, qual delas será "creditada"
        como origem deste passo (usado só para o anti-colagem-única).
        """
        tokens = list(candidatos.keys())
        pesos = [sum(fontes.values()) for fontes in candidatos.values()]
        tok = random.choices(tokens, weights=pesos, k=1)[0]
        fontes = candidatos[tok]
        fonte = random.choices(
            list(fontes.keys()), weights=list(fontes.values()), k=1
        )[0]
        return tok, fonte

    def _gerar_uma_vez(self, max_palavras: int, min_palavras: int) -> str | None:
        if not self.modelo:
            return None

        estado = self._escolher_estado_inicial()
        geradas: list[str] = []
        fontes_sequencia: list[int] = []   # fonte atribuída a cada palavra gerada
        vistos: set[tuple] = set()          # anti-loop: detecta estados repetidos

        streak_fonte: int | None = None
        streak_tamanho = 0

        for _ in range(max_palavras):
            # Anti-loop: se voltou a um estado já visitado, para
            if estado in vistos:
                break
            vistos.add(estado)

            candidatos = self.modelo.get(estado)
            if not candidatos:
                break

            prox, fonte = self._escolher_proximo(candidatos)

            if prox == _FIM:
                break
            if prox not in (_INICIO, _FIM):
                geradas.append(prox)
                fontes_sequencia.append(fonte)

            if fonte == streak_fonte:
                streak_tamanho += 1
            else:
                streak_fonte = fonte
                streak_tamanho = 1

            estado = tuple(list(estado[1:]) + [prox])

            # Anti-colagem-única "suave": paramos aqui em vez de forçar um
            # salto de assunto. A frase fica só um pouco mais curta, mas
            # continua fazendo sentido.
            if streak_tamanho >= MAX_TRECHO_MESMA_FONTE:
                break

        if len(geradas) < min_palavras:
            return None

        # Reconstrói: junta tokens e trata pontuação (remove espaço antes de vírgula etc.)
        frase = " ".join(geradas)
        frase = re.sub(r"\s([.,!?;:])", r"\1", frase)
        frase = frase[0].upper() + frase[1:]
        return frase

    def gerar(
        self,
        max_palavras: int = MAX_PALAVRAS,
        min_palavras: int = MIN_PALAVRAS,
        tentativas:   int = TENTATIVAS_GERACAO,
    ) -> str | None:
        """
        Tenta gerar até `tentativas` vezes; descarta frases que sejam
        cópias de mensagens já vistas (anti-plágio) ou que, sendo longas,
        não atinjam a colagem mínima de fontes exigida.
        """
        for _ in range(tentativas):
            frase = self._gerar_uma_vez(max_palavras, min_palavras)
            if frase is None:
                continue
            # Anti-plágio: rejeita se for igual a alguma mensagem original
            if self._normalizar(frase) not in self._frases_vistas:
                return frase
        return None


# ──────────────────────────────────────────────────────────────────
#  COG
# ──────────────────────────────────────────────────────────────────

class MarkovCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._modelos: dict[int, ModeloMarkov] = {}

    # ── helpers ──────────────────────────────────────────────────

    def _modelo(self, guild_id: int) -> ModeloMarkov:
        if guild_id not in self._modelos:
            m = ModeloMarkov(ordem=ORDEM)
            self._carregar_historico(guild_id, m)
            self._modelos[guild_id] = m
        return self._modelos[guild_id]

    def _carregar_historico(self, guild_id: int, modelo: ModeloMarkov):
        try:
            with sqlite3.connect("usuarios.db") as conn:
                rows = conn.execute(
                    "SELECT mensagem FROM mensagens WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
                    (guild_id, HISTORICO_LIMITE),
                ).fetchall()
            textos = [r[0] for r in rows if r[0]]
            modelo.treinar_lote(textos)
            print(f"[Markov] Guild {guild_id}: {len(textos)} mensagens carregadas.")
        except Exception as e:
            print(f"[Markov] Erro ao carregar historico: {e}")

    def _salvar(self, message: discord.Message):
        texto = message.content.strip()
        if not texto:
            return
        try:
            with sqlite3.connect("usuarios.db") as conn:
                conn.execute(
                    "INSERT INTO mensagens (usuario_id, mensagem, canal, data_envio, guild_id) VALUES (?, ?, ?, datetime('now'), ?)",
                    (message.author.id, texto, str(message.channel), message.guild.id),
                )
                conn.commit()
        except Exception as e:
            print(f"[Markov] Erro ao salvar mensagem: {e}")

    # ── listener ─────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not modulo_ativo(message.guild.id, "markov"):
            return
        if message.content.startswith(("/", "!", "?")):
            return

        modelo = self._modelo(message.guild.id)
        modelo.treinar(message.content)
        self._salvar(message)

        # Mantém o modelo em memória alinhado ao limite de histórico:
        # ao ultrapassar o limite, reconstrói a partir do banco (últimas N mensagens)
        if modelo.total_treinadas > HISTORICO_LIMITE:
            modelo_novo = ModeloMarkov(ordem=ORDEM)
            self._carregar_historico(message.guild.id, modelo_novo)
            self._modelos[message.guild.id] = modelo_novo
            modelo = modelo_novo

        if modelo.total_treinadas < MIN_MSGS_TREINO:
            return
        if random.random() < TRIGGER_CHANCE:
            frase = modelo.gerar()
            if frase:
                await message.channel.send(frase)

    # ── slash commands ────────────────────────────────────────────

    @app_commands.command(name="markov", description="Gera uma frase aleatoria com base no historico do chat")
    async def markov_cmd(self, interaction: discord.Interaction):
        if not modulo_ativo(interaction.guild_id, "markov"):
            await interaction.response.send_message(
                "🚫 O módulo Markov está desativado neste servidor.", ephemeral=True
            )
            return
        modelo = self._modelo(interaction.guild_id)
        if modelo.total_treinadas < MIN_MSGS_TREINO:
            await interaction.response.send_message(
                f"Ainda aprendendo... ({modelo.total_treinadas}/{MIN_MSGS_TREINO} mensagens coletadas)",
                ephemeral=True,
            )
            return
        frase = modelo.gerar()
        if frase:
            await interaction.response.send_message(frase)
        else:
            await interaction.response.send_message(
                "Nao consegui montar uma frase nova agora. Tente de novo!", ephemeral=True
            )

    @app_commands.command(name="markov_stats", description="Estatisticas do modelo Markov deste servidor")
    async def markov_stats(self, interaction: discord.Interaction):
        if not modulo_ativo(interaction.guild_id, "markov"):
            await interaction.response.send_message(
                "🚫 O módulo Markov está desativado neste servidor.", ephemeral=True
            )
            return
        modelo = self._modelo(interaction.guild_id)
        embed = discord.Embed(title="Markov — Estatisticas", color=discord.Color.purple())
        embed.add_field(name="Mensagens aprendidas", value=f"{modelo.total_treinadas:,}",    inline=True)
        embed.add_field(name="Frases no dicionario", value=f"{len(modelo._frases_vistas):,}", inline=True)
        embed.add_field(name="Estados no modelo",    value=f"{len(modelo.modelo):,}",        inline=True)
        embed.add_field(name="Ordem da cadeia",      value=str(modelo.ordem),                inline=True)
        embed.add_field(name="Chance de falar",      value=f"{TRIGGER_CHANCE * 100:.1f}%",   inline=True)
        embed.add_field(name="Min. para ativar",     value=str(MIN_MSGS_TREINO),             inline=True)
        embed.add_field(name="Limite de historico",  value=f"{HISTORICO_LIMITE:,}",          inline=True)
        embed.add_field(name="Max. trecho de 1 fonte", value=f"{MAX_TRECHO_MESMA_FONTE} palavras", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="markov_treinar",
        description="[Admin] Importa historico de um canal para treinar o modelo"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        canal="Canal para importar (padrao: canal atual)",
        limite="Quantas mensagens importar (max 2000, padrao 500)"
    )
    async def markov_treinar(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        limite: int = 500,
    ):
        if not modulo_ativo(interaction.guild_id, "markov"):
            await interaction.response.send_message(
                "🚫 O módulo Markov está desativado neste servidor.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        canal   = canal or interaction.channel
        limite  = max(1, min(limite, 2000))
        modelo  = self._modelo(interaction.guild_id)
        contagem = 0

        async for msg in canal.history(limit=limite, oldest_first=False):
            if msg.author.bot:
                continue
            if msg.content.startswith(("/", "!", "?")):
                continue
            texto = msg.content.strip()
            if not texto:
                continue
            modelo.treinar(texto)
            # Persiste no banco para sobreviver ao restart
            try:
                with sqlite3.connect("usuarios.db") as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO mensagens (usuario_id, mensagem, canal, data_envio, guild_id) VALUES (?, ?, ?, ?, ?)",
                        (msg.author.id, texto, str(canal), msg.created_at.strftime("%Y-%m-%d %H:%M:%S"), interaction.guild_id),
                    )
                    conn.commit()
            except Exception:
                pass
            contagem += 1

        await interaction.followup.send(
            f"Importadas **{contagem}** mensagens de {canal.mention}. "
            f"Total no modelo: **{modelo.total_treinadas:,}**",
            ephemeral=True,
        )