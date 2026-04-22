from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import discord
import os

def create_text_image(texto: str, fonte_path: str = None, fonte_size: int = 40) -> discord.File:
    """
    Gera uma imagem PNG em memória com o texto fornecido
    e retorna um discord.File. Não escreve nada em disco.
    """
    if fonte_path is None:
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "C:\\Windows\\Fonts\\arial.ttf",               # Windows
            "/System/Library/Fonts/Helvetica.ttc"               # macOS
        ]
        fonte_path = next((f for f in possible_fonts if os.path.exists(f)), None)

    if fonte_path is None:
        raise FileNotFoundError("Could not find a suitable font")

    fonte = ImageFont.truetype(fonte_path, fonte_size)

    imagem_temp = Image.new("RGB", (1, 1))
    draw_temp   = ImageDraw.Draw(imagem_temp)
    bbox        = draw_temp.textbbox((0, 0), texto, font=fonte)

    largura_texto  = bbox[2] - bbox[0]
    altura_texto   = bbox[3] - bbox[1]
    padding        = 20
    largura_imagem = largura_texto + 2 * padding
    altura_imagem  = altura_texto  + 2 * padding

    imagem = Image.new("RGB", (largura_imagem, altura_imagem), color=(255, 255, 255))
    draw   = ImageDraw.Draw(imagem)
    draw.text((padding, padding), texto, font=fonte, fill=(0, 0, 0))

    buffer = BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename="desafio.png")


def criar_imagem_texto(texto: str) -> discord.File:
    return create_text_image(texto)