"""
InsultCog — Shingo responde a xingamentos na altura certa.

3 níveis de xingamento (detecção e resposta):
  Nível 1 — Xingamentos leves (palavrões básicos, insultos suaves)
  Nível 2 — Xingamentos médios (ofensas pesadas, palavrões fortes)
  Nível 3 — Xingamentos pesados (combinações de ofensas, ataques diretos ao bot)

O Shingo SÓ responde quando é o alvo, detectado por:
  - Menção via @
  - Nome "shingo" na mensagem
  - Contexto: usuário citou o nome do bot na mensagem ANTERIOR
    (janela de CONTEXTO_JANELA segundos)

Exemplo de contexto:
  Usuário: "Shingo"          ← bot registra intenção
  Usuário: "vai tomar no cu" ← bot responde como se fosse direcionado
"""

import re
import time
import random
import discord
from discord.ext import commands


# ──────────────────────────────────────────────────────────────────
#  CONFIGURAÇÕES
# ──────────────────────────────────────────────────────────────────

# Nomes/apelidos do bot (minúsculo) — ajuste conforme necessário
NOMES_BOT = ["shingo"]

# Janela de contexto em segundos: quanto tempo após citar o nome do bot
# uma mensagem subsequente ainda é considerada direcionada a ele
CONTEXTO_JANELA = 30

# Cooldown de resposta por usuário (segundos) para evitar flood
COOLDOWN_RESPOSTA = 30


# ──────────────────────────────────────────────────────────────────
#  LISTAS DE PALAVRAS-CHAVE POR NÍVEL
# ──────────────────────────────────────────────────────────────────

NIVEL_1_PALAVRAS = [
    "merda", "bosta", "idiota", "burro", "estúpido", "lerdo",
    "bobo", "trouxa", "panaca", "babaca", "otário", "imbecil",
    "inútil", "ridículo", "patético", "besta",
]

NIVEL_2_PALAVRAS = [
    "corno", "viado", "vagabundo", "safado", "desgraçado",
    "arrombado", "vai se foder", "seu lixo", "animal",
    "miserável", "nojento", "lazarento", "pilantra", "canalha", "vai a merda",
]

NIVEL_3_PALAVRAS = [
    "filho da puta", "fdp", "sua mãe", "puta que pariu", "pqp"
    "vai tomar no cu", "vsf", "vai se fuder", 
    "porra", "cacete", "puta merda", "smt", "se mata", "pacóvio", "filho de",
]


# ──────────────────────────────────────────────────────────────────
#  RESPOSTAS POR NÍVEL
# ──────────────────────────────────────────────────────────────────

RESPOSTAS_NIVEL_1 = [
    "Vai você, seu {insult}! 😒",
    "Olha quem tá falando, seu {insult}!",
    "Aprende a falar direito antes de me xingar, {insult}.",
    "Sério? Isso foi o melhor que você conseguiu, {insult}?",
    "Tô até bocejando com esse xingamento fraco, {insult}.",
    "Que criativo... {insult}. Tenta de novo.",
    "Manda ver mais, {insult}. Não tô impressionado.",
    "Seu {insult}, eu já ouvi coisa melhor de um peixe.",
]

RESPOSTAS_NIVEL_2 = [
    "Cuidado com o que fala, seu {insult} fedorento!",
    "Falou e disse, seu {insult}! Agora vai lavar a boca.",
    "Que língua suja! Cala essa boca, {insult}.",
    "Não me testa, seu {insult} de merda!",
    "Olha a boca, {insult}! Sua mãe sabe que fala assim?",
    "Se continuar assim, seu {insult}, vou ignorar você pro resto da vida.",
    "Já vi cachorro com mais educação do que você, {insult}.",
    "Vai chorar no canto, {insult}. Ninguém pediu sua opinião.",
]

RESPOSTAS_NIVEL_3 = [
    "PARA LOGO, seu {insult} miserável! Vai tomar banho!",
    "Tô de saco cheio de você, seu {insult} do inferno!",
    "Pode ir embora, seu {insult} insuportável! Ninguém te quer aqui!",
    "Qual é a sua? Me respeita, seu {insult} lazarento!",
    "Sai daqui, seu {insult} de quinta categoria! Vai incomodar outro!",
    "Me xinga de novo e vou banir você pessoalmente, seu {insult} maldito!",
    "Você não vale nem a energia que gastei respondendo, seu {insult} ordinário!",
    "Que falta de respeito! Vai à merda você também, seu {insult} sem noção!",
]

INSULTOS_RETORNO = {
    1: ["trouxa", "bobo", "panaca", "lerdo", "tapado", "paspalho", "pamonha"],
    2: ["canalha", "pilantra", "safado", "miserável", "vagabundo", "lazarento"],
    3: ["arrombado", "sem-vergonha", "mal-educado", "desgraçado", "insuportável", "escroto", "saco de bosta"],
}


# ──────────────────────────────────────────────────────────────────
#  FUNÇÕES DE DETECÇÃO
# ──────────────────────────────────────────────────────────────────

def normalizar(texto: str) -> str:
    """Minúsculo e sem pontuação para comparações."""
    return re.sub(r"[^\w\s]", " ", texto.lower())


def menciona_bot(texto: str, bot_user: discord.User | None = None) -> bool:
    """Verifica se a mensagem menciona o bot pelo nome ou via @."""
    texto_norm = normalizar(texto)
    for nome in NOMES_BOT:
        # Usa \b para evitar falsos positivos (ex: "shingold")
        if re.search(r"\b" + re.escape(nome) + r"\b", texto_norm):
            return True
    return False


def detectar_nivel(texto: str) -> int | None:
    """
    Retorna o nível mais alto de xingamento encontrado no texto,
    ou None se não houver xingamento.
    """
    texto_norm = normalizar(texto)
    nivel = None

    for palavra in NIVEL_1_PALAVRAS:
        if re.search(r"\b" + re.escape(palavra) + r"\b", texto_norm):
            nivel = 1
            break

    for palavra in NIVEL_2_PALAVRAS:
        if re.search(r"\b" + re.escape(palavra) + r"\b", texto_norm):
            nivel = 2
            break

    for frase in NIVEL_3_PALAVRAS:
        if frase in texto_norm:
            nivel = 3
            break

    return nivel


def gerar_resposta(nivel: int, mencao_autor: str | None = None) -> str:
    """Monta a resposta no nível indicado."""
    templates = {1: RESPOSTAS_NIVEL_1, 2: RESPOSTAS_NIVEL_2, 3: RESPOSTAS_NIVEL_3}
    template = random.choice(templates[nivel])
    insulto = random.choice(INSULTOS_RETORNO[nivel])
    resposta = template.replace("{insult}", insulto)

    if mencao_autor and random.random() < 0.3:
        resposta = f"{mencao_autor}, {resposta[0].lower()}{resposta[1:]}"

    return resposta


# ──────────────────────────────────────────────────────────────────
#  COG
# ──────────────────────────────────────────────────────────────────

class InsultCog(commands.Cog):
    """Shingo só responde xingamentos quando é o alvo."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # {(guild_id, user_id): timestamp}
        # Registra quando o usuário citou o nome do bot pela última vez
        # sem xingamento junto — abre a janela de contexto
        self._contexto_aberto: dict[tuple, float] = {}

        # Cooldown de resposta por usuário
        self._cooldowns: dict[tuple, float] = {}

    # ── helpers ──────────────────────────────────────────────────

    def _chave(self, message: discord.Message) -> tuple:
        return (message.guild.id, message.author.id)

    def _em_cooldown(self, chave: tuple) -> bool:
        agora = time.time()
        if agora - self._cooldowns.get(chave, 0) < COOLDOWN_RESPOSTA:
            return True
        self._cooldowns[chave] = agora
        return False

    def _abrir_contexto(self, chave: tuple):
        """Marca que o usuário acabou de citar o bot (sem xingamento)."""
        self._contexto_aberto[chave] = time.time()

    def _contexto_ativo(self, chave: tuple) -> bool:
        """Retorna True se o usuário citou o bot há menos de CONTEXTO_JANELA segundos."""
        ultimo = self._contexto_aberto.get(chave, 0)
        return (time.time() - ultimo) < CONTEXTO_JANELA

    def _fechar_contexto(self, chave: tuple):
        self._contexto_aberto.pop(chave, None)

    # ── listener ─────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if message.content.startswith(("/", "!", "?")):
            return

        chave = self._chave(message)
        texto = message.content

        bot_citado = (
            self.bot.user in message.mentions
            or menciona_bot(texto)
        )
        nivel = detectar_nivel(texto)

        # ── Caso 1: bot citado na mesma mensagem com xingamento ──
        if bot_citado and nivel is not None:
            self._fechar_contexto(chave)          # contexto consumido
            if not self._em_cooldown(chave):
                mencao = message.author.mention if random.random() < 0.3 else None
                await message.channel.send(gerar_resposta(nivel, mencao))
            return

        # ── Caso 2: bot citado sem xingamento → abre janela de contexto ──
        if bot_citado and nivel is None:
            self._abrir_contexto(chave)
            return

        # ── Caso 3: mensagem seguinte de quem abriu contexto ──
        if self._contexto_ativo(chave) and nivel is not None:
            self._fechar_contexto(chave)          # consome o contexto
            if not self._em_cooldown(chave):
                mencao = message.author.mention if random.random() < 0.3 else None
                await message.channel.send(gerar_resposta(nivel, mencao))
            return

        # ── Se não citou o bot e não há contexto ativo: ignora ──
        # (Qualquer xingamento genérico no chat sem envolver o Shingo é ignorado)
        if nivel is not None and not bot_citado:
            # Fecha contexto se o usuário mandar outra coisa sem xingamento
            # para não acumular contextos velhos (tratado pelo timeout de CONTEXTO_JANELA)
            pass


# ──────────────────────────────────────────────────────────────────
#  SETUP
# ──────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(InsultCog(bot))