import json
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from discord import app_commands

from cogs.config_cog import get_config

MAL_API_BASE = "https://api.myanimelist.net/v2"
BRASILIA = timezone(timedelta(hours=-3))

PONTOS_TOP = 0.5         # pontos por aparecer nos favoritos do MAL de um membro
MIN_MEMBROS = 5         # mínimo de membros que assistiram p/ entrar na média
TOP_SERVIDOR_SIZE = 50  # quantos animes compõem o ranking total
PAGE_SIZE = 10           # itens exibidos por página


# ──────────────────────────────────────────────────────────────────
#  Helpers de banco
# ──────────────────────────────────────────────────────────────────

def carregar_usuarios(guild_id: int) -> list[str]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT username FROM mal_usuarios WHERE guild_id = ?", (guild_id,))
        rows = c.fetchall()
    return [r[0] for r in rows]


def carregar_snapshots_anime(username: str) -> list[dict]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT item_id, snapshot, is_favorite FROM mal_snapshots "
            "WHERE username = ? AND tipo = 'anime'",
            (username,)
        )
        rows = c.fetchall()

    result = []
    for item_id, snap_json, is_fav in rows:
        try:
            snap = json.loads(snap_json)
            snap["item_id"] = item_id
            snap["is_favorite"] = bool(is_fav)
            result.append(snap)
        except Exception:
            pass
    return result


# ──────────────────────────────────────────────────────────────────
#  Lógica de ranking
# ──────────────────────────────────────────────────────────────────

def calcular_ranking(todos_snapshots: dict[str, list[dict]]) -> list[dict]:
    acumulado: dict[str, dict] = {}

    def get_or_create(item_id: str) -> dict:
        if item_id not in acumulado:
            acumulado[item_id] = {
                "item_id": item_id,
                "pontos_top": 0,
                "notas": [],
                "membros_assistiram": 0,
                "membros_no_top": [],
            }
        return acumulado[item_id]

    for username, snaps in todos_snapshots.items():
        top_ids = {s["item_id"] for s in snaps if s.get("is_favorite")}

        for snap in snaps:
            item_id = snap["item_id"]
            score = snap.get("score", 0)
            status = snap.get("status", "")

            if status not in ("completed", "watching", "on_hold", "dropped") and score == 0:
                continue

            entrada = get_or_create(item_id)
            entrada["membros_assistiram"] += 1

            if score > 0:
                entrada["notas"].append(score)

            if item_id in top_ids:
                entrada["pontos_top"] += PONTOS_TOP
                entrada["membros_no_top"].append(username)

    resultado = []
    for item_id, dados in acumulado.items():
        notas = dados["notas"]
        membros = dados["membros_assistiram"]

        if membros == 0:
            continue

        media = round(sum(notas) / len(notas), 2) if len(notas) >= MIN_MEMBROS else 0.0
        dados["media_servidor"] = media
        dados["pontuacao_total"] = dados["pontos_top"] + media
        resultado.append(dados)

    resultado.sort(key=lambda x: (x["pontuacao_total"], x["membros_assistiram"]), reverse=True)
    return resultado[:TOP_SERVIDOR_SIZE]


# ──────────────────────────────────────────────────────────────────
#  Busca títulos na API do MAL
# ──────────────────────────────────────────────────────────────────

async def buscar_titulos_mal(
    session: aiohttp.ClientSession,
    item_ids: list[str],
    mal_client_id: str,
) -> dict[str, dict]:
    import os
    headers = {"X-MAL-CLIENT-ID": mal_client_id or os.getenv("MAL_CLIENT_ID", "")}
    titulos = {}

    async def fetch_one(item_id: str):
        url = f"{MAL_API_BASE}/anime/{item_id}"
        params = {"fields": "title,main_picture"}
        try:
            async with session.get(
                url, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    titulos[item_id] = {
                        "title": data.get("title", f"Anime #{item_id}"),
                        "image_url": (
                            data.get("main_picture", {}).get("medium")
                            or data.get("main_picture", {}).get("large")
                        ),
                    }
                else:
                    titulos[item_id] = {"title": f"Anime #{item_id}", "image_url": None}
        except Exception:
            titulos[item_id] = {"title": f"Anime #{item_id}", "image_url": None}

    sem = asyncio.Semaphore(5)

    async def fetch_with_sem(item_id):
        async with sem:
            await fetch_one(item_id)
            await asyncio.sleep(0.3)

    await asyncio.gather(*[fetch_with_sem(iid) for iid in item_ids])
    return titulos


# ──────────────────────────────────────────────────────────────────
#  Montagem de embed por página
# ──────────────────────────────────────────────────────────────────

def build_embed(
    ranking: list[dict],
    titulos: dict[str, dict],
    page: int,
    total_pages: int,
    num_usuarios: int,
) -> discord.Embed:
    agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    itens_pagina = ranking[start:end]

    medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}

    embed = discord.Embed(
        title="🏆 Top do Servidor — MyAnimeList",
        description=f"Baseado em **{num_usuarios}** usuário(s) · {agora}",
        color=0x2E51A2,
    )
    embed.set_thumbnail(
        url="https://image.myanimelist.net/ui/OK6W_koKDTOqqqLDbIoPAiC8a86sHufn_jOI-JGtoCQ"
    )

    for entry in itens_pagina:
        pos = ranking.index(entry) + 1
        item_id = entry["item_id"]
        info = titulos.get(item_id, {})
        titulo = info.get("title", f"Anime #{item_id}")
        url = f"https://myanimelist.net/anime/{item_id}"

        pontos_top = entry["pontos_top"]
        media_srv = entry["media_servidor"]
        membros = entry["membros_assistiram"]
        membros_no_top = entry["membros_no_top"]
        total = entry["pontuacao_total"]

        prefixo = medalhas.get(pos, f"#{pos}")

        # Linha 1: posição + título linkado
        # Linhas seguintes: detalhes
        linhas = [f"**{prefixo}**  [{titulo}]({url})"]

        if media_srv > 0:
            linhas.append(f"📊 Média servidor: **{media_srv:.2f}/10** ({membros} membros)")
        else:
            linhas.append(f"👥 {membros} membro(s) assistiram")

        if pontos_top > 0:
            tops_count = int(round(pontos_top / PONTOS_TOP))
            nomes = ", ".join(f"`{u}`" for u in membros_no_top[:5])
            if len(membros_no_top) > 5:
                nomes += f" +{len(membros_no_top) - 5}"
            linhas.append(f"🌟 Favorito de: **{tops_count}** membro(s): {nomes}")

        linhas.append(f"🧮 Total: **{total:.1f} pts**")

        embed.add_field(
            name="\u200b",
            value="\n".join(linhas),
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Página {page + 1}/{total_pages} · "
            f"Média = mín. {MIN_MEMBROS} membros com nota"
        )
    )
    return embed


# ──────────────────────────────────────────────────────────────────
#  View de paginação
# ──────────────────────────────────────────────────────────────────

class RankingView(discord.ui.View):
    def __init__(
        self,
        ranking: list[dict],
        titulos: dict[str, dict],
        num_usuarios: int,
        autor_id: int,
    ):
        super().__init__(timeout=300)
        self.ranking = ranking
        self.titulos = titulos
        self.num_usuarios = num_usuarios
        self.autor_id = autor_id
        self.page = 0
        self.total_pages = (len(ranking) + PAGE_SIZE - 1) // PAGE_SIZE
        self._atualizar_botoes()

    def _atualizar_botoes(self):
        self.btn_primeira.disabled = self.page == 0
        self.btn_anterior.disabled = self.page == 0
        self.btn_proxima.disabled = self.page >= self.total_pages - 1
        self.btn_ultima.disabled = self.page >= self.total_pages - 1

    def embed_atual(self) -> discord.Embed:
        return build_embed(
            self.ranking, self.titulos, self.page,
            self.total_pages, self.num_usuarios
        )

    async def _checar_autor(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "❌ Apenas quem usou o comando pode navegar nas páginas.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def btn_primeira(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = 0
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def btn_anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = max(0, self.page - 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def btn_proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = min(self.total_pages - 1, self.page + 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def btn_ultima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = self.total_pages - 1
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)


# ──────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────

class MalTopCog(commands.Cog):
    """Gera o Top 100 do Servidor combinando os tops pessoais e as médias do servidor."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="mal_top_servidor",
        description="Mostra o top 100 de animes do servidor (10 por página).",
    )
    async def mal_top_servidor(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        usuarios = carregar_usuarios(guild_id)

        if not usuarios:
            await interaction.followup.send(
                "📋 Nenhum usuário monitorado ainda. Use `/mal_entrar` para se adicionar!",
                ephemeral=True,
            )
            return

        todos_snapshots: dict[str, list[dict]] = {}
        for username in usuarios:
            snaps = carregar_snapshots_anime(username)
            if snaps:
                todos_snapshots[username] = snaps

        if not todos_snapshots:
            await interaction.followup.send(
                "⚠️ Nenhum dado sincronizado ainda. Aguarde a primeira sincronização completar.",
                ephemeral=True,
            )
            return

        ranking = calcular_ranking(todos_snapshots)

        if not ranking:
            await interaction.followup.send(
                "📊 Dados insuficientes para gerar o ranking. "
                "É preciso que os membros tenham animes com notas em suas listas.",
                ephemeral=True,
            )
            return

        import os
        mal_client_id = os.getenv("MAL_CLIENT_ID", "")
        item_ids = [entry["item_id"] for entry in ranking]

        async with aiohttp.ClientSession() as session:
            titulos = await buscar_titulos_mal(session, item_ids, mal_client_id)

        num_usuarios = len(todos_snapshots)
        view = RankingView(
            ranking=ranking,
            titulos=titulos,
            num_usuarios=num_usuarios,
            autor_id=interaction.user.id,
        )

        await interaction.followup.send(embed=view.embed_atual(), view=view)

    @mal_top_servidor.error
    async def mal_top_servidor_error(self, interaction: discord.Interaction, error):
        await interaction.followup.send(
            f"❌ Ocorreu um erro ao gerar o ranking: `{error}`", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MalTopCog(bot))