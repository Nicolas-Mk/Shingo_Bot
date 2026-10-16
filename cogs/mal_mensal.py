import asyncio
import sqlite3
from io import BytesIO
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands
from PIL import Image, ImageDraw, ImageOps

from cogs.config_cog import get_config, modulo_ativo
from cogs.mal_tracker import (
    carregar_usuarios,
    carregar_avatar,
    BRASILIA,
    COR_ANIME,
    COR_MANGA,
    _SS,
    _BG,
    _CARD_BG,
    _ROW_BG,
    _TEXT_MAIN,
    _TEXT_MUTED,
    _CARD_W,
    _PAD,
    _fonte,
    _mascara_circular,
    _abrir_imagem,
    _placeholder_avatar,
    _truncar_texto,
    _finalizar_canvas,
)

DB_PATH = "usuarios.db"

# Pontuação: 1 episódio = 1 ponto, 1 volume = 2 pontos (conforme definido).
PESO_EPISODIO = 1
PESO_VOLUME   = 2

# Normalização de episódios por duração real (ver _multiplicador_duracao):
# um anime de 1 min/episódio não pode valer o mesmo que um de 24 min — sem
# isso, dá pra inflar a pontuação só maratonando animes curtos. 20 min é a
# duração "padrão" usada como referência; entre 20 e 30 min (a faixa mais
# comum de anime de TV) não há ajuste, pra não penalizar a maioria dos casos
# por variações pequenas e irrelevantes de duração.
BASE_DURACAO_MIN = 20
FAIXA_SEM_AJUSTE_MIN = 20
FAIXA_SEM_AJUSTE_MAX = 30

# Quantos usuários entram em cada imagem do ranking antes de continuar em
# uma nova mensagem — mesmo valor do ITENS_POR_IMAGEM do relatório horário.
USUARIOS_POR_IMAGEM = 2

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# ──────────────────────────────────────────────
#  Persistência — controle de envio (evita relatório duplicado)
# ──────────────────────────────────────────────

def _garantir_tabela_envios(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mal_mensal_envios (
            guild_id INTEGER NOT NULL,
            mes      TEXT NOT NULL,  -- "YYYY-MM"
            enviado_em TEXT NOT NULL,
            PRIMARY KEY (guild_id, mes)
        )
        """
    )


def ja_enviado_neste_mes(guild_id: int, mes: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        _garantir_tabela_envios(conn)
        c = conn.cursor()
        c.execute(
            "SELECT 1 FROM mal_mensal_envios WHERE guild_id = ? AND mes = ?",
            (guild_id, mes)
        )
        return c.fetchone() is not None


def marcar_enviado(guild_id: int, mes: str):
    with sqlite3.connect(DB_PATH) as conn:
        _garantir_tabela_envios(conn)
        conn.execute(
            "INSERT OR REPLACE INTO mal_mensal_envios (guild_id, mes, enviado_em) VALUES (?, ?, ?)",
            (guild_id, mes, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()


# ──────────────────────────────────────────────
#  Agregação — lê direto de mal_progresso_mensal
# ──────────────────────────────────────────────

def _multiplicador_duracao(duracao_min: int) -> float:
    """
    Peso aplicado aos episódios de um anime conforme a duração real do
    episódio. Fora da faixa "normal" (20-30 min), escala linearmente com
    (duração / 20) — um episódio de 1 min vale 0.05x, um de 40 min vale 2x.
    Duração desconhecida (0, mangá ou ainda não populada) não penaliza nem
    beneficia: conta como peso cheio (1x).
    """
    if not duracao_min or duracao_min <= 0:
        return 1.0
    if FAIXA_SEM_AJUSTE_MIN <= duracao_min <= FAIXA_SEM_AJUSTE_MAX:
        return 1.0
    return duracao_min / BASE_DURACAO_MIN


def calcular_estatisticas_usuario(username: str, mes: str) -> dict:
    """
    Soma, para o `mes` informado ("YYYY-MM"), o progresso acumulado na
    tabela mal_progresso_mensal (episodios_mes/volumes_mes/completou_mes).

    Episódios de anime são ponderados pela duração real do episódio
    (duracao_ep_min, ver _multiplicador_duracao) ANTES de somar pontos —
    sem isso, maratonar um anime de 1 min/episódio vale o mesmo que um de
    24 min, o que permite inflar a pontuação artificialmente. O número de
    episódios exibido na UI continua sendo o bruto (real); só a pontuação
    usa o valor ponderado.

    Como esses acumuladores vivem numa linha por item (sem histórico de
    eventos), só entram na soma os itens cujo mes_referencia ainda é igual
    a `mes` — ou seja, itens que não foram tocados de novo depois que o mês
    virou. Ver nota sobre a janela de corte em
    mal_tracker_cog._atualizar_progresso_mensal.
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT episodios_mes, completou_mes, duracao_ep_min
            FROM mal_progresso_mensal
            WHERE username = ? AND tipo = 'anime' AND mes_referencia = ?
            """,
            (username, mes),
        )
        linhas_anime = c.fetchall()

        c.execute(
            """
            SELECT
                COALESCE(SUM(volumes_mes), 0),
                COALESCE(SUM(completou_mes), 0)
            FROM mal_progresso_mensal
            WHERE username = ? AND tipo = 'manga' AND mes_referencia = ?
            """,
            (username, mes),
        )
        volumes, mangas_completos = c.fetchone()

    episodios = 0
    episodios_ponderados = 0.0
    animes_completos = 0
    for ep_mes, comp_mes, duracao_min in linhas_anime:
        episodios += ep_mes
        animes_completos += comp_mes
        episodios_ponderados += ep_mes * _multiplicador_duracao(duracao_min)

    pontuacao = round(episodios_ponderados * PESO_EPISODIO + volumes * PESO_VOLUME)

    return {
        "username":             username,
        "episodios":            episodios,
        "episodios_ponderados": round(episodios_ponderados, 1),
        "animes_completos":     animes_completos,
        "volumes":              volumes,
        "mangas_completos":     mangas_completos,
        "pontuacao":            pontuacao,
    }


def montar_ranking_guild(guild_id: int, mes: str) -> list[dict]:
    """Retorna o ranking (desc. por pontuação) dos usuários monitorados nesta guild."""
    usuarios = carregar_usuarios(guild_id)
    stats = [calcular_estatisticas_usuario(u, mes) for u in usuarios]
    # Só entra no ranking quem teve alguma atividade no mês.
    stats = [s for s in stats if s["episodios"] or s["volumes"] or s["animes_completos"] or s["mangas_completos"]]
    stats.sort(key=lambda s: (s["pontuacao"], s["episodios"] + s["volumes"]), reverse=True)
    return stats


def mes_anterior(referencia: datetime) -> tuple[int, int]:
    """Retorna (ano, mes) do mês anterior ao de `referencia`."""
    if referencia.month == 1:
        return referencia.year - 1, 12
    return referencia.year, referencia.month - 1


def parse_mes_str(mes_str: str | None, agora: datetime) -> tuple[int, int]:
    """Converte 'MM/AAAA' pra (ano, mes); usa o mês anterior a `agora` se None."""
    if not mes_str:
        return mes_anterior(agora)
    try:
        mes_part, ano_part = mes_str.split("/")
        return int(ano_part), int(mes_part)
    except Exception:
        return mes_anterior(agora)


# ──────────────────────────────────────────────
#  Geração dos cards visuais (reaproveita o estilo do mal_tracker_cog)
# ──────────────────────────────────────────────

_ROW_H_RANKING = 136
_AV_TAM = 64
_MEDALHA_COR = {
    1: (255, 215, 0),    # ouro
    2: (200, 200, 210),  # prata
    3: (205, 127, 50),   # bronze
}


def _desenhar_badge_posicao(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, posicao: int):
    cor = _MEDALHA_COR.get(posicao, _ROW_BG)
    contorno = _MEDALHA_COR.get(posicao, _TEXT_MUTED)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=cor if posicao <= 3 else _ROW_BG, outline=contorno, width=max(2, int(r * 0.12)))

    fonte = _fonte(int(r * 1.1), negrito=True)
    texto = str(posicao)
    cor_texto = (20, 20, 20) if posicao <= 3 else _TEXT_MAIN

    # draw.text() posiciona pelo canto superior-esquerdo do bounding box do
    # texto, não pelo centro — e esse bounding box não é simétrico em torno
    # da baseline (varia por fonte/glifo). Usar textbbox e compensar o
    # próprio offset do bbox é o jeito correto de centralizar de verdade,
    # em vez de aproximar com uma fração fixa de r (que ficava desalinhado
    # verticalmente, principalmente em números de 2 dígitos).
    bbox = draw.textbbox((0, 0), texto, font=fonte)
    largura_texto = bbox[2] - bbox[0]
    altura_texto  = bbox[3] - bbox[1]
    tx = cx - largura_texto / 2 - bbox[0]
    ty = cy - altura_texto / 2 - bbox[1]
    draw.text((tx, ty), texto, font=fonte, fill=cor_texto)


def gerar_cabecalho_mensal(mes: int, ano: int, total_usuarios: int, total_pontos: int, pagina: int = 0, total_paginas: int = 1) -> bytes:
    s = _SS
    pad, card_w = _PAD * s, _CARD_W * s

    f_titulo = _fonte(32 * s, negrito=True)
    f_sub    = _fonte(19 * s)

    mes_label = f"{MESES_PT[mes]} de {ano}"
    titulo = f"RELATÓRIO MENSAL — {mes_label.upper()}"
    sub = f"{total_usuarios} membro(s) ativo(s)   ·   {total_pontos} pts no total"
    if total_paginas > 1:
        sub += f"   ·   página {pagina + 1}/{total_paginas}"

    titulo_y = pad + 10 * s
    sub_y    = titulo_y + 46 * s
    altura   = sub_y + 34 * s + pad

    canvas = Image.new("RGB", (card_w, altura), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

    titulo_disp = _truncar_texto(draw, titulo, f_titulo, card_w - pad * 2)
    titulo_w = draw.textlength(titulo_disp, font=f_titulo)
    draw.text(((card_w - titulo_w) // 2, titulo_y), titulo_disp, font=f_titulo, fill=(255, 255, 255))

    sub_w = draw.textlength(sub, font=f_sub)
    draw.text(((card_w - sub_w) // 2, sub_y), sub, font=f_sub, fill=_TEXT_MUTED)

    return _finalizar_canvas(canvas)


def gerar_imagem_ranking(usuarios_pagina: list[dict], indice_inicial: int, pagina: int, total_paginas: int) -> bytes:
    """
    Gera um card com uma linha por usuário: posição, avatar, nome, stats de
    anime e de mangá em linhas separadas (evita que o texto fique apertado
    quando o usuário tem atividade nos dois) e pontuação em destaque à direita.
    """
    s = _SS
    pad, row_h, card_w = _PAD * s, _ROW_H_RANKING * s, _CARD_W * s
    gap = 10 * s

    n = len(usuarios_pagina)
    altura = pad * 2 + n * row_h + max(0, n - 1) * gap
    canvas = Image.new("RGB", (card_w, altura), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

    f_nome    = _fonte(24 * s, negrito=True)
    f_stats   = _fonte(17 * s)
    f_pontos  = _fonte(30 * s, negrito=True)
    f_pontos_label = _fonte(14 * s)

    y = pad
    for i, dados in enumerate(usuarios_pagina):
        posicao = indice_inicial + i
        draw.rounded_rectangle((pad, y, card_w - pad, y + row_h - gap), radius=10 * s, fill=_ROW_BG)

        cy = y + (row_h - gap) / 2

        # Badge de posição
        badge_r = 20 * s
        badge_cx = pad + 26 * s
        _desenhar_badge_posicao(draw, badge_cx, cy, badge_r, posicao)

        # Avatar
        av_tam = _AV_TAM * s
        av_x = pad + 26 * s + badge_r + 14 * s
        av_y = int(cy - av_tam / 2)
        avatar_bytes = carregar_avatar(dados["username"])
        avatar_img = _abrir_imagem(avatar_bytes, _placeholder_avatar())
        av = ImageOps.fit(avatar_img, (av_tam, av_tam))
        canvas.paste(av, (av_x, av_y), _mascara_circular((av_tam, av_tam)))
        cor_borda = COR_ANIME if dados["episodios"] >= dados["volumes"] else COR_MANGA
        draw.ellipse((av_x, av_y, av_x + av_tam, av_y + av_tam), outline=cor_borda, width=3 * s)

        # Nome + stats (anime e mangá em linhas separadas — juntos numa linha
        # só, vez ou outra não cabia o texto dos dois)
        tx = av_x + av_tam + 18 * s
        largura_max_stats = card_w - pad - 12 * s - tx - 170 * s

        partes_anime = []
        if dados["animes_completos"]:
            partes_anime.append(f"{dados['animes_completos']} anime(s) completo(s)")
        if dados["episodios"]:
            # Mostra o valor ponderado entre parênteses só quando ele difere
            # de forma perceptível do bruto — a maioria dos animes fica na
            # faixa sem ajuste (20-30 min) e os dois números seriam iguais,
            # o que só poluiria a tela à toa.
            texto_ep = f"{dados['episodios']} ep assistidos"
            if abs(dados["episodios"] - dados["episodios_ponderados"]) >= 1:
                texto_ep += f" ({dados['episodios_ponderados']:.1f} ponderados)"
            partes_anime.append(texto_ep)
        linha_anime = " · ".join(partes_anime)

        partes_manga = []
        if dados["mangas_completos"]:
            partes_manga.append(f"{dados['mangas_completos']} mangá(s) completo(s)")
        if dados["volumes"]:
            partes_manga.append(f"{dados['volumes']} vol lidos")
        linha_manga = " · ".join(partes_manga)

        sem_atividade = not linha_anime and not linha_manga

        nome_y = int(cy - 44 * s)
        draw.text((tx, nome_y), f"@{dados['username']}", font=f_nome, fill=(255, 255, 255))

        linha_y = nome_y + 34 * s
        if sem_atividade:
            draw.text((tx, linha_y), "sem atividade registrada", font=f_stats, fill=_TEXT_MUTED)
        else:
            if linha_anime:
                linha_disp = _truncar_texto(draw, linha_anime, f_stats, largura_max_stats)
                draw.text((tx, linha_y), linha_disp, font=f_stats, fill=_TEXT_MUTED)
                linha_y += 22 * s
            if linha_manga:
                linha_disp = _truncar_texto(draw, linha_manga, f_stats, largura_max_stats)
                draw.text((tx, linha_y), linha_disp, font=f_stats, fill=_TEXT_MUTED)

        # Pontuação em destaque à direita
        pontos_texto = str(dados["pontuacao"])
        pontos_w = draw.textlength(pontos_texto, font=f_pontos)
        label_w = draw.textlength("PTS", font=f_pontos_label)
        px = card_w - pad - 16 * s - max(pontos_w, label_w)
        draw.text((px + (max(pontos_w, label_w) - pontos_w) / 2, cy - 24 * s), pontos_texto, font=f_pontos, fill=cor_borda)
        draw.text((px + (max(pontos_w, label_w) - label_w) / 2, cy + 10 * s), "PTS", font=f_pontos_label, fill=_TEXT_MUTED)

        y += row_h

    return _finalizar_canvas(canvas)


# ──────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────

class MalMensalCog(commands.Cog):
    """Relatório mensal de atividade (animes/mangás completados, episódios/volumes) no MAL."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monitorar_mensal_loop.start()

    def cog_unload(self):
        self.monitorar_mensal_loop.cancel()

    # ──────────────────────────────────────────
    #  Geração + envio do relatório
    # ──────────────────────────────────────────

    async def _enviar_relatorio_mensal(self, canal: discord.TextChannel, guild_id: int, ano: int, mes: int):
        mes_chave = f"{ano:04d}-{mes:02d}"
        ranking = montar_ranking_guild(guild_id, mes_chave)

        if not ranking:
            await canal.send(
                f"📊 **Relatório mensal — {MESES_PT[mes]} de {ano}**\n"
                f"Nenhuma atividade registrada por aqui neste período."
            )
            return 0

        total_pontos = sum(u["pontuacao"] for u in ranking)
        lotes = [ranking[i:i + USUARIOS_POR_IMAGEM] for i in range(0, len(ranking), USUARIOS_POR_IMAGEM)]

        cabecalho_png = gerar_cabecalho_mensal(mes, ano, len(ranking), total_pontos, 0, len(lotes))
        buffer = BytesIO(cabecalho_png)
        buffer.seek(0)
        await canal.send(file=discord.File(buffer, filename="mal_mensal_cabecalho.png"))

        indice = 1
        for pagina_idx, lote in enumerate(lotes):
            png_bytes = gerar_imagem_ranking(lote, indice, pagina_idx, len(lotes))
            buffer = BytesIO(png_bytes)
            buffer.seek(0)
            sufixo = f"_{pagina_idx + 1}" if len(lotes) > 1 else ""
            await canal.send(file=discord.File(buffer, filename=f"mal_mensal{sufixo}.png"))
            indice += len(lote)
            await asyncio.sleep(0.3)

        return len(ranking)

    # ──────────────────────────────────────────
    #  Loop — todo dia 1º às 12:00 (horário de Brasília)
    # ──────────────────────────────────────────

    @tasks.loop(hours=1)
    async def monitorar_mensal_loop(self):
        await self.bot.wait_until_ready()
        agora = datetime.now(BRASILIA)
        if agora.day != 1 or agora.hour != 12:
            return

        ano_alvo, mes_alvo = mes_anterior(agora)
        mes_chave = f"{ano_alvo:04d}-{mes_alvo:02d}"

        for guild in self.bot.guilds:
            try:
                if not modulo_ativo(guild.id, "mal_mensal"):
                    continue

                if ja_enviado_neste_mes(guild.id, mes_chave):
                    continue

                canal_id = get_config(guild.id, "canal_mal")
                if not canal_id:
                    print(f"[MAL Mensal] Guild '{guild.name}': canal_mal não configurado, pulando.")
                    continue

                canal = self.bot.get_channel(canal_id)
                if canal is None:
                    print(f"[MAL Mensal] ⚠️  Canal {canal_id} não encontrado na guild {guild.name}.")
                    continue

                total = await self._enviar_relatorio_mensal(canal, guild.id, ano_alvo, mes_alvo)
                marcar_enviado(guild.id, mes_chave)
                print(f"[MAL Mensal] ✅ {guild.name}: relatório de {mes_chave} enviado ({total} usuário(s)).")
            except Exception as e:
                print(f"[MAL Mensal] ❌ Erro ao processar guild '{guild.name}': {e}")
                import traceback
                traceback.print_exc()

    @monitorar_mensal_loop.before_loop
    async def antes_do_loop(self):
        await self.bot.wait_until_ready()

    @monitorar_mensal_loop.error
    async def monitorar_mensal_loop_error(self, error: Exception):
        print(f"[MAL Mensal] 🚨 ERRO FATAL no monitorar_mensal_loop: {error}")
        import traceback
        traceback.print_exc()
        if not self.monitorar_mensal_loop.is_running():
            print("[MAL Mensal] 🔁 Reiniciando monitorar_mensal_loop após erro fatal.")
            self.monitorar_mensal_loop.restart()

    # ──────────────────────────────────────────
    #  Comandos
    # ──────────────────────────────────────────

    @app_commands.command(name="mal_mensal", description="Mostra o relatório mensal de atividade no MAL.")
    @app_commands.describe(mes="Mês a consultar no formato MM/AAAA (padrão: mês anterior)")
    async def mal_mensal(self, interaction: discord.Interaction, mes: str | None = None):
        if not modulo_ativo(interaction.guild_id, "mal_mensal"):
            await interaction.response.send_message(
                "🚫 O módulo de relatório mensal do MAL está desativado neste servidor.", ephemeral=True
            )
            return
        await interaction.response.defer()
        agora = datetime.now(BRASILIA)
        ano_alvo, mes_alvo = parse_mes_str(mes, agora)

        mes_chave = f"{ano_alvo:04d}-{mes_alvo:02d}"
        ranking = montar_ranking_guild(interaction.guild_id, mes_chave)

        if not ranking:
            await interaction.followup.send(
                f"📊 Nenhuma atividade registrada em **{MESES_PT[mes_alvo]} de {ano_alvo}**."
            )
            return

        total_pontos = sum(u["pontuacao"] for u in ranking)
        lotes = [ranking[i:i + USUARIOS_POR_IMAGEM] for i in range(0, len(ranking), USUARIOS_POR_IMAGEM)]

        cabecalho_png = gerar_cabecalho_mensal(mes_alvo, ano_alvo, len(ranking), total_pontos, 0, len(lotes))
        buffer = BytesIO(cabecalho_png)
        buffer.seek(0)
        await interaction.followup.send(file=discord.File(buffer, filename="mal_mensal_cabecalho.png"))

        indice = 1
        for pagina_idx, lote in enumerate(lotes):
            png_bytes = gerar_imagem_ranking(lote, indice, pagina_idx, len(lotes))
            buffer = BytesIO(png_bytes)
            buffer.seek(0)
            sufixo = f"_{pagina_idx + 1}" if len(lotes) > 1 else ""
            await interaction.channel.send(file=discord.File(buffer, filename=f"mal_mensal{sufixo}.png"))
            indice += len(lote)

    @app_commands.command(name="mal_mensal_forcar", description="[Admin] Força o envio do relatório mensal agora.")
    @app_commands.describe(mes="Mês a enviar no formato MM/AAAA (padrão: mês anterior)")
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_mensal_forcar(self, interaction: discord.Interaction, mes: str | None = None):
        if not modulo_ativo(interaction.guild_id, "mal_mensal"):
            await interaction.response.send_message(
                "🚫 O módulo de relatório mensal do MAL está desativado neste servidor. "
                "Use `/modulo` para reativá-lo antes de forçar o envio.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        agora = datetime.now(BRASILIA)
        ano_alvo, mes_alvo = parse_mes_str(mes, agora)

        canal_id = get_config(interaction.guild_id, "canal_mal")
        canal = self.bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            await interaction.followup.send(
                "❌ Canal do MAL não configurado. Use `/config_canal` para definir.", ephemeral=True
            )
            return

        total = await self._enviar_relatorio_mensal(canal, interaction.guild_id, ano_alvo, mes_alvo)
        await interaction.followup.send(
            f"✅ Relatório de **{MESES_PT[mes_alvo]}/{ano_alvo}** enviado com **{total}** usuário(s) no ranking.",
            ephemeral=True
        )

    @app_commands.command(name="mal_mensal_status", description="[Admin] Mostra quando é o próximo relatório mensal.")
    @app_commands.checks.has_permissions(administrator=True)
    async def mal_mensal_status(self, interaction: discord.Interaction):
        proxima = self.monitorar_mensal_loop.next_iteration
        prox_str = f"<t:{int(proxima.timestamp())}:R>" if proxima else "desconhecido"

        agora = datetime.now(BRASILIA)
        if agora.day == 1 and agora.month == 1:
            ano_p, mes_p = agora.year, 1
        else:
            if agora.month == 12:
                ano_p, mes_p = agora.year + 1, 1
            else:
                ano_p, mes_p = agora.year, agora.month + 1
        mes_chave = f"{ano_p:04d}-{(mes_p - 1) or 12:02d}"

        modulo_status = "✅ Ativado" if modulo_ativo(interaction.guild_id, "mal_mensal") else "🚫 Desativado"

        embed = discord.Embed(title="📅 MAL Mensal — Status", color=0x2E51A2)
        embed.add_field(name="Módulo neste servidor", value=modulo_status, inline=False)
        embed.add_field(name="Próxima checagem do loop", value=prox_str, inline=False)
        embed.add_field(
            name="Envio",
            value="Todo dia 1º às 12:00 (horário de Brasília), reportando o mês anterior.",
            inline=False
        )
        embed.set_footer(text="Use /modulo para ativar ou desativar.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MalMensalCog(bot))