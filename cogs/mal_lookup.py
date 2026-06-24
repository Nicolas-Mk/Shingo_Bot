import asyncio
import aiohttp
import json
import os
import sqlite3

import discord
from discord.ext import commands
from discord import app_commands


MAL_CLIENT_ID: str = os.getenv("MAL_CLIENT_ID", "")
MAL_API_BASE        = "https://api.myanimelist.net/v2"

STATUS_LABEL_ANIME = {
    "watching":      ("▶️",  "Assistindo"),
    "completed":     ("✅",  "Completou"),
    "on_hold":       ("⏸️", "Em pausa"),
    "dropped":       ("🗑️", "Dropou"),
    "plan_to_watch": ("📋",  "Quer assistir"),
}

STATUS_LABEL_MANGA = {
    "reading":      ("📖",  "Lendo"),
    "completed":    ("✅",  "Completou"),
    "on_hold":      ("⏸️", "Em pausa"),
    "dropped":      ("🗑️", "Dropou"),
    "plan_to_read": ("📋",  "Quer ler"),
}


# ──────────────────────────────────────────────────────────────────
#  Helpers de banco
# ──────────────────────────────────────────────────────────────────

def _carregar_usuarios(guild_id: int) -> list[str]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT username FROM mal_usuarios WHERE guild_id = ?", (guild_id,))
        rows = c.fetchall()
    return [r[0] for r in rows]


def _buscar_entradas_locais(item_id: str, tipo: str, usuarios: list[str]) -> dict[str, dict]:
    """
    Retorna { username: snapshot_dict } para todos os usuários que têm
    o item_id na tabela mal_snapshots.
    """
    if not usuarios:
        return {}
    placeholders = ",".join("?" * len(usuarios))
    with sqlite3.connect("usuarios.db") as conn:
        rows = conn.execute(
            f"SELECT username, snapshot FROM mal_snapshots "
            f"WHERE tipo = ? AND item_id = ? AND username IN ({placeholders})",
            [tipo, item_id, *usuarios],
        ).fetchall()
    result = {}
    for username, snap_json in rows:
        try:
            result[username] = json.loads(snap_json)
        except Exception:
            pass
    return result


def _calcular_media(entradas: dict[str, dict]) -> tuple[float, int]:
    """Retorna (média, total_com_nota) considerando apenas notas > 0."""
    notas = [e.get("score", 0) for e in entradas.values() if e.get("score", 0) > 0]
    if not notas:
        return 0.0, 0
    return round(sum(notas) / len(notas), 2), len(notas)


# ──────────────────────────────────────────────────────────────────
#  Helpers de API (apenas para busca/autocomplete e detalhes do item)
# ──────────────────────────────────────────────────────────────────

async def _buscar_anime(session: aiohttp.ClientSession, query: str) -> list[dict]:
    url    = f"{MAL_API_BASE}/anime"
    params = {"q": query, "limit": 8, "fields": "id,title,main_picture,num_episodes,media_type"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar anime '{query}': {e}")
    return []


async def _buscar_manga(session: aiohttp.ClientSession, query: str) -> list[dict]:
    url    = f"{MAL_API_BASE}/manga"
    params = {"q": query, "limit": 8, "fields": "id,title,main_picture,num_chapters,media_type"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar manga '{query}': {e}")
    return []


async def _buscar_detalhes_anime(session: aiohttp.ClientSession, anime_id: int) -> dict:
    try:
        async with session.get(
            f"{MAL_API_BASE}/anime/{anime_id}",
            params={"fields": "id,title,main_picture,num_episodes,media_type"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar detalhes do anime {anime_id}: {e}")
    return {}


async def _buscar_detalhes_manga(session: aiohttp.ClientSession, manga_id: int) -> dict:
    try:
        async with session.get(
            f"{MAL_API_BASE}/manga/{manga_id}",
            params={"fields": "id,title,main_picture,num_chapters,media_type"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar detalhes do manga {manga_id}: {e}")
    return {}


# ──────────────────────────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────────────────────────

class MalLookupCog(commands.Cog):
    """Busca o status de um anime/mangá específico para todos os membros monitorados."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    def cog_unload(self):
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MAL-CLIENT-ID": MAL_CLIENT_ID}
            )
        return self._session

    # ──────────────────────────────────────────
    #  Autocomplete
    # ──────────────────────────────────────────

    async def _autocomplete_anime(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if len(current) < 2:
            return []
        try:
            session    = await self._get_session()
            resultados = await _buscar_anime(session, current)
            return [
                app_commands.Choice(
                    name=item["node"]["title"][:90],
                    value=str(item["node"]["id"]),
                )
                for item in resultados
            ][:25]
        except Exception:
            return []

    async def _autocomplete_manga(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if len(current) < 2:
            return []
        try:
            session    = await self._get_session()
            resultados = await _buscar_manga(session, current)
            return [
                app_commands.Choice(
                    name=item["node"]["title"][:90],
                    value=str(item["node"]["id"]),
                )
                for item in resultados
            ][:25]
        except Exception:
            return []

    # ──────────────────────────────────────────
    #  /mal_anime
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_anime",
        description="Veja o status e notas de todos os membros para um anime específico."
    )
    @app_commands.describe(anime="Nome do anime para buscar")
    @app_commands.autocomplete(anime=_autocomplete_anime)
    async def mal_anime(self, interaction: discord.Interaction, anime: str):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        usuarios = _carregar_usuarios(guild_id)

        if not usuarios:
            await interaction.followup.send(
                "📋 Nenhum usuário monitorado neste servidor. Use `/mal_entrar` para se adicionar.",
                ephemeral=True,
            )
            return

        session  = await self._get_session()
        titulo   = anime
        capa_url = None
        num_eps  = None

        # Resolve ID e detalhes via API
        if anime.isdigit():
            anime_id = int(anime)
            dados    = await _buscar_detalhes_anime(session, anime_id)
            titulo   = dados.get("title", anime)
            capa_url = dados.get("main_picture", {}).get("medium")
            num_eps  = dados.get("num_episodes")
        else:
            resultados = await _buscar_anime(session, anime)
            if not resultados:
                await interaction.followup.send(
                    f"❌ Nenhum anime encontrado para **{anime}**.", ephemeral=True
                )
                return
            primeiro = resultados[0]["node"]
            anime_id = primeiro["id"]
            titulo   = primeiro["title"]
            capa_url = primeiro.get("main_picture", {}).get("medium")
            num_eps  = primeiro.get("num_episodes")

        # Busca entradas direto no banco local
        entradas = _buscar_entradas_locais(str(anime_id), "anime", usuarios)
        media, total_notas = _calcular_media(entradas)

        mal_url = f"https://myanimelist.net/anime/{anime_id}"
        eps_str = f" • {num_eps} eps" if num_eps else ""

        # Linha de média do servidor
        if media > 0:
            media_str = f"📊 Média do servidor: **{media:.2f}/10** ({total_notas} nota(s))"
        else:
            media_str = "📊 Média do servidor: **sem notas ainda**"

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=f"{media_str}{eps_str}",
            color=0x2E51A2,
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)

        if entradas:
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(entradas.items(), key=lambda x: x[0].lower()):
                status = entrada.get("status", "")
                score  = entrada.get("score", 0)
                eps    = entrada.get("num_episodes_watched", 0)

                emoji, label = STATUS_LABEL_ANIME.get(status, ("📝", status))
                linha = f"`{username}`"

                detalhes = []
                if status == "watching" and eps:
                    detalhes.append(f"EP {eps}")
                if score and score > 0:
                    estrelas = "⭐" * min(score // 2, 5)
                    detalhes.append(f"**{score}/10** {estrelas}")

                if detalhes:
                    linha += f" — {' · '.join(detalhes)}"

                chave = f"{emoji} {label}"
                por_status.setdefault(chave, []).append(linha)

            for status_label, linhas in por_status.items():
                embed.add_field(name=status_label, value="\n".join(linhas), inline=False)
        else:
            embed.add_field(
                name="Nenhuma entrada encontrada",
                value="Nenhum membro da lista assistiu ou tem este anime na lista.",
                inline=False,
            )

        embed.set_footer(
            text=f"{len(entradas)} de {len(usuarios)} membros têm este anime na lista"
        )
        await interaction.followup.send(embed=embed)
        print(f"[MAL Lookup] {interaction.user} buscou anime '{titulo}' na guild {guild_id}.")

    # ──────────────────────────────────────────
    #  /mal_manga
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_manga",
        description="Veja o status e notas de todos os membros para um mangá específico."
    )
    @app_commands.describe(manga="Nome do mangá para buscar")
    @app_commands.autocomplete(manga=_autocomplete_manga)
    async def mal_manga(self, interaction: discord.Interaction, manga: str):
        await interaction.response.defer()

        guild_id = interaction.guild_id
        usuarios = _carregar_usuarios(guild_id)

        if not usuarios:
            await interaction.followup.send(
                "📋 Nenhum usuário monitorado neste servidor. Use `/mal_entrar` para se adicionar.",
                ephemeral=True,
            )
            return

        session  = await self._get_session()
        titulo   = manga
        capa_url = None
        num_caps = None

        if manga.isdigit():
            manga_id = int(manga)
            dados    = await _buscar_detalhes_manga(session, manga_id)
            titulo   = dados.get("title", manga)
            capa_url = dados.get("main_picture", {}).get("medium")
            num_caps = dados.get("num_chapters")
        else:
            resultados = await _buscar_manga(session, manga)
            if not resultados:
                await interaction.followup.send(
                    f"❌ Nenhum mangá encontrado para **{manga}**.", ephemeral=True
                )
                return
            primeiro = resultados[0]["node"]
            manga_id = primeiro["id"]
            titulo   = primeiro["title"]
            capa_url = primeiro.get("main_picture", {}).get("medium")
            num_caps = primeiro.get("num_chapters")

        # Busca entradas direto no banco local
        entradas = _buscar_entradas_locais(str(manga_id), "manga", usuarios)
        media, total_notas = _calcular_media(entradas)

        mal_url  = f"https://myanimelist.net/manga/{manga_id}"
        caps_str = f" • {num_caps} caps" if num_caps else ""

        if media > 0:
            media_str = f"📊 Média do servidor: **{media:.2f}/10** ({total_notas} nota(s))"
        else:
            media_str = "📊 Média do servidor: **sem notas ainda**"

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=f"{media_str}{caps_str}",
            color=0x4E0D0D,
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)

        if entradas:
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(entradas.items(), key=lambda x: x[0].lower()):
                status = entrada.get("status", "")
                score  = entrada.get("score", 0)
                caps   = entrada.get("num_chapters_read", 0)

                emoji, label = STATUS_LABEL_MANGA.get(status, ("📝", status))
                linha = f"`{username}`"

                detalhes = []
                if status == "reading" and caps:
                    detalhes.append(f"Cap {caps}")
                if score and score > 0:
                    estrelas = "⭐" * min(score // 2, 5)
                    detalhes.append(f"**{score}/10** {estrelas}")

                if detalhes:
                    linha += f" — {' · '.join(detalhes)}"

                chave = f"{emoji} {label}"
                por_status.setdefault(chave, []).append(linha)

            for status_label, linhas in por_status.items():
                embed.add_field(name=status_label, value="\n".join(linhas), inline=False)
        else:
            embed.add_field(
                name="Nenhuma entrada encontrada",
                value="Nenhum membro da lista leu ou tem este mangá na lista.",
                inline=False,
            )

        embed.set_footer(
            text=f"{len(entradas)} de {len(usuarios)} membros têm este mangá na lista"
        )
        await interaction.followup.send(embed=embed)
        print(f"[MAL Lookup] {interaction.user} buscou manga '{titulo}' na guild {guild_id}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MalLookupCog(bot))