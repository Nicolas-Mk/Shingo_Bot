import asyncio
import sqlite3
import traceback
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# AJUSTE o nome do módulo para o arquivo onde está o MalTrackerCog.
from cogs.mal_tracker import carregar_usuarios, MAL_API_BASE, MAL_CLIENT_ID, BRASILIA

# O Jikan público foi desativado (out/2026): os estúdios vêm da API oficial do MAL.
PAUSA_MAL_ESTUDIOS: float = 1.0       # pausa entre animes no sync (segundos)
MAL_MAX_TENTATIVAS: int = 4           # retries por anime em erro transitório
FALHAS_CONSECUTIVAS_MAX: int = 10     # aborta o sync após N falhas SEGUIDAS (API fora, client id inválido...)

# ── Critérios do ranking (fixos — o comando não recebe parâmetros) ──────────
MIN_MEMBROS_ESTUDIO: int = 5     # mínimo de membros DISTINTOS que deram nota a algum anime do estúdio
MIN_ANIMES_ESTUDIO: int = 10     # mínimo de animes DISTINTOS avaliados pelo servidor no estúdio
TOP_ESTUDIOS_SIZE: int = 50      # quantos estúdios compõem o ranking
PAGE_SIZE: int = 10              # estúdios por página

MEDALHAS = {1: "🥇", 2: "🥈", 3: "🥉"}

# ──────────────────────────────────────────────
#  Persistência — estúdios
# ──────────────────────────────────────────────

def carregar_animes_sem_estudio() -> list[str]:
    """item_ids de anime presentes em mal_snapshots que ainda não foram consultados."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT DISTINCT s.item_id
            FROM mal_snapshots s
            LEFT JOIN mal_anime_estudios_sync y ON y.anime_id = s.item_id
            WHERE s.tipo = 'anime' AND y.anime_id IS NULL
            """
        )
        return [r[0] for r in c.fetchall()]


def salvar_estudios_anime(anime_id: str, estudios: list[tuple[int, str]]):
    """Grava os estudios do anime e marca como sincronizado (mesmo se a lista for vazia)."""
    with sqlite3.connect("usuarios.db") as conn:
        conn.executemany(
            "INSERT INTO mal_estudios (id, nome) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET nome = excluded.nome",
            estudios,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO mal_anime_estudios (anime_id, estudio_id) VALUES (?, ?)",
            [(anime_id, est_id) for est_id, _ in estudios],
        )
        conn.execute(
            "INSERT OR REPLACE INTO mal_anime_estudios_sync (anime_id, sync_em) VALUES (?, ?)",
            (anime_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def ranking_estudios(usernames: list[str]) -> list[tuple]:
    """
    Retorna até TOP_ESTUDIOS_SIZE tuplas (nome, media, qtd_animes, qtd_membros, qtd_notas),
    ordenadas pela média (desempate: mais notas primeiro).

    Média = de todas as notas dadas pelos usernames a animes do estúdio (cada nota pesa
    igual). Só conta nota > 0 (no MAL, 0 = sem nota), em qualquer status da lista.

    Um estúdio só entra se tiver, ao mesmo tempo:
      - >= MIN_ANIMES_ESTUDIO animes distintos com nota do servidor
      - >= MIN_MEMBROS_ESTUDIO membros distintos que deram nota a algum anime dele
    """
    if not usernames:
        return []

    ph = ",".join("?" * len(usernames))
    score = "CAST(json_extract(m.snapshot, '$.score') AS REAL)"

    sql = f"""
        SELECT e.nome,
               AVG({score})               AS media,
               COUNT(DISTINCT m.item_id)  AS animes,
               COUNT(DISTINCT m.username) AS membros,
               COUNT(*)                   AS notas
        FROM mal_snapshots m
        JOIN mal_anime_estudios ae ON ae.anime_id = m.item_id
        JOIN mal_estudios e        ON e.id = ae.estudio_id
        WHERE m.tipo = 'anime' AND m.username IN ({ph}) AND {score} > 0
        GROUP BY e.id
        HAVING animes >= ? AND membros >= ?
        ORDER BY media DESC, notas DESC
        LIMIT ?
    """

    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(sql, (*usernames, MIN_ANIMES_ESTUDIO, MIN_MEMBROS_ESTUDIO, TOP_ESTUDIOS_SIZE))
        return c.fetchall()


# ──────────────────────────────────────────────
#  API do MAL — estúdios de um anime
# ──────────────────────────────────────────────

async def buscar_estudios_mal(
    session: aiohttp.ClientSession,
    anime_id: str,
    tentativas: int = MAL_MAX_TENTATIVAS,
) -> list[tuple[int, str]] | None:
    """
    Retorna [(id_estudio, nome), ...] do anime via API oficial do MAL.
    [] se o anime não existe (404) ou não tem estúdio listado.
    None em erro (retries esgotados ou erro não recuperável) — nesse caso o
    anime NÃO é marcado como sincronizado e entra de novo na próxima execução.
    A session precisa ter o header X-MAL-CLIENT-ID.
    """
    url = f"{MAL_API_BASE}/anime/{anime_id}"
    params = {"fields": "studios"}

    for tentativa in range(1, tentativas + 1):
        espera = 2 * tentativa

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [
                        (s["id"], s["name"])
                        for s in data.get("studios", [])
                        if "id" in s and "name" in s
                    ]

                elif resp.status == 404:
                    return []

                elif resp.status == 401:
                    print("[MAL Estúdios] ❌ MAL respondeu 401 — MAL_CLIENT_ID ausente ou inválido.")
                    return None

                elif resp.status in (403, 429) or 500 <= resp.status < 600:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            espera = float(retry_after)
                        except ValueError:
                            pass
                    print(
                        f"[MAL Estúdios] MAL {resp.status} no anime {anime_id} "
                        f"— tentativa {tentativa}/{tentativas}, aguardando {espera:.1f}s."
                    )

                else:
                    print(f"[MAL Estúdios] MAL {resp.status} no anime {anime_id}. Não recuperável, desistindo.")
                    return None

        except asyncio.TimeoutError:
            print(f"[MAL Estúdios] Timeout no anime {anime_id} — tentativa {tentativa}/{tentativas}.")
        except Exception as e:
            print(f"[MAL Estúdios] Erro no anime {anime_id}: {e} — tentativa {tentativa}/{tentativas}.")

        if tentativa < tentativas:
            await asyncio.sleep(espera)

    print(f"[MAL Estúdios] ❌ Desisti do anime {anime_id} após {tentativas} tentativa(s).")
    return None


# ──────────────────────────────────────────────
#  Embed e paginação do ranking
# ──────────────────────────────────────────────

def build_embed_estudios(
    ranking: list[tuple],
    page: int,
    total_pages: int,
    num_usuarios: int,
    pendentes: int,
) -> discord.Embed:
    agora = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M")
    start = page * PAGE_SIZE
    itens = ranking[start:start + PAGE_SIZE]

    linhas = []
    for pos, (nome, media, animes, membros, notas) in enumerate(itens, start=start + 1):
        marca = MEDALHAS.get(pos, f"`#{pos}`")
        linhas.append(
            f"{marca} **{nome}** — **{media:.2f}/10**\n"
            f"　{animes} animes · {membros} membros · {notas} notas"
        )

    embed = discord.Embed(
        title="🏆 Top Estúdios do Servidor — MyAnimeList",
        description=f"Baseado em **{num_usuarios}** usuário(s) · {agora}\n\n" + "\n".join(linhas),
        color=0x2E51A2,
    )
    rodape = (
        f"Página {page + 1}/{total_pages} · mín. {MIN_ANIMES_ESTUDIO} animes e "
        f"{MIN_MEMBROS_ESTUDIO} membros por estúdio"
    )
    if pendentes:
        rodape += f" · {pendentes} anime(s) ainda sem estúdio"
    embed.set_footer(text=rodape)
    return embed


class RankingEstudiosView(discord.ui.View):
    def __init__(self, ranking: list[tuple], num_usuarios: int, pendentes: int, autor_id: int):
        super().__init__(timeout=300)
        self.ranking = ranking
        self.num_usuarios = num_usuarios
        self.pendentes = pendentes
        self.autor_id = autor_id
        self.page = 0
        self.total_pages = (len(ranking) + PAGE_SIZE - 1) // PAGE_SIZE

        # O botão "última página" mostra o total real de páginas: 1 < > N
        self.btn_ultima.label = str(self.total_pages)
        self._atualizar_botoes()

    def _atualizar_botoes(self):
        self.btn_primeira.disabled = self.page == 0
        self.btn_anterior.disabled = self.page == 0
        self.btn_proxima.disabled = self.page >= self.total_pages - 1
        self.btn_ultima.disabled = self.page >= self.total_pages - 1

    def embed_atual(self) -> discord.Embed:
        return build_embed_estudios(
            self.ranking, self.page, self.total_pages, self.num_usuarios, self.pendentes
        )

    async def _checar_autor(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Apenas quem usou o comando pode navegar nas páginas.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="1", style=discord.ButtonStyle.secondary, row=0)
    async def btn_primeira(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = 0
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary, row=0)
    async def btn_anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = max(0, self.page - 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary, row=0)
    async def btn_proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = min(self.total_pages - 1, self.page + 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)

    @discord.ui.button(label="Última", style=discord.ButtonStyle.secondary, row=0)
    async def btn_ultima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._checar_autor(interaction):
            return
        self.page = self.total_pages - 1
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.embed_atual(), view=self)


# ──────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────

class MalEstudiosCog(commands.Cog):
    """Ranking de estúdios pela média das notas dos membros monitorados."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sync_em_andamento: bool = False
        self._task_sync: asyncio.Task | None = None
        self.estudios_loop.start()

    def cog_unload(self):
        self.estudios_loop.cancel()

    # ──────────────────────────────────────────
    #  Sincronização (carga inicial e incremental)
    # ──────────────────────────────────────────

    async def _sincronizar_estudios(self, canal: discord.abc.Messageable | None):
        if self._sync_em_andamento:
            return  # já existe um sync rodando (loop ou comando manual)
        self._sync_em_andamento = True
        ok = falhas = seguidas = 0
        abortou = False
        try:
            pendentes = carregar_animes_sem_estudio()
            total = len(pendentes)
            if not total:
                return  # nada a fazer (o finally libera a flag); silencioso para não poluir o log
            print(f"[MAL Estúdios] 🔄 Sincronizando estúdios de {total} anime(s)...")

            headers = {"X-MAL-CLIENT-ID": MAL_CLIENT_ID}
            async with aiohttp.ClientSession(headers=headers) as session:
                for i, anime_id in enumerate(pendentes, start=1):
                    try:
                        estudios = await buscar_estudios_mal(session, anime_id)
                        if estudios is not None:
                            salvar_estudios_anime(anime_id, estudios)
                    except Exception as e:
                        estudios = None
                        print(f"[MAL Estúdios] ❌ Erro ao processar anime {anime_id}: {e}")
                        traceback.print_exc()

                    if estudios is None:
                        falhas += 1  # não marca; entra de novo na próxima execução
                        seguidas += 1
                        if seguidas >= FALHAS_CONSECUTIVAS_MAX:
                            abortou = True
                            print(
                                f"[MAL Estúdios] ⛔ {seguidas} falhas seguidas — API indisponível ou "
                                f"configuração inválida. Sync interrompido ({ok} salvos)."
                            )
                            break
                    else:
                        seguidas = 0
                        ok += 1

                    if i % 100 == 0:
                        print(f"[MAL Estúdios]    {i}/{total} processados ({ok} ok, {falhas} falhas)...")

                    await asyncio.sleep(PAUSA_MAL_ESTUDIOS)
        finally:
            self._sync_em_andamento = False

        print(f"[MAL Estúdios] Fim do sync: {ok} ok, {falhas} falha(s){' — ABORTADO' if abortou else ''}.")

        if canal is not None:
            if abortou:
                msg = (
                    f"⛔ Sync interrompido após {FALHAS_CONSECUTIVAS_MAX} falhas seguidas "
                    f"(**{ok}** animes já salvos). Veja o console, corrija e rode `/mal_estudios_sync` "
                    f"de novo — ele continua de onde parou."
                )
            else:
                msg = f"✅ Estúdios sincronizados: **{ok}** anime(s)."
                if falhas:
                    msg += f"\n⚠️ {falhas} falharam — rode `/mal_estudios_sync` de novo para tentar outra vez."
            try:
                await canal.send(msg)
            except Exception as e:
                print(f"[MAL Estúdios] ⚠️  Não consegui avisar no canal: {e}")

    @app_commands.command(
        name="mal_estudios_sync",
        description="[Admin] Busca na API do MAL os estúdios dos animes ainda não sincronizados."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_estudios_sync(self, interaction: discord.Interaction):
        if self._sync_em_andamento:
            await interaction.response.send_message(
                "⏳ Já existe uma sincronização de estúdios em andamento.", ephemeral=True
            )
            return

        if not MAL_CLIENT_ID:
            await interaction.response.send_message(
                "❌ `MAL_CLIENT_ID` não está configurado — o sync usa a API oficial do MAL.", ephemeral=True
            )
            return

        pendentes = len(carregar_animes_sem_estudio())
        if pendentes == 0:
            await interaction.response.send_message(
                "✅ Todos os animes já têm estúdio sincronizado.", ephemeral=True
            )
            return

        minutos = int(pendentes * PAUSA_MAL_ESTUDIOS / 60) + 1
        await interaction.response.send_message(
            f"🔄 Sincronizando estúdios de **{pendentes}** anime(s) em segundo plano "
            f"(~{minutos} min). Aviso aqui no canal quando terminar.",
            ephemeral=True,
        )
        self._task_sync = asyncio.create_task(self._sincronizar_estudios(interaction.channel))
        print(f"[MAL Estúdios] {interaction.user} disparou o sync de estúdios ({pendentes} pendente(s)).")

    # ──────────────────────────────────────────
    #  Manutenção automática
    # ──────────────────────────────────────────
    #
    # De hora em hora busca o estúdio de qualquer anime que tenha entrado em
    # mal_snapshots e ainda não foi consultado (usuário novo, anime novo na
    # lista...). Não depende do mal_tracker: ele só lê o banco. Se não houver
    # pendentes, não faz nada (e não loga). Também retoma sozinho uma carga
    # inicial interrompida. O mesmo guard de _sync_em_andamento impede que o
    # loop e o /mal_estudios_sync rodem ao mesmo tempo.
    @tasks.loop(hours=1)
    async def estudios_loop(self):
        if not MAL_CLIENT_ID:
            return
        # try/except aqui dentro (e não só em um handler de erro): uma exceção que
        # escapasse do corpo encerraria o tasks.loop de vez, e o restart feito no
        # handler não chega a disparar porque, lá dentro, o loop ainda conta como ativo.
        try:
            await self._sincronizar_estudios(None)
        except Exception as e:
            print(f"[MAL Estúdios] ❌ Erro no estudios_loop (tenta de novo no próximo ciclo): {e}")
            traceback.print_exc()

    @estudios_loop.before_loop
    async def antes_do_loop_estudios(self):
        await self.bot.wait_until_ready()

    @estudios_loop.error
    async def estudios_loop_error(self, error: Exception):
        # Rede de segurança: o corpo do loop já trata as exceções, então isto só
        # deve aparecer em caso de erro inesperado fora dele.
        print(f"[MAL Estúdios] 🚨 ERRO FATAL no estudios_loop: {error}")
        traceback.print_exc()

    # ──────────────────────────────────────────
    #  Ranking
    # ──────────────────────────────────────────

    @app_commands.command(
        name="mal_estudios",
        description="Top 50 estúdios com a melhor média de notas do servidor.",
    )
    async def mal_estudios(self, interaction: discord.Interaction):
        await interaction.response.defer()

        usuarios = carregar_usuarios(interaction.guild_id)
        if not usuarios:
            await interaction.followup.send(
                "📋 Nenhum usuário monitorado. Use `/mal_entrar` para se adicionar!"
            )
            return

        rows = await asyncio.to_thread(ranking_estudios, usuarios)
        pendentes = len(carregar_animes_sem_estudio())

        if not rows:
            msg = (
                f"Nenhum estúdio atende aos critérios mínimos "
                f"({MIN_ANIMES_ESTUDIO} animes avaliados por {MIN_MEMBROS_ESTUDIO}+ membros)."
            )
            if pendentes:
                msg += f"\n⚠️ {pendentes} anime(s) ainda sem estúdio — um admin pode rodar `/mal_estudios_sync`."
            await interaction.followup.send(msg)
            return

        view = RankingEstudiosView(rows, len(usuarios), pendentes, interaction.user.id)
        await interaction.followup.send(embed=view.embed_atual(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(MalEstudiosCog(bot))