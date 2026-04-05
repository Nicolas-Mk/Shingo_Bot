import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from migrations.runner import rodar_migrations

from cogs.config_cog import ConfigCog
from cogs.economy import EconomyCog
from cogs.user_profile import UserProfileCog
from cogs.games import GamesCog
from cogs.voice_xp import VoiceXPCog
from cogs.utility import UtilityCog
from cogs.mal_tracker import MalTrackerCog
from cogs.loja import LojaCog
from cogs.mal_lookup import MalLookupCog



load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True


class CustomBot(commands.Bot):
    async def setup_hook(self):
        rodar_migrations()

        await self.add_cog(ConfigCog(self))
        await self.add_cog(EconomyCog(self))
        await self.add_cog(UserProfileCog(self))
        await self.add_cog(GamesCog(self))
        await self.add_cog(VoiceXPCog(self))
        await self.add_cog(UtilityCog(self))
        await self.add_cog(MalTrackerCog(self))
        await self.add_cog(MalLookupCog(self))
        await self.add_cog(LojaCog(self))

        synced = await self.tree.sync()
        print(f"✅ {len(synced)} comandos sincronizados.")


bot = CustomBot(command_prefix='!', intents=intents)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar este comando.", ephemeral=True
        )
        print(f"[Permissão] {interaction.user} tentou usar /{interaction.command.name} sem permissão em '{interaction.guild.name}'.")
    else:
        # Outros erros continuam aparecendo no console normalmente
        print(f"[Erro] Comando /{interaction.command.name} por {interaction.user}: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Ocorreu um erro ao executar este comando.", ephemeral=True
            )


@bot.event
async def on_ready():
    print(f'🤖 Bot conectado como {bot.user}')


bot.run(TOKEN)