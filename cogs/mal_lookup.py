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
ANILIST_API_BASE    = "https://graphql.anilist.co"

STATUS_LABEL_ANIME = {
    "watching":      "Assistindo",
    "completed":     "Completou",
    "on_hold":       "Em pausa",
    "dropped":       "Dropou",
    "plan_to_watch": "Quer assistir",
}

STATUS_LABEL_MANGA = {
    "reading":      "Lendo",
    "completed":    "Completou",
    "on_hold":      "Em pausa",
    "dropped":      "Dropou",
    "plan_to_read": "Quer ler",
}

# Tradução dos valores do campo "source" do MAL/AniList (material de origem do anime)
SOURCE_LABELS = {
    "original":      "Original",
    "manga":         "Mangá",
    "4_koma_manga":  "Mangá 4-koma",
    "web_manga":     "Web Mangá",
    "digital_manga": "Mangá Digital",
    "novel":         "Novel",
    "light_novel":   "Light Novel",
    "visual_novel":  "Visual Novel",
    "game":          "Jogo",
    "card_game":     "Card Game",
    "book":          "Livro",
    "picture_book":  "Livro Ilustrado",
    "radio":         "Rádio",
    "music":         "Música",
    "web_novel":     "Web Novel",
    "mixed_media":   "Mixed Media",
    "doujinshi":     "Doujinshi",
    "anime":         "Anime",
    "other":         "Outro",
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
#  Helpers de formatação — linhas extras do embed (formato, nota,
#  estúdio/autor, ano e fonte)
# ──────────────────────────────────────────────────────────────────

def _campos_extra_anime(dados_mal: dict, extra_al: dict) -> list[tuple[str, str]]:
    """
    Monta os campos (label/valor) da grade de dados do anime, no estilo
    AniSearch: Episódios, Formato, Nota, Estúdio, Ano, Fonte.
    Prioriza dados do MAL e usa o AniList como fallback quando algo faltar.
    Só inclui campos cujo valor foi encontrado.
    """
    campos: list[tuple[str, str]] = []

    num_eps = dados_mal.get("num_episodes")
    if num_eps:
        campos.append(("Episódios", str(num_eps)))

    formato = (dados_mal.get("media_type") or extra_al.get("format") or "").upper().replace("_", " ")
    if formato:
        campos.append(("Formato", formato))

    nota = dados_mal.get("mean")
    if not nota and extra_al.get("averageScore"):
        nota = round(extra_al["averageScore"] / 10, 2)
    if nota:
        campos.append(("Nota MAL", f"{nota}/10"))

    estudios = dados_mal.get("studios") or []
    nome_estudio = ", ".join(s.get("name", "") for s in estudios[:2] if s.get("name"))
    if not nome_estudio:
        estudios_al = (extra_al.get("studios") or {}).get("nodes") or []
        nome_estudio = ", ".join(s.get("name", "") for s in estudios_al[:2] if s.get("name"))
    if nome_estudio:
        campos.append(("Estúdio", nome_estudio))

    ano = (dados_mal.get("start_season") or {}).get("year")
    if not ano:
        ano = (extra_al.get("startDate") or {}).get("year")
    if ano:
        campos.append(("Ano", str(ano)))

    fonte_raw = (dados_mal.get("source") or extra_al.get("source") or "").lower()
    fonte = SOURCE_LABELS.get(fonte_raw, fonte_raw.replace("_", " ").title() if fonte_raw else None)
    if fonte:
        campos.append(("Fonte", fonte))

    return campos


def _campos_extra_manga(dados_mal: dict, extra_al: dict) -> list[tuple[str, str]]:
    """
    Monta os campos (label/valor) da grade de dados do mangá, no estilo
    AniSearch: Capítulos, Formato, Nota, Autor, Ano.
    """
    campos: list[tuple[str, str]] = []

    num_caps = dados_mal.get("num_chapters")
    if num_caps:
        campos.append(("Capítulos", str(num_caps)))

    formato = (dados_mal.get("media_type") or extra_al.get("format") or "").upper().replace("_", " ")
    if formato:
        campos.append(("Formato", formato))

    nota = dados_mal.get("mean")
    if not nota and extra_al.get("averageScore"):
        nota = round(extra_al["averageScore"] / 10, 2)
    if nota:
        campos.append(("Nota MAL", f"{nota}/10"))

    autores = dados_mal.get("authors") or []
    nomes_autores = ", ".join(
        filter(None, (
            f"{a.get('node', {}).get('first_name', '')} {a.get('node', {}).get('last_name', '')}".strip()
            for a in autores[:2]
        ))
    )
    if nomes_autores:
        campos.append(("Autor", nomes_autores))

    serializacoes = dados_mal.get("serialization") or []
    nome_revista = ", ".join(
        s.get("node", {}).get("name", "") for s in serializacoes[:2] if s.get("node", {}).get("name")
    )
    if nome_revista:
        campos.append(("Revista", nome_revista))

    ano = None
    start_date = dados_mal.get("start_date")
    if start_date:
        ano = start_date.split("-")[0]
    if not ano:
        ano = (extra_al.get("startDate") or {}).get("year")
    if ano:
        campos.append(("Ano", str(ano)))

    return campos


# ──────────────────────────────────────────────────────────────────
#  Helpers de API — MyAnimeList (fonte primária)
# ──────────────────────────────────────────────────────────────────

async def _buscar_anime(session: aiohttp.ClientSession, query: str) -> list[dict]:
    url    = f"{MAL_API_BASE}/anime"
    params = {
        "q": query,
        "limit": 8,
        "fields": "id,title,main_picture,num_episodes,media_type,mean,studios{name},start_season{year},source",
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar anime '{query}' no MAL: {e}")
    return []


async def _buscar_manga(session: aiohttp.ClientSession, query: str) -> list[dict]:
    url    = f"{MAL_API_BASE}/manga"
    params = {
        "q": query,
        "limit": 8,
        "fields": "id,title,main_picture,num_chapters,media_type,mean,authors{first_name,last_name},start_date,serialization{name}",
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return (await resp.json()).get("data", [])
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar manga '{query}' no MAL: {e}")
    return []


async def _buscar_detalhes_anime(session: aiohttp.ClientSession, anime_id: int) -> dict:
    try:
        async with session.get(
            f"{MAL_API_BASE}/anime/{anime_id}",
            params={
                "fields": "id,title,main_picture,num_episodes,media_type,mean,studios{name},start_season{year},source",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar detalhes do anime {anime_id} no MAL: {e}")
    return {}


async def _buscar_detalhes_manga(session: aiohttp.ClientSession, manga_id: int) -> dict:
    try:
        async with session.get(
            f"{MAL_API_BASE}/manga/{manga_id}",
            params={
                "fields": "id,title,main_picture,num_chapters,media_type,mean,authors{first_name,last_name},start_date,serialization{name}",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[MAL Lookup] Erro ao buscar detalhes do manga {manga_id} no MAL: {e}")
    return {}


# ──────────────────────────────────────────────────────────────────
#  Helpers de API — AniList (fallback de nome/detalhes + banner)
# ──────────────────────────────────────────────────────────────────
#
# Usados quando o MAL falha ou não retorna resultado, e também para
# buscar o banner (o MAL não expõe esse campo). O AniList expõe o
# campo `idMal`, que é o mesmo ID usado nas listas do MAL — por isso
# conseguimos usar o resultado dele e ainda casar corretamente com as
# entradas salvas em mal_snapshots (que são indexadas por ID do MAL).

_ANILIST_SEARCH_QUERY = """
query ($search: String, $type: MediaType) {
  Page(perPage: 8) {
    media(search: $search, type: $type) {
      idMal
      title { romaji english }
      coverImage { medium }
      episodes
      chapters
    }
  }
}
"""

_ANILIST_DETAIL_QUERY = """
query ($idMal: Int, $type: MediaType) {
  Media(idMal: $idMal, type: $type) {
    idMal
    title { romaji english }
    coverImage { medium }
    episodes
    chapters
  }
}
"""

# Query complementar: banner + campos extras (usada como fallback e
# sempre chamada para pegar o banner, já que o MAL não tem esse campo)
_ANILIST_EXTRA_QUERY = """
query ($idMal: Int, $type: MediaType) {
  Media(idMal: $idMal, type: $type) {
    bannerImage
    format
    averageScore
    startDate { year }
    source
    studios(isMain: true) { nodes { name } }
  }
}
"""


async def _anilist_query(session: aiohttp.ClientSession, query: str, variables: dict) -> dict:
    try:
        async with session.post(
            ANILIST_API_BASE,
            json={"query": query, "variables": variables},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                payload = await resp.json()
                return payload.get("data") or {}
    except Exception as e:
        print(f"[MAL Lookup] Erro ao consultar AniList: {e}")
    return {}


def _anilist_media_to_node(media: dict, tipo: str) -> dict | None:
    """
    Converte um objeto 'media' retornado pelo AniList para o mesmo formato
    de 'node' usado nas respostas do MAL (id, title, main_picture, num_episodes/num_chapters).
    Retorna None se não houver idMal, já que sem ele não conseguimos casar
    com as entradas locais dos usuários.
    """
    id_mal = media.get("idMal")
    if not id_mal:
        return None

    title_obj = media.get("title") or {}
    titulo = title_obj.get("english") or title_obj.get("romaji")
    if not titulo:
        return None

    node = {
        "id": id_mal,
        "title": titulo,
        "main_picture": {"medium": (media.get("coverImage") or {}).get("medium")},
    }
    if tipo == "anime":
        node["num_episodes"] = media.get("episodes")
    else:
        node["num_chapters"] = media.get("chapters")
    return node


async def _buscar_anime_anilist(session: aiohttp.ClientSession, query: str) -> list[dict]:
    data   = await _anilist_query(session, _ANILIST_SEARCH_QUERY, {"search": query, "type": "ANIME"})
    medias = (data.get("Page") or {}).get("media") or []
    nodes  = []
    for m in medias:
        node = _anilist_media_to_node(m, "anime")
        if node:
            nodes.append({"node": node})
    return nodes


async def _buscar_manga_anilist(session: aiohttp.ClientSession, query: str) -> list[dict]:
    data   = await _anilist_query(session, _ANILIST_SEARCH_QUERY, {"search": query, "type": "MANGA"})
    medias = (data.get("Page") or {}).get("media") or []
    nodes  = []
    for m in medias:
        node = _anilist_media_to_node(m, "manga")
        if node:
            nodes.append({"node": node})
    return nodes


async def _buscar_detalhes_anime_anilist(session: aiohttp.ClientSession, mal_id: int) -> dict:
    data  = await _anilist_query(session, _ANILIST_DETAIL_QUERY, {"idMal": mal_id, "type": "ANIME"})
    media = data.get("Media")
    if not media:
        return {}
    return _anilist_media_to_node(media, "anime") or {}


async def _buscar_detalhes_manga_anilist(session: aiohttp.ClientSession, mal_id: int) -> dict:
    data  = await _anilist_query(session, _ANILIST_DETAIL_QUERY, {"idMal": mal_id, "type": "MANGA"})
    media = data.get("Media")
    if not media:
        return {}
    return _anilist_media_to_node(media, "manga") or {}


async def _buscar_extra_anilist(session: aiohttp.ClientSession, mal_id: int, tipo: str) -> dict:
    """
    Busca dados complementares no AniList a partir do ID do MAL: banner,
    formato, nota, ano, fonte e estúdio principal. Chamada sempre (não só
    como fallback) porque o banner só existe no AniList.
    """
    media_type = "ANIME" if tipo == "anime" else "MANGA"
    data = await _anilist_query(session, _ANILIST_EXTRA_QUERY, {"idMal": mal_id, "type": media_type})
    return data.get("Media") or {}


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
            if not resultados:
                resultados = await _buscar_anime_anilist(session, current)
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
            if not resultados:
                resultados = await _buscar_manga_anilist(session, current)
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
                "Nenhum usuário monitorado neste servidor. Use `/mal_entrar` para se adicionar.",
                ephemeral=True,
            )
            return

        session   = await self._get_session()
        titulo    = None
        capa_url  = None
        dados_mal = {}

        # Resolve ID e detalhes via API (1ª barreira: MAL / 2ª barreira: AniList)
        if anime.isdigit():
            anime_id  = int(anime)
            dados_mal = await _buscar_detalhes_anime(session, anime_id)
            titulo    = dados_mal.get("title")
            capa_url  = dados_mal.get("main_picture", {}).get("medium")

            if not titulo:
                dados_al  = await _buscar_detalhes_anime_anilist(session, anime_id)
                titulo    = dados_al.get("title")
                capa_url  = capa_url or dados_al.get("main_picture", {}).get("medium")
                dados_mal.setdefault("num_episodes", dados_al.get("num_episodes"))

            if not titulo:
                titulo = f"Anime #{anime_id}"
        else:
            resultados = await _buscar_anime(session, anime)
            if not resultados:
                resultados = await _buscar_anime_anilist(session, anime)

            if not resultados:
                await interaction.followup.send(
                    f"Nenhum anime encontrado para **{anime}**.", ephemeral=True
                )
                return

            primeiro  = resultados[0]["node"]
            anime_id  = primeiro["id"]
            titulo    = primeiro["title"]
            capa_url  = primeiro.get("main_picture", {}).get("medium")
            dados_mal = primeiro  # já traz mean/studios/start_season/source quando vem do MAL

        # Dados complementares: banner (só existe no AniList) + fallback de
        # formato/nota/estúdio/ano/fonte quando o MAL não trouxe algo
        extra_al   = await _buscar_extra_anilist(session, anime_id, "anime")
        banner_url = extra_al.get("bannerImage")

        # Busca entradas direto no banco local
        entradas = _buscar_entradas_locais(str(anime_id), "anime", usuarios)
        media, total_notas = _calcular_media(entradas)

        mal_url = f"https://myanimelist.net/anime/{anime_id}"

        # Linha 1: média do servidor
        if media > 0:
            media_str = f"Média do servidor: **{media:.2f}/10** ({total_notas} nota(s))"
        else:
            media_str = "Média do servidor: **sem notas ainda**"

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=media_str,
            color=0x2E51A2,
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)
        if banner_url:
            embed.set_image(url=banner_url)

        # Grade de dados (estilo AniSearch): Episódios, Formato, Nota, Estúdio, Ano, Fonte
        for label, valor in _campos_extra_anime(dados_mal, extra_al):
            embed.add_field(name=label, value=valor, inline=True)

        if entradas:
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(entradas.items(), key=lambda x: x[0].lower()):
                status = entrada.get("status", "")
                score  = entrada.get("score", 0)
                eps    = entrada.get("num_episodes_watched", 0)

                label = STATUS_LABEL_ANIME.get(status, status)
                linha = f"`{username}`"

                detalhes = []
                if status == "watching" and eps:
                    detalhes.append(f"EP {eps}")
                if score and score > 0:
                    detalhes.append(f"**{score}/10**")

                if detalhes:
                    linha += f" — {' · '.join(detalhes)}"

                por_status.setdefault(label, []).append(linha)

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
                "Nenhum usuário monitorado neste servidor. Use `/mal_entrar` para se adicionar.",
                ephemeral=True,
            )
            return

        session   = await self._get_session()
        titulo    = None
        capa_url  = None
        dados_mal = {}

        # Resolve ID e detalhes via API (1ª barreira: MAL / 2ª barreira: AniList)
        if manga.isdigit():
            manga_id  = int(manga)
            dados_mal = await _buscar_detalhes_manga(session, manga_id)
            titulo    = dados_mal.get("title")
            capa_url  = dados_mal.get("main_picture", {}).get("medium")

            if not titulo:
                dados_al  = await _buscar_detalhes_manga_anilist(session, manga_id)
                titulo    = dados_al.get("title")
                capa_url  = capa_url or dados_al.get("main_picture", {}).get("medium")
                dados_mal.setdefault("num_chapters", dados_al.get("num_chapters"))

            if not titulo:
                titulo = f"Mangá #{manga_id}"
        else:
            resultados = await _buscar_manga(session, manga)
            if not resultados:
                resultados = await _buscar_manga_anilist(session, manga)

            if not resultados:
                await interaction.followup.send(
                    f"Nenhum mangá encontrado para **{manga}**.", ephemeral=True
                )
                return

            primeiro  = resultados[0]["node"]
            manga_id  = primeiro["id"]
            titulo    = primeiro["title"]
            capa_url  = primeiro.get("main_picture", {}).get("medium")
            dados_mal = primeiro  # já traz mean/authors/start_date quando vem do MAL

        # Dados complementares: banner (só existe no AniList) + fallback de
        # formato/nota/ano quando o MAL não trouxe algo
        extra_al   = await _buscar_extra_anilist(session, manga_id, "manga")
        banner_url = extra_al.get("bannerImage")

        # Busca entradas direto no banco local
        entradas = _buscar_entradas_locais(str(manga_id), "manga", usuarios)
        media, total_notas = _calcular_media(entradas)

        mal_url = f"https://myanimelist.net/manga/{manga_id}"

        # Linha 1: média do servidor
        if media > 0:
            media_str = f"Média do servidor: **{media:.2f}/10** ({total_notas} nota(s))"
        else:
            media_str = "Média do servidor: **sem notas ainda**"

        embed = discord.Embed(
            title=titulo,
            url=mal_url,
            description=media_str,
            color=0x4E0D0D,
        )
        if capa_url:
            embed.set_thumbnail(url=capa_url)
        if banner_url:
            embed.set_image(url=banner_url)

        # Grade de dados (estilo AniSearch): Capítulos, Formato, Nota, Autor, Ano
        for label, valor in _campos_extra_manga(dados_mal, extra_al):
            embed.add_field(name=label, value=valor, inline=True)

        if entradas:
            por_status: dict[str, list[str]] = {}
            for username, entrada in sorted(entradas.items(), key=lambda x: x[0].lower()):
                status = entrada.get("status", "")
                score  = entrada.get("score", 0)
                caps   = entrada.get("num_chapters_read", 0)

                label = STATUS_LABEL_MANGA.get(status, status)
                linha = f"`{username}`"

                detalhes = []
                if status == "reading" and caps:
                    detalhes.append(f"Cap {caps}")
                if score and score > 0:
                    detalhes.append(f"**{score}/10**")

                if detalhes:
                    linha += f" — {' · '.join(detalhes)}"

                por_status.setdefault(label, []).append(linha)

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