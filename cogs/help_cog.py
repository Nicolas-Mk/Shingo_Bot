import discord
from discord.ext import commands
from discord import app_commands

# ──────────────────────────────────────────────────────────────────
#  /ajuda — central de ajuda com menu de categorias
# ──────────────────────────────────────────────────────────────────
#
# Os nomes/descrições dos comandos são puxados AO VIVO da árvore de
# comandos do bot (bot.tree) — este arquivo só define o AGRUPAMENTO por
# categoria (lista de nomes) e um texto de contexto por categoria. Assim,
# se a descrição de um comando mudar no cog original, a ajuda atualiza
# sozinha, sem precisar editar duas vezes.
#
# Cada entrada de HELP_CATEGORIAS:
#   emoji    -> usado no título da categoria e na opção do menu
#   titulo   -> nome da categoria
#   resumo   -> uma linha, usada na página "Visão geral"
#   nota     -> texto opcional sobre recursos passivos dessa categoria
#               (coisas que acontecem sozinhas, sem comando — ex.: XP de
#               call, desafios de digitação automáticos)
#   comandos -> nomes dos slash commands, na ordem que devem aparecer
#               (comandos [Admin] ficam no fim da lista de cada categoria;
#               a tag [Admin] já vem embutida na própria descrição)
#
# Ao criar um comando novo, basta adicionar o nome na categoria certa aqui.
#
# Limites do Discord a ter em mente: um embed aceita no máximo 25 campos (um por
# comando) e o menu de categorias no máximo 25 opções.

HELP_CATEGORIAS: dict[str, dict] = {
    "economia": {
        "emoji": "💰",
        "titulo": "Economia",
        "resumo": "Flingers, trabalho, roubo e desafios de digitação.",
        "nota": (
            "O bot também posta desafios de digitação aleatórios no canal "
            "configurado (`/config_canal`) — quem digitar certo primeiro "
            "ganha flingers."
        ),
        "comandos": [
            "saldo", "trabalhar", "depositar", "sacar", "roubar",
            "transferir_flingers", "topflingers", "status_gerador",
            "admin_add_flingers", "admin_remove_flingers",
        ],
    },
    "loja": {
        "emoji": "🛍️",
        "titulo": "Loja",
        "resumo": "Compre e use itens com seus flingers.",
        "nota": None,
        "comandos": [
            "loja", "loja_comprar", "inventario", "usar_item",
            "adicionar_item", "loja_editar_item", "item_remover",
        ],
    },
    "perfil": {
        "emoji": "👤",
        "titulo": "Perfil & Nível",
        "resumo": "Registro, perfil e ranking de nível/XP.",
        "nota": (
            "XP de nível é ganho automaticamente ficando desmutado em "
            "calls de voz (a cada 15min válidos). O bot avisa no chat "
            "quando alguém sobe de nível."
        ),
        "comandos": ["registrar", "perfil", "editarperfil", "topnivel"],
    },
    "jogos": {
        "emoji": "🎲",
        "titulo": "Jogos",
        "resumo": "Blackjack apostando flingers.",
        "nota": None,
        "comandos": ["blackjack"],
    },
    "mal": {
        "emoji": "📺",
        "titulo": "MyAnimeList",
        "resumo": "Listas de anime/mangá do servidor, rankings e relatórios.",
        "nota": (
            "O bot monitora as listas dos usuários cadastrados com "
            "`/mal_entrar` e posta relatórios automáticos de atualizações "
            "no canal configurado, além de um resumo mensal todo dia 1º. "
            "Os estúdios dos animes novos também são buscados sozinhos "
            "na API do MAL, para alimentar o ranking de estúdios."
        ),
        "comandos": [
            "mal_entrar", "mal_sair", "mal_lista", "mal_avatar",
            "mal_anime", "mal_manga", "mal_top_servidor", "mal_top_servidor_manga",
            "mal_estudios",
            "mal_mensal", "resumo_mes",
            "mal_filtros", "mal_filtro_toggle", "mal_relatorio",
            "mal_forcar_favoritos", "mal_sincronizar_nsfw", "mal_estudios_sync",
            "mal_status", "mal_mensal_forcar", "mal_mensal_status",
        ],
    },
    "quests": {
        "emoji": "📜",
        "titulo": "Quests de Anime",
        "resumo": "Anime sorteado que rende flingers, pedidos da guilda e mural de quests.",
        "nota": (
            "Peça uma quest com `/anime_aleatorio`: o bot sorteia um anime que você ainda "
            "não viu, e você ganha flingers ao marcá-lo como Completed no MAL dentro do "
            "prazo. Para participar é preciso estar registrado (`/registrar`) e ter o "
            "usuário do MAL cadastrado (`/mal_entrar`).\n\n"
            "• Animes de nota baixa pagam um bônus (cata-lixo), e a recompensa sobe a cada "
            "quest recusada no servidor, até alguém aceitar uma.\n"
            "• Gênero e década são filtros opcionais e pagos. Na oferta há um botão de "
            "reroll e, durante a espera entre pedidos, um botão para pular a espera — "
            "tudo isso custa flingers, e o valor aparece na hora.\n"
            "• De tempos em tempos o bot posta um **pedido da guilda** no canal do MAL "
            "Tracker (`/config_canal`): qualquer pessoa pode aceitar, e quem clicar "
            "primeiro leva. O aviso de quest concluída também sai nesse canal."
        ),
        "comandos": [
            "anime_aleatorio", "anime_quests", "anime_abandonar", "anime_mural",
            "anime_pedido_agora",
        ],
    },
    "utilidades": {
        "emoji": "🛠️",
        "titulo": "Utilidades",
        "resumo": "Clima, mensagens anônimas e diversão.",
        "nota": (
            "O bot também troca automaticamente links do X/Twitter por "
            "vxtwitter (pro preview aparecer direito) e avisa quando uma "
            "call de voz enche (módulo `call_cheia`, ativável em `/modulo`)."
        ),
        "comandos": ["clima", "anonimo", "lupagom"],
    },
    "chat": {
        "emoji": "💬",
        "titulo": "Chat Automático",
        "resumo": "O bot aprende com o chat e fala sozinho de vez em quando.",
        "nota": (
            "Depois de aprender o suficiente, o bot tem uma pequena chance "
            "por mensagem de soltar uma frase gerada a partir do histórico "
            "real do servidor."
        ),
        "comandos": ["markov", "markov_stats", "markov_treinar"],
    },
    "config": {
        "emoji": "⚙️",
        "titulo": "Configuração",
        "resumo": "Canais e módulos do bot (admin).",
        "nota": None,
        "comandos": ["config_canal", "config_ver", "modulo", "modulo_status"],
    },
}

COR_EMBED = 0x2E51A2


# ──────────────────────────────────────────────────────────────────
#  Helpers — puxam nome/descrição/parâmetros direto da árvore do bot
# ──────────────────────────────────────────────────────────────────

def _obter_comando(bot: commands.Bot, nome: str) -> app_commands.Command | None:
    for cmd in bot.tree.get_commands():
        if isinstance(cmd, app_commands.Command) and cmd.name == nome:
            return cmd
    return None


def _formatar_assinatura(cmd: app_commands.Command) -> str:
    partes = [f"<{p.name}>" if p.required else f"[{p.name}]" for p in cmd.parameters]
    sufixo = f" {' '.join(partes)}" if partes else ""
    return f"/{cmd.name}{sufixo}"


def _montar_embed_geral() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Central de Ajuda",
        description=(
            "Resumo das principais funcionalidades do bot. Escolha uma "
            "categoria no menu abaixo pra ver os comandos de cada uma.\n\n"
            "**Moeda do servidor:** flingers 💵 — ganhos trabalhando, "
            "respondendo desafios de digitação ou (se tiver coragem) "
            "roubando de outros membros."
        ),
        color=COR_EMBED,
    )
    for dados in HELP_CATEGORIAS.values():
        embed.add_field(
            name=f"{dados['emoji']} {dados['titulo']}",
            value=dados["resumo"],
            inline=False,
        )
    embed.set_footer(text="Comandos marcados [Admin] exigem permissão de administrador.")
    return embed


def _montar_embed_categoria(bot: commands.Bot, categoria: str) -> discord.Embed:
    if categoria not in HELP_CATEGORIAS:
        return _montar_embed_geral()

    dados = HELP_CATEGORIAS[categoria]
    embed = discord.Embed(title=f"{dados['emoji']} {dados['titulo']}", color=COR_EMBED)
    if dados["nota"]:
        embed.description = dados["nota"]

    for nome in dados["comandos"]:
        cmd = _obter_comando(bot, nome)
        if cmd is None:
            # Comando não encontrado na árvore (cog desativado, nome
            # desatualizado etc.) — pula em vez de quebrar a ajuda inteira.
            continue
        embed.add_field(
            name=_formatar_assinatura(cmd),
            value=cmd.description or "\u200b",
            inline=False,
        )

    embed.set_footer(text="Use o menu abaixo pra ver outra categoria.")
    return embed


# ──────────────────────────────────────────────────────────────────
#  View — menu de seleção de categoria
# ──────────────────────────────────────────────────────────────────

class AjudaSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Visão geral", value="geral", emoji="📖"),
            *[
                discord.SelectOption(label=dados["titulo"], value=chave, emoji=dados["emoji"])
                for chave, dados in HELP_CATEGORIAS.items()
            ],
        ]
        super().__init__(placeholder="Escolha uma categoria...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        categoria = self.values[0]
        if categoria == "geral":
            embed = _montar_embed_geral()
        else:
            embed = _montar_embed_categoria(interaction.client, categoria)
        await interaction.response.edit_message(embed=embed, view=self.view)


class AjudaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(AjudaSelect())


# ──────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────

class HelpCog(commands.Cog):
    """Central de ajuda do bot — lista as principais funcionalidades por categoria."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ajuda", description="Mostra as principais funcionalidades do bot, por categoria.")
    async def ajuda(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_montar_embed_geral(), view=AjudaView(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))