import random
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from config import BOT_CONFIG

# Termos que NÃO podem aparecer no título/tags do gif escolhido para cada período
BLOCKLIST = {
    "bom dia":   ["boa tarde", "boa noite", "good afternoon", "good night", "goodnight", "afternoon"],
    "boa tarde": ["bom dia", "boa noite", "good morning", "good night", "goodnight", "morning"],
    "boa noite": ["bom dia", "boa tarde", "good morning", "good afternoon", "morning", "afternoon"],
}


class GiphyHandler:
    def __init__(self):
        self.usage_tracker: Dict[int, List[datetime]] = {}

    async def get_giphy_gif(self, query: str, user_id: int, max_gifs: int = 25) -> Optional[str]:
        """
        Fetch a random GIF from Giphy for a given query with usage tracking,
        filtrando resultados cujo título/tags pertençam a outro período do dia.

        :param query: Search term for the GIF
        :param user_id: Discord user ID for tracking usage
        :param max_gifs: Quantos candidatos buscar antes de filtrar (default 25)
        :return: URL of a random GIF or None
        """
        if not BOT_CONFIG.get('GIPHY_API_KEY'):
            return None

        current_time = datetime.now()
        user_usage = self.usage_tracker.get(user_id, [])
        user_usage = [t for t in user_usage if current_time - t < timedelta(hours=1)]

        if len(user_usage) >= 5:
            return None

        user_usage.append(current_time)
        self.usage_tracker[user_id] = user_usage

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    'https://api.giphy.com/v1/gifs/search',
                    params={
                        'api_key': BOT_CONFIG['GIPHY_API_KEY'],
                        'q': query,
                        'limit': max_gifs,
                        'offset': 0,          # sem offset aleatório: evita listas vazias
                        'rating': 'g',
                        'lang': 'pt'
                    }
                ) as resp:
                    data = await resp.json()
                    gifs = data.get('data', [])

                    if not gifs:
                        return None

                    termos_proibidos = BLOCKLIST.get(query.lower(), [])
                    candidatos_validos = []

                    for gif in gifs:
                        titulo = (gif.get('title') or '').lower()
                        slug   = (gif.get('slug') or '').lower()
                        texto_busca = f"{titulo} {slug}"

                        if any(termo in texto_busca for termo in termos_proibidos):
                            continue
                        candidatos_validos.append(gif)

                    # se o filtro zerou tudo, cai pro pool bruto (evita nunca responder)
                    pool = candidatos_validos if candidatos_validos else gifs

                    return random.choice(pool)['images']['original']['url']
            except Exception as e:
                print(f"Giphy API error: {e}")
                return None


# Create a singleton instance
giphy_handler = GiphyHandler()

async def get_giphy_gif(query: str, user_id: int, max_gifs: int = 25) -> Optional[str]:
    return await giphy_handler.get_giphy_gif(query, user_id, max_gifs)