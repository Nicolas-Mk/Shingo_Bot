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

# ──────────────────────────────────────────────────────────────────
#  Pontuação do Top do Servidor
#
#  Regras:
#  1. Para cada usuário, pega os top 10 animes com maior nota pessoal.
#     Cada anime que aparece no top de alguém ganha +5 pontos.
#  2. Para cada anime assistido por mais de MIN_MEMBROS membros,
#     calcula a média das notas do servidor (somente notas > 0).
#     Essa média é somada à pontuação do anime.
#  3. O ranking final ordena por pontuação total (top_pts + media_srv).
# ──────────────────────────────────────────────────────────────────

PONTOS_TOP = 0.5         # pontos por aparecer no top-10 pessoal de um membro
MIN_MEMBROS = 3         # mínimo de membros que assistiram p/ entrar na média
TOP_SERVIDOR_SIZE = 10  # quantos animes mostrar no resultado final


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
    """
    Retorna todos os snapshots de anime do usuário como lista de dicts,
    incluindo o item_id e is_favorite para consulta posterior.
    Formato: [{"item_id": str, "score": int, "is_favorite": bool, ...}, ...]
    """
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

def calcular_ranking(
    todos_snapshots: dict[str, list[dict]]
) -> list[dict]:
    """
    Recebe { username: [snap, ...] } com snaps de anime de todos os usuários.
    Retorna lista ordenada por pontuação:
      [
        {
          "item_id": str,
          "pontos_top": int,      # quantas vezes apareceu em top-10 × PONTOS_TOP
          "media_servidor": float,  # média das notas do servidor (0 se < MIN_MEMBROS)
          "pontuacao_total": float,
          "membros_assistiram": int,
          "notas": [int, ...],    # notas de cada membro (somente > 0)
          "membros_no_top": [str, ...],  # usernames cujo top-10 inclui este anime
        },
        ...
      ]
    """
    # item_id → dados acumulados
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
        # Top pessoal = animes marcados como favorito no MAL pelo usuário
        top_ids = {s["item_id"] for s in snaps if s.get("is_favorite")}

        for snap in snaps:
            item_id = snap["item_id"]
            score = snap.get("score", 0)

            # Conta como "assistido" se tem status completed, ou qualquer nota
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

    # Calcula médias e pontuação final
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

    # Ordena: pontuação total ↓, depois membros que assistiram ↓
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
    """
    Busca título e capa de cada anime_id na API do MAL.
    Retorna { item_id: {"title": str, "image_url": str | None} }
    """
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

    # Busca em paralelo com semáforo para não estourar o rate limit
    sem = asyncio.Semaphore(5)

    async def fetch_with_sem(item_id):
        async with sem:
            await fetch_one(item_id)
            await asyncio.sleep(0.3)

    await asyncio.gather(*[fetch_with_sem(iid) for iid in item_ids])
    return titulos


# ──────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────

class MalTopCog(commands.Cog):
    """Gera o Top do Servidor combinando os tops pessoais e as médias do servidor."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="mal_top_servidor",
        description="Mostra o top 10 de animes do servidor com base nos tops pessoais e médias.",
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

        # ── Coleta snapshots do banco (sem chamada à API) ─────────────
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

        # ── Calcula o ranking ─────────────────────────────────────────
        ranking = calcular_ranking(todos_snapshots)

        if not ranking:
            await interaction.followup.send(
                "📊 Dados insuficientes para gerar o ranking. "
                "É preciso que os membros tenham animes com notas em suas listas.",
                ephemeral=True,
            )
            return

        # ── Busca títulos na API do MAL ───────────────────────────────
        import os
        mal_client_id = os.getenv("MAL_CLIENT_ID", "")
        item_ids = [entry["item_id"] for entry in ranking]

        async with aiohttp.ClientSession() as session:
            titulos = await buscar_titulos_mal(session, item_ids, mal_client_id)

        # ── Monta o embed ─────────────────────────────────────────────
        agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        num_usuarios = len(todos_snapshots)

        embed = discord.Embed(
            title="🏆 Top do Servidor — MyAnimeList",
            description=(
                f"Ranking combinando os **favoritos do MAL** (+{PONTOS_TOP} pts cada) "
                f"e a **média do servidor** (mín. {MIN_MEMBROS} membros).\n"
                f"Baseado em **{num_usuarios}** usuário(s) • {agora}"
            ),
            color=0x2E51A2,
        )
        embed.set_thumbnail(
            url="https://image.myanimelist.net/ui/OK6W_koKDTOqqqLDbIoPAiC8a86sHufn_jOI-JGtoCQ"
        )

        medalhas = ["🥇", "🥈", "🥉"]

        for pos, entry in enumerate(ranking, start=1):
            item_id = entry["item_id"]
            info = titulos.get(item_id, {})
            titulo = info.get("title", f"Anime #{item_id}")
            url = f"https://myanimelist.net/anime/{item_id}"

            pontos_top = entry["pontos_top"]
            media_srv = entry["media_servidor"]
            total = entry["pontuacao_total"]
            membros = entry["membros_assistiram"]
            membros_no_top = entry["membros_no_top"]

            medalha = medalhas[pos - 1] if pos <= 3 else f"**#{pos}**"

            # Linha de detalhes
            detalhes_parts = []

            if pontos_top > 0:
                tops_count = pontos_top // PONTOS_TOP
                detalhes_parts.append(f"❤️ Favorito de: **{tops_count}** membro(s) (+{pontos_top} pts)")

            if media_srv > 0:
                detalhes_parts.append(
                    f"📊 Média servidor: **{media_srv:.2f}/10** ({membros} membros)"
                )
            else:
                detalhes_parts.append(f"👥 {membros} membro(s) assistiram")

            detalhes_parts.append(f"🎯 Total: **{total:.1f} pts**")

            if membros_no_top:
                nomes = ", ".join(f"`{u}`" for u in membros_no_top[:5])
                if len(membros_no_top) > 5:
                    nomes += f" +{len(membros_no_top) - 5}"
                detalhes_parts.append(f"❤️ Favoritado por: {nomes}")

            embed.add_field(
                name=f"{medalha} [{titulo}]({url})",
                value="\n".join(detalhes_parts),
                inline=False,
            )

        embed.set_footer(
            text=(
                f"Top = favoritos oficiais do MAL de cada membro · "
                f"Média = mín. {MIN_MEMBROS} membros com nota"
            )
        )

        await interaction.followup.send(embed=embed)

    @mal_top_servidor.error
    async def mal_top_servidor_error(self, interaction: discord.Interaction, error):
        await interaction.followup.send(
            f"❌ Ocorreu um erro ao gerar o ranking: `{error}`", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MalTopCog(bot))