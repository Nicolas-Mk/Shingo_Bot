import json
import sqlite3
from io import BytesIO
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from PIL import Image, ImageDraw, ImageOps

from cogs.mal_tracker import (
    carregar_usuarios,
    carregar_avatar,
    carregar_capa,
    salvar_capa,
    comprimir_capa,
    BRASILIA,
    COR_ANIME,
    MAL_API_BASE,
    MAL_CLIENT_ID,
    _SS,
    _BG,
    _CARD_BG,
    _TEXT_MAIN,
    _TEXT_MUTED,
    _CARD_W,
    _PAD,
    _COVER_RATIO,
    _fonte,
    _mascara_circular,
    _mascara_arredondada,
    _abrir_imagem,
    _placeholder_avatar,
    _placeholder_cover,
    _truncar_texto,
    _finalizar_canvas,
)
from cogs.mal_mensal import (
    calcular_estatisticas_usuario,
    parse_mes_str,
    MESES_PT,
    _MEDALHA_COR,
)

DB_PATH = "usuarios.db"
ANILIST_API = "https://graphql.anilist.co"

_ANILIST_QUERY = """
query ($idMal: Int, $type: MediaType) {
  Media(idMal: $idMal, type: $type) {
    title { romaji english }
    coverImage { medium large }
  }
}
"""

# Top 3: capas lado a lado na proporção real do pôster (_COVER_RATIO, mesma
# usada nos cards de atualização), com uma barra escura semi-transparente
# sobreposta na base de cada uma, com o nome e a nota. Sem pódio, sem "1º/2º/3º".
# A altura da barra acompanha o texto (título em 1-2 linhas + nota).
_BANNER_GAP        = 10
_BANNER_BAR_ALPHA  = 165   # opacidade da barra (0-255, baixa = mais transparente)
_BANNER_PAD        = 6     # respiro interno da barra
_BANNER_LINE_H     = 18    # altura de cada linha do título
_BANNER_NOTA_H     = 20    # altura da linha da nota
_BANNER_MAX_LINHAS = 2     # título quebra em até 2 linhas, depois "..."
_STAT_ROW_H        = 62    # era 46
_STAT_COLS         = 3
_AV_TAM_HEADER     = 52


# ──────────────────────────────────────────────
#  Dados — itens tocados no mês + nota atual do usuário
# ──────────────────────────────────────────────
#
# mal_progresso_mensal não guarda nota (só progresso); a nota "do mês" é uma
# aproximação deliberada: pegamos a nota ATUAL em mal_snapshots pros itens
# que tiveram atividade naquele mes_referencia. Não há histórico de nota por
# mês no sistema — mesma limitação que o resto do app já assume (ver nota em
# mal_tracker_cog._atualizar_progresso_mensal).

def _buscar_itens_do_mes(username: str, mes: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT tipo, item_id, episodios_mes, capitulos_mes, volumes_mes,
                   completou_mes, duracao_ep_min, watched_at
            FROM mal_progresso_mensal
            WHERE username = ? AND mes_referencia = ?
            """,
            (username, mes),
        )
        linhas = c.fetchall()

        itens = []
        for tipo, item_id, ep_mes, cap_mes, vol_mes, comp_mes, dur_min, watched_at in linhas:
            c.execute(
                "SELECT snapshot FROM mal_snapshots WHERE username = ? AND tipo = ? AND item_id = ?",
                (username, tipo, item_id),
            )
            row = c.fetchone()
            score = 0
            if row:
                try:
                    score = json.loads(row[0]).get("score", 0) or 0
                except Exception:
                    score = 0

            itens.append({
                "tipo":           tipo,
                "item_id":        item_id,
                "episodios_mes":  ep_mes,
                "capitulos_mes":  cap_mes,
                "volumes_mes":    vol_mes,
                "completou_mes":  comp_mes,
                "duracao_ep_min": dur_min,
                "watched_at":     watched_at or "",
                "score":          score,
            })
    return itens


def _teve_atividade_real(item: dict) -> bool:
    """
    True se o item teve progresso de verdade neste mês (episódios, capítulos,
    volumes ou conclusão) — não apenas uma troca de nota.

    mal_snapshots só guarda o estado ATUAL (sem histórico), então não dá pra
    confirmar isoladamente ali "o que mudou". Mas mal_progresso_mensal já
    responde essa pergunta de graça: se o item está aqui é porque seu
    mes_referencia bate com o mês consultado — e se, mesmo assim, todos os
    deltas (episodios_mes/capitulos_mes/volumes_mes/completou_mes) ficaram
    em zero, a ÚNICA coisa que pode ter gerado esse evento foi uma troca de
    nota (qualquer progresso real teria produzido um delta > 0 em
    _atualizar_progresso_mensal — ver cogs/mal_tracker.py).
    """
    return bool(
        item["episodios_mes"] or item["capitulos_mes"]
        or item["volumes_mes"] or item["completou_mes"]
    )


def _top3_por_nota(itens: list[dict]) -> list[dict]:
    """
    Os até-3 itens com maior nota (>0) no mês, EXCLUINDO itens cuja única
    atividade no mês foi trocar a nota (ver _teve_atividade_real) — senão
    um re-rate de algo assistido/lido em outro mês aparece como se fosse
    destaque "deste mês" sem ter progredido nada de fato.
    Desempate por ter completado no mês.
    """
    avaliados = [i for i in itens if i["score"] > 0 and _teve_atividade_real(i)]
    avaliados.sort(key=lambda i: (i["score"], i["completou_mes"]), reverse=True)
    return avaliados[:3]


def _nota_media(itens: list[dict]) -> float | None:
    """Média das notas dos itens com atividade real no mês (None se não houver nenhum avaliado)."""
    notas = [i["score"] for i in itens if i["score"] > 0 and _teve_atividade_real(i)]
    if not notas:
        return None
    return round(sum(notas) / len(notas), 2)


def _mes_chave_anterior(mes_chave: str) -> str:
    ano, mes = (int(p) for p in mes_chave.split("-"))
    if mes == 1:
        return f"{ano - 1:04d}-12"
    return f"{ano:04d}-{mes - 1:02d}"


# Removido: "dia mais ativo". O banco só guarda o acumulado mensal por item
# (episodios_mes/capitulos_mes) + a data do ÚLTIMO evento daquele item no mês
# (watched_at) — não um log por evento/data. Pra estimar um "dia" seria
# preciso atribuir TODO o progresso do item ao dia do último toque, o que dá
# um número que parece exato mas não é garantido pelo que o banco registra
# de verdade (se o item foi tocado em dias diferentes no mesmo mês, só o
# último "herda" o total). Sem uma tabela de log por evento, não dá pra
# afirmar isso com confiança — por isso ficou de fora.


# ──────────────────────────────────────────────
#  Título + capa (MAL, com fallback AniList — mesmo padrão do mal_top)
# ──────────────────────────────────────────────

async def _buscar_info_anilist(session: aiohttp.ClientSession, tipo: str, item_id: str) -> dict | None:
    tipo_al = "ANIME" if tipo == "anime" else "MANGA"
    try:
        async with session.post(
            ANILIST_API,
            json={"query": _ANILIST_QUERY, "variables": {"idMal": int(item_id), "type": tipo_al}},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            media = (data or {}).get("data", {}).get("Media")
            if not media:
                return None
            titulo_obj = media.get("title") or {}
            capa_obj = media.get("coverImage") or {}
            return {
                "title": titulo_obj.get("english") or titulo_obj.get("romaji"),
                "capa_url": capa_obj.get("medium") or capa_obj.get("large"),
            }
    except Exception:
        return None


async def _buscar_info_mal(session: aiohttp.ClientSession, tipo: str, item_id: str) -> dict | None:
    endpoint = "anime" if tipo == "anime" else "manga"
    url = f"{MAL_API_BASE}/{endpoint}/{item_id}"
    try:
        async with session.get(
            url,
            params={"fields": "title,main_picture"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                titulo = data.get("title")
                capa = data.get("main_picture", {}) or {}
                if titulo:
                    return {"title": titulo, "capa_url": capa.get("medium") or capa.get("large")}
    except Exception:
        pass
    return await _buscar_info_anilist(session, tipo, item_id)


async def _baixar_bytes(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception:
        pass
    return None


async def _obter_capa(session: aiohttp.ClientSession, item_id: str, tipo: str, url: str | None) -> bytes | None:
    if not url:
        return None
    cache = carregar_capa(item_id, tipo)
    if cache:
        return cache
    img_bytes = await _baixar_bytes(session, url)
    if not img_bytes:
        return None
    try:
        comprimido = comprimir_capa(img_bytes)
    except Exception:
        return None
    salvar_capa(item_id, tipo, comprimido)
    return comprimido


# ──────────────────────────────────────────────
#  Montagem dos dados do resumo
# ──────────────────────────────────────────────

async def montar_resumo(username: str, mes_chave: str) -> dict | None:
    """
    Retorna {"stats": ..., "capitulos": int, "top3": [...]} ou None se o
    usuário não teve nenhuma atividade registrada naquele mês.
    `stats` reaproveita calcular_estatisticas_usuario do mal_mensal (mesma
    ponderação de episódios por duração já usada no ranking do servidor).
    """
    itens = _buscar_itens_do_mes(username, mes_chave)
    # Se NENHUM item teve progresso real (só trocas de nota, ver
    # _teve_atividade_real), trata como se o mês não tivesse atividade —
    # senão o comando mostraria um card "vazio" (0 episódios, 0 volumes,
    # pódio sem ninguém) só porque alguém reavaliou algo de outro mês.
    if not itens or not any(_teve_atividade_real(i) for i in itens):
        return None

    stats = calcular_estatisticas_usuario(username, mes_chave)
    capitulos = sum(i["capitulos_mes"] for i in itens if i["tipo"] == "manga")
    top3_raw = _top3_por_nota(itens)

    nota_media = _nota_media(itens)

    # Mês anterior — só pra comparar a nota média (uma consulta leve a mais;
    # não precisa buscar título/capa de nada aqui).
    itens_mes_anterior = _buscar_itens_do_mes(username, _mes_chave_anterior(mes_chave))
    nota_media_anterior = _nota_media(itens_mes_anterior)
    delta_nota = (
        round(nota_media - nota_media_anterior, 2)
        if nota_media is not None and nota_media_anterior is not None
        else None
    )

    top3 = []
    async with aiohttp.ClientSession(headers={"X-MAL-CLIENT-ID": MAL_CLIENT_ID}) as session:
        for item in top3_raw:
            info = await _buscar_info_mal(session, item["tipo"], item["item_id"]) or {}
            titulo = info.get("title") or (
                f"{'Anime' if item['tipo'] == 'anime' else 'Mangá'} #{item['item_id']}"
            )
            capa = await _obter_capa(session, item["item_id"], item["tipo"], info.get("capa_url"))
            top3.append({**item, "titulo": titulo, "capa": capa})

    return {
        "stats":       stats,
        "capitulos":   capitulos,
        "top3":        top3,
        "nota_media":  nota_media,
        "delta_nota":  delta_nota,
    }


def _formatar_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    if delta == 0:
        return "0.0"
    return f"{delta:+.1f}"


# ──────────────────────────────────────────────
#  Card visual — cabeçalho + banners do top 3 + estatísticas
# ──────────────────────────────────────────────

def _quebrar_titulo(draw, texto: str, fonte, largura_max: float, max_linhas: int = 2) -> list[str]:
    """Quebra o texto por palavras em até `max_linhas`; se sobrar, termina com '...'."""
    def cabe(t: str) -> bool:
        return draw.textlength(t, font=fonte) <= largura_max

    def com_reticencias(t: str) -> str:
        while t and not cabe(t + "..."):
            t = t[:-1]
        return t.rstrip() + "..."

    linhas, atual = [], ""
    for palavra in texto.split():
        teste = f"{atual} {palavra}".strip()
        if cabe(teste) or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)

    if len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        linhas[-1] = com_reticencias(linhas[-1])

    # palavra gigante que não cabe nem sozinha na linha
    return [l if cabe(l) else com_reticencias(l) for l in linhas] or [""]


def gerar_imagem_resumo(username: str, avatar_bytes: bytes | None, mes: int, ano: int, dados: dict) -> bytes:
    s = _SS
    pad, card_w = _PAD * s, _CARD_W * s

    f_user      = _fonte(24 * s, negrito=True)
    f_sub       = _fonte(15 * s)
    f_titulo_it = _fonte(14 * s, negrito=True)
    f_nota      = _fonte(16 * s, negrito=True)
    f_stat_lbl  = _fonte(16 * s)                  # era 13
    f_stat_val  = _fonte(26 * s, negrito=True)    # era 20
    f_vazio     = _fonte(15 * s)

    stats = dados["stats"]
    capitulos = dados["capitulos"]
    top3 = dados["top3"]
    nota_media = dados["nota_media"]
    delta_nota = dados["delta_nota"]

    # "Nota média" já embute a comparação com o mês passado numa estatística
    # só (em vez de duas), pra caber certinho nas 7 que sobraram.
    if nota_media is not None:
        valor_nota_media = f"{nota_media:.1f}"
        if delta_nota is not None:
            valor_nota_media += f" ({_formatar_delta(delta_nota)})"
    else:
        valor_nota_media = "—"

    pares_stats = [
        ("Episódios assistidos", str(stats["episodios"])),
        ("Animes completos",     str(stats["animes_completos"])),
        ("Capítulos lidos",      str(capitulos)),
        ("Volumes lidos",        str(stats["volumes"])),
        ("Mangás completos",     str(stats["mangas_completos"])),
        ("Pontuação (ranking)",  str(stats["pontuacao"])),
        ("Nota média",           valor_nota_media),
    ]

    # ── Alturas dos blocos ──
    av_tam = _AV_TAM_HEADER * s
    header_h = av_tam + 16 * s
    gap = _BANNER_GAP * s

    # Capas lado a lado (3 colunas), na proporção REAL do pôster — a
    # largura da coluna precisa ser calculada antes da altura da capa.
    n_colunas = 3
    largura_colunas = card_w - pad * 2
    col_w = (largura_colunas - gap * (n_colunas - 1)) / n_colunas
    cov_h = col_w / _COVER_RATIO

    bloco_capas_h = cov_h if top3 else (26 * s)

    linhas_stats = (len(pares_stats) + _STAT_COLS - 1) // _STAT_COLS
    stats_h = linhas_stats * _STAT_ROW_H * s

    # col_w/cov_h vêm de divisões (ficam float) — Image.new exige altura
    # inteira, daí o arredondamento aqui no final.
    altura = int(round(pad + header_h + gap + bloco_capas_h + gap + stats_h + pad))

    # ── Canvas ──
    canvas = Image.new("RGB", (card_w, altura), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

    # ── Cabeçalho: avatar + nome + subtítulo (estilo mal_tracker) ──
    avatar_img = _abrir_imagem(avatar_bytes, _placeholder_avatar())
    av = ImageOps.fit(avatar_img, (av_tam, av_tam))
    canvas.paste(av, (pad, pad), _mascara_circular((av_tam, av_tam)))
    draw.ellipse((pad, pad, pad + av_tam, pad + av_tam), outline=COR_ANIME, width=3 * s)

    tx = pad + av_tam + 16 * s
    draw.text((tx, pad), f"@{username}", font=f_user, fill=(255, 255, 255))
    draw.text((tx, pad + 30 * s), f"Resumo de {MESES_PT[mes]} de {ano}", font=f_sub, fill=_TEXT_MUTED)

    y = pad + header_h + gap

    # ── Top 3 em colunas, capa na proporção real + barra semi-transparente
    # sobreposta na base, com o nome (até 2 linhas) e a nota — sem "1º/2º/3º".
    if top3:
        cov_w_i, cov_h_i = int(col_w), int(cov_h)
        pad_in = _BANNER_PAD * s
        line_h = _BANNER_LINE_H * s
        nota_h = _BANNER_NOTA_H * s

        for idx, item in enumerate(top3, start=1):
            cor = _MEDALHA_COR.get(idx, _TEXT_MUTED)
            col_x = int(pad + (idx - 1) * (col_w + gap))

            # Título quebrado em até 2 linhas (com "..." se passar disso)
            linhas_titulo = _quebrar_titulo(
                draw, item["titulo"], f_titulo_it,
                col_w - pad_in * 2, _BANNER_MAX_LINHAS,
            )

            # Altura da barra acompanha o conteúdo: 1 linha = barra menor
            bar_h_i = min(
                cov_h_i,
                int(pad_in + len(linhas_titulo) * line_h + 2 * s + nota_h + pad_in),
            )
            bar_y_local = cov_h_i - bar_h_i

            # Compõe a barra DENTRO da capa e só depois aplica a máscara
            # arredondada, assim os cantos de baixo ficam arredondados.
            cover_img = _abrir_imagem(item.get("capa"), _placeholder_cover())
            cov = ImageOps.fit(cover_img, (cov_w_i, cov_h_i)).convert("RGB")
            overlay = Image.new("RGBA", (cov_w_i, bar_h_i), (0, 0, 0, _BANNER_BAR_ALPHA))
            cov.paste(overlay.convert("RGB"), (0, bar_y_local), overlay)
            canvas.paste(cov, (col_x, int(y)), _mascara_arredondada((cov_w_i, cov_h_i), 10 * s))

            # Texto: título (1-2 linhas) e nota logo abaixo
            bar_y = int(y) + bar_y_local
            ty = bar_y + pad_in
            for linha in linhas_titulo:
                draw.text((col_x + pad_in, ty), linha, font=f_titulo_it, fill=(255, 255, 255))
                ty += line_h
            draw.text((col_x + pad_in, ty + 2 * s), str(item["score"]), font=f_nota, fill=cor)

        y += cov_h + gap
    else:
        draw.text((pad, y), "Nenhuma nota registrada neste mês.", font=f_vazio, fill=_TEXT_MUTED)
        y += 26 * s + gap

    # ── Estatísticas (grade 3 colunas, 7 itens — a última linha fica com 1 só) ──
    col_w_stats = (card_w - pad * 2) / _STAT_COLS
    for i, (label, valor) in enumerate(pares_stats):
        col = i % _STAT_COLS
        lin = i // _STAT_COLS
        sx = pad + col * col_w_stats
        sy = y + lin * _STAT_ROW_H * s

        draw.text((sx, sy), label, font=f_stat_lbl, fill=_TEXT_MUTED)
        draw.text((sx, sy + 21 * s), valor, font=f_stat_val, fill=(255, 255, 255))

    return _finalizar_canvas(canvas)


# ──────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────

class ResumoMesCog(commands.Cog):
    """Resumo pessoal de atividade no MAL (top 3 por nota + estatísticas), por mês."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="resumo_mes",
        description="Mostra o resumo pessoal de atividade no MAL de um usuário, num mês específico."
    )
    @app_commands.describe(
        usuario="Usuário do MyAnimeList monitorado nesta guild (comece a digitar para filtrar)",
        mes="Mês a consultar no formato MM/AAAA (padrão: mês anterior)",
    )
    async def resumo_mes(self, interaction: discord.Interaction, usuario: str, mes: str | None = None):
        await interaction.response.defer()

        usuarios_guild = carregar_usuarios(interaction.guild_id)
        username_real = next((u for u in usuarios_guild if u.lower() == usuario.lower()), None)
        if username_real is None:
            await interaction.followup.send(
                f"⚠️ `{usuario}` não está na lista de monitorados desta guild. "
                f"Use `/mal_entrar` primeiro, ou confira o nome com `/mal_lista`.",
                ephemeral=True
            )
            return

        agora = datetime.now(BRASILIA)
        ano_alvo, mes_alvo = parse_mes_str(mes, agora)
        mes_chave = f"{ano_alvo:04d}-{mes_alvo:02d}"

        dados = await montar_resumo(username_real, mes_chave)
        if dados is None:
            await interaction.followup.send(
                f"📊 Nenhuma atividade de `{username_real}` registrada em **{MESES_PT[mes_alvo]} de {ano_alvo}**."
            )
            return

        avatar_bytes = carregar_avatar(username_real)
        png_bytes = gerar_imagem_resumo(username_real, avatar_bytes, mes_alvo, ano_alvo, dados)

        buffer = BytesIO(png_bytes)
        buffer.seek(0)
        await interaction.followup.send(
            file=discord.File(buffer, filename=f"resumo_{username_real}_{mes_chave}.png")
        )

    @resumo_mes.autocomplete("usuario")
    async def usuario_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Sugere os usuários monitorados na guild onde o comando está sendo digitado."""
        if interaction.guild_id is None:
            return []

        usuarios = list(carregar_usuarios(interaction.guild_id))
        busca = current.lower().strip()

        # Quem começa com o texto digitado vem primeiro, depois quem só contém.
        comecam = sorted((u for u in usuarios if u.lower().startswith(busca)), key=str.lower)
        contem = sorted(
            (u for u in usuarios if busca in u.lower() and not u.lower().startswith(busca)),
            key=str.lower,
        )

        # O Discord aceita no máximo 25 opções por vez.
        return [app_commands.Choice(name=u, value=u) for u in (comecam + contem)[:25]]


async def setup(bot: commands.Bot):
    await bot.add_cog(ResumoMesCog(bot))