import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database.db_manager import UserManager

from cogs.economy import EconomyCog
from cogs.user_profile import UserProfileCog
from cogs.games import GamesCog
from cogs.voice_xp import VoiceXPCog
from cogs.utility import UtilityCog
from cogs.mal_tracker import MalTrackerCog


load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

class CustomBot(commands.Bot):
    async def setup_hook(self):
        # Criar e atualizar banco de dados
        UserManager.criar_tabela()
        UserManager.atualizar_tabela()

        # Adicionar Cogs
        await self.add_cog(EconomyCog(self))
        await self.add_cog(UserProfileCog(self))
        await self.add_cog(GamesCog(self))
        await self.add_cog(VoiceXPCog(self))
        await self.add_cog(UtilityCog(self))
        await self.add_cog(MalTrackerCog(self))

        await self.tree.sync()

bot = CustomBot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'🤖 Bot conectado como {bot.user}')

bot.run(TOKEN)