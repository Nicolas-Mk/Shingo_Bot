import random
import sqlite3
import discord
from discord.ui import View, Button
from discord import Interaction
from utils.baralho import criar_baralho

def adicionar_flingers(usuario_id, quantidade):
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    c.execute("SELECT flingers FROM usuarios WHERE id = ?", (usuario_id,))
    row = c.fetchone()
    if row:
        flingers_atual = row[0] if row[0] is not None else 0
        novo_flingers = flingers_atual + quantidade
        c.execute("UPDATE usuarios SET flingers = ? WHERE id = ?", (novo_flingers, usuario_id))
        conn.commit()
    conn.close()

class BlackjackView(View):
    def __init__(self, interaction: Interaction, aposta: int):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.aposta = aposta
        self.baralho = criar_baralho()
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
        aces = 0
        for carta in hand:
            valor = carta[:-1]
            if valor in ['J', 'Q', 'K']:
                total += 10
            elif valor == 'A':
                aces += 1
                total += 11
            else:
                total += int(valor)
        while total > 21 and aces > 0:
            total -= 10
            aces -= 1
        return total

    def dealer_jogar(self, pontuacao_jogador: int):
        """
        O dealer vê a pontuação do jogador e compra cartas
        até superar essa pontuação ou estourar.
        Isso garante vantagem ao dealer, pois ele sempre
        tenta bater o jogador em vez de parar em 17 às cegas.
        """
        while True:
            dealer_total = self.calcular_pontuacao(self.dealer_hand)

            # Se já estourou, para
            if dealer_total > 21:
                break

            # Se já vence o jogador, para — não precisa arriscar mais
            if dealer_total > pontuacao_jogador:
                break

            # Se empatou ou está atrás, compra mais uma carta
            self.dealer_hand.append(self.draw_card())

    async def atualizar_mensagem(self, interaction):
        player_total = self.calcular_pontuacao(self.player_hand)
        dealer_total = self.calcular_pontuacao(self.dealer_hand)

        if self.finished:
            resultado = ""
            ganho = 0

            if player_total > 21:
                resultado = f"💥 Você estourou! Perdeu {self.aposta} flingers."
            elif dealer_total > 21:
                resultado = f"🏆 O dealer estourou! Você ganhou {self.aposta * 2} flingers!"
                ganho = self.aposta * 2
            elif player_total > dealer_total:
                resultado = f"🏆 Você venceu! Ganhou {self.aposta * 2} flingers!"
                ganho = self.aposta * 2
            elif player_total < dealer_total:
                resultado = f"❌ Você perdeu {self.aposta} flingers."
            else:
                resultado = f"🤝 Empate! Você recuperou {self.aposta} flingers."
                ganho = self.aposta

            if ganho > 0:
                adicionar_flingers(self.interaction.user.id, ganho)

            embed = discord.Embed(title="🃏 Blackjack Finalizado")
            embed.add_field(name="Sua mão", value=f"{' | '.join(self.player_hand)}\n**Total: {player_total}**", inline=True)
            embed.add_field(name="Dealer", value=f"{' | '.join(self.dealer_hand)}\n**Total: {dealer_total}**", inline=True)
            embed.set_footer(text=resultado)
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            embed = discord.Embed(title="🃏 Blackjack")
            embed.add_field(name="Sua mão", value=f"{' | '.join(self.player_hand)}\n**Total: {player_total}**", inline=True)
            embed.add_field(name="Dealer", value=f"{self.dealer_hand[0]} | ❓", inline=True)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: Interaction, button: Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Esse jogo não é seu!", ephemeral=True)
            return

        self.player_hand.append(self.draw_card())
        if self.calcular_pontuacao(self.player_hand) > 21:
            self.finished = True
            # Jogador estourou — dealer não precisa comprar, vitória automática
        await self.atualizar_mensagem(interaction)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: Interaction, button: Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("❌ Esse jogo não é seu!", ephemeral=True)
            return

        self.finished = True
        self.dealer_jogar(self.calcular_pontuacao(self.player_hand))
        await self.atualizar_mensagem(interaction)