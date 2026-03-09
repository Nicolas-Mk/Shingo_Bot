from PIL import Image, ImageDraw, ImageFont
import discord
import os

def create_text_image(texto: str, fonte_path: str = None, fonte_size: int = 40) -> discord.File:
    """
    Gera uma imagem simples (PNG) com o texto fornecido
    e retorna um 'discord.File' para envio.
    """
    # Use a default font path if not provided
    if fonte_path is None:
        # Try to find a suitable font
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "C:\\Windows\\Fonts\\arial.ttf",  # Windows
            "/System/Library/Fonts/Helvetica.ttc"  # macOS
        ]
        fonte_path = next((f for f in possible_fonts if os.path.exists(f)), None)
    
    if fonte_path is None:
        raise FileNotFoundError("Could not find a suitable font")

    fonte = ImageFont.truetype(fonte_path, fonte_size)

    # Measure text size
    imagem_temp = Image.new("RGB", (1, 1))
    draw_temp = ImageDraw.Draw(imagem_temp)
    bbox = draw_temp.textbbox((0, 0), texto, font=fonte)

    # Calculate image dimensions
    largura_texto = bbox[2] - bbox[0]
    altura_texto = bbox[3] - bbox[1]
    padding = 20
    largura_imagem = largura_texto + 2 * padding
    altura_imagem = altura_texto + 2 * padding

    # Create actual image
    imagem = Image.new("RGB", (largura_imagem, altura_imagem), color=(255, 255, 255))
    draw = ImageDraw.Draw(imagem)
    draw.text((padding, padding), texto, font=fonte, fill=(0, 0, 0))

    # Save and return as Discord file
    imagem.save("texto_impossivel.png")
    return discord.File("texto_impossivel.png", filename="texto_impossivel.png")

# Maintain backward compatibility with existing function name
def criar_imagem_texto(texto: str) -> discord.File:
    return create_text_image(texto)