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
LIMITE_ITENS: int = 100

# ══════════════════════════════════════════════

USUARIOS_FILE   = "mal_usuarios.json"  # usado apenas na migração inicial
FILTROS_FILE    = "mal_filtros.json"   # usado apenas na migração inicial
MAL_API_BASE    = "https://api.myanimelist.net/v2"
BRASILIA        = timezone(timedelta(hours=-3))

# Todos os status possíveis para anime e mangá
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

# Rótulos legíveis para exibir nos comandos
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
#  Persistência no banco (por servidor)
# ──────────────────────────────────────────────

def criar_tabelas_mal():
    """Cria as tabelas do MAL Tracker se não existirem."""
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS mal_usuarios (
            guild_id INTEGER NOT NULL,
            username TEXT    NOT NULL,
            PRIMARY KEY (guild_id, username)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS mal_filtros (
            guild_id INTEGER NOT NULL,
            tipo     TEXT    NOT NULL,
            status   TEXT    NOT NULL,
            PRIMARY KEY (guild_id, tipo, status)
        )
    """)
    conn.commit()
    conn.close()


def migrar_json_para_banco():
    """
    Migração única: importa mal_usuarios.json e mal_filtros.json
    para o banco usando guild_id = 0 como placeholder.
    Apaga os arquivos após a migração bem-sucedida.
    """
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()

    if os.path.exists(USUARIOS_FILE):
        with open(USUARIOS_FILE, "r") as f:
            usuarios = json.load(f)
        for u in usuarios:
            c.execute(
                "INSERT OR IGNORE INTO mal_usuarios (guild_id, username) VALUES (?, ?)",
                (0, u)
            )
        conn.commit()
        os.rename(USUARIOS_FILE, USUARIOS_FILE + ".migrado")
        print(f"[MAL Tracker] {len(usuarios)} usuário(s) migrados do JSON para o banco (guild_id=0).")

    if os.path.exists(FILTROS_FILE):
        with open(FILTROS_FILE, "r") as f:
            filtros = json.load(f)
        for tipo, statuses in filtros.items():
            for status in statuses:
                c.execute(
                    "INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, ?, ?)",
                    (0, tipo, status)
                )
        conn.commit()
        os.rename(FILTROS_FILE, FILTROS_FILE + ".migrado")
        print("[MAL Tracker] Filtros migrados do JSON para o banco (guild_id=0).")

    conn.close()


def carregar_usuarios(guild_id: int) -> list[str]:
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()
    c.execute("SELECT username FROM mal_usuarios WHERE guild_id = ?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def salvar_usuario(guild_id: int, username: str):
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO mal_usuarios (guild_id, username) VALUES (?, ?)",
        (guild_id, username)
    )
    conn.commit()
    conn.close()


def remover_usuario(guild_id: int, username: str):
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()
    c.execute(
        "DELETE FROM mal_usuarios WHERE guild_id = ? AND username = ?",
        (guild_id, username)
    )
    conn.commit()
    conn.close()


def carregar_filtros(guild_id: int) -> dict[str, list[str]]:
    """
    Retorna { 'anime': [...status ativos...], 'manga': [...status ativos...] }.
    Se o servidor não tiver filtros configurados ainda, retorna todos habilitados.
    """
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()
    c.execute(
        "SELECT tipo, status FROM mal_filtros WHERE guild_id = ?",
        (guild_id,)
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        return {"anime": list(TODOS_STATUS_ANIME), "manga": list(TODOS_STATUS_MANGA)}

    resultado: dict[str, list[str]] = {"anime": [], "manga": []}
    for tipo, status in rows:
        if tipo in resultado:
            resultado[tipo].append(status)
    return resultado


def toggle_filtro(guild_id: int, tipo: str, status: str) -> bool:
    """
    Alterna um status de filtro. Retorna True se habilitado, False se desabilitado.
    Na primeira vez que o servidor usa filtros, inicializa todos como ativos.
    """
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()

    # Verifica se o servidor já tem algum filtro configurado
    c.execute("SELECT COUNT(*) FROM mal_filtros WHERE guild_id = ?", (guild_id,))
    total = c.fetchone()[0]

    if total == 0:
        # Primeira vez: inicializa todos os status como ativos
        for s in TODOS_STATUS_ANIME:
            c.execute("INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, 'anime', ?)", (guild_id, s))
        for s in TODOS_STATUS_MANGA:
            c.execute("INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, 'manga', ?)", (guild_id, s))
        conn.commit()

    # Verifica se o status está ativo
    c.execute(
        "SELECT 1 FROM mal_filtros WHERE guild_id = ? AND tipo = ? AND status = ?",
        (guild_id, tipo, status)
    )
    ativo = c.fetchone() is not None

    if ativo:
        c.execute(
            "DELETE FROM mal_filtros WHERE guild_id = ? AND tipo = ? AND status = ?",
            (guild_id, tipo, status)
        )
        habilitado = False
    else:
        c.execute(
            "INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, ?, ?)",
            (guild_id, tipo, status)
        )
        habilitado = True

    conn.commit()
    conn.close()
    return habilitado


# ──────────────────────────────────────────────
#  Utilitário de imagem
# ──────────────────────────────────────────────

def recortar_avatar(img_bytes: bytes, username: str) -> discord.File:
    """
    Recorta a imagem em quadrado focado no topo (rosto),
    redimensiona para 128x128 e retorna como discord.File.
    """
    img  = Image.open(BytesIO(img_bytes)).convert("RGBA")
    w, h = img.size
    lado = min(w, h)
    left = (w - lado) // 2
    top  = 0
    img  = img.crop((left, top, left + lado, top + lado))
    img  = img.resize((128, 128), Image.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=f"avatar_{username}.png")


class MalTrackerCog(commands.Cog):
    """Cog que monitora listas de anime e mangá do MyAnimeList e envia relatórios horários."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._estado_anime: dict[str, dict[str, str]] = {}
        self._estado_manga: dict[str, dict[str, str]] = {}
        self._session: aiohttp.ClientSession | None = None
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
    #  Buscas na API
    # ──────────────────────────────────────────

    async def _buscar_animelist(self, username: str) -> list[dict] | None:
        session = await self._get_session()
        url = f"{MAL_API_BASE}/users/{username}/animelist"
        params = {"fields": "list_status", "sort": "list_updated_at", "limit": LIMITE_ITENS}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("data", [])
                elif resp.status == 403:
                    print(f"[MAL Tracker] ⚠️  Animelist de '{username}' é privada ou não encontrada.")
                else:
                    print(f"[MAL Tracker] Erro {resp.status} ao buscar animelist de '{username}'.")
        except asyncio.TimeoutError:
            print(f"[MAL Tracker] Timeout ao buscar animelist de '{username}'.")
        except Exception as e:
            print(f"[MAL Tracker] Exceção ao buscar animelist de '{username}': {e}")
        return None

    async def _buscar_mangalist(self, username: str) -> list[dict] | None:
        session = await self._get_session()
        url = f"{MAL_API_BASE}/users/{username}/mangalist"
        params = {"fields": "list_status", "sort": "list_updated_at", "limit": LIMITE_ITENS}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("data", [])
                elif resp.status == 403:
                    print(f"[MAL Tracker] ⚠️  Mangalist de '{username}' é privada ou não encontrada.")
                else:
                    print(f"[MAL Tracker] Erro {resp.status} ao buscar mangalist de '{username}'.")
        except asyncio.TimeoutError:
            print(f"[MAL Tracker] Timeout ao buscar mangalist de '{username}'.")
        except Exception as e:
            print(f"[MAL Tracker] Exceção ao buscar mangalist de '{username}': {e}")
        return None

    async def _buscar_avatar(self, username: str) -> discord.File | None:
        """Baixa avatar via Jikan e retorna como discord.File recortado."""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://api.jikan.moe/v4/users/{username}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
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

            return recortar_avatar(img_bytes, username)

        except Exception as e:
            print(f"[MAL Tracker] Erro ao processar avatar de '{username}': {e}")
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

    def _processar_itens(self, itens: list[dict], estado: dict[str, str], tipo: str, guild_id: int) -> list[dict]:
        novas     = []
        agora_utc = datetime.now(timezone.utc)
        filtros   = carregar_filtros(guild_id)
        status_permitidos = filtros.get(tipo, [])

        for item in itens:
            item_id    = str(item["node"]["id"])
            titulo     = item["node"]["title"]
            status     = item["list_status"]
            snap_atual = self._snapshot(status, tipo)
            updated_at = status.get("updated_at", "")
            status_val = status.get("status", "")

            snap_anterior = estado.get(item_id)

            if snap_anterior is None:
                estado[item_id] = snap_atual
                if updated_at and status_val in status_permitidos:
                    try:
                        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if (agora_utc - dt).total_seconds() <= 3600:
                            novas.append(self._montar_entrada(titulo, item_id, status, updated_at, tipo))
                    except Exception:
                        pass
                continue

            if snap_anterior != snap_atual:
                estado[item_id] = snap_atual
                # Só reporta se o status atual está na lista de permitidos
                if status_val in status_permitidos:
                    novas.append(self._montar_entrada(titulo, item_id, status, updated_at, tipo))

        return novas

    def _montar_entrada(self, titulo: str, item_id: str, status: dict, updated_at: str, tipo: str) -> dict:
        entrada = {
            "titulo":     titulo,
            "item_id":    item_id,
            "status":     status.get("status", ""),
            "score":      status.get("score", 0),
            "updated_at": updated_at,
            "tipo":       tipo,
        }
        if tipo == "anime":
            entrada["episodios"]  = status.get("num_episodes_watched", 0)
            entrada["rewatching"] = status.get("is_rewatching", False)
        else:
            entrada["capitulos"] = status.get("num_chapters_read", 0)
            entrada["volumes"]   = status.get("num_volumes_read", 0)
            entrada["rereading"] = status.get("is_rereading", False)
        return entrada

    async def _coletar_mudancas(self, guild_id: int) -> dict[str, dict[str, list[dict]]]:
        resultado: dict[str, dict[str, list[dict]]] = {}

        for usuario in carregar_usuarios(guild_id):
            estado_anime = self._estado_anime.setdefault((guild_id, usuario), {})
            estado_manga = self._estado_manga.setdefault((guild_id, usuario), {})

            itens_anime = await self._buscar_animelist(usuario)
            itens_manga = await self._buscar_mangalist(usuario)

            novas_anime = self._processar_itens(itens_anime or [], estado_anime, "anime", guild_id)
            novas_manga = self._processar_itens(itens_manga or [], estado_manga, "manga", guild_id)

            novas_anime.sort(key=lambda x: x["updated_at"])
            novas_manga.sort(key=lambda x: x["updated_at"])

            if novas_anime or novas_manga:
                resultado[usuario] = {"anime": novas_anime, "manga": novas_manga}

        return resultado

    # ──────────────────────────────────────────
    #  Formatação do relatório
    # ──────────────────────────────────────────

    def _formatar_linhas_anime(self, itens: list[dict]) -> list[str]:
        linhas = []
        for item in itens:
            emoji, label = STATUS_ANIME.get(item["status"], ("📝", item["status"]))
            url   = f"https://myanimelist.net/anime/{item['item_id']}"
            linha = f"{emoji} **[{item['titulo']}]({url})**"
            detalhes: list[str] = [label]

            if item["status"] == "watching" or item.get("rewatching"):
                ep = item.get("episodios", 0)
                detalhes.append(f"Reassistindo • EP {ep}" if item.get("rewatching") else f"EP {ep}")

            if item["status"] == "completed" and item["score"] > 0:
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
            emoji, label = STATUS_MANGA.get(item["status"], ("📝", item["status"]))
            url   = f"https://myanimelist.net/manga/{item['item_id']}"
            linha = f"{emoji} **[{item['titulo']}]({url})**"
            detalhes: list[str] = [label]

            if item["status"] == "reading" or item.get("rereading"):
                cap = item.get("capitulos", 0)
                detalhes.append(f"Relendo • Cap {cap}" if item.get("rereading") else f"Cap {cap}")

            if item["status"] == "completed" and item["score"] > 0:
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
                f"attachment://avatar_{usuario}.png"
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

        lote_embeds: list[discord.Embed] = []
        lote_files: list[discord.File]   = []

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

        for guild in self.bot.guilds:
            canal_id = get_config(guild.id, "canal_mal")
            if not canal_id:
                continue

            mudancas = await self._coletar_mudancas(guild.id)
            if not mudancas:
                continue

            canal = self.bot.get_channel(canal_id)
            if canal is None:
                print(f"[MAL Tracker] ⚠️  Canal {canal_id} não encontrado na guild {guild.name}.")
                continue

            await self._enviar_relatorio(canal, mudancas)
            total_anime = sum(len(v["anime"]) for v in mudancas.values())
            total_manga = sum(len(v["manga"]) for v in mudancas.values())
            print(f"[MAL Tracker] ✅ Relatório enviado ({guild.name}): {total_anime} anime, {total_manga} mangá — {len(mudancas)} usuário(s).")

    @monitorar_loop.before_loop
    async def antes_do_loop(self):
        await self.bot.wait_until_ready()
        print("[MAL Tracker] Carregando estado inicial...")
        for guild in self.bot.guilds:
            await self._coletar_mudancas(guild.id)
        print("[MAL Tracker] Estado inicial carregado. Primeiro relatório em 1 hora.")

    # ──────────────────────────────────────────
    #  Comandos — auto-cadastro
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

        itens = await self._buscar_animelist(username)
        if itens is None:
            await interaction.followup.send(
                f"❌ Não consegui acessar a lista de `{username}`. Verifique se o nome está correto e se a lista é pública.",
                ephemeral=True
            )
            return

        salvar_usuario(guild_id, username)
        await interaction.followup.send(
            f"✅ `{username}` adicionado ao monitoramento! Será incluído no próximo relatório.", ephemeral=True
        )
        print(f"[MAL Tracker] Usuário '{username}' adicionado por {interaction.user} na guild {guild_id}.")

    @app_commands.command(name="mal_sair", description="Remove um usuário do MAL do monitoramento.")
    @app_commands.describe(username="Nome de usuário no MyAnimeList a remover")
    async def mal_sair(self, interaction: discord.Interaction, username: str):
        guild_id = interaction.guild_id
        usuarios = carregar_usuarios(guild_id)

        if username.lower() not in [u.lower() for u in usuarios]:
            await interaction.response.send_message(f"⚠️ `{username}` não está na lista.", ephemeral=True)
            return

        remover_usuario(guild_id, username)
        self._estado_anime.pop((guild_id, username), None)
        self._estado_manga.pop((guild_id, username), None)

        await interaction.response.send_message(f"✅ `{username}` removido do monitoramento.", ephemeral=True)
        print(f"[MAL Tracker] Usuário '{username}' removido por {interaction.user} na guild {guild_id}.")

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
    #  Comandos — filtros de status (admin)
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_filtros",
        description="[Admin] Exibe os status atualmente ativos no monitoramento."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_filtros(self, interaction: discord.Interaction):
        filtros = carregar_filtros(interaction.guild_id)

        def listar(ativos: list[str], rotulos: dict) -> str:
            linhas = []
            for s, label in rotulos.items():
                icone = "✅" if s in ativos else "❌"
                linhas.append(f"{icone} {label} (`{s}`)")
            return "\n".join(linhas)

        embed = discord.Embed(title="🎛️ Filtros de Status — MAL Tracker", color=0x2E51A2)
        embed.add_field(name="🎬 Anime", value=listar(filtros["anime"], ROTULOS_ANIME), inline=True)
        embed.add_field(name="📚 Mangá", value=listar(filtros["manga"], ROTULOS_MANGA), inline=True)
        embed.set_footer(text="Use /mal_filtro_toggle para habilitar ou desabilitar um status.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="mal_filtro_toggle",
        description="[Admin] Habilita ou desabilita um status no monitoramento."
    )
    @app_commands.describe(
        tipo="Anime ou mangá",
        status="Status a alternar"
    )
    @app_commands.choices(
        tipo=[
            app_commands.Choice(name="Anime", value="anime"),
            app_commands.Choice(name="Mangá", value="manga"),
        ],
        status=[
            app_commands.Choice(name="Assistindo / Lendo",       value="watching_reading"),
            app_commands.Choice(name="Completou",                value="completed"),
            app_commands.Choice(name="Em pausa",                 value="on_hold"),
            app_commands.Choice(name="Dropou",                   value="dropped"),
            app_commands.Choice(name="Planeja assistir / ler",   value="plan_to"),
        ]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_filtro_toggle(
        self,
        interaction: discord.Interaction,
        tipo: str,
        status: str,
    ):
        # Resolve o valor real do status baseado no tipo escolhido
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
        proxima = self.monitorar_loop.next_iteration
        prox_str = f"<t:{int(proxima.timestamp())}:R>" if proxima else "desconhecido"

        usuarios = carregar_usuarios(interaction.guild_id)
        guild_id = interaction.guild_id
        usuarios_str = (
            "\n".join(
                f"• `{u}` — {len(self._estado_anime.get((guild_id, u), {}))} animes, {len(self._estado_manga.get((guild_id, u), {}))} mangás no cache"
                for u in usuarios
            )
            if usuarios else "nenhum"
        )

        embed = discord.Embed(title="📊 MAL Tracker — Status", color=0x2E51A2)
        embed.add_field(name="Próximo relatório",    value=prox_str,    inline=False)
        embed.add_field(name="Usuários monitorados", value=usuarios_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MalTrackerCog(bot))