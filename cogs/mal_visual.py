"""
Utilitários visuais compartilhados para os cards em PNG do MAL Tracker
(estilo AniList).

Este módulo não conhece nada de Discord, MyAnimeList ou lógica de negócio —
só primitivas de desenho (fontes, máscaras, ícones, quebra de texto,
supersampling). Ele existe separado de mal_tracker_cog.py e
mal_easter_eggs.py justamente para os dois poderem compartilhar o mesmo
estilo visual sem que um dependa do outro (o que causaria import circular,
já que o cog importa o manager de easter eggs).
"""

import math
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

# ──────────────────────────────────────────────
#  Paleta e dimensões compartilhadas
# ──────────────────────────────────────────────

_FONT_DIR = "/usr/share/fonts/truetype/dejavu/"

_BG         = (13, 17, 25)
_CARD_BG    = (22, 27, 38)
_ROW_BG     = (28, 34, 48)
_TEXT_MAIN  = (222, 226, 235)
_TEXT_MUTED = (135, 143, 160)

_CARD_W    = 780
_HEADER_H  = 88
_ROW_H     = 172
_GAP       = 10
_PAD       = 16

# Proporção das capas de anime/mangá (largura / altura), igual à usada pelo
# AniList (80 x 115.97) — retrato, mais alta que larga.
_COVER_RATIO = 80 / 115.97

# Fator de supersampling: tudo é desenhado nesta escala e depois reduzido
# com LANCZOS, o que elimina o serrilhado em círculos, bordas arredondadas e texto.
_SS = 3

# Cores de destaque por tipo, usadas nos cards
COR_ANIME = (99, 155, 255)   # azul
COR_MANGA = (255, 130, 130)  # vermelho/rosa

_PLACEHOLDER_COVER  = None
_PLACEHOLDER_AVATAR = None


def _fonte(tamanho: int, negrito: bool = False) -> ImageFont.FreeTypeFont:
    nome = "DejaVuSans-Bold.ttf" if negrito else "DejaVuSans.ttf"
    return ImageFont.truetype(_FONT_DIR + nome, tamanho)


def _mascara_arredondada(tamanho: tuple[int, int], raio: int) -> Image.Image:
    mask = Image.new("L", tamanho, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tamanho[0] - 1, tamanho[1] - 1), radius=raio, fill=255)
    return mask


def _mascara_circular(tamanho: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", tamanho, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, tamanho[0] - 1, tamanho[1] - 1), fill=255)
    return mask


def _desenhar_icone_status(
    draw: ImageDraw.ImageDraw,
    tipo: str,
    cx: float,
    cy: float,
    r: float,
    cor: tuple[int, int, int],
):
    """
    Desenha um ícone de status como forma vetorial simples, centrado em (cx, cy)
    com "raio" r. Não depende de nenhuma fonte ter glifos de emoji — a maioria
    das fontes do sistema (como DejaVu Sans) não tem cobertura de emoji colorido,
    então desenhar as formas na mão garante que sempre vão aparecer corretamente.
    """
    espessura = max(2, int(r * 0.24))

    if tipo == "watching":
        # Triângulo de "play"
        draw.polygon(
            [(cx - r * 0.6, cy - r * 0.85), (cx - r * 0.6, cy + r * 0.85), (cx + r * 0.9, cy)],
            fill=cor,
        )
    elif tipo == "reading":
        # Livro aberto: retângulo com "lombada" central
        draw.rounded_rectangle(
            (cx - r * 0.95, cy - r * 0.7, cx + r * 0.95, cy + r * 0.7),
            radius=r * 0.18, outline=cor, width=espessura,
        )
        draw.line([(cx, cy - r * 0.7), (cx, cy + r * 0.7)], fill=cor, width=espessura)
    elif tipo == "completed":
        # Checkmark
        draw.line(
            [(cx - r * 0.8, cy + r * 0.05), (cx - r * 0.15, cy + r * 0.7), (cx + r * 0.9, cy - r * 0.7)],
            fill=cor, width=espessura, joint="curve",
        )
    elif tipo == "on_hold":
        # Duas barras (pausa)
        largura_barra = max(2, int(r * 0.42))
        draw.rounded_rectangle(
            (cx - r * 0.75, cy - r * 0.85, cx - r * 0.75 + largura_barra, cy + r * 0.85),
            radius=largura_barra * 0.3, fill=cor,
        )
        draw.rounded_rectangle(
            (cx + r * 0.3, cy - r * 0.85, cx + r * 0.3 + largura_barra, cy + r * 0.85),
            radius=largura_barra * 0.3, fill=cor,
        )
    elif tipo == "dropped":
        # X
        draw.line([(cx - r * 0.75, cy - r * 0.75), (cx + r * 0.75, cy + r * 0.75)], fill=cor, width=espessura)
        draw.line([(cx - r * 0.75, cy + r * 0.75), (cx + r * 0.75, cy - r * 0.75)], fill=cor, width=espessura)
    elif tipo == "plan":
        # Três linhas (fila / planejado)
        for dy in (-0.55, 0.0, 0.55):
            draw.line(
                [(cx - r * 0.85, cy + r * dy), (cx + r * 0.85, cy + r * dy)],
                fill=cor, width=max(2, int(r * 0.22)),
            )
    elif tipo == "note":
        # Estrela (mudança de nota)
        pontos = []
        for i in range(10):
            angulo = -math.pi / 2 + i * math.pi / 5
            raio = r if i % 2 == 0 else r * 0.45
            pontos.append((cx + raio * math.cos(angulo), cy + raio * math.sin(angulo)))
        draw.polygon(pontos, fill=cor)
    else:
        draw.ellipse((cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6), outline=cor, width=espessura)


def _truncar_texto(draw: ImageDraw.ImageDraw, texto: str, fonte: ImageFont.FreeTypeFont, largura_max: int) -> str:
    if draw.textlength(texto, font=fonte) <= largura_max:
        return texto
    while texto and draw.textlength(texto + "…", font=fonte) > largura_max:
        texto = texto[:-1]
    return texto + "…"


def _quebrar_texto(draw: ImageDraw.ImageDraw, texto: str, fonte: ImageFont.FreeTypeFont, largura_max: int) -> list[str]:
    """Quebra `texto` em várias linhas que cabem em `largura_max`, por palavra inteira."""
    palavras = texto.split()
    linhas: list[str] = []
    atual = ""
    for palavra in palavras:
        teste = f"{atual} {palavra}".strip()
        if draw.textlength(teste, font=fonte) <= largura_max:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _placeholder_cover() -> Image.Image:
    global _PLACEHOLDER_COVER
    if _PLACEHOLDER_COVER is None:
        _PLACEHOLDER_COVER = Image.new("RGB", (200, 200), (45, 50, 66))
    return _PLACEHOLDER_COVER


def _placeholder_avatar() -> Image.Image:
    global _PLACEHOLDER_AVATAR
    if _PLACEHOLDER_AVATAR is None:
        _PLACEHOLDER_AVATAR = Image.new("RGB", (150, 150), (60, 66, 84))
    return _PLACEHOLDER_AVATAR


def _abrir_imagem(img_bytes: bytes | None, fallback: Image.Image) -> Image.Image:
    if not img_bytes:
        return fallback
    try:
        return Image.open(BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return fallback


def _finalizar_canvas(canvas: Image.Image) -> bytes:
    """Reduz o canvas supersampleado para o tamanho final (antialiasing) e serializa em PNG."""
    tamanho_final = (canvas.width // _SS, canvas.height // _SS)
    canvas = canvas.resize(tamanho_final, Image.LANCZOS)
    buffer = BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer.getvalue()