import os
from dotenv import load_dotenv

load_dotenv()

# Configurações gerais do bot
BOT_CONFIG = {
    'TOKEN': os.getenv('DISCORD_TOKEN'),
    'GIPHY_API_KEY': os.getenv('GIPHY_API_KEY'),  # Ensure this is set in your .env file
    'WEATHER_API_KEY': os.getenv('WEATHER_API_KEY'),
    'ECONOMY_CHANNEL_ID': 1023331057707257927,
    'DEFAULT_XP_GAIN': 5,
    'XP_COOLDOWN': 300,  # 5 minutos
    'FIXED_REWARD': 10,

}

def is_weather_api_configured():
    """
    Retorna True se a chave WEATHER_API_KEY estiver configurada
    e não estiver vazia.
    """
    return BOT_CONFIG['WEATHER_API_KEY'] is not None and BOT_CONFIG['WEATHER_API_KEY'].strip() != ''