import random
import asyncio
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import sqlite3
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from config import BOT_CONFIG
from utils.image_generator import criar_imagem_texto
from database.db_manager import UserManager

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.economy_active_text = None
        self.economy_can_win = False
        self.economy_reward = 0
        self.uso_giphy = defaultdict(list)
        self.ultimo_trabalho = {}
        self.gerador_ativo = True
        self.recent_messages = []  # Lista de timestamps das mensagens no canal de economia
        self.economy_channel_id = BOT_CONFIG['ECONOMY_CHANNEL_ID']
        self.contador_economia_loop.start()  # Inicia o loop fixo

    def gerar_texto_aleatorio(self):
        base = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        acentos = {
            "A": "ÁÃÂÀ", "E": "ÉÊÈ", "I": "ÍÎÌ",
            "O": "ÓÕÔÒ", "U": "ÚÛÙ"
        }
        especiais = "!@#$%&*()"

        def gerar_com_acentos(tamanho):
            texto = ""
            for _ in range(tamanho):
                letra = random.choice(base)
                if letra in acentos and random.random() < 0.5:
                    letra = random.choice(acentos[letra])
                elif random.random() < 0.2:
                    letra = random.choice(especiais)
                texto += letra
            return texto

        tipos = [
            {"nivel": "fácil", "chance": 0.50, "tamanho": 5, "recompensa": 3, "caracteres": base},
            {"nivel": "médio", "chance": 0.40, "tamanho": 12, "recompensa": 5, "caracteres": base},
            {"nivel": "difícil", "chance": 0.09, "tamanho": 25, "recompensa": 10, "caracteres": base},
            {"nivel": "impossível", "chance": 0.01, "tamanho": 25, "recompensa": 50, "caracteres": "custom_acentuado"}
        ]

        escolha = random.choices(tipos, weights=[t["chance"] for t in tipos])[0]

        if escolha["caracteres"] == "custom_acentuado":
            texto = gerar_com_acentos(escolha["tamanho"])
        else:
            texto = "".join(random.choice(escolha["caracteres"]) for _ in range(escolha["tamanho"]))

        return texto, escolha["recompensa"], escolha["nivel"]

    @tasks.loop(minutes=20)
    async def contador_economia_loop(self):
        if not self.gerador_ativo:
            return

        # Reseta qualquer desafio pendente
        self.economy_can_win = False
        self.economy_active_text = None
        self.economy_reward = 0

        agora = datetime.now(timezone.utc)
        limite = agora - timedelta(minutes=30)

        # Limpa mensagens antigas e conta as dos últimos 30 minutos
        self.recent_messages = [ts for ts in self.recent_messages if ts >= limite]
        
        if len(self.recent_messages) >= 20:
            texto, recompensa, nivel = self.gerar_texto_aleatorio()
            self.economy_active_text = texto
            self.economy_reward = recompensa
            self.economy_can_win = True

            file_image = criar_imagem_texto(texto)
            channel = self.bot.get_channel(self.economy_channel_id)

            if channel:
                await channel.send(
                    f"**Desafio de digitação!** (Nível: {nivel})\n"
                    f"Digite exatamente o texto (pode ser maiúsculas ou minúsculas) para ganhar {recompensa} flingers!\n",
                    file=file_image
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Conta apenas mensagens no canal de economia como atividade
        if message.channel.id == self.economy_channel_id:
            self.recent_messages.append(datetime.now(timezone.utc))

            # Verifica se a mensagem é a resposta correta (só no canal de economia)
            if self.economy_can_win and self.economy_active_text is not None:
                if message.content.strip().lower() == self.economy_active_text.lower():
                    self.economy_can_win = False
                    self.economy_active_text = None  # Limpa para evitar ganhos duplicados
                    UserManager.adicionar_flingers(message.author.id, self.economy_reward)
                    await message.channel.send(
                        f"🎉 {message.author.mention} digitou primeiro o texto correto e ganhou {self.economy_reward} flingers!"
                    )

    @app_commands.command(name="topflingers", description="Veja os usuários com mais flingers")
    async def top_flingers(self, interaction: discord.Interaction):
        conn = sqlite3.connect("usuarios.db")
        c = conn.cursor()
        c.execute("SELECT nome, discriminator, flingers FROM usuarios ORDER BY flingers DESC LIMIT 10")
        top = c.fetchall()
        conn.close()

        if not top:
            await interaction.response.send_message("⚠️ Ninguém possui flingers ainda!", ephemeral=True)
            return

        embed = discord.Embed(
            title="💰 Top 10 Usuários com mais Flingers",
            color=discord.Color.gold()
        )

        for i, (nome, discriminator, flingers) in enumerate(top, start=1):
            embed.add_field(
                name=f"#{i} - {nome}#{discriminator}",
                value=f"💵 {flingers} flingers",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="trabalhar", description="Trabalhe e ganhe de 1 a 5 flingers (10min de cooldown)")
    async def trabalhar(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        agora = datetime.now(timezone.utc)
        anterior = self.ultimo_trabalho.get(user_id)

        if anterior and (agora - anterior).total_seconds() < 600:
            restante = 600 - int((agora - anterior).total_seconds())
            minutos = restante // 60
            segundos = restante % 60
            return await interaction.response.send_message(
                f"⏳ Você está cansado! Tente novamente em {minutos}m{segundos}s.",
                ephemeral=True
            )

        ganho = random.randint(1, 5)
        UserManager.adicionar_flingers(user_id, ganho)
        self.ultimo_trabalho[user_id] = agora

        await interaction.response.send_message(
            f"🛠️ {interaction.user.mention}, você trabalhou duro e ganhou **{ganho} flingers!**"
        )

    @app_commands.command(name="gerador", description="Habilita ou desabilita o gerador de texto aleatório")
    async def toggle_gerador(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Você precisa ter permissão de administrador para usar este comando!",
                ephemeral=True
            )
            return

        self.gerador_ativo = not self.gerador_ativo
        
        if self.gerador_ativo:
            await interaction.response.send_message(
                "✅ **Gerador de texto aleatório habilitado!**\n"
                "O gerador verificará a cada 20 minutos se há atividade suficiente.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "⏸️ **Gerador de texto aleatório desabilitado!**\n"
                "Use o comando novamente para reativar.",
                ephemeral=True
            )

    @app_commands.command(name="status_gerador", description="Mostra o status atual do gerador de texto")
    async def status_gerador(self, interaction: discord.Interaction):
        status = "🟢 **ATIVO**" if self.gerador_ativo else "🔴 **DESABILITADO**"
        
        embed = discord.Embed(
            title="📊 Status do Gerador de Texto",
            color=discord.Color.green() if self.gerador_ativo else discord.Color.red()
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Intervalo", value="A cada 20 minutos", inline=True)
        embed.add_field(name="Condição para enviar", value="≥ 20 mensagens nos últimos 30 min", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))