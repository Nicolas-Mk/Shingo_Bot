import os
import json
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands

MAL_CLIENT_ID: str = os.getenv("MAL_CLIENT_ID", "")
CANAL_RELATORIO: int = int(os.getenv("CANAL_RELATORIO", "0"))
LIMITE_ITENS: int = 100

USUARIOS_FILE = "mal_usuarios.json"
MAL_API_BASE = "https://api.myanimelist.net/v2"
BRASILIA = timezone(timedelta(hours=-3))

STATUS_LABEL = {
    "watching":      ("▶️",  "Assistindo"),
    "completed":     ("✅",  "Completou"),
    "on_hold":       ("⏸️", "Pausou"),
    "dropped":       ("🗑️", "Dropou"),
    "plan_to_watch": ("📋",  "Planeja assistir"),
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
    """Cog que monitora listas de anime do MyAnimeList e envia relatórios horários."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._estado: dict[str, dict[str, str]] = {}
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
                    print(f"[MAL Tracker] ⚠️  Lista de '{username}' é privada ou não encontrada.")
                else:
                    print(f"[MAL Tracker] Erro {resp.status} ao buscar '{username}'.")
        except asyncio.TimeoutError:
            print(f"[MAL Tracker] Timeout ao buscar '{username}'.")
        except Exception as e:
            print(f"[MAL Tracker] Exceção ao buscar '{username}': {e}")
        return None

    async def _buscar_avatar(self, username: str) -> str | None:
        """Retorna a URL do avatar do usuário no MAL."""
        session = await self._get_session()
        url = f"{MAL_API_BASE}/users/{username}?fields=picture"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("picture")
        except Exception:
            pass
        return None

    def _snapshot(self, status: dict) -> str:
        """Serializa o status relevante do item para comparação."""
        return json.dumps({
            "status":               status.get("status"),
            "num_episodes_watched": status.get("num_episodes_watched"),
            "score":                status.get("score"),
            "is_rewatching":        status.get("is_rewatching"),
        }, sort_keys=True)

    async def _coletar_mudancas(self) -> dict[str, list[dict]]:
        """
        Para cada usuário, compara o estado atual com o anterior.
        Retorna { username: [ lista de mudanças ] } ordenada por updated_at.
        """
        mudancas: dict[str, list[dict]] = {}

        for usuario in carregar_usuarios():
            itens = await self._buscar_animelist(usuario)
            if itens is None:
                continue

            estado_usuario = self._estado.setdefault(usuario, {})
            novas = []

            for item in itens:
                anime_id   = str(item["node"]["id"])
                titulo     = item["node"]["title"]
                status     = item["list_status"]
                snap_atual = self._snapshot(status)
                updated_at = status.get("updated_at", "")

                snap_anterior = estado_usuario.get(anime_id)

                if snap_anterior is None:
                    estado_usuario[anime_id] = snap_atual
                    if updated_at:
                        try:
                            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                            agora_utc = datetime.now(timezone.utc)
                            diferenca = (agora_utc - dt).total_seconds()
                            if diferenca <= 3600:
                                novas.append({
                                    "titulo":     titulo,
                                    "anime_id":   anime_id,
                                    "status":     status.get("status", ""),
                                    "episodios":  status.get("num_episodes_watched", 0),
                                    "score":      status.get("score", 0),
                                    "rewatching": status.get("is_rewatching", False),
                                    "updated_at": updated_at,
                                })
                        except Exception:
                            pass
                    continue

                if snap_anterior != snap_atual:
                    estado_usuario[anime_id] = snap_atual
                    novas.append({
                        "titulo":     titulo,
                        "anime_id":   anime_id,
                        "status":     status.get("status", ""),
                        "episodios":  status.get("num_episodes_watched", 0),
                        "score":      status.get("score", 0),
                        "rewatching": status.get("is_rewatching", False),
                        "updated_at": updated_at,
                    })

            if novas:
                novas.sort(key=lambda x: x["updated_at"])
                mudancas[usuario] = novas

        return mudancas

    async def _formatar_relatorio(self, mudancas: dict[str, list[dict]]) -> list[discord.Embed]:
        """Gera uma lista de Embeds com o relatório de atualizações."""
        embeds: list[discord.Embed] = []

        agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")

        header = discord.Embed(
            title="🎌 FISCALIZAÇÃO MYANIMELIST",
            description=f"Atualizações da última hora • {agora}",
            color=0x2E51A2,
        )
        header.set_thumbnail(url="https://image.myanimelist.net/ui/OK6W_koKDTOqqqLDbIoPAiC8a86sHufn_jOI-JGtoCQ")
        embeds.append(header)

        for usuario, itens in mudancas.items():
            linhas: list[str] = []

            for item in itens:
                emoji, label = STATUS_LABEL.get(item["status"], ("📝", item["status"]))

                mal_url = f"https://myanimelist.net/anime/{item['anime_id']}"
                linha = f"{emoji} **[{item['titulo']}]({mal_url})**"

                detalhes: list[str] = [label]

                if item["status"] == "watching" or item["rewatching"]:
                    ep = item["episodios"]
                    if item["rewatching"]:
                        detalhes.append(f"Reassistindo • EP {ep}")
                    else:
                        detalhes.append(f"EP {ep}")

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

            avatar_url = await self._buscar_avatar(usuario)

            embed = discord.Embed(
                description="\n".join(linhas),
                color=0x2E51A2,
            )
            embed.set_author(
                name=f"@{usuario}",
                url=f"https://myanimelist.net/profile/{usuario}",
                icon_url=avatar_url or f"https://api.dicebear.com/8.x/initials/png?seed={usuario}&size=64",
            )
            embed.set_footer(text=f"{len(itens)} atualização{'ões' if len(itens) != 1 else ''}")
            embeds.append(embed)

        return embeds

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

        embeds = await self._formatar_relatorio(mudancas)
        for i in range(0, len(embeds), 10):
            await canal.send(embeds=embeds[i:i + 10])

        total = sum(len(v) for v in mudancas.values())
        print(f"[MAL Tracker] ✅ Relatório enviado: {total} atualização(ões) de {len(mudancas)} usuário(s).")

    @monitorar_loop.before_loop
    async def antes_do_loop(self):
        await self.bot.wait_until_ready()
        print("[MAL Tracker] Carregando estado inicial da animelist...")
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
        self._estado.pop(username, None)

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

        embeds = await self._formatar_relatorio(mudancas)
        for i in range(0, len(embeds), 10):
            await canal.send(embeds=embeds[i:i + 10])

        total = sum(len(v) for v in mudancas.values())
        await interaction.followup.send(
            f"✅ Relatório enviado com **{total}** atualização(ões).", ephemeral=True
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
                f"• `{u}` — {len(self._estado.get(u, {}))} animes no cache"
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