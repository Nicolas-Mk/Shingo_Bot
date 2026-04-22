import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import time
import datetime
from database.db_manager import UserManager


class UserProfileCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="registrar", description="Registrar-se no sistema do bot")
    async def registrar(self, interaction: discord.Interaction):
        user     = interaction.user
        guild_id = interaction.guild_id
        registrado = UserManager.registrar_usuario(user.id, guild_id, user.name, user.discriminator)
        if registrado:
            await interaction.response.send_message(f'✅ {user.mention}, você foi registrado com sucesso!')
        else:
            await interaction.response.send_message(f'⚠️ {user.mention}, você já está registrado!')

    def calcular_xp_necessario(self, nivel):
        base     = 50
        xp_total = base
        for i in range(1, nivel):
            mult = 0.10 + ((i - 1) // 10) * 0.05
            xp_total += xp_total * mult
        return int(xp_total)

    def dar_xp(self, usuario_id, guild_id, nome, discriminator):
        with sqlite3.connect('usuarios.db') as conn:
            c = conn.cursor()
        c.execute(
            "SELECT xp, nivel, ultimo_xp FROM usuarios WHERE id = ? AND guild_id = ?",
            (usuario_id, guild_id)
        )
        row   = c.fetchone()
        agora = time.time()

        if row:
            xp, nivel, ultimo_xp = row
            if agora - ultimo_xp >= 300:
                xp += 5
                xp_necessario = self.calcular_xp_necessario(nivel)
                upou = False
                while xp >= xp_necessario:
                    xp -= xp_necessario
                    nivel += 1
                    xp_necessario = self.calcular_xp_necessario(nivel)
                    upou = True

                if upou:
                    c.execute(
                        "UPDATE usuarios SET flingers = flingers + 50 WHERE id = ? AND guild_id = ?",
                        (usuario_id, guild_id)
                    )

                c.execute(
                    "UPDATE usuarios SET xp = ?, nivel = ?, ultimo_xp = ? WHERE id = ? AND guild_id = ?",
                    (xp, nivel, agora, usuario_id, guild_id)
                )
                conn.commit()
                return upou, nivel
            else:
                return False, nivel
        else:
            # Usuário ainda não registrado neste servidor — cria o registro automaticamente
            c.execute("""
                INSERT INTO usuarios (id, guild_id, nome, discriminator, data_registro, descricao, xp, nivel, ultimo_xp)
                VALUES (?, ?, ?, ?, ?, '', 5, 1, ?)
            """, (usuario_id, guild_id, nome, discriminator,
                  discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S'), agora))
            conn.commit()
            return False, 1

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        upou, nivel = self.dar_xp(
            message.author.id,
            message.guild.id,
            message.author.name,
            message.author.discriminator,
        )

        if upou:
            await message.channel.send(
                f"🎉 Parabéns, {message.author.mention}, você subiu para o nível {nivel} de fracasso! 💀"
            )

    @app_commands.command(name="perfil", description="Ver seu perfil de registro")
    async def perfil(self, interaction: discord.Interaction):
        user     = interaction.user
        guild_id = interaction.guild_id
        with sqlite3.connect('usuarios.db') as conn:
            c = conn.cursor()

        c.execute("""
            SELECT nome, discriminator, data_registro, descricao, xp, nivel, flingers
            FROM usuarios
            WHERE id = ? AND guild_id = ?
        """, (user.id, guild_id))

        dados = c.fetchone()

        if dados:
            nome, discriminator, data_registro, descricao, xp, nivel, flingers = dados
            embed = discord.Embed(title="📋 Perfil do Usuário", color=discord.Color.blue())
            embed.set_thumbnail(url=user.avatar.url if user.avatar else user.default_avatar.url)
            embed.add_field(name="👤 Usuário",      value=f"{nome}#{discriminator}",          inline=False)
            embed.add_field(name="📅 Registrado em", value=data_registro,                     inline=False)
            embed.add_field(name="📝 Descrição",     value=descricao or "Sem descrição definida", inline=False)
            embed.add_field(name="📊 XP",            value=f"{xp} XP",                        inline=True)
            embed.add_field(name="🔝 Nível",         value=f"{nivel}",                        inline=True)
            embed.add_field(name="💰 Flingers",      value=f"{flingers}",                     inline=True)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                f'⚠️ {user.mention}, você ainda não está registrado. Use `/registrar` para se cadastrar.'
            )

    @app_commands.command(name="editarperfil", description="Edite a descrição do seu perfil")
    @app_commands.describe(descricao="Sua nova descrição")
    async def editarperfil(self, interaction: discord.Interaction, descricao: str):
        with sqlite3.connect('usuarios.db') as conn:
            c = conn.cursor()
        c.execute(
            "UPDATE usuarios SET descricao = ? WHERE id = ? AND guild_id = ?",
            (descricao, interaction.user.id, interaction.guild_id)
        )
        conn.commit()
        await interaction.response.send_message("✅ Sua descrição foi atualizada com sucesso!", ephemeral=True)

    @app_commands.command(name="topnivel", description="Veja os usuários com mais nível e XP")
    async def topnivel(self, interaction: discord.Interaction):
        with sqlite3.connect('usuarios.db') as conn:
            c = conn.cursor()
        c.execute("""
            SELECT nome, discriminator, nivel, xp
            FROM usuarios
            WHERE guild_id = ?
            ORDER BY nivel DESC, xp DESC
            LIMIT 10
        """, (interaction.guild_id,))
        top = c.fetchall()

        if not top:
            await interaction.response.send_message("⚠️ Ninguém possui nível ainda!", ephemeral=True)
            return

        embed = discord.Embed(title="🏆 Top 10 Usuários por Nível", color=discord.Color.purple())
        for i, (nome, discriminator, nivel, xp) in enumerate(top, start=1):
            embed.add_field(
                name=f"#{i} - {nome}#{discriminator}",
                value=f"🔝 Nível: {nivel} | 📊 XP: {xp}",
                inline=False
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(UserProfileCog(bot))