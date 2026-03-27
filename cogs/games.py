import discord
from discord import app_commands
from discord.ext import commands
from views.blackjack_view import BlackjackView
import sqlite3
import time


class GamesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ultimo_blackjack = {}

    @app_commands.command(name="blackjack", description="Jogue blackjack contra o bot apostando flingers!")
    @app_commands.describe(valor="Quantidade de flingers para apostar")
    async def blackjack(self, interaction: discord.Interaction, valor: int):
        user_id  = interaction.user.id
        guild_id = interaction.guild_id
        agora    = time.time()

        key    = (user_id, guild_id)
        ultimo = self.ultimo_blackjack.get(key)
        if ultimo and agora - ultimo < 300:
            restante = int(300 - (agora - ultimo))
            minutos  = restante // 60
            segundos = restante % 60
            await interaction.response.send_message(
                f"⏳ Espere {minutos}m{segundos}s antes de jogar novamente.",
                ephemeral=True
            )
            return

        if valor <= 0:
            await interaction.response.send_message("❌ Aposta inválida. Escolha um valor maior que zero.", ephemeral=True)
            return

        conn = sqlite3.connect("usuarios.db")
        c    = conn.cursor()
        c.execute(
            "SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?",
            (user_id, guild_id)
        )
        row = c.fetchone()
        conn.close()

        if not row or row[0] is None or row[0] < valor:
            await interaction.response.send_message("❌ Você não tem flingers suficientes para essa aposta.", ephemeral=True)
            return

        await interaction.response.defer()

        conn = sqlite3.connect("usuarios.db")
        c    = conn.cursor()
        c.execute(
            "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ?",
            (valor, user_id, guild_id)
        )
        conn.commit()
        conn.close()

        self.ultimo_blackjack[key] = agora

        view         = BlackjackView(interaction, aposta=valor, guild_id=guild_id)
        player_total = view.calcular_pontuacao(view.player_hand)

        embed = discord.Embed(title="🃏 Blackjack")
        embed.add_field(name="Sua mão", value=f"{' | '.join(view.player_hand)}\n**Total: {player_total}**", inline=True)
        embed.add_field(name="Dealer",  value=f"{view.dealer_hand[0]} | ❓", inline=True)

        try:
            await interaction.followup.send(
                f"🎲 Apostando **{valor} flingers**...\nBoa sorte!",
                embed=embed,
                view=view,
            )
        except Exception as e:
            conn = sqlite3.connect("usuarios.db")
            c    = conn.cursor()
            c.execute(
                "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                (valor, user_id, guild_id)
            )
            conn.commit()
            conn.close()
            self.ultimo_blackjack.pop(key, None)
            print(f"[Blackjack] Erro ao enviar mensagem, flingers devolvidos para {interaction.user}: {e}")


async def setup(bot):
    await bot.add_cog(GamesCog(bot))