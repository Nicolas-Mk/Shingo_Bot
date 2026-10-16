import os
import json
import sqlite3
import asyncio
import traceback
import aiohttp
from io import BytesIO
from datetime import datetime, timezone, timedelta

from PIL import Image, ImageDraw, ImageOps
import discord
from discord.ext import commands, tasks
from discord import app_commands
from cogs.config_cog import get_config

from cogs.mal_visual import (
    _BG, _CARD_BG, _ROW_BG, _TEXT_MAIN, _TEXT_MUTED,
    _CARD_W, _HEADER_H, _ROW_H, _GAP, _PAD, _COVER_RATIO, _SS,
    COR_ANIME, COR_MANGA,
    _fonte, _mascara_arredondada, _mascara_circular, _desenhar_icone_status,
    _truncar_texto, _placeholder_cover, _placeholder_avatar, _abrir_imagem,
    _finalizar_canvas,
)
from cogs.mal_easter_eggs import EasterEggManager, EventoEasterEgg

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

# Qualidade de compressão JPEG para capas de anime/mangá
QUALIDADE_CAPA: int = 65

# Intervalo mínimo entre sincronizações de favoritos (Jikan) por usuário
COOLDOWN_FAVORITOS = timedelta(days=7)

# Quantas atualizações de um mesmo usuário entram em uma única imagem
# antes de continuar em uma nova mensagem.
ITENS_POR_IMAGEM: int = 2

# Pausa entre chamadas ao Jikan ao processar vários usuários em sequência
# (sync de favoritos). O Jikan é um proxy comunitário gratuito e documenta
# dois limites simultâneos: 3 req/s E 60 req/min — o segundo é o que
# realmente importa aqui, porque passa despercebido: 0.5s de pausa dá
# 120 req/min, o DOBRO do permitido, e passa a estourar 429 conforme a
# base de usuários cresce. 1.1s mantém uma margem segura (~54 req/min).
PAUSA_JIKAN: float = 1.1

# Tentativas de retry em erros transitórios do Jikan (429/5xx/timeout)
# antes de desistir e devolver None (o que preserva os dados anteriores).
JIKAN_MAX_TENTATIVAS: int = 4

# ══════════════════════════════════════════════

MAL_API_BASE = "https://api.myanimelist.net/v2"
BRASILIA     = timezone(timedelta(hours=-3))

TODOS_STATUS_ANIME = ["watching", "completed", "on_hold", "dropped", "plan_to_watch"]
TODOS_STATUS_MANGA = ["reading",  "completed", "on_hold", "dropped", "plan_to_read"]

STATUS_ANIME = {
    "watching":      ("watching", "Assistindo"),
    "completed":     ("completed", "Completou"),
    "on_hold":       ("on_hold",  "Pausou"),
    "dropped":       ("dropped",  "Dropou"),
    "plan_to_watch": ("plan",     "Planeja assistir"),
}

STATUS_MANGA = {
    "reading":      ("reading",  "Lendo"),
    "completed":    ("completed", "Completou"),
    "on_hold":      ("on_hold",  "Pausou"),
    "dropped":      ("dropped",  "Dropou"),
    "plan_to_read": ("plan",     "Planeja ler"),
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
#  Persistência — capas de anime/mangá (cache local)
# ──────────────────────────────────────────────

def _garantir_tabela_capas(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mal_capas (
            item_id        TEXT NOT NULL,
            tipo           TEXT NOT NULL,
            imagem         BLOB NOT NULL,
            atualizado_em  TEXT NOT NULL,
            PRIMARY KEY (item_id, tipo)
        )
        """
    )


def carregar_capa(item_id: str, tipo: str) -> bytes | None:
    with sqlite3.connect("usuarios.db") as conn:
        _garantir_tabela_capas(conn)
        c = conn.cursor()
        c.execute(
            "SELECT imagem FROM mal_capas WHERE item_id = ? AND tipo = ?",
            (item_id, tipo)
        )
        row = c.fetchone()
    return row[0] if row else None


def salvar_capa(item_id: str, tipo: str, imagem_bytes: bytes):
    with sqlite3.connect("usuarios.db") as conn:
        _garantir_tabela_capas(conn)
        conn.execute(
            "INSERT OR REPLACE INTO mal_capas (item_id, tipo, imagem, atualizado_em) "
            "VALUES (?, ?, ?, ?)",
            (item_id, tipo, imagem_bytes, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()


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
    """
    Upsert em lote. guild_id propositalmente ausente — snapshots são globais por username.

    Usa INSERT ... ON CONFLICT DO UPDATE (em vez de INSERT OR REPLACE) e
    deliberadamente NÃO inclui `is_favorite` no SET: REPLACE apagava e
    recriava a linha inteira, resetando is_favorite pra 0 toda vez que
    qualquer campo de progresso mudava (favoritos só voltavam no próximo
    /mal_forcar_favoritos ou na sincronização semanal). Com upsert seletivo,
    is_favorite fica intocado aqui — quem escreve nele é atualizar_favoritos_db.

    IMPORTANTE: assume que existe uma constraint UNIQUE/PRIMARY KEY em
    (username, tipo, item_id) em mal_snapshots (o INSERT OR REPLACE anterior
    já dependia disso).
    """
    if not itens:
        return
    with sqlite3.connect("usuarios.db") as conn:
        conn.executemany(
            """
            INSERT INTO mal_snapshots (username, tipo, item_id, snapshot, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username, tipo, item_id) DO UPDATE SET
                snapshot   = excluded.snapshot,
                updated_at = excluded.updated_at
            """,
            [(username, tipo, item_id, snap, upd) for item_id, snap, upd in itens]
        )
        conn.commit()


# ──────────────────────────────────────────────
#  Persistência — progresso mensal (tabela própria)
# ──────────────────────────────────────────────
#
# Guardado separado de mal_snapshots (que representa só o estado ATUAL do
# item no MAL). Cada linha acumula o progresso feito num item DENTRO do mês
# corrente (mes_referencia); o acumulador reinicia sozinho assim que o item
# é tocado de novo num mês diferente do salvo — então progresso parcial
# (sem terminar o anime/mangá) também conta, não só conclusões.

def carregar_progresso_mensal(username: str, tipo: str) -> dict[str, dict]:
    """Retorna { item_id: {watched_at, mes_referencia, episodios_mes, capitulos_mes, volumes_mes, completou_mes, duracao_ep_min} }."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT item_id, watched_at, mes_referencia, episodios_mes, "
            "capitulos_mes, volumes_mes, completou_mes, duracao_ep_min "
            "FROM mal_progresso_mensal WHERE username = ? AND tipo = ?",
            (username, tipo)
        )
        rows = c.fetchall()

    resultado: dict[str, dict] = {}
    for item_id, watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes, dur_min in rows:
        resultado[item_id] = {
            "watched_at":     watched_at or "",
            "mes_referencia": mes_ref or "",
            "episodios_mes":  ep_mes or 0,
            "capitulos_mes":  cap_mes or 0,
            "volumes_mes":    vol_mes or 0,
            "completou_mes":  comp_mes or 0,
            "duracao_ep_min": dur_min or 0,
        }
    return resultado


# [(item_id, watched_at, mes_referencia, episodios_mes, capitulos_mes, volumes_mes, completou_mes, duracao_ep_min), ...]
# duracao_ep_min: duração média do episódio em minutos (0 = desconhecida/não aplicável a mangá).
# Diferente dos outros campos, NÃO é um acumulador mensal — é um atributo do
# próprio anime, então é sempre sobrescrito com o valor mais recente conhecido
# (não reinicia quando o mês vira).
ProgressoUpsert = tuple[str, str, str, int, int, int, int, int]


def salvar_progresso_mensal_em_lote(username: str, tipo: str, itens: list[ProgressoUpsert]):
    if not itens:
        return
    with sqlite3.connect("usuarios.db") as conn:
        conn.executemany(
            """
            INSERT INTO mal_progresso_mensal
                (username, tipo, item_id, watched_at, mes_referencia,
                 episodios_mes, capitulos_mes, volumes_mes, completou_mes, duracao_ep_min)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username, tipo, item_id) DO UPDATE SET
                watched_at     = excluded.watched_at,
                mes_referencia = excluded.mes_referencia,
                episodios_mes  = excluded.episodios_mes,
                capitulos_mes  = excluded.capitulos_mes,
                volumes_mes    = excluded.volumes_mes,
                completou_mes  = excluded.completou_mes,
                duracao_ep_min = excluded.duracao_ep_min
            """,
            [
                (username, tipo, item_id, watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes, dur_min)
                for item_id, watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes, dur_min in itens
            ]
        )
        conn.commit()


# ──────────────────────────────────────────────
#  Persistência — favoritos
# ──────────────────────────────────────────────

JIKAN_BASE = "https://api.jikan.moe/v4"


def carregar_ultima_sync_favoritos(username: str) -> datetime | None:
    """Retorna o timestamp (UTC) da última sincronização de favoritos, ou None se nunca rodou."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            "SELECT favoritos_sync_em FROM mal_usuarios_meta WHERE username = ?",
            (username,)
        )
        row = c.fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def salvar_ultima_sync_favoritos(username: str, quando: datetime):
    with sqlite3.connect("usuarios.db") as conn:
        conn.execute(
            "INSERT INTO mal_usuarios_meta (username, favoritos_sync_em) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET favoritos_sync_em = excluded.favoritos_sync_em",
            (username, quando.isoformat())
        )
        conn.commit()


def _garantir_tabela_favoritos_lote(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mal_favoritos_lote_meta (
            id             INTEGER PRIMARY KEY CHECK (id = 1),
            ultimo_lote_em TEXT NOT NULL
        )
        """
    )


def carregar_ultimo_lote_favoritos() -> datetime | None:
    """
    Timestamp (UTC) do início da última rodada SEMANAL de sincronização de
    favoritos (todos os usuários). Diferente de carregar_ultima_sync_favoritos
    (que é por usuário e só grava em caso de SUCESSO): este é um controle de
    lote único, gravado assim que a rodada começa — então, mesmo que o Jikan
    esteja fora do ar e todo mundo falhe, o bot não fica tentando de novo a
    cada ciclo, só na próxima semana.
    """
    with sqlite3.connect("usuarios.db") as conn:
        _garantir_tabela_favoritos_lote(conn)
        c = conn.cursor()
        c.execute("SELECT ultimo_lote_em FROM mal_favoritos_lote_meta WHERE id = 1")
        row = c.fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except Exception:
        return None


def marcar_inicio_lote_favoritos(quando: datetime):
    with sqlite3.connect("usuarios.db") as conn:
        _garantir_tabela_favoritos_lote(conn)
        conn.execute(
            "INSERT INTO mal_favoritos_lote_meta (id, ultimo_lote_em) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET ultimo_lote_em = excluded.ultimo_lote_em",
            (quando.isoformat(),)
        )
        conn.commit()


async def buscar_ids_favoritos_jikan(
    session: aiohttp.ClientSession,
    username: str,
    tentativas: int = JIKAN_MAX_TENTATIVAS,
) -> set[str] | None:
    """
    Consulta a API do Jikan e retorna os anime_ids dos favoritos do usuário.
    Retorna None em caso de erro de rede (para não zerar dados por falha temporária).
    Retorna set vazio se o usuário não tem favoritos ou a lista é privada.

    O Jikan é um proxy comunitário gratuito em cima do MAL e sofre 429
    (rate limit) e 5xx (instabilidade) com alguma frequência — isso é
    esperado, não excepcional, então tentamos de novo com backoff em vez
    de desistir na primeira falha. Um 429 respeita o header Retry-After
    quando presente.
    """
    url = f"{JIKAN_BASE}/users/{username}/favorites"

    for tentativa in range(1, tentativas + 1):
        espera = 2 * tentativa  # backoff linear simples: 2s, 4s, 6s, 8s...

        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    animes = data.get("data", {}).get("anime", [])
                    return {str(a["mal_id"]) for a in animes if "mal_id" in a}

                elif resp.status in (403, 404):
                    # Lista privada ou usuário não encontrado — não é erro, não insiste
                    return set()

                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            espera = float(retry_after)
                        except ValueError:
                            pass
                    print(
                        f"[MAL Tracker] Jikan rate-limited (429) para '{username}' "
                        f"— tentativa {tentativa}/{tentativas}, aguardando {espera:.1f}s."
                    )

                elif 500 <= resp.status < 600:
                    print(
                        f"[MAL Tracker] Jikan retornou {resp.status} (instável) para '{username}' "
                        f"— tentativa {tentativa}/{tentativas}, aguardando {espera:.1f}s."
                    )

                else:
                    print(f"[MAL Tracker] Jikan retornou {resp.status} para '{username}' (favoritos). Não é recuperável, desistindo.")
                    return None

        except asyncio.TimeoutError:
            print(
                f"[MAL Tracker] Timeout ao buscar favoritos de '{username}' "
                f"— tentativa {tentativa}/{tentativas}, aguardando {espera:.1f}s."
            )
        except Exception as e:
            print(
                f"[MAL Tracker] Erro ao buscar favoritos de '{username}': {e} "
                f"— tentativa {tentativa}/{tentativas}, aguardando {espera:.1f}s."
            )

        if tentativa < tentativas:
            await asyncio.sleep(espera)

    print(f"[MAL Tracker] ❌ Desisti de buscar favoritos de '{username}' após {tentativas} tentativa(s) — Jikan seguiu instável.")
    return None


def atualizar_favoritos_db(username: str, ids_favoritos: set[str]):
    """
    Atualiza is_favorite em mal_snapshots para o usuário:
      - 1 para os item_ids presentes em ids_favoritos
      - 0 para todos os outros animes do usuário
    """
    with sqlite3.connect("usuarios.db") as conn:
        if ids_favoritos:
            placeholders = ",".join("?" * len(ids_favoritos))
            conn.execute(
                f"UPDATE mal_snapshots SET is_favorite = 1 "
                f"WHERE username = ? AND tipo = 'anime' AND item_id IN ({placeholders})",
                (username, *ids_favoritos),
            )
            conn.execute(
                f"UPDATE mal_snapshots SET is_favorite = 0 "
                f"WHERE username = ? AND tipo = 'anime' AND item_id NOT IN ({placeholders})",
                (username, *ids_favoritos),
            )
        else:
            conn.execute(
                "UPDATE mal_snapshots SET is_favorite = 0 WHERE username = ? AND tipo = 'anime'",
                (username,),
            )
        conn.commit()


# ──────────────────────────────────────────────
#  Utilitário de imagem — avatares
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


def comprimir_capa(img_bytes: bytes, qualidade: int = QUALIDADE_CAPA) -> bytes:
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    img.thumbnail((300, 300), Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=qualidade, optimize=True)
    buffer.seek(0)
    return buffer.getvalue()


# ──────────────────────────────────────────────
#  Geração dos cards visuais (estilo AniList)
# ──────────────────────────────────────────────
#
# As primitivas de desenho compartilhadas (fontes, máscaras, ícones de
# status, supersampling etc.) vivem em cogs/mal_visual.py — importadas no
# topo deste arquivo — para que os easter eggs (cogs/mal_easter_eggs.py)
# usem exatamente o mesmo estilo visual sem depender deste módulo (e
# vice-versa, evitando import circular).

def gerar_imagem_lote(
    usuario: str,
    avatar_bytes: bytes | None,
    itens: list[dict],
    cor_destaque: tuple[int, int, int],
    subtitulo: str,
) -> bytes:
    """
    Gera um único PNG contendo até ITENS_POR_IMAGEM atividades do usuário,
    no estilo de um card do AniList: avatar + nome no topo, e para cada
    atividade uma linha com capa, status, título e detalhes.

    Cada item em `itens` deve ter as chaves:
        icone, label, titulo, meta (str opcional), timestamp (str), capa (bytes | None)

    Desenhado em escala _SS e reduzido no final para eliminar serrilhado.
    """
    s = _SS
    pad, header_h, row_h, gap, card_w = _PAD * s, _HEADER_H * s, _ROW_H * s, _GAP * s, _CARD_W * s

    n = len(itens)
    altura = pad * 2 + header_h + n * row_h + max(0, n - 1) * gap
    canvas = Image.new("RGB", (card_w, altura), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

    # Tamanhos definidos como proporção da largura do card (não em px absolutos),
    # pra continuar legível tanto em tela de desktop quanto de celular — o Discord
    # sempre escala a imagem inteira pra caber na largura disponível, então o que
    # importa é a razão fonte/canvas, não o valor em pixels do arquivo original.
    f_user   = _fonte(27 * s, negrito=True)
    f_sub    = _fonte(17 * s)
    f_status = _fonte(20 * s)
    f_title  = _fonte(24 * s, negrito=True)
    f_meta   = _fonte(18 * s)

    # ── Cabeçalho: avatar + username + subtítulo ──
    av_tam = 54 * s
    avatar_img = _abrir_imagem(avatar_bytes, _placeholder_avatar())
    av = ImageOps.fit(avatar_img, (av_tam, av_tam))
    canvas.paste(av, (pad, pad), _mascara_circular((av_tam, av_tam)))
    draw.ellipse((pad, pad, pad + av_tam, pad + av_tam), outline=cor_destaque, width=3 * s)

    tx = pad + av_tam + 16 * s
    draw.text((tx, pad + 2 * s), f"@{usuario}", font=f_user, fill=(255, 255, 255))
    draw.text((tx, pad + 2 * s + 34 * s), subtitulo, font=f_sub, fill=_TEXT_MUTED)

    # ── Linhas de atividade ──
    y = pad + header_h
    for item in itens:
        draw.rounded_rectangle((pad, y, card_w - pad, y + row_h - gap), radius=10 * s, fill=_ROW_BG)

        cov_altura = row_h - gap - 16 * s
        cov_largura = int(cov_altura * _COVER_RATIO)
        cover_img = _abrir_imagem(item.get("capa"), _placeholder_cover())
        cov = ImageOps.fit(cover_img, (cov_largura, cov_altura))
        canvas.paste(cov, (pad + 8 * s, y + 8 * s), _mascara_arredondada((cov_largura, cov_altura), 8 * s))

        tx2 = pad + 8 * s + cov_largura + 16 * s
        largura_max = card_w - pad - 12 * s - tx2

        status_y = y + 24 * s
        icone_r = 12 * s
        icone_cx = tx2 + icone_r
        icone_cy = status_y + 13 * s
        _desenhar_icone_status(draw, item["icone"], icone_cx, icone_cy, icone_r, _TEXT_MUTED)

        label_x = tx2 + icone_r * 2 + 10 * s
        draw.text((label_x, status_y), item["label"], font=f_status, fill=_TEXT_MUTED)

        if item.get("timestamp"):
            ts_w = draw.textlength(item["timestamp"], font=f_meta)
            draw.text((card_w - pad - 10 * s - ts_w, status_y + 2 * s), item["timestamp"], font=f_meta, fill=_TEXT_MUTED)

        titulo_disp = _truncar_texto(draw, item["titulo"], f_title, largura_max)
        draw.text((tx2, y + 64 * s), titulo_disp, font=f_title, fill=cor_destaque)

        if item.get("meta"):
            meta_disp = _truncar_texto(draw, item["meta"], f_meta, largura_max)
            draw.text((tx2, y + 112 * s), meta_disp, font=f_meta, fill=_TEXT_MAIN)

        y += row_h

    return _finalizar_canvas(canvas)


_ICONE_CABECALHO_PATH = os.path.join("assets", "fiscal.jpg")
_ICONE_CABECALHO_CACHE: bytes | None = None
_ICONE_CABECALHO_CARREGADO = False


def _carregar_icone_cabecalho() -> bytes | None:
    """Lê assets/fiscal.jpg do disco uma única vez e mantém em cache em memória."""
    global _ICONE_CABECALHO_CACHE, _ICONE_CABECALHO_CARREGADO
    if not _ICONE_CABECALHO_CARREGADO:
        _ICONE_CABECALHO_CARREGADO = True
        if os.path.isfile(_ICONE_CABECALHO_PATH):
            try:
                with open(_ICONE_CABECALHO_PATH, "rb") as f:
                    _ICONE_CABECALHO_CACHE = f.read()
            except Exception as e:
                print(f"[MAL Tracker] ⚠️  Não foi possível carregar '{_ICONE_CABECALHO_PATH}': {e}")
                _ICONE_CABECALHO_CACHE = None
        else:
            print(f"[MAL Tracker] ⚠️  Ícone '{_ICONE_CABECALHO_PATH}' não encontrado.")
            _ICONE_CABECALHO_CACHE = None
    return _ICONE_CABECALHO_CACHE


def gerar_imagem_cabecalho(
    total_itens: int,
    total_usuarios: int,
    quando_str: str,
) -> bytes:
    """
    Gera o card de abertura do relatório ("FISCALIZAÇÃO MYANIMELIST"),
    no mesmo estilo visual dos cards de atualização. As informações ficam
    no topo (fontes maiores) e, abaixo, um quadrado com a imagem fixa
    assets/fiscal.jpg, centralizado e em tamanho legível.
    """
    s = _SS
    pad, card_w = _PAD * s, _CARD_W * s

    f_titulo = _fonte(32 * s, negrito=True)
    f_sub    = _fonte(21 * s)

    texto_atualizacoes = "atualização" if total_itens == 1 else "atualizações"
    texto_usuarios = "usuário" if total_usuarios == 1 else "usuários"
    linha_sub = (
        f"{quando_str}   ·   {total_itens} {texto_atualizacoes} de {total_usuarios} {texto_usuarios}"
    )

    icone_tam = 200 * s

    titulo_y  = pad + 6 * s
    sub_y     = titulo_y + 44 * s
    imagem_y  = sub_y + 38 * s
    altura    = imagem_y + icone_tam + pad

    canvas = Image.new("RGB", (card_w, altura), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

    titulo_w = draw.textlength("FISCALIZAÇÃO MYANIMELIST", font=f_titulo)
    draw.text(((card_w - titulo_w) // 2, titulo_y), "FISCALIZAÇÃO MYANIMELIST", font=f_titulo, fill=(255, 255, 255))

    sub_w = draw.textlength(linha_sub, font=f_sub)
    draw.text(((card_w - sub_w) // 2, sub_y), linha_sub, font=f_sub, fill=_TEXT_MUTED)

    icone_img = _abrir_imagem(_carregar_icone_cabecalho(), _placeholder_avatar())
    icone = ImageOps.fit(icone_img, (icone_tam, icone_tam))
    icone_x = (card_w - icone_tam) // 2
    canvas.paste(icone, (icone_x, imagem_y), _mascara_arredondada((icone_tam, icone_tam), 16 * s))
    draw.rounded_rectangle(
        (icone_x, imagem_y, icone_x + icone_tam, imagem_y + icone_tam),
        radius=16 * s, outline=COR_ANIME, width=3 * s
    )

    return _finalizar_canvas(canvas)


def _duracao_min_do_node(node: dict) -> int:
    """
    Extrai average_episode_duration (segundos, só existe pra anime) de um
    node da API do MAL e converte pra minutos arredondados. Retorna 0 se
    ausente/zero (mangá, ou duração desconhecida) — usado pelo /mal_mensal
    pra normalizar pontuação de episódios por duração real.
    """
    segundos = node.get("average_episode_duration")
    if not segundos:
        return 0
    return round(segundos / 60)


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
        # Manager central de easter eggs (ver cogs/mal_easter_eggs.py). O cog
        # não conhece nenhum easter egg específico — só delega a detecção e
        # o envio a esta instância, que despacha para quem estiver registrado.
        self._easter_eggs = EasterEggManager()
        # Cache de eventos de easter egg detectados no ciclo atual:
        # { username: [EventoEasterEgg, ...] } — normalmente terá no máximo
        # 1 evento por usuário, mas fica como lista para suportar múltiplos
        # easter eggs (ou o mesmo disparando mais de uma vez) no mesmo ciclo.
        self._cache_eventos_easter_eggs: dict[str, list[EventoEasterEgg]] = {}
        self.monitorar_loop.start()
        self.favoritos_semanal_loop.start()

    def cog_unload(self):
        self.monitorar_loop.cancel()
        self.favoritos_semanal_loop.cancel()
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
        # main_picture é necessário para gerar os cards com a capa do anime/mangá;
        # num_episodes/num_chapters é o total da obra, usado para exibir "EP 15 de 24".
        # average_episode_duration (segundos) só existe pra anime — usado pelo
        # /mal_mensal pra normalizar a pontuação de episódios por duração real.
        campos = "list_status,main_picture,num_episodes,average_episode_duration" if tipo == "anime" else "list_status,main_picture,num_chapters,num_volumes"
        params = {
            "fields": campos,
            "sort":   "list_updated_at",
            "limit":  limite,
            "offset": offset,
            # Sem isso, a API omite silenciosamente da resposta qualquer item
            # marcado como nsfw=gray ou nsfw=black — mesmo títulos só PG-13/R+
            # (ex: Apocalypse Hotel, Lycoris Recoil, Sayonara Lara, Prison
            # School, Kanashimi no Belladonna). O item nunca aparece em
            # nenhuma página, pra nenhum usuário, e nunca entra em
            # mal_snapshots — confirmado via debug manual em 2026-09.
            "nsfw": "true",
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

            # Baseline zerado na tabela de progresso mensal — o histórico de
            # antes de o bot existir não conta como progresso "do mês"; o
            # loop horário é quem vai preencher os acumuladores dali pra frente.
            # duracao_ep_min já entra preenchida (quando disponível) mesmo no
            # baseline — não é um acumulador, é só um atributo do anime.
            upserts_progresso = [
                (str(item["node"]["id"]), "", "", 0, 0, 0, 0, _duracao_min_do_node(item["node"]))
                for item in pagina
            ]
            salvar_progresso_mensal_em_lote(username, tipo, upserts_progresso)
            total  += len(pagina)
            offset += len(pagina)

            print(f"[MAL Tracker]    '{username}' ({tipo}): {total} itens sincronizados...")

            if len(pagina) < LIMITE_PAGINA:
                break  # Última página

            # Pausa antes da próxima página para não estourar o rate limit
            print(f"[MAL Tracker]    Aguardando {PAUSA_PAGINACAO:.0f}s antes da próxima página...")
            await asyncio.sleep(PAUSA_PAGINACAO)

        print(f"[MAL Tracker] ✅ Sync completo de '{username}' ({tipo}): {total} itens.")

    async def _backfill_nsfw_usuario(self, username: str) -> tuple[int, int]:
        """
        Sincronização adicional, sob demanda, pra preencher no banco os itens
        NSFW (nsfw=gray/black) que a API omitia da lista ANTES da correção do
        parâmetro nsfw=true em _buscar_lista (confirmado em 2026-09 com títulos
        como Apocalypse Hotel, Lycoris Recoil, Sayonara Lara, Prison School e
        Kanashimi no Belladonna).

        Só insere itens que AINDA NÃO estão em mal_snapshots — não sobrescreve
        nada já sincronizado, e não refaz a sincronização completa inteira.
        Usuários novos daqui pra frente já recebem esses itens automaticamente
        via _sincronizar_lista_completa normal (que já usa nsfw=true agora),
        então este backfill só é necessário pra quem já tinha snapshots ANTES
        da correção.

        Itens preenchidos aqui entram como BASELINE (igual sync inicial): não
        disparam relatório retroativo de atualizações antigas — só passam a
        ser comparados/rastreados a partir do próximo loop horário.

        Retorna (novos_anime, novos_manga).
        """
        if username in self._sync_em_andamento:
            print(f"[MAL Tracker] '{username}' já em sync, pulando backfill NSFW.")
            return (0, 0)

        self._sync_em_andamento.add(username)
        resultado = {"anime": 0, "manga": 0}
        try:
            for tipo in ("anime", "manga"):
                snaps_existentes = carregar_snapshots(username, tipo)
                offset = 0

                while True:
                    pagina = await self._buscar_lista(username, tipo, limite=LIMITE_PAGINA, offset=offset)
                    if pagina is None:
                        print(f"[MAL Tracker] ⚠️  Backfill NSFW de '{username}' ({tipo}) interrompido no offset={offset}.")
                        break
                    if not pagina:
                        break

                    faltantes = [item for item in pagina if str(item["node"]["id"]) not in snaps_existentes]
                    if faltantes:
                        upserts = [
                            (
                                str(item["node"]["id"]),
                                self._snapshot(item["list_status"], tipo),
                                item["list_status"].get("updated_at", ""),
                            )
                            for item in faltantes
                        ]
                        salvar_snapshots_em_lote(username, tipo, upserts)

                        upserts_progresso = [
                            (str(item["node"]["id"]), "", "", 0, 0, 0, 0, _duracao_min_do_node(item["node"]))
                            for item in faltantes
                        ]
                        salvar_progresso_mensal_em_lote(username, tipo, upserts_progresso)

                        resultado[tipo] += len(faltantes)
                        for item in faltantes:
                            print(
                                f"[MAL Tracker]    [{username}] Backfill NSFW: "
                                f"'{item['node']['title']}' ({tipo}/{item['node']['id']}) adicionado como baseline."
                            )

                    offset += len(pagina)
                    if len(pagina) < LIMITE_PAGINA:
                        break

                    await asyncio.sleep(PAUSA_PAGINACAO)

                print(f"[MAL Tracker] ✅ Backfill NSFW de '{username}' ({tipo}): {resultado[tipo]} item(ns) novo(s).")
        finally:
            self._sync_em_andamento.discard(username)

        return (resultado["anime"], resultado["manga"])

    async def _sincronizar_favoritos_usuario(self, username: str, forcar: bool = False):
        """
        Consulta o Jikan e atualiza is_favorite no banco para o usuário.
        Respeita COOLDOWN_FAVORITOS: só consulta o Jikan novamente se já tiver
        passado o intervalo desde a última sincronização (a menos que forcar=True).
        Chamado no boot, ao adicionar um usuário e a cada ciclo do loop horário.
        Não faz nada se a resposta do Jikan indicar erro temporário (None).
        """
        if not forcar:
            ultima = carregar_ultima_sync_favoritos(username)
            if ultima is not None:
                agora = datetime.now(timezone.utc)
                if agora - ultima < COOLDOWN_FAVORITOS:
                    return

        session = await self._get_session()
        ids_favoritos = await buscar_ids_favoritos_jikan(session, username)
        if ids_favoritos is None:
            # Erro de rede — mantém os dados anteriores intactos, não atualiza timestamp
            return
        atualizar_favoritos_db(username, ids_favoritos)
        salvar_ultima_sync_favoritos(username, datetime.now(timezone.utc))
        print(
            f"[MAL Tracker] Favoritos de '{username}' atualizados "
            f"({len(ids_favoritos)} anime(s) marcado(s))."
        )

    async def _sincronizar_usuario_completo(self, username: str):
        """
        Dispara a sincronização completa de anime + manga para um usuário.
        Idempotente: não faz nada se o usuário já tiver snapshots.
        Usa _sync_em_andamento para evitar execuções paralelas.
        Ao final, sincroniza também os favoritos via Jikan (sempre, ignorando cooldown,
        pois é a primeira sincronização do usuário).
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
            # Popula favoritos logo após o sync completo
            await self._sincronizar_favoritos_usuario(username, forcar=True)
        finally:
            self._sync_em_andamento.discard(username)

    # ──────────────────────────────────────────
    #  Avatar
    # ──────────────────────────────────────────

    async def _baixar_bytes(self, session: aiohttp.ClientSession, url: str) -> bytes | None:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    print(f"[MAL Tracker] ⚠️  Download falhou (HTTP {resp.status}) para {url}")
                    return None
                return await resp.read()
        except Exception as e:
            print(f"[MAL Tracker] ⚠️  Erro ao baixar {url}: {e}")
            return None

    async def _obter_avatar_bytes(self, username: str) -> bytes | None:
        """Retorna os bytes JPEG já comprimidos do avatar, usando cache local."""
        icon_bytes = carregar_avatar(username)
        if icon_bytes:
            return icon_bytes

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

                img_bytes = await self._baixar_bytes(s, img_url)
                if not img_bytes:
                    return None

            icon_comprimido    = recortar_e_comprimir_avatar(img_bytes, username)
            tamanho_original   = len(img_bytes)
            tamanho_comprimido = len(icon_comprimido)
            reducao = round(((tamanho_original - tamanho_comprimido) / tamanho_original) * 100, 1)

            salvar_avatar(username, icon_comprimido)
            print(f"[MAL Tracker] 💾 Avatar de '{username}' em cache ({reducao}% menor).")
            return icon_comprimido

        except Exception as e:
            print(f"[MAL Tracker] ❌ Erro ao processar avatar de '{username}': {e}")
            return None

    async def _obter_capa_bytes(self, item_id: str, tipo: str, url: str | None) -> bytes | None:
        """Retorna os bytes JPEG já comprimidos da capa (anime/mangá), usando cache local."""
        if not url:
            return None

        cache = carregar_capa(item_id, tipo)
        if cache:
            return cache

        session = await self._get_session()
        img_bytes = await self._baixar_bytes(session, url)
        if not img_bytes:
            return None

        try:
            comprimido = comprimir_capa(img_bytes)
        except Exception as e:
            print(f"[MAL Tracker] ❌ Erro ao comprimir capa {tipo}/{item_id}: {e}")
            return None

        salvar_capa(item_id, tipo, comprimido)
        return comprimido

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

    # ──────────────────────────────────────────
    #  Progresso mensal (acumulado direto nas colunas de mal_snapshots)
    # ──────────────────────────────────────────

    def _mes_chave(self, timestamp: str) -> str:
        """Retorna 'YYYY-MM' a partir de um timestamp ISO (ou do mês atual em UTC se inválido/vazio)."""
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m")
            except Exception:
                pass
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _atualizar_progresso_mensal(
        self,
        registro_progresso_anterior: dict | None,
        delta_episodios: int,
        delta_capitulos: int,
        delta_volumes: int,
        completou_agora: bool,
        updated_at: str,
    ) -> tuple[str, str, int, int, int, int]:
        """
        Calcula os novos valores de (watched_at, mes_referencia, episodios_mes,
        capitulos_mes, volumes_mes, completou_mes) para a linha do item em
        mal_progresso_mensal. `registro_progresso_anterior` vem de
        carregar_progresso_mensal() (ou None se o item ainda não tem linha lá).

        Se o mês do evento (derivado de updated_at) for igual ao mes_referencia
        já salvo, ACUMULA sobre o que já existia. Caso contrário (mês virou,
        ou item nunca teve progresso), REINICIA o acumulador a partir de 0 —
        ou seja, é o próprio ato de detectar um novo progresso num mês novo
        que "fecha" o mês anterior daquele item específico.

        NOTA (limitação conhecida e aceita): como isso reinicia por item, se
        um item específico for atualizado entre 00:00 e 12:00 do dia 1º (já
        no mês novo) ANTES do /mal_mensal gerar o relatório do mês anterior,
        o total daquele item pro mês anterior já terá sido perdido/zerado.
        Na prática costuma afetar no máximo 1-2 itens por usuário, só quando
        a pessoa mexe na lista bem na virada do mês.
        """
        mes_evento = self._mes_chave(updated_at)

        if registro_progresso_anterior and registro_progresso_anterior.get("mes_referencia") == mes_evento:
            base_ep   = registro_progresso_anterior.get("episodios_mes", 0)
            base_cap  = registro_progresso_anterior.get("capitulos_mes", 0)
            base_vol  = registro_progresso_anterior.get("volumes_mes", 0)
            base_comp = registro_progresso_anterior.get("completou_mes", 0)
        else:
            base_ep = base_cap = base_vol = base_comp = 0

        episodios_mes = base_ep + max(0, delta_episodios)
        capitulos_mes = base_cap + max(0, delta_capitulos)
        volumes_mes   = base_vol + max(0, delta_volumes)
        completou_mes = 1 if (base_comp == 1 or completou_agora) else 0

        watched_at = updated_at or datetime.now(timezone.utc).isoformat()

        return watched_at, mes_evento, episodios_mes, capitulos_mes, volumes_mes, completou_mes

    def _processar_itens(
        self,
        itens: list[dict],
        snapshots_db: dict[str, tuple[str, str]],    # { item_id: (snap_json, updated_at) } — mal_snapshots
        progresso_db: dict[str, dict],                # { item_id: {mes_referencia, episodios_mes, ...} } — mal_progresso_mensal
        tipo: str,
        status_permitidos: list[str],              # filtros já resolvidos pela guild
        primeira_vez: bool = False,                # True = usuário sem snapshots ainda
        username: str = "",                        # apenas para contexto nos logs
    ) -> tuple[list[dict], list[tuple[str, str, str]], list[tuple[str, str, str, int, int, int, int]], list[EventoEasterEgg]]:
        """
        Compara itens da API contra snapshots do banco.

        Retorna:
          - mudanças detectadas (para o relatório desta guild)
          - upserts de mal_snapshots a persistir
          - upserts de mal_progresso_mensal a persistir
          - eventos de easter egg (ver EasterEggManager, em
            cogs/mal_easter_eggs.py) disparados neste lote — cada easter
            egg registrado decide por conta própria, a partir do snapshot
            anterior x atual de cada item, se deve disparar. Este método
            não conhece nenhum easter egg específico; só delega a decisão.
        """
        novas:             list[dict]                 = []
        upserts:            list[tuple[str, str, str]] = []
        upserts_progresso:  list[tuple[str, str, str, int, int, int, int]] = []
        eventos_easter_egg:  list[EventoEasterEgg]      = []

        for item in itens:
            item_id    = str(item["node"]["id"])
            titulo     = item["node"]["title"]
            capa_url   = item["node"].get("main_picture", {}).get("medium") or item["node"].get("main_picture", {}).get("large")
            if tipo == "anime":
                total_unidades = item["node"].get("num_episodes") or 0
            else:
                total_unidades = item["node"].get("num_chapters") or 0
            status     = item["list_status"]
            snap_atual = self._snapshot(status, tipo)
            updated_at = status.get("updated_at", "")
            status_val = status.get("status", "")

            # Duração do episódio (minutos) — só existe pra anime. Não é um
            # acumulador mensal, então sempre usa o valor mais recente vindo
            # da API; se por algum motivo a API não trouxer desta vez, cai
            # pro último valor já conhecido em vez de zerar.
            registro_anterior     = snapshots_db.get(item_id)
            registro_progresso_anterior = progresso_db.get(item_id)
            duracao_min = _duracao_min_do_node(item["node"]) if tipo == "anime" else 0
            if not duracao_min and registro_progresso_anterior:
                duracao_min = registro_progresso_anterior.get("duracao_ep_min", 0)

            # ── Item novo (usuário com sync completo, mas item não estava na lista antes)
            if registro_anterior is None:
                upserts.append((item_id, snap_atual, updated_at))

                if primeira_vez:
                    # Baseline da sincronização inicial — não conta como progresso do mês.
                    upserts_progresso.append((item_id, "", "", 0, 0, 0, 0, duracao_min))
                    continue

                # Item genuinamente novo na lista (já sincronizada) — o "anterior"
                # é a linha de base (0 ou o que já estiver em mal_progresso_mensal,
                # ex.: item que saiu dos 50 mais recentes e voltou), então o
                # progresso já registrado no MAL conta pro mês do updated_at.
                # Isso vale independente da janela de 1h do loop ou dos filtros
                # de status desta guild (que só afetam o relatório horário de
                # novidades, não as estatísticas mensais cumulativas).
                ep_atual  = status.get("num_episodes_watched", 0) if tipo == "anime" else 0
                cap_atual = status.get("num_chapters_read", 0)    if tipo == "manga" else 0
                vol_atual = status.get("num_volumes_read", 0)     if tipo == "manga" else 0
                completou_agora = status_val == "completed"
                watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes = self._atualizar_progresso_mensal(
                    registro_progresso_anterior, ep_atual, cap_atual, vol_atual, completou_agora, updated_at
                )
                upserts_progresso.append((item_id, watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes, duracao_min))

                # Nota: itens "novos" pro banco (primeira vez que aparecem nos 50
                # mais recentes) não são avaliados por nenhum easter egg — não
                # temos como confirmar que a transição pra "completed" aconteceu
                # agora e não há muito tempo atrás, então preferimos não arriscar
                # um falso disparo. Os easter eggs só são avaliados na
                # comparação de snapshot anterior x atual, mais abaixo.

                if not updated_at:
                    print(f"[MAL Tracker] [{username}] '{titulo}' ({tipo}/{item_id}) é novo na lista mas sem updated_at — não reportado.")
                    continue
                if status_val not in status_permitidos:
                    print(f"[MAL Tracker] [{username}] '{titulo}' ({tipo}/{item_id}) é novo (status={status_val}) mas fora dos status permitidos nesta checagem — não reportado.")
                    continue
                try:
                    agora_utc = datetime.now(timezone.utc)
                    dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    diferenca_seg = (agora_utc - dt).total_seconds()
                except Exception as e:
                    print(f"[MAL Tracker] [{username}] ⚠️  Erro ao interpretar updated_at='{updated_at}' de '{titulo}' ({tipo}/{item_id}): {e}. Item NÃO reportado.")
                    continue
                if diferenca_seg <= 3600:
                    print(f"[MAL Tracker] [{username}] ➕ Novo item detectado: '{titulo}' ({tipo}/{item_id}) status={status_val} — adicionado ao relatório.")
                    novas.append(self._montar_entrada(titulo, item_id, status, updated_at, tipo, capa_url, total_unidades))
                else:
                    # Item novo, mas com updated_at fora da janela de 1h — provavelmente o loop
                    # atrasou (reinício do bot, hospedagem lenta, etc.) e a mudança real já
                    # tinha acontecido há mais tempo. Fica registrado no banco (upsert acima),
                    # mas não é reportado no chat. Logamos para não ficar um "sumiço" silencioso.
                    print(
                        f"[MAL Tracker] [{username}] ⏭️  '{titulo}' ({tipo}/{item_id}) é novo mas updated_at "
                        f"tem {diferenca_seg/60:.0f} min (> 60 min) — fora da janela do loop horário, NÃO reportado."
                    )
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

            # Progresso mensal: soma o delta de episódios (anime) ou capítulos/
            # volumes (mangá) entre o snapshot anterior e o atual — independente
            # dos filtros de status da guild (que só afetam o relatório horário).
            if tipo == "anime":
                delta_ep, delta_cap, delta_vol = atu.get("num_episodes_watched", 0) - ant.get("num_episodes_watched", 0), 0, 0
            else:
                delta_ep = 0
                delta_cap = atu.get("num_chapters_read", 0) - ant.get("num_chapters_read", 0)
                delta_vol = atu.get("num_volumes_read", 0) - ant.get("num_volumes_read", 0)
            completou_agora = atu.get("status") == "completed" and ant.get("status") != "completed"

            # 🎉 Easter eggs: cada um registrado em EasterEggManager decide,
            # a partir da comparação snapshot anterior x atual deste item
            # específico, se deve disparar — este método não sabe (nem
            # precisa saber) que easter eggs existem. Isso evita falso
            # positivo em itens "novos" pro banco (onde não sabemos o estado
            # real anterior) e é avaliado independente dos filtros de status
            # da guild (assim como o progresso mensal): é uma reação do bot
            # ao evento em si, não uma notificação de atualização de lista.
            # Para adicionar um novo easter egg, ver cogs/mal_easter_eggs.py
            # — nada aqui precisa mudar.
            eventos_easter_egg.extend(
                self._easter_eggs.detectar(tipo, item_id, ant, atu, completou_agora, username=username)
            )

            watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes = self._atualizar_progresso_mensal(
                registro_progresso_anterior, delta_ep, delta_cap, delta_vol, completou_agora, updated_at
            )
            upserts_progresso.append((item_id, watched_at, mes_ref, ep_mes, cap_mes, vol_mes, comp_mes, duracao_min))

            if so_nota or status_val in status_permitidos:
                nota_anterior = ant.get("score") if so_nota else None
                print(
                    f"[MAL Tracker] [{username}] 🔄 Mudança detectada: '{titulo}' ({tipo}/{item_id}) "
                    f"status={status_val} so_nota={so_nota} snapshot_anterior={snap_anterior} → snapshot_atual={snap_atual} "
                    f"— adicionado ao relatório."
                )
                novas.append(self._montar_entrada(
                    titulo, item_id, status, updated_at, tipo, capa_url, total_unidades,
                    so_nota=so_nota, nota_anterior=nota_anterior
                ))
            else:
                print(
                    f"[MAL Tracker] [{username}] ⏭️  '{titulo}' ({tipo}/{item_id}) mudou (status={status_val}) "
                    f"mas está fora dos status permitidos nesta checagem — NÃO reportado (snapshot atualizado no banco)."
                )

        return novas, upserts, upserts_progresso, eventos_easter_egg

    def _montar_entrada(
        self,
        titulo: str,
        item_id: str,
        status: dict,
        updated_at: str,
        tipo: str,
        capa_url: str | None,
        total_unidades: int = 0,
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
            "capa_url":      capa_url,
            "total_unidades": total_unidades,  # total de episódios (anime) ou capítulos (mangá); 0 = desconhecido/em lançamento
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
    ) -> tuple[dict[str, list[dict]], list[EventoEasterEgg]]:
        """
        Busca os itens recentes de um usuário, compara com os snapshots do banco
        e persiste as atualizações. Retorna (mudanças brutas, eventos_easter_egg):
          - mudanças brutas, sem filtro de guild, para que cada guild aplique
            seus próprios filtros depois;
          - eventos de easter egg (ver EasterEggManager, cogs/mal_easter_eggs.py)
            disparados neste ciclo — cada easter egg registrado decide por
            conta própria se aplica a anime, mangá ou ambos.

        Chamado UMA VEZ por usuário por ciclo — garante que todas as guilds
        recebem o mesmo conjunto de mudanças/eventos antes de qualquer snapshot
        ser atualizado.
        """
        if username in self._sync_em_andamento:
            print(f"[MAL Tracker] '{username}' em sync inicial, pulando loop horário.")
            return {}, []

        snaps_anime = carregar_snapshots(username, "anime")
        snaps_manga = carregar_snapshots(username, "manga")
        progresso_anime = carregar_progresso_mensal(username, "anime")
        progresso_manga = carregar_progresso_mensal(username, "manga")

        itens_anime = await self._buscar_lista(username, "anime", limite=LIMITE_LOOP)
        itens_manga = await self._buscar_lista(username, "manga", limite=LIMITE_LOOP)

        if itens_anime is None:
            print(f"[MAL Tracker] [{username}] ⚠️  Falha ao buscar animelist neste ciclo — nenhuma atualização de anime será detectada agora (ver erro acima).")
        if itens_manga is None:
            print(f"[MAL Tracker] [{username}] ⚠️  Falha ao buscar mangalist neste ciclo — nenhuma atualização de mangá será detectada agora (ver erro acima).")

        # Passa todos os status — queremos detectar tudo aqui; o filtro é aplicado
        # por guild depois em _filtrar_mudancas_para_guild.
        todos_status = TODOS_STATUS_ANIME + TODOS_STATUS_MANGA

        novas_anime, upserts_anime, upserts_progresso_anime, eventos_anime = self._processar_itens(
            itens_anime or [], snaps_anime, progresso_anime, "anime", todos_status, username=username
        )
        novas_manga, upserts_manga, upserts_progresso_manga, eventos_manga = self._processar_itens(
            itens_manga or [], snaps_manga, progresso_manga, "manga", todos_status, username=username
        )

        # Persiste snapshots e progresso mensal DEPOIS de processar — todas as
        # guilds já leram o estado anterior correto neste ponto (chamadas
        # sequenciais no loop).
        salvar_snapshots_em_lote(username, "anime", upserts_anime)
        salvar_snapshots_em_lote(username, "manga", upserts_manga)
        salvar_progresso_mensal_em_lote(username, "anime", upserts_progresso_anime)
        salvar_progresso_mensal_em_lote(username, "manga", upserts_progresso_manga)

        novas_anime.sort(key=lambda x: x["updated_at"])
        novas_manga.sort(key=lambda x: x["updated_at"])

        total = len(novas_anime) + len(novas_manga)
        if total:
            print(f"[MAL Tracker] '{username}': {len(novas_anime)} anime, {len(novas_manga)} mangá detectados.")

        eventos_easter_egg = eventos_anime + eventos_manga
        return {"anime": novas_anime, "manga": novas_manga}, eventos_easter_egg

    def _filtrar_mudancas_para_guild(
        self,
        mudancas_brutas: dict[str, list[dict]],
        filtros: dict[str, list[str]],
        contexto: str = "",  # "usuario@guild_id", só para log
    ) -> dict[str, list[dict]]:
        """
        Aplica os filtros de status da guild sobre as mudanças brutas de um usuário.
        Trocas de nota (so_nota=True) sempre passam, independente do filtro.
        """
        def filtrar(itens: list[dict], tipo: str) -> list[dict]:
            permitidos = set(filtros.get(tipo, []))
            passou = [
                item for item in itens
                if item.get("so_nota") or item.get("status") in permitidos
            ]
            descartados = [item for item in itens if item not in passou]
            for item in descartados:
                print(
                    f"[MAL Tracker] [{contexto}] ⏭️  '{item.get('titulo')}' ({tipo}) status={item.get('status')} "
                    f"descartado pelo filtro de status desta guild (permitidos: {sorted(permitidos)})."
                )
            return passou

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
            total_bruto = len(mudancas_brutas.get("anime", [])) + len(mudancas_brutas.get("manga", []))
            filtrado = self._filtrar_mudancas_para_guild(mudancas_brutas, filtros, contexto=f"{usuario}@guild:{guild_id}")
            if filtrado:
                total_filtrado = len(filtrado.get("anime", [])) + len(filtrado.get("manga", []))
                if total_filtrado < total_bruto:
                    print(f"[MAL Tracker] [{usuario}@guild:{guild_id}] {total_bruto - total_filtrado} de {total_bruto} mudança(s) descartada(s) pelos filtros da guild.")
                resultado[usuario] = filtrado
            elif total_bruto:
                print(f"[MAL Tracker] [{usuario}@guild:{guild_id}] todas as {total_bruto} mudança(s) foram descartadas pelos filtros da guild — nada será reportado.")

        return resultado

    # ──────────────────────────────────────────
    #  Formatação do relatório (cards visuais)
    # ──────────────────────────────────────────

    def _formatar_meta(self, item: dict) -> str:
        """Monta o texto de detalhes (episódio/capítulo, nota, hora) de um item."""
        detalhes: list[str] = []
        total = item.get("total_unidades") or 0

        if item.get("so_nota"):
            nota_ant = item.get("nota_anterior")
            if nota_ant is not None:
                detalhes.append(f"Nota: {nota_ant}/10 → {item['score']}/10")
            else:
                detalhes.append(f"Nota: {item['score']}/10")
        else:
            if item["tipo"] == "anime":
                if item["status"] == "watching" or item.get("rewatching"):
                    ep = item.get("episodios", 0)
                    progresso = f"EP {ep} de {total}" if total > 0 else f"EP {ep}"
                    detalhes.append(f"Reassistindo · {progresso}" if item.get("rewatching") else progresso)
            else:
                if item["status"] == "reading" or item.get("rereading"):
                    cap = item.get("capitulos", 0)
                    progresso = f"Cap {cap} de {total}" if total > 0 else f"Cap {cap}"
                    detalhes.append(f"Relendo · {progresso}" if item.get("rereading") else progresso)
            if item["score"] > 0:
                detalhes.append(f"Nota: {item['score']}/10")

        return " · ".join(detalhes)

    def _formatar_timestamp(self, item: dict) -> str:
        if not item.get("updated_at"):
            return ""
        try:
            dt = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
            return dt.astimezone(BRASILIA).strftime("%H:%M")
        except Exception:
            return ""

    async def _montar_item_visual(self, item: dict) -> dict:
        """Converte uma entrada de mudança em dados prontos para o card (com capa já baixada)."""
        if item.get("so_nota"):
            icone, label = "note", "Atualizou nota"
        elif item["tipo"] == "anime":
            icone, label = STATUS_ANIME.get(item["status"], ("note", item["status"]))
        else:
            icone, label = STATUS_MANGA.get(item["status"], ("note", item["status"]))

        capa_bytes = await self._obter_capa_bytes(item["item_id"], item["tipo"], item.get("capa_url"))

        return {
            "icone":     icone,
            "label":     label,
            "titulo":    item["titulo"],
            "meta":      self._formatar_meta(item),
            "timestamp": self._formatar_timestamp(item),
            "capa":      capa_bytes,
        }

    async def _gerar_arquivos_usuario(self, usuario: str, dados: dict[str, list[dict]]) -> list[tuple[discord.File, tuple[int, int, int]]]:
        """
        Gera as imagens (uma para cada lote de até ITENS_POR_IMAGEM atividades)
        para um único usuário, misturando anime e mangá em ordem cronológica.
        Se o usuário tiver mais de ITENS_POR_IMAGEM atualizações, gera múltiplas
        imagens — cada uma vira uma mensagem separada depois.

        Retorna pares (arquivo, cor) — a cor é usada para colorir a borda do
        embed que envolve a imagem (ver _enviar_relatorio).
        """
        combinados = []
        for item in dados.get("anime", []):
            combinados.append(item)
        for item in dados.get("manga", []):
            combinados.append(item)
        combinados.sort(key=lambda x: x.get("updated_at", ""))

        if not combinados:
            return []

        avatar_bytes = await self._obter_avatar_bytes(usuario)

        arquivos: list[tuple[discord.File, tuple[int, int, int]]] = []
        lotes = [combinados[i:i + ITENS_POR_IMAGEM] for i in range(0, len(combinados), ITENS_POR_IMAGEM)]

        for indice, lote in enumerate(lotes, start=1):
            itens_visuais = [await self._montar_item_visual(item) for item in lote]

            tem_manga = any(i["tipo"] == "manga" for i in lote)
            tem_anime = any(i["tipo"] == "anime" for i in lote)
            if tem_anime and tem_manga:
                cor = COR_ANIME
                subtitulo = "MyAnimeList · Anime & Mangá"
            elif tem_manga:
                cor = COR_MANGA
                subtitulo = "MyAnimeList · Mangá"
            else:
                cor = COR_ANIME
                subtitulo = "MyAnimeList · Anime"

            if len(lotes) > 1:
                subtitulo += f" ({indice}/{len(lotes)})"

            png_bytes = gerar_imagem_lote(usuario, avatar_bytes, itens_visuais, cor, subtitulo)
            buffer = BytesIO(png_bytes)
            buffer.seek(0)
            sufixo = f"_{indice}" if len(lotes) > 1 else ""
            arquivos.append((discord.File(buffer, filename=f"mal_{usuario}{sufixo}.png"), cor))

        return arquivos

    async def _enviar_relatorio(self, canal: discord.TextChannel, mudancas: dict) -> int:
        """
        Envia o relatório: um card de cabeçalho (no mesmo estilo visual dos cards
        de atualização) seguido de uma mensagem por lote de até ITENS_POR_IMAGEM
        atualizações de cada usuário, como anexo de imagem direto (sem embed).
        """
        if not mudancas:
            return 0

        agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
        total_itens = sum(len(v["anime"]) + len(v["manga"]) for v in mudancas.values())

        cabecalho_png = gerar_imagem_cabecalho(total_itens, len(mudancas), agora)
        buffer = BytesIO(cabecalho_png)
        buffer.seek(0)
        await canal.send(file=discord.File(buffer, filename="mal_cabecalho.png"))

        for usuario, dados in mudancas.items():
            try:
                arquivos = await self._gerar_arquivos_usuario(usuario, dados)
            except Exception as e:
                print(f"[MAL Tracker] ❌ Erro ao gerar imagens de '{usuario}': {e}")
                traceback.print_exc()
                continue

            for arquivo, _cor in arquivos:
                try:
                    await canal.send(file=arquivo)
                except Exception as e:
                    print(f"[MAL Tracker] ❌ Erro ao enviar imagem '{arquivo.filename}' de '{usuario}' no canal #{canal}: {e}")
                    traceback.print_exc()
                await asyncio.sleep(0.3)

        return total_itens

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
        #
        # IMPORTANTE: cada usuário é processado dentro de seu próprio try/except.
        # Sem isso, um erro num único usuário (API do MAL fora do ar, resposta
        # malformada, timeout, etc.) derrubaria o loop inteiro — o discord.py
        # NÃO reagenda um tasks.loop automaticamente depois de uma exceção não
        # tratada, então ISSO faria o bot parar de mandar relatório pra TODO
        # MUNDO, silenciosamente, até reiniciar. É o suspeito nº 1 de usuários
        # "sumindo" do relatório sem nenhum erro visível.
        todos_usuarios = carregar_todos_usuarios()
        self._cache_mudancas_ciclo = {}
        self._cache_eventos_easter_eggs = {}
        for username in todos_usuarios:
            try:
                mudancas, eventos = await self._detectar_mudancas_usuario(username)
                self._cache_mudancas_ciclo[username] = mudancas
                self._cache_eventos_easter_eggs[username] = eventos
            except Exception as e:
                print(f"[MAL Tracker] ❌ Erro ao detectar mudanças de '{username}' — usuário pulado neste ciclo: {e}")
                traceback.print_exc()
                self._cache_mudancas_ciclo[username] = {}
                self._cache_eventos_easter_eggs[username] = []

            # Favoritos NÃO são mais sincronizados aqui — isso rodava a cada
            # ciclo horário pra TODOS os usuários (o cooldown individual só
            # era gravado em caso de sucesso, então uma instabilidade no
            # Jikan fazia o bot tentar de novo pra todo mundo hora a hora).
            # Ver favoritos_semanal_loop, que roda em lote, uma vez por semana.

            await asyncio.sleep(0.3)  # pausa de cortesia pro rate limit da API do MAL

        # ── Fase 2: relatório — cada guild filtra e envia ────────────────────
        async def processar_guild(guild):
            try:
                canal_id = get_config(guild.id, "canal_mal")
                if not canal_id:
                    print(f"[MAL Tracker] Guild '{guild.name}': canal_mal não configurado, pulando.")
                    return

                canal = self.bot.get_channel(canal_id)
                if canal is None:
                    print(f"[MAL Tracker] ⚠️  Canal {canal_id} não encontrado na guild {guild.name}.")
                    return

                mudancas = await self._coletar_mudancas(guild.id)
                if mudancas:
                    await self._enviar_relatorio(canal, mudancas)
                    total_anime = sum(len(v["anime"]) for v in mudancas.values())
                    total_manga = sum(len(v["manga"]) for v in mudancas.values())
                    print(f"[MAL Tracker] ✅ {guild.name}: {total_anime} anime, {total_manga} mangá — {len(mudancas)} usuário(s).")
                else:
                    print(f"[MAL Tracker] Guild '{guild.name}': nenhuma atualização.")

                # Easter eggs (ver cogs/mal_easter_eggs.py) — disparam
                # independente dos filtros de status da guild, em todo
                # servidor onde o usuário estiver monitorado.
                for usuario in carregar_usuarios(guild.id):
                    for evento in self._cache_eventos_easter_eggs.get(usuario, []):
                        await self._easter_eggs.enviar(canal, evento)
            except Exception as e:
                print(f"[MAL Tracker] ❌ Erro ao processar guild '{guild.name}': {e}")
                traceback.print_exc()

        await asyncio.gather(*[processar_guild(guild) for guild in self.bot.guilds])

    @monitorar_loop.error
    async def monitorar_loop_error(self, error: Exception):
        """
        Rede de segurança final: se qualquer exceção ainda assim escapar de todos
        os try/except acima e chegar aqui, o discord.py PARA o tasks.loop
        silenciosamente (sem reagendar sozinho). Logamos bem alto pra isso nunca
        passar despercebido, e reiniciamos o loop manualmente pra não deixar o
        monitoramento morto até o próximo restart do bot.
        """
        print(f"[MAL Tracker] 🚨 ERRO FATAL no monitorar_loop — o loop seria interrompido: {error}")
        traceback.print_exc()
        if not self.monitorar_loop.is_running():
            print("[MAL Tracker] 🔁 Reiniciando monitorar_loop automaticamente após o erro fatal.")
            self.monitorar_loop.restart()

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
    #  Loop semanal — favoritos (Jikan)
    # ──────────────────────────────────────────
    #
    # Roda em LOTE (todos os usuários de uma vez), separado do monitorar_loop
    # horário. O controle de "quando foi a última rodada" é de nível de lote
    # (marcar_inicio_lote_favoritos/carregar_ultimo_lote_favoritos), não por
    # usuário — isso é o que garante a cadência semanal mesmo se o Jikan
    # estiver instável e boa parte dos usuários falhar na rodada: como a
    # marca é gravada no INÍCIO da rodada (não só em caso de sucesso), a
    # próxima tentativa só acontece na semana seguinte, em vez de a cada
    # checagem horária como acontecia antes.
    #
    # Roda de hora em hora só pra CHECAR se já passou uma semana — o
    # trabalho pesado (uma chamada ao Jikan por usuário) só acontece quando
    # a rodada realmente é disparada.
    @tasks.loop(hours=1)
    async def favoritos_semanal_loop(self):
        await self.bot.wait_until_ready()

        ultimo_lote = carregar_ultimo_lote_favoritos()
        agora = datetime.now(timezone.utc)
        if ultimo_lote is not None and (agora - ultimo_lote) < COOLDOWN_FAVORITOS:
            return

        marcar_inicio_lote_favoritos(agora)
        todos_usuarios = carregar_todos_usuarios()
        print(f"[MAL Tracker] 🌟 Iniciando sincronização SEMANAL de favoritos ({len(todos_usuarios)} usuário(s))...")

        sucesso = 0
        falhas = 0
        for username in todos_usuarios:
            try:
                # forcar=True: o cooldown de 7 dias já foi resolvido no nível
                # do lote acima: se a rodada disparou, todo mundo é sincronizado.
                antes = carregar_ultima_sync_favoritos(username)
                await self._sincronizar_favoritos_usuario(username, forcar=True)
                depois = carregar_ultima_sync_favoritos(username)
                if depois != antes:
                    sucesso += 1
                else:
                    falhas += 1
            except Exception as e:
                falhas += 1
                print(f"[MAL Tracker] ❌ Erro ao sincronizar favoritos de '{username}': {e}")
                traceback.print_exc()
            # Ver PAUSA_JIKAN: 0.5s aqui equivalia a 120 req/min, o dobro do
            # limite de 60/min do Jikan — provável causa de instabilidade
            # persistente conforme a base de usuários cresce.
            await asyncio.sleep(PAUSA_JIKAN)

        print(
            f"[MAL Tracker] ✅ Sincronização semanal de favoritos concluída "
            f"({sucesso}/{len(todos_usuarios)} sem erro, {falhas} falha(s) — ver log acima)."
        )

    @favoritos_semanal_loop.before_loop
    async def antes_do_loop_favoritos(self):
        await self.bot.wait_until_ready()

    @favoritos_semanal_loop.error
    async def favoritos_semanal_loop_error(self, error: Exception):
        print(f"[MAL Tracker] 🚨 ERRO FATAL no favoritos_semanal_loop: {error}")
        traceback.print_exc()
        if not self.favoritos_semanal_loop.is_running():
            print("[MAL Tracker] 🔁 Reiniciando favoritos_semanal_loop após erro fatal.")
            self.favoritos_semanal_loop.restart()

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
            # Usuário já sincronizado — atualiza favoritos imediatamente (ignora cooldown)
            asyncio.create_task(self._sincronizar_favoritos_usuario(username, forcar=True))
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

    @app_commands.command(
        name="mal_avatar",
        description="Define manualmente o avatar de um usuário monitorado, enviando uma imagem."
    )
    @app_commands.describe(
        username="Nome de usuário do MAL (precisa já estar na lista de monitorados)",
        imagem="Imagem para usar como avatar (png, jpg, webp...)"
    )
    async def mal_avatar(
        self,
        interaction: discord.Interaction,
        username: str,
        imagem: discord.Attachment,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        usuarios = carregar_usuarios(guild_id)

        # Resolve para a grafia exata já cadastrada (case-insensitive),
        # pra usar a mesma chave que o resto do código usa em mal_avatares.
        username_real = next((u for u in usuarios if u.lower() == username.lower()), None)
        if username_real is None:
            await interaction.followup.send(
                f"⚠️ `{username}` não está na lista de monitorados desta guild. "
                f"Use `/mal_entrar` primeiro, ou confira o nome com `/mal_lista`.",
                ephemeral=True
            )
            return

        if not (imagem.content_type or "").startswith("image/"):
            await interaction.followup.send(
                "❌ O arquivo enviado não parece ser uma imagem.", ephemeral=True
            )
            return

        if imagem.size > 8 * 1024 * 1024:
            await interaction.followup.send(
                "❌ Imagem muito grande (máximo 8 MB).", ephemeral=True
            )
            return

        try:
            img_bytes = await imagem.read()
            icon_comprimido = recortar_e_comprimir_avatar(img_bytes, username_real)
        except Exception as e:
            print(f"[MAL Tracker] ❌ Erro ao processar imagem enviada para '{username_real}': {e}")
            await interaction.followup.send(
                "❌ Não consegui processar essa imagem. Tente outro arquivo (png, jpg ou webp).",
                ephemeral=True
            )
            return

        salvar_avatar(username_real, icon_comprimido)

        preview_buffer = BytesIO(icon_comprimido)
        preview_buffer.seek(0)
        arquivo_preview = discord.File(preview_buffer, filename=f"avatar_{username_real}.jpg")

        embed = discord.Embed(
            title="🖼️ Avatar atualizado",
            description=f"Avatar de `{username_real}` definido manualmente por {interaction.user.mention}.",
            color=0x2E51A2
        )
        embed.set_thumbnail(url=f"attachment://avatar_{username_real}.jpg")

        await interaction.followup.send(embed=embed, file=arquivo_preview, ephemeral=True)
        print(f"[MAL Tracker] Avatar de '{username_real}' definido manualmente por {interaction.user} na guild {guild_id}.")

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

        total = await self._enviar_relatorio(canal, mudancas)
        await interaction.followup.send(
            f"✅ Relatório enviado com **{total}** atualização(ões).", ephemeral=True
        )

    @app_commands.command(name="mal_forcar_favoritos", description="[Admin] Força a verificação de favoritos (ignora o cooldown semanal).")
    @app_commands.describe(username="Apenas este usuário (opcional). Se omitido, força todos os monitorados nesta guild.")
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_forcar_favoritos(self, interaction: discord.Interaction, username: str | None = None):
        await interaction.response.defer(ephemeral=True)

        if username:
            usuarios = carregar_usuarios(interaction.guild_id)
            if username.lower() not in [u.lower() for u in usuarios]:
                await interaction.followup.send(f"⚠️ `{username}` não está na lista desta guild.", ephemeral=True)
                return
            alvos = [username]
        else:
            alvos = carregar_usuarios(interaction.guild_id)
            if not alvos:
                await interaction.followup.send("📋 Nenhum usuário monitorado nesta guild.", ephemeral=True)
                return

        falhas = []
        for u in alvos:
            antes = carregar_ultima_sync_favoritos(u)
            await self._sincronizar_favoritos_usuario(u, forcar=True)
            depois = carregar_ultima_sync_favoritos(u)
            if depois == antes:
                falhas.append(u)
            # Ver PAUSA_JIKAN — mesma lógica do loop semanal: 0.5s excedia
            # o limite de 60 req/min do Jikan.
            await asyncio.sleep(PAUSA_JIKAN)

        if len(alvos) == 1:
            if falhas:
                await interaction.followup.send(
                    f"❌ Não consegui atualizar os favoritos de `{alvos[0]}` — o Jikan seguiu instável mesmo "
                    f"após {JIKAN_MAX_TENTATIVAS} tentativas. Veja o console para detalhes e tente de novo mais tarde.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(f"✅ Favoritos de `{alvos[0]}` atualizados.", ephemeral=True)
        else:
            sucesso = len(alvos) - len(falhas)
            msg = f"✅ Favoritos de **{sucesso}/{len(alvos)}** usuário(s) atualizados."
            if falhas:
                msg += f"\n⚠️ Falharam (Jikan instável): {', '.join(f'`{u}`' for u in falhas)}"
            await interaction.followup.send(msg, ephemeral=True)

        print(f"[MAL Tracker] {interaction.user} forçou sync de favoritos: {', '.join(alvos)}.")

    @app_commands.command(
        name="mal_sincronizar_nsfw",
        description="[Admin] Preenche no banco itens NSFW que a API omitia antes da correção (rodar uma vez)."
    )
    @app_commands.describe(
        username="Apenas este usuário (opcional). Se omitido, roda para todos os monitorados nesta guild."
    )
    @app_commands.checks.has_permissions()
    async def mal_sincronizar_nsfw(self, interaction: discord.Interaction, username: str | None = None):
        await interaction.response.defer(ephemeral=True)

        if username:
            usuarios_alvo = carregar_usuarios(interaction.guild_id)
            username_real = next((u for u in usuarios_alvo if u.lower() == username.lower()), None)
            if username_real is None:
                await interaction.followup.send(f"⚠️ `{username}` não está na lista desta guild.", ephemeral=True)
                return
            alvos = [username_real]
        else:
            alvos = carregar_usuarios(interaction.guild_id)
            if not alvos:
                await interaction.followup.send("📋 Nenhum usuário monitorado nesta guild.", ephemeral=True)
                return

        await interaction.followup.send(
            f"🔄 Iniciando backfill NSFW em segundo plano para **{len(alvos)}** usuário(s). "
            f"Cada usuário pode levar de minutos a horas, dependendo do tamanho da lista "
            f"(paginação respeita o rate limit do MAL). Acompanhe pelo console/log.",
            ephemeral=True
        )

        async def rodar_todos():
            for u in alvos:
                novos_anime, novos_manga = await self._backfill_nsfw_usuario(u)
                print(f"[MAL Tracker] Backfill NSFW de '{u}' concluído: {novos_anime} anime, {novos_manga} mangá.")

        asyncio.create_task(rodar_todos())
        print(f"[MAL Tracker] {interaction.user} disparou backfill NSFW para: {', '.join(alvos)}.")

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