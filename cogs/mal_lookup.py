import asyncio
import aiohttp
import os
import sqlite3

import discord
from discord.ext import commands
from discord import app_commands

from cogs.config_cog import get_config


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


def _carregar_usuarios(guild_id: int) -> list[str]:
    conn = sqlite3.connect("usuarios.db")
    c    = conn.cursor()
    c.execute("SELECT username FROM mal_usuarios WHERE guild_id = ?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


async def _buscar_anime(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Busca animes pelo nome e retorna os primeiros resultados."""
    url    = f"{MAL_API_BASE}/anime"
    params = {"q": query, "limit": 8, "fields": "id,title,main_picture,num_episodes,media_type"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar anime '{query}': {e}")
    return []


async def _buscar_entrada_usuario(
    session: aiohttp.ClientSession,
    username: str,
    anime_id: int,
) -> dict | None:
    """Busca o status de um anime específico na lista de um usuário."""
    url    = f"{MAL_API_BASE}/users/{username}/animelist"
    params = {"fields": "list_status", "sort": "list_updated_at", "limit": 1000}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                dados = (await resp.json()).get("data", [])
                for item in dados:
                    if item["node"]["id"] == anime_id:
                        return item["list_status"]
            elif resp.status == 403:
                print(f"[MAL Lookup] Lista de '{username}' é privada.")
    except asyncio.TimeoutError:
        print(f"[MAL Lookup] Timeout ao buscar lista de '{username}'.")
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar lista de '{username}': {e}")
    return None


async def _buscar_manga(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Busca mangás pelo nome e retorna os primeiros resultados."""
    url    = f"{MAL_API_BASE}/manga"
    params = {"q": query, "limit": 8, "fields": "id,title,main_picture,num_chapters,media_type"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar manga '{query}': {e}")
    return []


async def _buscar_entrada_usuario_manga(
    session: aiohttp.ClientSession,
    username: str,
    manga_id: int,
) -> dict | None:
    """Busca o status de um mangá específico na lista de um usuário."""
    url    = f"{MAL_API_BASE}/users/{username}/mangalist"
    params = {"fields": "list_status", "sort": "list_updated_at", "limit": 1000}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                dados = (await resp.json()).get("data", [])
                for item in dados:
                    if item["node"]["id"] == manga_id:
                        return item["list_status"]
            elif resp.status == 403:
                print(f"[MAL Lookup] Mangalist de '{username}' é privada.")
    except asyncio.TimeoutError:
        print(f"[MAL Lookup] Timeout ao buscar mangalist de '{username}'.")
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar mangalist de '{username}': {e}")
    return None


class MalLookupCog(commands.Cog):
    """Busca o status de um anime específico para todos os membros monitorados."""

    def __init__(self, bot: commands.Bot):
        self.bot     = bot
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
            session = await self._get_session()
            resultados = await _buscar_anime(session, current)
            return [
                app_commands.Choice(
                    name=f"{item['node']['title'][:90]}",
                    value=str(item["node"]["id"]),
                )
                for item in resultados
            ][:25]
        except Exception:
            return []

    # ──────────────────────────────────────────
    #  Comando principal
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
                ephemeral=True
            )
            return

        # Busca os detalhes do anime pelo id retornado pelo autocomplete
        session = await self._get_session()
        anime_id = None
        titulo   = anime
        capa_url = None
        num_eps  = None

        # Se o valor for um ID numérico (veio do autocomplete), busca diretamente
        if anime.isdigit():
            anime_id = int(anime)
            try:
                url = f"{MAL_API_BASE}/anime/{anime_id}"
                async with session.get(
                    url,
                    params={"fields": "id,title,main_picture,num_episodes,media_type"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        dados    = await resp.json()
                        titulo   = dados.get("title", anime)
                        capa_url = dados.get("main_picture", {}).get("medium")
                        num_eps  = dados.get("num_episodes")
            except Exception as e:
                print(f"[MAL Lookup] Erro ao buscar detalhes do anime {anime_id}: {e}")
        else:
            # Texto livre — busca e pega o primeiro resultado
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

        # Busca o status de cada usuário em paralelo
        async def buscar(username: str):
            entrada = await _buscar_entrada_usuario(session, username, anime_id)
            return username, entrada

        resultados_usuarios = await asyncio.gather(*[buscar(u) for u in usuarios])

        # Separa quem tem entrada de quem não tem
        com_entrada = [(u, e) for u, e in resultados_usuarios if e is not None]

        mal_url = f"https://myanimelist.net/anime/{anime_id}"
        eps_str = f" • {num_eps} eps" if num_eps else ""

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=f"[Ver no MAL]({mal_url}){eps_str}",
            color=0x2E51A2
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)

        if com_entrada:
            # Agrupa por status para exibição mais organizada
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(com_entrada, key=lambda x: x[0].lower()):
                status  = entrada.get("status", "")
                score   = entrada.get("score", 0)
                eps     = entrada.get("num_episodes_watched", 0)

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
                embed.add_field(
                    name=status_label,
                    value="\n".join(linhas),
                    inline=False
                )
        else:
            embed.add_field(
                name="Nenhuma entrada encontrada",
                value="Nenhum membro da lista assistiu ou tem este anime na lista.",
                inline=False
            )

        embed.set_footer(text=f"{len(com_entrada)} de {len(usuarios)} membros têm este anime na lista")

        await interaction.followup.send(embed=embed)
        print(f"[MAL Lookup] {interaction.user} buscou anime '{titulo}' na guild {guild_id}.")


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
                ephemeral=True
            )
            return

        session  = await self._get_session()
        manga_id = None
        titulo   = manga
        capa_url = None
        num_caps = None

        if manga.isdigit():
            manga_id = int(manga)
            try:
                url = f"{MAL_API_BASE}/manga/{manga_id}"
                async with session.get(
                    url,
                    params={"fields": "id,title,main_picture,num_chapters,media_type"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        dados    = await resp.json()
                        titulo   = dados.get("title", manga)
                        capa_url = dados.get("main_picture", {}).get("medium")
                        num_caps = dados.get("num_chapters")
            except Exception as e:
                print(f"[MAL Lookup] Erro ao buscar detalhes do manga {manga_id}: {e}")
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

        async def buscar(username: str):
            entrada = await _buscar_entrada_usuario_manga(session, username, manga_id)
            return username, entrada

        resultados_usuarios = await asyncio.gather(*[buscar(u) for u in usuarios])
        com_entrada = [(u, e) for u, e in resultados_usuarios if e is not None]

        mal_url  = f"https://myanimelist.net/manga/{manga_id}"
        caps_str = f" • {num_caps} caps" if num_caps else ""

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=f"[Ver no MAL]({mal_url}){caps_str}",
            color=0x4E0D0D
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)

        if com_entrada:
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(com_entrada, key=lambda x: x[0].lower()):
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
                inline=False
            )

        embed.set_footer(text=f"{len(com_entrada)} de {len(usuarios)} membros têm este mangá na lista")
        await interaction.followup.send(embed=embed)
        print(f"[MAL Lookup] {interaction.user} buscou manga '{titulo}' na guild {guild_id}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(MalLookupCog(bot))