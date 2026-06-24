import sqlite3
import discord
from discord.ui import View, Button
from discord import Interaction
from utils.baralho import criar_baralho


def adicionar_flingers(usuario_id: int, guild_id: int, quantidade: int):
    conn = sqlite3.connect('usuarios.db')
    c    = conn.cursor()

    c.execute(
        "SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?",
        (usuario_id, guild_id)
    )

    row = c.fetchone()

    if row:
        novo_flingers = (row[0] or 0) + quantidade

        c.execute(
            "UPDATE usuarios SET flingers = ? WHERE id = ? AND guild_id = ?",
            (novo_flingers, usuario_id, guild_id)
        )

        conn.commit()

    conn.close()


def is_blackjack_natural(hand: list[str]) -> bool:
    """
    Retorna True se a mão for um blackjack natural
    (Ás + carta de valor 10, com exatamente 2 cartas).
    """

    if len(hand) != 2:
        return False

    valores = [c[:-1] for c in hand]

    tem_as  = "A" in valores
    tem_dez = any(v in {"10", "J", "Q", "K"} for v in valores)

    return tem_as and tem_dez


class BlackjackView(View):
    def __init__(self, interaction: Interaction, aposta: int, guild_id: int):

        # 3 minutos
        super().__init__(timeout=180)

        self.interaction = interaction
        self.aposta      = aposta
        self.guild_id    = guild_id

        self.baralho     = criar_baralho()

        self.player_hand = self.draw_hand()
        self.dealer_hand = self.draw_hand()

        self.finished = False

    def draw_card(self):
        if not self.baralho:
            raise ValueError("O baralho acabou!")

        return self.baralho.pop()

    def draw_hand(self):
        return [self.draw_card(), self.draw_card()]

    def calcular_pontuacao(self, hand):

        total = 0
        aces  = 0

        for carta in hand:

            valor = carta[:-1]

            if valor in ['J', 'Q', 'K']:
                total += 10

            elif valor == 'A':
                aces  += 1
                total += 11

            else:
                total += int(valor)

        while total > 21 and aces > 0:
            total -= 10
            aces  -= 1

        return total

    def dealer_deve_comprar(self) -> bool:
        """
        Regra padrão S17:
        dealer compra até 16 e para em 17+.
        """

        return self.calcular_pontuacao(self.dealer_hand) < 17

    def dealer_jogar(self):

        while self.dealer_deve_comprar():
            self.dealer_hand.append(self.draw_card())

    async def on_timeout(self):

        if self.finished:
            return

        self.finished = True

        # desativa botões
        for item in self.children:
            item.disabled = True

        # dealer joga normalmente
        self.dealer_jogar()

        player_total = self.calcular_pontuacao(self.player_hand)
        dealer_total = self.calcular_pontuacao(self.dealer_hand)

        embed = discord.Embed(
            title="🃏 Blackjack Finalizado"
        )

        embed.add_field(
            name="Sua mão",
            value=f"{' | '.join(self.player_hand)}\n**Total: {player_total}**",
            inline=True
        )

        embed.add_field(
            name="Dealer",
            value=f"{' | '.join(self.dealer_hand)}\n**Total: {dealer_total}**",
            inline=True
        )

        embed.set_footer(
            text=f"⏰ Você demorou mais de 3 minutos para jogar e perdeu {self.aposta} flingers."
        )

        try:
            await self.interaction.edit_original_response(
                embed=embed,
                view=None
            )

        except Exception as e:
            print(f"[Blackjack Timeout] Erro ao finalizar partida: {e}")

    async def atualizar_mensagem(self, interaction: Interaction):

        player_total = self.calcular_pontuacao(self.player_hand)
        dealer_total = self.calcular_pontuacao(self.dealer_hand)

        if self.finished:

            ganho = 0

            player_natural = is_blackjack_natural(self.player_hand)
            dealer_natural = is_blackjack_natural(self.dealer_hand)

            if player_total > 21:

                resultado = (
                    f"💥 Você estourou! "
                    f"Perdeu {self.aposta} flingers."
                )

            elif dealer_total > 21:

                if player_natural:

                    ganho = int(self.aposta * 2.5)

                    resultado = (
                        f"🃏 Blackjack natural! "
                        f"O dealer estourou! "
                        f"Você ganhou {ganho} flingers!"
                    )

                else:

                    ganho = self.aposta * 2

                    resultado = (
                        f"🏆 O dealer estourou! "
                        f"Você ganhou {ganho} flingers!"
                    )

            elif player_natural and dealer_natural:

                ganho = self.aposta

                resultado = (
                    f"🤝 Ambos com blackjack natural! "
                    f"Empate — você recuperou "
                    f"{self.aposta} flingers."
                )

            elif player_natural and not dealer_natural:

                ganho = int(self.aposta * 2.5)

                resultado = (
                    f"🃏 Blackjack natural! "
                    f"Você ganhou {ganho} flingers!"
                )

            elif dealer_natural and not player_natural:

                resultado = (
                    f"❌ Dealer tem blackjack natural! "
                    f"Você perdeu {self.aposta} flingers."
                )

            elif player_total > dealer_total:

                ganho = self.aposta * 2

                resultado = (
                    f"🏆 Você venceu! "
                    f"Ganhou {ganho} flingers!"
                )

            elif player_total < dealer_total:

                resultado = (
                    f"❌ Você perdeu "
                    f"{self.aposta} flingers."
                )

            else:

                ganho = self.aposta

                resultado = (
                    f"🤝 Empate! "
                    f"Você recuperou {self.aposta} flingers."
                )

            if ganho > 0:
                adicionar_flingers(
                    self.interaction.user.id,
                    self.guild_id,
                    ganho
                )

            embed = discord.Embed(
                title="🃏 Blackjack Finalizado"
            )

            embed.add_field(
                name="Sua mão",
                value=(
                    f"{' | '.join(self.player_hand)}\n"
                    f"**Total: {player_total}**"
                    f"{'  🃏' if player_natural else ''}"
                ),
                inline=True
            )

            embed.add_field(
                name="Dealer",
                value=(
                    f"{' | '.join(self.dealer_hand)}\n"
                    f"**Total: {dealer_total}**"
                    f"{'  🃏' if dealer_natural else ''}"
                ),
                inline=True
            )

            embed.set_footer(text=resultado)

            for item in self.children:
                item.disabled = True

            await interaction.response.defer()

            await interaction.message.edit(
                embed=embed,
                view=None
            )

        else:

            embed = discord.Embed(title="🃏 Blackjack")

            embed.add_field(
                name="Sua mão",
                value=(
                    f"{' | '.join(self.player_hand)}\n"
                    f"**Total: {player_total}**"
                ),
                inline=True
            )

            embed.add_field(
                name="Dealer",
                value=(
                    f"{self.dealer_hand[0]} | ❓\n"
                    f"*({len(self.dealer_hand)} cartas)*"
                ),
                inline=True
            )

            embed.set_footer(
                text="⏳ Você tem 3 minutos para jogar."
            )

            await interaction.response.defer()

            await interaction.message.edit(
                embed=embed,
                view=self
            )

    @discord.ui.button(
        label="Hit",
        style=discord.ButtonStyle.primary
    )
    async def hit(
        self,
        interaction: Interaction,
        button: Button
    ):

        if interaction.user != self.interaction.user:

            await interaction.response.send_message(
                "❌ Esse jogo não é seu!",
                ephemeral=True
            )

            return

        self.player_hand.append(self.draw_card())

        player_total = self.calcular_pontuacao(
            self.player_hand
        )

        if player_total > 21:
            self.finished = True

        await self.atualizar_mensagem(interaction)

    @discord.ui.button(
        label="Stand",
        style=discord.ButtonStyle.secondary
    )
    async def stand(
        self,
        interaction: Interaction,
        button: Button
    ):

        if interaction.user != self.interaction.user:

            await interaction.response.send_message(
                "❌ Esse jogo não é seu!",
                ephemeral=True
            )

            return

        self.finished = True

        self.dealer_jogar()

        await self.atualizar_mensagem(interaction)