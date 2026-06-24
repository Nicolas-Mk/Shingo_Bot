import discord
from discord import app_commands
from discord.ext import commands
from views.blackjack_view import BlackjackView, is_blackjack_natural
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

        # Verificação e débito na mesma transação — evita race condition
        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ? AND flingers >= ?",
                (valor, user_id, guild_id, valor)
            )
            atualizado = c.rowcount
            conn.commit()

        if atualizado == 0:
            await interaction.response.send_message("❌ Você não tem flingers suficientes para essa aposta.", ephemeral=True)
            return

        await interaction.response.defer()

        self.ultimo_blackjack[key] = agora

        view = BlackjackView(interaction, aposta=valor, guild_id=guild_id)

        # Resolução imediata: se qualquer lado tem blackjack natural, encerra sem mostrar botões
        if is_blackjack_natural(view.player_hand) or is_blackjack_natural(view.dealer_hand):
            view.finished = True
            # dealer_jogar não é necessário — resultado já decidido pelas naturais
            player_total   = view.calcular_pontuacao(view.player_hand)
            dealer_total   = view.calcular_pontuacao(view.dealer_hand)
            player_natural = is_blackjack_natural(view.player_hand)
            dealer_natural = is_blackjack_natural(view.dealer_hand)

            if player_natural and dealer_natural:
                ganho     = valor
                resultado = f"🤝 Ambos com blackjack natural! Empate — você recuperou {valor} flingers."
            elif player_natural:
                ganho     = int(valor * 2.5)
                resultado = f"🃏 Blackjack natural! Você ganhou {ganho} flingers!"
            else:
                ganho     = 0
                resultado = f"❌ Dealer tem blackjack natural! Você perdeu {valor} flingers."

            if ganho > 0:
                from views.blackjack_view import adicionar_flingers
                adicionar_flingers(user_id, guild_id, ganho)

            embed = discord.Embed(title="🃏 Blackjack Finalizado")
            embed.add_field(
                name="Sua mão",
                value=f"{' | '.join(view.player_hand)}\n**Total: {player_total}**{'  🃏' if player_natural else ''}",
                inline=True
            )
            embed.add_field(
                name="Dealer",
                value=f"{' | '.join(view.dealer_hand)}\n**Total: {dealer_total}**{'  🃏' if dealer_natural else ''}",
                inline=True
            )
            embed.set_footer(text=resultado)

            try:
                await interaction.followup.send(
                    f"🎲 Apostando **{valor} flingers**...",
                    embed=embed,
                )
            except Exception as e:
                with sqlite3.connect("usuarios.db") as conn:
                    c = conn.cursor()
                    c.execute(
                        "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                        (valor, user_id, guild_id)
                    )
                    conn.commit()
                self.ultimo_blackjack.pop(key, None)
                print(f"[Blackjack] Erro ao enviar mensagem (natural), flingers devolvidos para {interaction.user}: {e}")
            return

        # Jogo normal — exibe mão inicial com botões
        player_total = view.calcular_pontuacao(view.player_hand)

        embed = discord.Embed(title="🃏 Blackjack")
        embed.add_field(
            name="Sua mão",
            value=f"{' | '.join(view.player_hand)}\n**Total: {player_total}**",
            inline=True
        )
        embed.add_field(
            name="Dealer",
            value=f"{view.dealer_hand[0]} | ❓\n*({len(view.dealer_hand)} cartas)*",
            inline=True
        )

        try:
            await interaction.followup.send(
                f"🎲 Apostando **{valor} flingers**...\nBoa sorte!",
                embed=embed,
                view=view,
            )
        except Exception as e:
            with sqlite3.connect("usuarios.db") as conn:
                c = conn.cursor()
                c.execute(
                    "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                    (valor, user_id, guild_id)
                )
                conn.commit()
            self.ultimo_blackjack.pop(key, None)
            print(f"[Blackjack] Erro ao enviar mensagem, flingers devolvidos para {interaction.user}: {e}")


async def setup(bot):
    await bot.add_cog(GamesCog(bot))