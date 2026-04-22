# python
import os
import re
import random
import asyncio
from io import BytesIO
from datetime import datetime, timedelta
from typing import Dict, Set
import sqlite3

import discord
from discord.ext import commands, tasks
from discord import app_commands
from PIL import Image, ImageEnhance

import aiohttp
from utils.giphy_handler import get_giphy_gif
from utils.weather_handler import get_weather
from cogs.config_cog import get_config
from database.db_manager import UserManager

RECEBA_EMOJI_ID   = 1385739583861428345
MENSAGEM_AUTOR_ID = 273325876530380800
X_LINK_REGEX      = re.compile(r"https?://(www\.)?(x\.com|twitter\.com)/\S+", re.IGNORECASE)

ASSET_IMAGE_PATH  = os.path.join("assets", "cuidado.png")
PEOPLE_THRESHOLD  = 6        # notifica quando a call atinge esse número
CHANNEL_COOLDOWN_MIN = 60    # cooldown por canal de voz (minutos)
USUARIO_OBRIGATORIO = 275940282405617665  # deve estar na call para o aviso ser enviado


class UtilityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.uso_giphy: Dict[int, list[datetime]]  = {}
        self.receba_usos: Set[int]                 = set()
        self._last_sent_per_voice: Dict[int, datetime] = {}
        self.scan_calls.start()

    def cog_unload(self):
        self.scan_calls.cancel()

    def converter_x_para_fixup(self, texto: str) -> str:
        return re.sub(
            r"https?://(www\.)?(x\.com|twitter\.com)/",
            "https://fixupx.com/",
            texto,
            flags=re.IGNORECASE
        )

    # ──────────────────────────────────────────
    #  MONITORAMENTO DE CALL
    # ──────────────────────────────────────────

    def _can_send_for_channel(self, voice_channel_id: int) -> bool:
        last = self._last_sent_per_voice.get(voice_channel_id)
        if not last:
            return True
        return datetime.now() - last >= timedelta(minutes=CHANNEL_COOLDOWN_MIN)

    def _mark_sent(self, voice_channel_id: int):
        self._last_sent_per_voice[voice_channel_id] = datetime.now()

    async def _maybe_send_image_for_full_call(self, channel: discord.VoiceChannel):
        """Notifica o canal de texto configurado quando uma call enche."""
        canal_id = get_config(channel.guild.id, "canal_call")
        if not canal_id:
            return

        try:
            member_count = len([m for m in channel.members if not m.bot])
        except Exception:
            member_count = len(channel.members)

        if member_count >= PEOPLE_THRESHOLD and self._can_send_for_channel(channel.id):
            usuario_presente = any(m.id == USUARIO_OBRIGATORIO for m in channel.members)
            if not usuario_presente:
                return
            text_ch = channel.guild.get_channel(canal_id)

            if isinstance(text_ch, discord.TextChannel):
                try:
                    if not os.path.isfile(ASSET_IMAGE_PATH):
                        await text_ch.send(
                            f"📞 O canal de voz **{channel.name}** está com {member_count} pessoas!"
                        )
                    else:
                        await text_ch.send(
                            f"📞 O canal de voz **{channel.name}** está com {member_count} pessoas!",
                            file=discord.File(ASSET_IMAGE_PATH)
                        )
                    self._mark_sent(channel.id)
                except discord.Forbidden:
                    print(f"[WARN] Sem permissão para enviar mensagem em #{text_ch} (guild {channel.guild.name})")
                except Exception as e:
                    print(f"[ERRO] Não consegui enviar no canal de texto: {e}")
            else:
                print(f"[WARN] Canal de call {canal_id} não encontrado na guild {channel.guild.name}")

    @tasks.loop(seconds=60)
    async def scan_calls(self):
        for guild in self.bot.guilds:
            for ch in guild.voice_channels:
                await self._maybe_send_image_for_full_call(ch)

    @scan_calls.before_loop
    async def before_scan_calls(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if after and after.channel:
            await self._maybe_send_image_for_full_call(after.channel)

    # ──────────────────────────────────────────
    #  LISTENERS
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if X_LINK_REGEX.search(message.content):
            try:
                novo_link            = self.converter_x_para_fixup(message.content)
                usuarios_mencionados = message.mentions
                await message.delete()

                resposta = (
                    f"🚨 {message.author.mention}, **aprende a mandar link direito, animal.**\n"
                    f"Isso aqui melhora a visualização no Discord, não custa nada:\n\n"
                    f"{novo_link}"
                )
                if usuarios_mencionados:
                    mencoes   = " ".join([u.mention for u in usuarios_mencionados])
                    resposta += f"\n\n**Usuários mencionados:** {mencoes}"

                await message.channel.send(resposta)
            except discord.Forbidden:
                print("[WARN] Sem permissão para apagar mensagem ou enviar resposta.")
            except Exception as e:
                print(f"[ERRO] Falha ao processar link do X: {e}")
            return

        conteudo = message.content.lower()
        if "bom dia" in conteudo:
            await self.responder_cumprimento(message.author, message.channel, "bomdia")
        elif "boa tarde" in conteudo:
            await self.responder_cumprimento(message.author, message.channel, "boatarde")
        elif "boa noite" in conteudo:
            await self.responder_cumprimento(message.author, message.channel, "boanoite")

    async def responder_cumprimento(self, user, canal, tipo_comando):
        hora  = datetime.now().hour
        agora = datetime.now()
        uso   = self.uso_giphy.get(user.id, [])
        self.uso_giphy[user.id] = [t for t in uso if agora - t < timedelta(hours=1)]

        if len(self.uso_giphy[user.id]) >= 5:
            await canal.send(
                f"⚠️ {user.mention}, você já usou comandos GIPHY 5 vezes na última hora. Tente novamente depois ⏳"
            )
            return

        self.uso_giphy[user.id].append(agora)

        if 0 <= hora < 5:
            await canal.send(f"🛌 {user.mention}, vai dormir! Ainda é de madrugada...")
            return

        if 5 <= hora < 12:
            periodo_real = "bomdia"
            mensagem     = f"🌞 Bom dia, {user.mention}!"
            query        = "bom dia"
        elif 12 <= hora < 18:
            periodo_real = "boatarde"
            mensagem     = f"🌤️ Boa tarde, {user.mention}!"
            query        = "boa tarde"
        else:
            periodo_real = "boanoite"
            mensagem     = f"🌙 Boa noite, {user.mention}!"
            query        = "boa noite"

        respostas_erradas = {
            ("bomdia",   "boatarde"): "🌤️ Bom dia nada, já é de tarde!",
            ("bomdia",   "boanoite"): "🌙 Bom dia nada, já escureceu!",
            ("boatarde", "bomdia"):   "🌞 Boa tarde? Tá cedo, bom dia ainda!",
            ("boatarde", "boanoite"): "🌙 Boa tarde nada, já é de noite!",
            ("boanoite", "bomdia"):   "🌞 Boa noite? O sol tá brilhando!",
            ("boanoite", "boatarde"): "🌤️ Boa noite nada, ainda é de tarde!",
        }

        if tipo_comando != periodo_real:
            msg = respostas_erradas.get((tipo_comando, periodo_real))
            if msg:
                await canal.send(msg)

        from config import BOT_CONFIG
        gif_url = await get_giphy_gif(query, user.id) if BOT_CONFIG.get("GIPHY_API_KEY") else None

        if gif_url:
            embed = discord.Embed(description=mensagem)
            embed.set_image(url=gif_url)
            await canal.send(embed=embed)
        else:
            await canal.send(mensagem)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.emoji.id != RECEBA_EMOJI_ID or payload.user_id == self.bot.user.id:
            return

        canal = self.bot.get_channel(payload.channel_id)
        if not canal:
            return

        mensagem = await canal.fetch_message(payload.message_id)
        if mensagem.author.id != MENSAGEM_AUTOR_ID:
            return

        try:
            user = await self.bot.fetch_user(payload.user_id)
            await mensagem.remove_reaction(payload.emoji, user)
        except Exception as e:
            print(f"Erro ao remover reação: {e}")

        if mensagem.id in self.receba_usos:
            return
        self.receba_usos.add(mensagem.id)

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            return

        try:
            buffer = await self.sobrepor_emote_no_avatar(member, "assets/receba_overlay.webp")
        except Exception as e:
            print(f"Erro ao gerar imagem: {e}")
            return

        try:
            embed = discord.Embed(title="💥 RECEBA!", color=discord.Color.red())
            embed.set_image(url="attachment://receba.png")
            await canal.send(embed=embed, file=discord.File(buffer, filename="receba.png"))
        except Exception as e:
            print(f"Erro ao enviar imagem: {e}")

    async def sobrepor_emote_no_avatar(self, user: discord.User, emote_path: str) -> BytesIO:
        avatar_asset = user.avatar.with_format("png").replace(size=128)
        avatar_bytes = await avatar_asset.read()

        avatar_img = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
        emote_img  = Image.open(emote_path).convert("RGBA").resize(avatar_img.size)

        alpha = emote_img.getchannel("A")
        alpha = ImageEnhance.Brightness(alpha).enhance(0.7)
        emote_img.putalpha(alpha)

        avatar_img.paste(emote_img, (0, 0), emote_img)

        buffer = BytesIO()
        avatar_img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    # ──────────────────────────────────────────
    #  SLASH COMMANDS
    # ──────────────────────────────────────────

    @app_commands.command(name="clima", description="Veja a previsão do tempo para sua cidade")
    async def clima(self, interaction: discord.Interaction, cidade: str):
        await interaction.response.defer()
        embed, error = await get_weather(cidade)
        if embed:
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ {error}")

    @app_commands.command(name="anonimo", description="Envie uma mensagem anônima para outro usuário")
    @app_commands.describe(destinatario="Quem receberá a mensagem", mensagem="Mensagem a ser enviada anonimamente")
    async def anonimo(self, interaction: discord.Interaction, destinatario: discord.Member, mensagem: str):
        await interaction.response.defer(ephemeral=True)
        autor        = interaction.user
        guild_id     = interaction.guild_id
        CUSTO_ANONIMO = 25

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (autor.id, guild_id))
            row = c.fetchone()

            if not row or row[0] is None or row[0] < CUSTO_ANONIMO:
                await interaction.followup.send(
                    f"❌ Você não tem flingers suficientes! O custo é **{CUSTO_ANONIMO} flingers**.", ephemeral=True
                )
                return

            novo_saldo = row[0] - CUSTO_ANONIMO
            c.execute("UPDATE usuarios SET flingers = ? WHERE id = ? AND guild_id = ?", (novo_saldo, autor.id, guild_id))
            conn.commit()

        try:
            embed_msg = discord.Embed(
                title="📩 Você recebeu uma mensagem anônima!",
                description=mensagem,
                color=discord.Color.purple()
            )
            await destinatario.send(embed=embed_msg)

            await interaction.followup.send(
                f"✅ Sua mensagem anônima foi enviada com sucesso!\n"
                f"💰 **-{CUSTO_ANONIMO} flingers** (Saldo atual: **{novo_saldo}**)",
                ephemeral=True
            )

            async def desafio():
                try:
                    await destinatario.send(
                        f"🤔 Quem você acha que te mandou essa mensagem? "
                        f"Responda com o nome de usuário exato (sem @). Você tem **1 chance**!"
                    )

                    def check(m):
                        return m.author == destinatario and isinstance(m.channel, discord.DMChannel)

                    try:
                        guess_msg = await self.bot.wait_for("message", check=check, timeout=300)
                    except asyncio.TimeoutError:
                        await destinatario.send("⏰ Tempo esgotado! Você não respondeu a tempo.")
                        return
                    tentativa_nome = guess_msg.content.strip().lower()
                    nome_autor     = autor.name.lower()

                    if tentativa_nome == nome_autor:
                        # Usa o canal configurado; se não houver, avisa o destinatário
                        canal_id     = get_config(guild_id, "canal_anonimo")
                        canal_publico = interaction.guild.get_channel(canal_id) if canal_id else None

                        if canal_publico:
                            embed_exposto = discord.Embed(
                                title="💀 Alguém foi desmascarado!",
                                description=f"**{destinatario.mention} descobriu quem enviou a mensagem anônima!**",
                                color=discord.Color.red()
                            )
                            embed_exposto.add_field(
                                name="💌 Mensagem enviada por",
                                value=f"**{autor.name}#{autor.discriminator}**",
                                inline=False
                            )
                            embed_exposto.add_field(
                                name="📨 Conteúdo da mensagem",
                                value=f"```{mensagem}```",
                                inline=False
                            )
                            embed_exposto.set_thumbnail(url=autor.display_avatar.url)
                            await canal_publico.send(embed=embed_exposto)
                        else:
                            await destinatario.send(
                                "⚠️ Canal de exposição não configurado no servidor. "
                                "Peça a um admin para usar `/config_canal`."
                            )
                    else:
                        await destinatario.send("🙊 Errou! A identidade continua oculta. 😉")
                except Exception as e:
                    print(f"Erro no desafio anônimo: {e}")

            asyncio.create_task(desafio())

        except Exception as e:
            # Devolve os flingers se falhou ao enviar
            with sqlite3.connect("usuarios.db") as conn:
                c = conn.cursor()
                c.execute(
                "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                (CUSTO_ANONIMO, autor.id, guild_id)
                )
                conn.commit()

            await interaction.followup.send(
                "❌ Não consegui enviar a mensagem. Talvez o usuário tenha DMs fechadas.\n"
                "💰 Seus flingers foram devolvidos.",
                ephemeral=True
            )
            print(f"Erro ao enviar anônimo: {e}")

    @app_commands.command(name="lupagom", description="Receba uma bênção aleatória do Lupagom")
    async def lupagom(self, interaction: discord.Interaction):
        respostas = [
            "https://i.imgur.com/fDW2Mvo.gif",
            "💩",
            "<:lupagom:1241049434566168708>"
        ]
        escolhido = random.choice(respostas)
        if escolhido.startswith("http"):
            embed = discord.Embed(title="Invocando Lupagom...", color=discord.Color.gold())
            embed.set_image(url=escolhido)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(escolhido)


async def setup(bot):
    await bot.add_cog(UtilityCog(bot))