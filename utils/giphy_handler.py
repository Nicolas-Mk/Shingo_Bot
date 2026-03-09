import random
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from config import BOT_CONFIG

class GiphyHandler:
    def __init__(self):
        self.usage_tracker: Dict[int, List[datetime]] = {}

    async def get_giphy_gif(self, query: str, user_id: int, max_gifs: int = 10) -> Optional[str]:
        """
        Fetch a random GIF from Giphy for a given query with usage tracking
        
        :param query: Search term for the GIF
        :param user_id: Discord user ID for tracking usage
        :param max_gifs: Maximum number of GIFs to select from
        :return: URL of a random GIF or None
        """
        # Check Giphy API key
        if not BOT_CONFIG.get('GIPHY_API_KEY'):
            return None

        # Track and limit Giphy usage
        current_time = datetime.now()
        user_usage = self.usage_tracker.get(user_id, [])
        
        # Remove entries older than 1 hour
        user_usage = [t for t in user_usage if current_time - t < timedelta(hours=1)]
        
        # Check if user has exceeded 5 Giphy calls in an hour
        if len(user_usage) >= 5:
            return None

        # Record this usage
        user_usage.append(current_time)
        self.usage_tracker[user_id] = user_usage

        # Fetch GIFs from Giphy
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    'https://api.giphy.com/v1/gifs/search',
                    params={
                        'api_key': BOT_CONFIG['GIPHY_API_KEY'],
                        'q': query,
                        'limit': max_gifs,
                        'offset': random.randint(0, 50),
                        'rating': 'g',
                        'lang': 'pt'
                    }
                ) as resp:
                    data = await resp.json()
                    gifs = data.get('data', [])
                    
                    if gifs:
                        # Return URL of a random GIF
                        return random.choice(gifs)['images']['original']['url']
                    return None
            except Exception as e:
                print(f"Giphy API error: {e}")
                return None

# Create a singleton instance
giphy_handler = GiphyHandler()

# Convenience function for direct import
async def get_giphy_gif(query: str, user_id: int, max_gifs: int = 10) -> Optional[str]:
    return await giphy_handler.get_giphy_gif(query, user_id, max_gifs)