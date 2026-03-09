import aiohttp
import discord
from config import BOT_CONFIG, is_weather_api_configured

async def get_weather(cidade: str):
    """
    Busca informações de clima para uma cidade.
    
    Args:
        cidade (str): Nome da cidade
    
    Returns:
        tuple: Uma tupla contendo (embed, error)
               - Se sucesso: (discord.Embed, None)
               - Se falha: (None, mensagem de erro)
    """
    # Verifica se a API está configurada
    if not is_weather_api_configured():
        return None, "API de clima não configurada. Configure a chave WEATHER_API_KEY no .env."

    url = f"https://api.openweathermap.org/data/2.5/weather?q={cidade}&appid={BOT_CONFIG['WEATHER_API_KEY']}&units=metric&lang=pt_br"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()

                if resp.status == 200:
                    temp = data['main']['temp']
                    desc = data['weather'][0]['description'].capitalize()
                    nome_cidade = data['name']
                    pais = data['sys']['country']
                    
                    embed = discord.Embed(
                        title=f"🌤️ Clima em {nome_cidade}, {pais}",
                        description=f"📋 {desc}\n🌡️ {temp:.1f}°C",
                        color=discord.Color.teal()
                    )
                    return embed, None
                else:
                    erro = data.get("message", "Erro desconhecido")
                    return None, f'Não consegui encontrar a cidade "{cidade}". Erro: {erro}'
    
    except Exception as e:
        return None, f"Erro ao buscar informações de clima: {str(e)}"