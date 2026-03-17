import os
import json
import asyncio
import aiohttp
from io import BytesIO
from datetime import datetime, timezone, timedelta

from PIL import Image
import discord
from discord.ext import commands, tasks
from discord import app_commands


# ══════════════════════════════════════════════
#  ⚙️  CONFIGURAÇÕES — edite aqui
# ══════════════════════════════════════════════

MAL_CLIENT_ID: str = os.getenv("MAL_CLIENT_ID", "")
CANAL_RELATORIO: int = int(os.getenv("CANAL_RELATORIO", "0"))
LIMITE_ITENS: int = 100

# ══════════════════════════════════════════════

USUARIOS_FILE = "mal_usuarios.json"
MAL_API_BASE = "https://api.myanimelist.net/v2"
BRASILIA = timezone(timedelta(hours=-3))

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


def carregar_usuarios() -> list[str]:
    if not os.path.exists(USUARIOS_FILE):
        return []
    with open(USUARIOS_FILE, "r") as f:
        return json.load(f)


def salvar_usuarios(usuarios: list[str]):
    print(f"[MAL Tracker] Salvando em: {os.path.abspath(USUARIOS_FILE)}")
    with open(USUARIOS_FILE, "w") as f:
        json.dump(usuarios, f, indent=2)


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

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MAL-CLIENT-ID": MAL_CLIENT_ID}
            )
        return self._session

    async def _buscar_animelist(self, username: str) -> list[dict] | None:
        """Retorna os itens mais recentes da animelist de um usuário."""
        session = await self._get_session()
        url = f"{MAL_API_BASE}/users/{username}/animelist"
        params = {
            "fields": "list_status",
            "sort":   "list_updated_at",
            "limit":  LIMITE_ITENS,
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
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
        """Retorna os itens mais recentes da mangalist de um usuário."""
        session = await self._get_session()
        url = f"{MAL_API_BASE}/users/{username}/mangalist"
        params = {
            "fields": "list_status",
            "sort":   "list_updated_at",
            "limit":  LIMITE_ITENS,
        }
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
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
        """Baixa avatar via Jikan, recorta em quadrado central e retorna como discord.File."""
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

                # Baixa a imagem
                async with s.get(img_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    img_bytes = await resp.read()

            img = Image.open(BytesIO(img_bytes)).convert("RGBA")
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

        except Exception as e:
            print(f"[MAL Tracker] Erro ao processar avatar de '{username}': {e}")
            return None

    def _snapshot(self, status: dict, tipo: str) -> str:
        """Serializa o status relevante do item para comparação."""
        base = {
            "status": status.get("status"),
            "score":  status.get("score"),
        }
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
        estado: dict[str, str],
        tipo: str,
    ) -> list[dict]:
        """Detecta mudanças numa lista (anime ou mangá) e retorna as novas entradas."""
        novas = []
        agora_utc = datetime.now(timezone.utc)

        for item in itens:
            item_id    = str(item["node"]["id"])
            titulo     = item["node"]["title"]
            status     = item["list_status"]
            snap_atual = self._snapshot(status, tipo)
            updated_at = status.get("updated_at", "")

            snap_anterior = estado.get(item_id)

            if snap_anterior is None:
                estado[item_id] = snap_atual
                if updated_at:
                    try:
                        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if (agora_utc - dt).total_seconds() <= 3600:
                            novas.append(self._montar_entrada(titulo, item_id, status, updated_at, tipo))
                    except Exception:
                        pass
                continue

            if snap_anterior != snap_atual:
                estado[item_id] = snap_atual
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

    async def _coletar_mudancas(self) -> dict[str, dict[str, list[dict]]]:
        """
        Para cada usuário, compara anime e mangá com o estado anterior.
        Retorna { username: { "anime": [...], "manga": [...] } }
        """
        resultado: dict[str, dict[str, list[dict]]] = {}

        for usuario in carregar_usuarios():
            estado_anime = self._estado_anime.setdefault(usuario, {})
            estado_manga = self._estado_manga.setdefault(usuario, {})

            itens_anime = await self._buscar_animelist(usuario)
            itens_manga = await self._buscar_mangalist(usuario)

            novas_anime = self._processar_itens(itens_anime or [], estado_anime, "anime")
            novas_manga = self._processar_itens(itens_manga or [], estado_manga, "manga")

            novas_anime.sort(key=lambda x: x["updated_at"])
            novas_manga.sort(key=lambda x: x["updated_at"])

            if novas_anime or novas_manga:
                resultado[usuario] = {"anime": novas_anime, "manga": novas_manga}

        return resultado

    def _formatar_linhas_anime(self, itens: list[dict]) -> list[str]:
        linhas = []
        for item in itens:
            emoji, label = STATUS_ANIME.get(item["status"], ("📝", item["status"]))
            url  = f"https://myanimelist.net/anime/{item['item_id']}"
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
            url  = f"https://myanimelist.net/manga/{item['item_id']}"
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
        """
        Gera lista de tuplas (embed, file) com o relatório de atualizações.
        O file é o avatar recortado, ou None se não disponível.
        """
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
            # Busca avatar com delay para respeitar rate limit do Jikan (3 req/s)
            avatar_file = await self._buscar_avatar(usuario)
            await asyncio.sleep(1)

            if avatar_file:
                icon = f"attachment://avatar_{usuario}.png"
            else:
                icon = f"https://api.dicebear.com/8.x/initials/png?seed={usuario}&size=64"

            novas_anime = dados.get("anime", [])
            novas_manga = dados.get("manga", [])

            # ── Embed de anime ──
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
                # Só anexa o file no primeiro embed do usuário para não duplicar
                resultado.append((embed_anime, avatar_file))
                avatar_file = None  # próximos embeds do mesmo usuário não reenviam o file

            # ── Embed de mangá ──
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
        """Formata e envia o relatório no canal. Retorna total de atualizações."""
        pares = await self._formatar_relatorio(mudancas)

        # Agrupa em lotes de até 10 embeds por mensagem,
        # mas cada embed com file precisa de uma mensagem própria
        lote_embeds: list[discord.Embed] = []
        lote_files: list[discord.File] = []

        async def flush():
            if lote_embeds:
                await canal.send(embeds=lote_embeds, files=lote_files or discord.utils.MISSING)
                lote_embeds.clear()
                lote_files.clear()

        for embed, file in pares:
            if file:
                # Embeds com file vão sozinhos na mensagem
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
        mudancas = await self._coletar_mudancas()

        if not mudancas:
            print("[MAL Tracker] Nenhuma atualização encontrada.")
            return

        canal = self.bot.get_channel(CANAL_RELATORIO)
        if canal is None:
            print(f"[MAL Tracker] ⚠️  Canal {CANAL_RELATORIO} não encontrado.")
            return

        total = await self._enviar_relatorio(canal, mudancas)
        print(f"[MAL Tracker] ✅ Relatório enviado: {total} atualização(ões) de {len(mudancas)} usuário(s).")

    @monitorar_loop.before_loop
    async def antes_do_loop(self):
        await self.bot.wait_until_ready()
        print("[MAL Tracker] Carregando estado inicial da animelist e mangalist...")
        await self._coletar_mudancas()
        print("[MAL Tracker] Estado inicial carregado. Primeiro relatório em 1 hora.")

    # ──────────────────────────────────────────
    #  Comandos — auto-cadastro
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_entrar",
        description="Adiciona seu usuário do MAL ao monitoramento."
    )
    @app_commands.describe(username="Seu nome de usuário no MyAnimeList")
    async def mal_entrar(self, interaction: discord.Interaction, username: str):
        await interaction.response.defer(ephemeral=True)

        usuarios = carregar_usuarios()

        if username.lower() in [u.lower() for u in usuarios]:
            await interaction.followup.send(
                f"⚠️ O usuário `{username}` já está na lista.", ephemeral=True
            )
            return

        itens = await self._buscar_animelist(username)
        if itens is None:
            await interaction.followup.send(
                f"❌ Não consegui acessar a lista de `{username}`. Verifique se o nome está correto e se a lista é pública.",
                ephemeral=True
            )
            return

        usuarios.append(username)
        salvar_usuarios(usuarios)

        await interaction.followup.send(
            f"✅ `{username}` adicionado ao monitoramento! Será incluído no próximo relatório.",
            ephemeral=True
        )
        print(f"[MAL Tracker] Usuário '{username}' adicionado por {interaction.user}.")

    @app_commands.command(
        name="mal_sair",
        description="Remove um usuário do MAL do monitoramento."
    )
    @app_commands.describe(username="Nome de usuário no MyAnimeList a remover")
    async def mal_sair(self, interaction: discord.Interaction, username: str):
        usuarios = carregar_usuarios()

        if username.lower() not in [u.lower() for u in usuarios]:
            await interaction.response.send_message(
                f"⚠️ `{username}` não está na lista.", ephemeral=True
            )
            return

        usuarios = [u for u in usuarios if u.lower() != username.lower()]
        salvar_usuarios(usuarios)
        self._estado_anime.pop(username, None)
        self._estado_manga.pop(username, None)

        await interaction.response.send_message(
            f"✅ `{username}` removido do monitoramento.", ephemeral=True
        )
        print(f"[MAL Tracker] Usuário '{username}' removido por {interaction.user}.")

    @app_commands.command(
        name="mal_lista",
        description="Mostra todos os usuários sendo monitorados."
    )
    async def mal_lista(self, interaction: discord.Interaction):
        usuarios = carregar_usuarios()

        if not usuarios:
            await interaction.response.send_message(
                "📋 Nenhum usuário na lista ainda. Use `/mal_entrar` para se adicionar!",
                ephemeral=True
            )
            return

        linhas = "\n".join(
            f"• [{u}](https://myanimelist.net/profile/{u})" for u in usuarios
        )
        embed = discord.Embed(
            title="📋 Usuários monitorados",
            description=linhas,
            color=0x2E51A2
        )
        embed.set_footer(text=f"{len(usuarios)} usuário(s)")
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────
    #  Comandos — admin
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_relatorio",
        description="[Admin] Força o envio imediato do relatório MAL."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_relatorio(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mudancas = await self._coletar_mudancas()

        if not mudancas:
            await interaction.followup.send(
                "✅ Nenhuma atualização nova desde a última verificação.", ephemeral=True
            )
            return

        canal = self.bot.get_channel(CANAL_RELATORIO)
        if canal is None:
            await interaction.followup.send(
                f"❌ Canal `{CANAL_RELATORIO}` não encontrado.", ephemeral=True
            )
            return

        total = await self._enviar_relatorio(canal, mudancas)
        total_anime = sum(len(v["anime"]) for v in mudancas.values())
        total_manga = sum(len(v["manga"]) for v in mudancas.values())
        await interaction.followup.send(
            f"✅ Relatório enviado com **{total_anime}** anime e **{total_manga}** mangá.", ephemeral=True
        )

    @app_commands.command(
        name="mal_status",
        description="[Admin] Mostra o status atual do monitoramento MAL."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_status(self, interaction: discord.Interaction):
        proxima = self.monitorar_loop.next_iteration
        if proxima:
            ts = int(proxima.timestamp())
            prox_str = f"<t:{ts}:R>"
        else:
            prox_str = "desconhecido"

        usuarios = carregar_usuarios()
        if usuarios:
            usuarios_str = "\n".join(
                f"• `{u}` — {len(self._estado_anime.get(u, {}))} animes, {len(self._estado_manga.get(u, {}))} mangás no cache"
                for u in usuarios
            )
        else:
            usuarios_str = "nenhum"

        embed = discord.Embed(title="📊 MAL Tracker — Status", color=0x2E51A2)
        embed.add_field(name="Próximo relatório", value=prox_str, inline=False)
        embed.add_field(name="Usuários monitorados", value=usuarios_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MalTrackerCog(bot))