import os
import json
import sqlite3
import asyncio
import aiohttp
from io import BytesIO
from datetime import datetime, timezone, timedelta

from PIL import Image
import discord
from discord.ext import commands, tasks
from discord import app_commands
from cogs.config_cog import get_config


# ══════════════════════════════════════════════
#  ⚙️  CONFIGURAÇÕES — edite aqui
# ══════════════════════════════════════════════

MAL_CLIENT_ID: str = os.getenv("MAL_CLIENT_ID", "")

# Itens buscados no loop horário (detecção de mudanças recentes)
LIMITE_LOOP: int = 50

# Itens por página na sincronização completa inicial
LIMITE_PAGINA: int = 100

# Pausa entre páginas na sincronização completa (segundos)
PAUSA_PAGINACAO: float = 300.0  # 5 minutos

# Qualidade de compressão JPEG para avatares (50-70 recomendado)
QUALIDADE_AVATAR: int = 60

# ══════════════════════════════════════════════

MAL_API_BASE = "https://api.myanimelist.net/v2"
BRASILIA     = timezone(timedelta(hours=-3))

TODOS_STATUS_ANIME = ["watching", "completed", "on_hold", "dropped", "plan_to_watch"]
TODOS_STATUS_MANGA = ["reading",  "completed", "on_hold", "dropped", "plan_to_read"]

STATUS_ANIME = {
    "watching":      ("▶️",  "Assistindo"),
    "completed":     ("✅",  "Completou"),
    "on_hold":       ("⏸️", "Pausou"),
    "dropped":       ("🗑️", "Dropou"),
    "plan_to_watch": ("📋",  "Planeja assistir"),
}

STATUS_MANGA = {
    "reading":      ("📖",  "Lendo"),
    "completed":    ("✅",  "Completou"),
    "on_hold":      ("⏸️", "Pausou"),
    "dropped":      ("🗑️", "Dropou"),
    "plan_to_read": ("📋",  "Planeja ler"),
}

ROTULOS_ANIME = {
    "watching":      "Assistindo",
    "completed":     "Completou",
    "on_hold":       "Em pausa",
    "dropped":       "Dropou",
    "plan_to_watch": "Planeja assistir",
}

ROTULOS_MANGA = {
    "reading":      "Lendo",
    "completed":    "Completou",
    "on_hold":      "Em pausa",
    "dropped":      "Dropou",
    "plan_to_read": "Planeja ler",
}


# ──────────────────────────────────────────────
#  Persistência — usuários
# ──────────────────────────────────────────────

def carregar_usuarios(guild_id: int) -> list[str]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT username FROM mal_usuarios WHERE guild_id = ?", (guild_id,))
        rows = c.fetchall()
    return [r[0] for r in rows]


def carregar_todos_usuarios() -> list[str]:
    """Retorna lista deduplicada de todos os usernames monitorados em qualquer guild."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT username FROM mal_usuarios")
        rows = c.fetchall()
    return [r[0] for r in rows]


def salvar_usuario(guild_id: int, username: str):
    with sqlite3.connect("usuarios.db") as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mal_usuarios (guild_id, username) VALUES (?, ?)",
            (guild_id, username)
        )
        conn.commit()


def remover_usuario(guild_id: int, username: str):
    with sqlite3.connect("usuarios.db") as conn:
        conn.execute(
            "DELETE FROM mal_usuarios WHERE guild_id = ? AND username = ?",
            (guild_id, username)
        )
        # Limpa snapshots apenas se o usuário não estiver em nenhuma outra guild
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM mal_usuarios WHERE username = ?", (username,))
        if c.fetchone()[0] == 0:
            conn.execute("DELETE FROM mal_snapshots WHERE username = ?", (username,))
            print(f"[MAL Tracker] Snapshots de '{username}' removidos (sem guilds restantes).")
        conn.commit()


# ──────────────────────────────────────────────
#  Persistência — avatares
# ──────────────────────────────────────────────

def carregar_avatar(username: str) -> bytes | None:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT icon FROM mal_avatares WHERE username = ?", (username,))
        row = c.fetchone()
    return row[0] if row else None


def salvar_avatar(username: str, icon_bytes: bytes):
    with sqlite3.connect("usuarios.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mal_avatares (username, icon, tamanho, atualizado_em) "
            "VALUES (?, ?, ?, ?)",
            (username, icon_bytes, len(icon_bytes), datetime.now(timezone.utc).isoformat())
        )
        conn.commit()


def bytes_para_file(img_bytes: bytes, username: str) -> discord.File:
    buffer = BytesIO(img_bytes)
    buffer.seek(0)
    return discord.File(buffer, filename=f"avatar_{username}.png")


# ──────────────────────────────────────────────
#  Persistência — filtros
# ──────────────────────────────────────────────

def carregar_filtros(guild_id: int) -> dict[str, list[str]]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT tipo, status FROM mal_filtros WHERE guild_id = ?", (guild_id,))
        rows = c.fetchall()

    if not rows:
        return {"anime": list(TODOS_STATUS_ANIME), "manga": list(TODOS_STATUS_MANGA)}

    resultado: dict[str, list[str]] = {"anime": [], "manga": []}
    for tipo, status in rows:
        if tipo in resultado:
            resultado[tipo].append(status)
    return resultado


def toggle_filtro(guild_id: int, tipo: str, status: str) -> bool:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM mal_filtros WHERE guild_id = ?", (guild_id,))
        if c.fetchone()[0] == 0:
            for s in TODOS_STATUS_ANIME:
                conn.execute("INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, 'anime', ?)", (guild_id, s))
            for s in TODOS_STATUS_MANGA:
                conn.execute("INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, 'manga', ?)", (guild_id, s))
            conn.commit()

        c.execute(
            "SELECT 1 FROM mal_filtros WHERE guild_id = ? AND tipo = ? AND status = ?",
            (guild_id, tipo, status)
        )
        ativo = c.fetchone() is not None

        if ativo:
            conn.execute(
                "DELETE FROM mal_filtros WHERE guild_id = ? AND tipo = ? AND status = ?",
                (guild_id, tipo, status)
            )
            habilitado = False
        else:
            conn.execute(
                "INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, ?, ?)",
                (guild_id, tipo, status)
            )
            habilitado = True

        conn.commit()
    return habilitado


# ──────────────────────────────────────────────
#  Persistência — snapshots (por username, sem guild_id)
# ──────────────────────────────────────────────

def usuario_tem_snapshots(username: str) -> bool:
    """Verifica se o usuário já teve sua lista completa sincronizada."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM mal_snapshots WHERE username = ?",
            (username,)
        )
        return c.fetchone()[0] > 0


def carregar_snapshots(username: str, tipo: str) -> dict[str, tuple[str, str]]:
    """
    Retorna { item_id: (snapshot_json, updated_at) } para o usuário/tipo.
    Snapshots são compartilhados entre guilds — um usuário tem apenas uma cópia
    de sua lista, independente de quantos servidores ele está monitorado.
    """
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT item_id, snapshot, updated_at FROM mal_snapshots "
            "WHERE username = ? AND tipo = ?",
            (username, tipo)
        )
        rows = c.fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def salvar_snapshots_em_lote(
    username: str,
    tipo: str,
    itens: list[tuple[str, str, str]]  # [(item_id, snapshot_json, updated_at), ...]
):
    """Upsert em lote. guild_id propositalmente ausente — snapshots são globais por username."""
    if not itens:
        return
    with sqlite3.connect("usuarios.db") as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mal_snapshots (username, tipo, item_id, snapshot, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(username, tipo, item_id, snap, upd) for item_id, snap, upd in itens]
        )
        conn.commit()


# ──────────────────────────────────────────────
#  Utilitário de imagem
# ──────────────────────────────────────────────

def recortar_e_comprimir_avatar(img_bytes: bytes, username: str, qualidade: int = QUALIDADE_AVATAR) -> bytes:
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    lado = min(w, h)
    left = (w - lado) // 2
    img = img.crop((left, 0, left + lado, lado))
    img = img.resize((128, 128), Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=qualidade, optimize=True)
    buffer.seek(0)
    return buffer.getvalue()


def bytes_para_file_do_avatar(img_bytes: bytes, username: str) -> discord.File:
    buffer = BytesIO(img_bytes)
    buffer.seek(0)
    return discord.File(buffer, filename=f"avatar_{username}.jpg")


# ──────────────────────────────────────────────
#  Cog principal
# ──────────────────────────────────────────────

class MalTrackerCog(commands.Cog):
    """Monitora listas de anime e mangá do MyAnimeList e envia relatórios horários."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        # Conjunto de usernames com sincronização completa em andamento,
        # para evitar disparar múltiplas tarefas paralelas para o mesmo usuário.
        self._sync_em_andamento: set[str] = set()
        # Cache de mudanças do ciclo atual: { username: {"anime": [...], "manga": [...]} }
        # Populado UMA VEZ por usuário antes de processar qualquer guild,
        # para que guilds múltiplas recebam exatamente o mesmo conjunto de mudanças.
        self._cache_mudancas_ciclo: dict[str, dict[str, list[dict]]] = {}
        self.monitorar_loop.start()

    def cog_unload(self):
        self.monitorar_loop.cancel()
        if self._session and not self._session.closed:
            asyncio.create_task(self._session.close())

    # ──────────────────────────────────────────
    #  Sessão HTTP
    # ──────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MAL-CLIENT-ID": MAL_CLIENT_ID}
            )
        return self._session

    # ──────────────────────────────────────────
    #  Buscas na API — página única
    # ──────────────────────────────────────────

    async def _buscar_lista(
        self,
        username: str,
        tipo: str,          # "anime" ou "manga"
        limite: int = LIMITE_LOOP,
        offset: int = 0,
    ) -> list[dict] | None:
        """Busca uma página da animelist ou mangalist. Retorna None em caso de erro."""
        session = await self._get_session()
        endpoint = "animelist" if tipo == "anime" else "mangalist"
        url = f"{MAL_API_BASE}/users/{username}/{endpoint}"
        params = {
            "fields": "list_status",
            "sort":   "list_updated_at",
            "limit":  limite,
            "offset": offset,
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("data", [])
                elif resp.status == 403:
                    print(f"[MAL Tracker] ⚠️  Lista de '{username}' ({tipo}) é privada.")
                else:
                    print(f"[MAL Tracker] Erro {resp.status} ao buscar {tipo}list de '{username}'.")
        except asyncio.TimeoutError:
            print(f"[MAL Tracker] Timeout ao buscar {tipo}list de '{username}'.")
        except Exception as e:
            print(f"[MAL Tracker] Exceção ao buscar {tipo}list de '{username}': {e}")
        return None

    # ──────────────────────────────────────────
    #  Sincronização completa (paginada, com rate limiting)
    # ──────────────────────────────────────────

    async def _sincronizar_lista_completa(self, username: str, tipo: str):
        """
        Percorre todas as páginas da lista do usuário e persiste snapshots.
        Pausa PAUSA_PAGINACAO segundos entre cada página para respeitar o rate limit do MAL.
        Não gera relatório — serve apenas para popular o banco.
        """
        print(f"[MAL Tracker] 🔄 Iniciando sync completo de '{username}' ({tipo})...")
        offset = 0
        total  = 0

        while True:
            pagina = await self._buscar_lista(username, tipo, limite=LIMITE_PAGINA, offset=offset)

            if pagina is None:
                print(f"[MAL Tracker] ⚠️  Sync de '{username}' ({tipo}) interrompido na página offset={offset}.")
                break

            if not pagina:
                break  # Lista esgotada

            upserts = [
                (
                    str(item["node"]["id"]),
                    self._snapshot(item["list_status"], tipo),
                    item["list_status"].get("updated_at", ""),
                )
                for item in pagina
            ]
            salvar_snapshots_em_lote(username, tipo, upserts)
            total  += len(pagina)
            offset += len(pagina)

            print(f"[MAL Tracker]    '{username}' ({tipo}): {total} itens sincronizados...")

            if len(pagina) < LIMITE_PAGINA:
                break  # Última página

            # Pausa antes da próxima página para não estourar o rate limit
            print(f"[MAL Tracker]    Aguardando {PAUSA_PAGINACAO:.0f}s antes da próxima página...")
            await asyncio.sleep(PAUSA_PAGINACAO)

        print(f"[MAL Tracker] ✅ Sync completo de '{username}' ({tipo}): {total} itens.")

    async def _sincronizar_usuario_completo(self, username: str):
        """
        Dispara a sincronização completa de anime + manga para um usuário.
        Idempotente: não faz nada se o usuário já tiver snapshots.
        Usa _sync_em_andamento para evitar execuções paralelas.
        """
        if username in self._sync_em_andamento:
            print(f"[MAL Tracker] Sync de '{username}' já em andamento, pulando.")
            return
        if usuario_tem_snapshots(username):
            print(f"[MAL Tracker] '{username}' já tem snapshots. Sync completo não necessário.")
            return

        self._sync_em_andamento.add(username)
        try:
            await self._sincronizar_lista_completa(username, "anime")
            await self._sincronizar_lista_completa(username, "manga")
        finally:
            self._sync_em_andamento.discard(username)

    # ──────────────────────────────────────────
    #  Avatar
    # ──────────────────────────────────────────

    async def _buscar_avatar(self, username: str) -> discord.File | None:
        icon_bytes = carregar_avatar(username)
        if icon_bytes:
            print(f"[MAL Tracker] ✅ Avatar de '{username}' carregado do cache.")
            return bytes_para_file_do_avatar(icon_bytes, username)

        print(f"[MAL Tracker] 🔄 Baixando avatar de '{username}' do Jikan...")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://api.jikan.moe/v4/users/{username}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        print(f"[MAL Tracker] ⚠️  Jikan retornou {resp.status} para '{username}'.")
                        return None
                    data = await resp.json()
                    img_url = (
                        data.get("data", {})
                        .get("images", {})
                        .get("jpg", {})
                        .get("image_url")
                    )
                    if not img_url:
                        return None

                async with s.get(img_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    img_bytes = await resp.read()

            icon_comprimido    = recortar_e_comprimir_avatar(img_bytes, username)
            tamanho_original   = len(img_bytes)
            tamanho_comprimido = len(icon_comprimido)
            reducao = round(((tamanho_original - tamanho_comprimido) / tamanho_original) * 100, 1)

            salvar_avatar(username, icon_comprimido)
            print(f"[MAL Tracker] 💾 Avatar de '{username}' em cache ({reducao}% menor).")
            return bytes_para_file_do_avatar(icon_comprimido, username)

        except Exception as e:
            print(f"[MAL Tracker] ❌ Erro ao processar avatar de '{username}': {e}")
            return None

    # ──────────────────────────────────────────
    #  Lógica de comparação
    # ──────────────────────────────────────────

    def _snapshot(self, status: dict, tipo: str) -> str:
        base = {"status": status.get("status"), "score": status.get("score")}
        if tipo == "anime":
            base["num_episodes_watched"] = status.get("num_episodes_watched")
            base["is_rewatching"]        = status.get("is_rewatching")
        else:
            base["num_chapters_read"] = status.get("num_chapters_read")
            base["num_volumes_read"]  = status.get("num_volumes_read")
            base["is_rereading"]      = status.get("is_rereading")
        return json.dumps(base, sort_keys=True)

    def _processar_itens(
        self,
        itens: list[dict],
        snapshots_db: dict[str, tuple[str, str]],  # { item_id: (snap_json, updated_at) }
        tipo: str,
        status_permitidos: list[str],              # filtros já resolvidos pela guild
        primeira_vez: bool = False,                # True = usuário sem snapshots ainda
    ) -> tuple[list[dict], list[tuple[str, str, str]]]:
        """
        Compara itens da API contra snapshots do banco.

        Retorna:
          - mudanças detectadas (para o relatório desta guild)
          - upserts a persistir no banco
        """
        novas:   list[dict]                 = []
        upserts: list[tuple[str, str, str]] = []

        for item in itens:
            item_id    = str(item["node"]["id"])
            titulo     = item["node"]["title"]
            status     = item["list_status"]
            snap_atual = self._snapshot(status, tipo)
            updated_at = status.get("updated_at", "")
            status_val = status.get("status", "")

            registro_anterior = snapshots_db.get(item_id)

            # ── Item novo (usuário com sync completo, mas item não estava na lista antes)
            if registro_anterior is None:
                upserts.append((item_id, snap_atual, updated_at))
                # Se é a primeira vez do usuário, apenas popula — não reporta nada.
                # Se o usuário já tem snapshots e este item é genuinamente novo (recente):
                if not primeira_vez and updated_at and status_val in status_permitidos:
                    try:
                        agora_utc = datetime.now(timezone.utc)
                        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if (agora_utc - dt).total_seconds() <= 3600:
                            novas.append(self._montar_entrada(titulo, item_id, status, updated_at, tipo))
                    except Exception:
                        pass
                continue

            snap_anterior, _ = registro_anterior

            if snap_anterior == snap_atual:
                continue

            # Algo mudou
            ant = json.loads(snap_anterior)
            atu = json.loads(snap_atual)

            campos_progresso = [
                "status", "num_episodes_watched", "is_rewatching",
                "num_chapters_read", "num_volumes_read", "is_rereading"
            ]
            so_nota = (
                ant.get("score") != atu.get("score") and
                all(ant.get(k) == atu.get(k) for k in campos_progresso if k in ant or k in atu)
            )

            upserts.append((item_id, snap_atual, updated_at))

            if so_nota or status_val in status_permitidos:
                nota_anterior = ant.get("score") if so_nota else None
                novas.append(self._montar_entrada(
                    titulo, item_id, status, updated_at, tipo,
                    so_nota=so_nota, nota_anterior=nota_anterior
                ))

        return novas, upserts

    def _montar_entrada(
        self,
        titulo: str,
        item_id: str,
        status: dict,
        updated_at: str,
        tipo: str,
        so_nota: bool = False,
        nota_anterior: int | None = None,
    ) -> dict:
        entrada = {
            "titulo":        titulo,
            "item_id":       item_id,
            "status":        status.get("status", ""),
            "score":         status.get("score", 0),
            "updated_at":    updated_at,
            "tipo":          tipo,
            "so_nota":       so_nota,
            "nota_anterior": nota_anterior,
        }
        if tipo == "anime":
            entrada["episodios"]  = status.get("num_episodes_watched", 0)
            entrada["rewatching"] = status.get("is_rewatching", False)
        else:
            entrada["capitulos"] = status.get("num_chapters_read", 0)
            entrada["volumes"]   = status.get("num_volumes_read", 0)
            entrada["rereading"] = status.get("is_rereading", False)
        return entrada

    async def _detectar_mudancas_usuario(
        self, username: str
    ) -> dict[str, list[dict]]:
        """
        Busca os itens recentes de um usuário, compara com os snapshots do banco
        e persiste as atualizações. Retorna as mudanças brutas (sem filtro de guild)
        para que cada guild aplique seus próprios filtros depois.

        Chamado UMA VEZ por usuário por ciclo — garante que todas as guilds
        recebem o mesmo conjunto de mudanças antes de qualquer snapshot ser atualizado.
        """
        if username in self._sync_em_andamento:
            print(f"[MAL Tracker] '{username}' em sync inicial, pulando loop horário.")
            return {}

        snaps_anime = carregar_snapshots(username, "anime")
        snaps_manga = carregar_snapshots(username, "manga")

        itens_anime = await self._buscar_lista(username, "anime", limite=LIMITE_LOOP)
        itens_manga = await self._buscar_lista(username, "manga", limite=LIMITE_LOOP)

        # Passa todos os status — queremos detectar tudo aqui; o filtro é aplicado
        # por guild depois em _filtrar_mudancas_para_guild.
        todos_status = TODOS_STATUS_ANIME + TODOS_STATUS_MANGA

        novas_anime, upserts_anime = self._processar_itens(
            itens_anime or [], snaps_anime, "anime", todos_status
        )
        novas_manga, upserts_manga = self._processar_itens(
            itens_manga or [], snaps_manga, "manga", todos_status
        )

        # Persiste snapshots DEPOIS de processar — todas as guilds já leram
        # o estado anterior correto neste ponto (chamadas sequenciais no loop).
        salvar_snapshots_em_lote(username, "anime", upserts_anime)
        salvar_snapshots_em_lote(username, "manga", upserts_manga)

        novas_anime.sort(key=lambda x: x["updated_at"])
        novas_manga.sort(key=lambda x: x["updated_at"])

        total = len(novas_anime) + len(novas_manga)
        if total:
            print(f"[MAL Tracker] '{username}': {len(novas_anime)} anime, {len(novas_manga)} mangá detectados.")

        return {"anime": novas_anime, "manga": novas_manga}

    def _filtrar_mudancas_para_guild(
        self,
        mudancas_brutas: dict[str, list[dict]],
        filtros: dict[str, list[str]],
    ) -> dict[str, list[dict]]:
        """
        Aplica os filtros de status da guild sobre as mudanças brutas de um usuário.
        Trocas de nota (so_nota=True) sempre passam, independente do filtro.
        """
        def filtrar(itens: list[dict], tipo: str) -> list[dict]:
            permitidos = set(filtros.get(tipo, []))
            return [
                item for item in itens
                if item.get("so_nota") or item.get("status") in permitidos
            ]

        anime_filtrado = filtrar(mudancas_brutas.get("anime", []), "anime")
        manga_filtrado = filtrar(mudancas_brutas.get("manga", []), "manga")

        if not anime_filtrado and not manga_filtrado:
            return {}
        return {"anime": anime_filtrado, "manga": manga_filtrado}

    async def _coletar_mudancas(self, guild_id: int) -> dict[str, dict[str, list[dict]]]:
        """
        Aplica os filtros desta guild sobre o cache de mudanças do ciclo atual.
        A detecção já foi feita antes por _detectar_mudancas_usuario.
        """
        resultado: dict[str, dict[str, list[dict]]] = {}
        usuarios = carregar_usuarios(guild_id)
        filtros  = carregar_filtros(guild_id)

        if not usuarios:
            print(f"[MAL Tracker] Guild {guild_id}: nenhum usuário monitorado.")
            return resultado

        for usuario in usuarios:
            mudancas_brutas = self._cache_mudancas_ciclo.get(usuario)
            if not mudancas_brutas:
                continue
            filtrado = self._filtrar_mudancas_para_guild(mudancas_brutas, filtros)
            if filtrado:
                resultado[usuario] = filtrado

        return resultado

    # ──────────────────────────────────────────
    #  Formatação do relatório
    # ──────────────────────────────────────────

    def _formatar_linhas_anime(self, itens: list[dict]) -> list[str]:
        linhas = []
        for item in itens:
            url = f"https://myanimelist.net/anime/{item['item_id']}"
            if item.get("so_nota"):
                linha    = f"📝 **[{item['titulo']}]({url})**"
                nota_ant = item.get("nota_anterior")
                estrelas = "⭐" * min(item["score"] // 2, 5) if item["score"] > 0 else ""
                if nota_ant is not None:
                    detalhes = [f"Atualizou nota: **{nota_ant}/10 → {item['score']}/10** {estrelas}".strip()]
                else:
                    detalhes = [f"Atualizou nota: **{item['score']}/10** {estrelas}".strip()]
            else:
                emoji, label = STATUS_ANIME.get(item["status"], ("📝", item["status"]))
                linha    = f"{emoji} **[{item['titulo']}]({url})**"
                detalhes: list[str] = [label]
                if item["status"] == "watching" or item.get("rewatching"):
                    ep = item.get("episodios", 0)
                    detalhes.append(f"Reassistindo • EP {ep}" if item.get("rewatching") else f"EP {ep}")
                if item["score"] > 0:
                    estrelas = "⭐" * min(item["score"] // 2, 5)
                    detalhes.append(f"Nota: **{item['score']}/10** {estrelas}")
            if item["updated_at"]:
                try:
                    dt = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                    detalhes.append(dt.astimezone(BRASILIA).strftime("%H:%M"))
                except Exception:
                    pass
            linha += f"\n　└ {' · '.join(detalhes)}"
            linhas.append(linha)
        return linhas

    def _formatar_linhas_manga(self, itens: list[dict]) -> list[str]:
        linhas = []
        for item in itens:
            url = f"https://myanimelist.net/manga/{item['item_id']}"
            if item.get("so_nota"):
                linha    = f"📝 **[{item['titulo']}]({url})**"
                nota_ant = item.get("nota_anterior")
                estrelas = "⭐" * min(item["score"] // 2, 5) if item["score"] > 0 else ""
                if nota_ant is not None:
                    detalhes = [f"Atualizou nota: **{nota_ant}/10 → {item['score']}/10** {estrelas}".strip()]
                else:
                    detalhes = [f"Atualizou nota: **{item['score']}/10** {estrelas}".strip()]
            else:
                emoji, label = STATUS_MANGA.get(item["status"], ("📝", item["status"]))
                linha    = f"{emoji} **[{item['titulo']}]({url})**"
                detalhes: list[str] = [label]
                if item["status"] == "reading" or item.get("rereading"):
                    cap = item.get("capitulos", 0)
                    detalhes.append(f"Relendo • Cap {cap}" if item.get("rereading") else f"Cap {cap}")
                if item["score"] > 0:
                    estrelas = "⭐" * min(item["score"] // 2, 5)
                    detalhes.append(f"Nota: **{item['score']}/10** {estrelas}")
            if item["updated_at"]:
                try:
                    dt = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                    detalhes.append(dt.astimezone(BRASILIA).strftime("%H:%M"))
                except Exception:
                    pass
            linha += f"\n　└ {' · '.join(detalhes)}"
            linhas.append(linha)
        return linhas

    async def _formatar_relatorio(
        self, mudancas: dict[str, dict[str, list[dict]]]
    ) -> list[tuple[discord.Embed, discord.File | None]]:
        resultado: list[tuple[discord.Embed, discord.File | None]] = []
        if not mudancas:
            return resultado

        agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        header = discord.Embed(
            title="🎌 FISCALIZAÇÃO MYANIMELIST",
            description=f"Atualizações da última hora • {agora}",
            color=0x2E51A2,
        )
        header.set_thumbnail(url="https://image.myanimelist.net/ui/OK6W_koKDTOqqqLDbIoPAiC8a86sHufn_jOI-JGtoCQ")
        resultado.append((header, None))

        for usuario, dados in mudancas.items():
            avatar_file = await self._buscar_avatar(usuario)
            await asyncio.sleep(1)

            icon = (
                f"attachment://avatar_{usuario}.jpg"
                if avatar_file
                else f"https://api.dicebear.com/8.x/initials/png?seed={usuario}&size=64"
            )

            novas_anime = dados.get("anime", [])
            novas_manga = dados.get("manga", [])

            if novas_anime:
                linhas = self._formatar_linhas_anime(novas_anime)
                embed_anime = discord.Embed(description="\n".join(linhas), color=0x2E51A2)
                embed_anime.set_author(
                    name=f"@{usuario} — Anime",
                    url=f"https://myanimelist.net/profile/{usuario}",
                    icon_url=icon,
                )
                embed_anime.set_footer(
                    text=f"{len(novas_anime)} atualização{'ões' if len(novas_anime) != 1 else ''}"
                )
                resultado.append((embed_anime, avatar_file))
                avatar_file = None

            if novas_manga:
                linhas = self._formatar_linhas_manga(novas_manga)
                embed_manga = discord.Embed(description="\n".join(linhas), color=0x4E0D0D)
                embed_manga.set_author(
                    name=f"@{usuario} — Mangá",
                    url=f"https://myanimelist.net/profile/{usuario}",
                    icon_url=icon,
                )
                embed_manga.set_footer(
                    text=f"{len(novas_manga)} atualização{'ões' if len(novas_manga) != 1 else ''}"
                )
                resultado.append((embed_manga, avatar_file))
                avatar_file = None

        return resultado

    async def _enviar_relatorio(self, canal: discord.TextChannel, mudancas: dict) -> int:
        pares = await self._formatar_relatorio(mudancas)
        if not pares:
            return 0

        lote_embeds: list[discord.Embed] = []
        lote_files:  list[discord.File]  = []

        async def flush():
            if lote_embeds:
                await canal.send(
                    embeds=lote_embeds,
                    files=lote_files if lote_files else discord.utils.MISSING,
                )
                lote_embeds.clear()
                lote_files.clear()

        for embed, file in pares:
            if file:
                await flush()
                await canal.send(embed=embed, file=file)
            else:
                lote_embeds.append(embed)
                if len(lote_embeds) == 10:
                    await flush()

        await flush()
        return sum(len(v["anime"]) + len(v["manga"]) for v in mudancas.values())

    # ──────────────────────────────────────────
    #  Loop principal
    # ──────────────────────────────────────────

    @tasks.loop(hours=1)
    async def monitorar_loop(self):
        await self.bot.wait_until_ready()
        print(f"[MAL Tracker] Verificando atualizações... ({datetime.now().strftime('%H:%M:%S')})")

        # ── Fase 1: detecção — uma chamada por usuário único, sem duplicar ────
        # Todos os snapshots são lidos ANTES de qualquer gravação, garantindo
        # que guilds múltiplas vejam o mesmo "estado anterior".
        todos_usuarios = carregar_todos_usuarios()
        self._cache_mudancas_ciclo = {}
        for username in todos_usuarios:
            self._cache_mudancas_ciclo[username] = await self._detectar_mudancas_usuario(username)

        # ── Fase 2: relatório — cada guild filtra e envia ────────────────────
        async def processar_guild(guild):
            try:
                canal_id = get_config(guild.id, "canal_mal")
                if not canal_id:
                    print(f"[MAL Tracker] Guild '{guild.name}': canal_mal não configurado, pulando.")
                    return

                mudancas = await self._coletar_mudancas(guild.id)
                if not mudancas:
                    print(f"[MAL Tracker] Guild '{guild.name}': nenhuma atualização.")
                    return

                canal = self.bot.get_channel(canal_id)
                if canal is None:
                    print(f"[MAL Tracker] ⚠️  Canal {canal_id} não encontrado na guild {guild.name}.")
                    return

                await self._enviar_relatorio(canal, mudancas)
                total_anime = sum(len(v["anime"]) for v in mudancas.values())
                total_manga = sum(len(v["manga"]) for v in mudancas.values())
                print(f"[MAL Tracker] ✅ {guild.name}: {total_anime} anime, {total_manga} mangá — {len(mudancas)} usuário(s).")
            except Exception as e:
                print(f"[MAL Tracker] ❌ Erro ao processar guild '{guild.name}': {e}")

        await asyncio.gather(*[processar_guild(guild) for guild in self.bot.guilds])

    @monitorar_loop.before_loop
    async def antes_do_loop(self):
        await self.bot.wait_until_ready()
        print("[MAL Tracker] Boot — verificando usuários sem sync completo...")
        todos = carregar_todos_usuarios()
        # Dispara sync completo em background para quem ainda não tem snapshots.
        # Cada sync pode levar horas (rate limiting), então roda como task separada.
        for username in todos:
            if not usuario_tem_snapshots(username):
                asyncio.create_task(self._sincronizar_usuario_completo(username))
            else:
                print(f"[MAL Tracker] '{username}' já sincronizado.")
        print("[MAL Tracker] Primeiro relatório em 1 hora.")

    # ──────────────────────────────────────────
    #  Comandos — cadastro
    # ──────────────────────────────────────────

    @app_commands.command(name="mal_entrar", description="Adiciona seu usuário do MAL ao monitoramento.")
    @app_commands.describe(username="Seu nome de usuário no MyAnimeList")
    async def mal_entrar(self, interaction: discord.Interaction, username: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        usuarios = carregar_usuarios(guild_id)

        if username.lower() in [u.lower() for u in usuarios]:
            await interaction.followup.send(f"⚠️ O usuário `{username}` já está na lista.", ephemeral=True)
            return

        # Valida que a lista existe e é pública
        teste = await self._buscar_lista(username, "anime", limite=1)
        if teste is None:
            await interaction.followup.send(
                f"❌ Não consegui acessar a lista de `{username}`. Verifique o nome e se a lista é pública.",
                ephemeral=True
            )
            return

        salvar_usuario(guild_id, username)

        # Se for a primeira vez deste username em qualquer guild, dispara sync completo
        if not usuario_tem_snapshots(username):
            asyncio.create_task(self._sincronizar_usuario_completo(username))
            await interaction.followup.send(
                f"✅ `{username}` adicionado! Estou sincronizando a lista completa em segundo plano "
                f"(pode levar alguns minutos dependendo do tamanho da lista). "
                f"As notificações começam após a sincronização.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"✅ `{username}` adicionado ao monitoramento! Será incluído no próximo relatório.",
                ephemeral=True
            )

        print(f"[MAL Tracker] '{username}' adicionado por {interaction.user} na guild {guild_id}.")

    @app_commands.command(name="mal_sair", description="Remove um usuário do MAL do monitoramento.")
    @app_commands.describe(username="Nome de usuário no MyAnimeList a remover")
    async def mal_sair(self, interaction: discord.Interaction, username: str):
        guild_id = interaction.guild_id
        usuarios = carregar_usuarios(guild_id)

        if username.lower() not in [u.lower() for u in usuarios]:
            await interaction.response.send_message(f"⚠️ `{username}` não está na lista.", ephemeral=True)
            return

        remover_usuario(guild_id, username)
        await interaction.response.send_message(f"✅ `{username}` removido do monitoramento.", ephemeral=True)
        print(f"[MAL Tracker] '{username}' removido por {interaction.user} na guild {guild_id}.")

    @app_commands.command(name="mal_lista", description="Mostra todos os usuários sendo monitorados.")
    async def mal_lista(self, interaction: discord.Interaction):
        usuarios = carregar_usuarios(interaction.guild_id)
        if not usuarios:
            await interaction.response.send_message(
                "📋 Nenhum usuário na lista ainda. Use `/mal_entrar` para se adicionar!", ephemeral=True
            )
            return

        linhas = "\n".join(f"• [{u}](https://myanimelist.net/profile/{u})" for u in usuarios)
        embed  = discord.Embed(title="📋 Usuários monitorados", description=linhas, color=0x2E51A2)
        embed.set_footer(text=f"{len(usuarios)} usuário(s)")
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────
    #  Comandos — filtros (admin)
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_filtros",
        description="[Admin] Exibe os status atualmente ativos no monitoramento."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_filtros(self, interaction: discord.Interaction):
        filtros = carregar_filtros(interaction.guild_id)

        def listar(ativos: list[str], rotulos: dict) -> str:
            return "\n".join(
                f"{'✅' if s in ativos else '❌'} {label} (`{s}`)"
                for s, label in rotulos.items()
            )

        embed = discord.Embed(title="🎛️ Filtros de Status — MAL Tracker", color=0x2E51A2)
        embed.add_field(name="🎬 Anime", value=listar(filtros["anime"], ROTULOS_ANIME), inline=True)
        embed.add_field(name="📚 Mangá", value=listar(filtros["manga"], ROTULOS_MANGA), inline=True)
        embed.set_footer(text="Use /mal_filtro_toggle para habilitar ou desabilitar um status.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="mal_filtro_toggle",
        description="[Admin] Habilita ou desabilita um status no monitoramento."
    )
    @app_commands.describe(tipo="Anime ou mangá", status="Status a alternar")
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Anime", value="anime"),
            app_commands.Choice(name="Mangá", value="manga"),
        ],
        status=[
            app_commands.Choice(name="Assistindo / Lendo",     value="watching_reading"),
            app_commands.Choice(name="Completou",              value="completed"),
            app_commands.Choice(name="Em pausa",               value="on_hold"),
            app_commands.Choice(name="Dropou",                 value="dropped"),
            app_commands.Choice(name="Planeja assistir / ler", value="plan_to"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_filtro_toggle(self, interaction: discord.Interaction, tipo: str, status: str):
        mapa = {
            "watching_reading": {"anime": "watching",      "manga": "reading"},
            "completed":        {"anime": "completed",     "manga": "completed"},
            "on_hold":          {"anime": "on_hold",       "manga": "on_hold"},
            "dropped":          {"anime": "dropped",       "manga": "dropped"},
            "plan_to":          {"anime": "plan_to_watch", "manga": "plan_to_read"},
        }
        status_real = mapa[status][tipo]
        rotulos     = ROTULOS_ANIME if tipo == "anime" else ROTULOS_MANGA
        label       = rotulos.get(status_real, status_real)

        habilitado = toggle_filtro(interaction.guild_id, tipo, status_real)
        acao = "habilitado ✅" if habilitado else "desabilitado ❌"
        await interaction.response.send_message(
            f"**{label}** ({tipo}) foi **{acao}** no monitoramento.", ephemeral=True
        )
        print(f"[MAL Tracker] Status '{status_real}' ({tipo}) {acao} por {interaction.user}.")

    # ──────────────────────────────────────────
    #  Comandos — admin
    # ──────────────────────────────────────────

    @app_commands.command(name="mal_relatorio", description="[Admin] Força o envio imediato do relatório MAL.")
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_relatorio(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mudancas = await self._coletar_mudancas(interaction.guild_id)

        if not mudancas:
            await interaction.followup.send("✅ Nenhuma atualização nova desde a última verificação.", ephemeral=True)
            return

        canal_id = get_config(interaction.guild_id, "canal_mal")
        canal    = self.bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            await interaction.followup.send(
                "❌ Canal do MAL não configurado. Use `/config_canal` para definir.", ephemeral=True
            )
            return

        await self._enviar_relatorio(canal, mudancas)
        total_anime = sum(len(v["anime"]) for v in mudancas.values())
        total_manga = sum(len(v["manga"]) for v in mudancas.values())
        await interaction.followup.send(
            f"✅ Relatório enviado com **{total_anime}** anime e **{total_manga}** mangá.", ephemeral=True
        )

    @app_commands.command(name="mal_status", description="[Admin] Mostra o status atual do monitoramento MAL.")
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_status(self, interaction: discord.Interaction):
        proxima  = self.monitorar_loop.next_iteration
        prox_str = f"<t:{int(proxima.timestamp())}:R>" if proxima else "desconhecido"

        usuarios = carregar_usuarios(interaction.guild_id)
        guild_id = interaction.guild_id

        linhas_usuarios = []
        for u in usuarios:
            snaps_anime = carregar_snapshots(u, "anime")
            snaps_manga = carregar_snapshots(u, "manga")
            em_sync     = "⏳ sincronizando..." if u in self._sync_em_andamento else f"{len(snaps_anime)} animes, {len(snaps_manga)} mangás"
            linhas_usuarios.append(f"• `{u}` — {em_sync}")
        usuarios_str = "\n".join(linhas_usuarios) if linhas_usuarios else "nenhum"

        embed = discord.Embed(title="📊 MAL Tracker — Status", color=0x2E51A2)
        embed.add_field(name="Próximo relatório",    value=prox_str,    inline=False)
        embed.add_field(name="Usuários monitorados", value=usuarios_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MalTrackerCog(bot))