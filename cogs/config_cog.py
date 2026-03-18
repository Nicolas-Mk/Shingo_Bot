import sqlite3
import discord
from discord.ext import commands
from discord import app_commands


# ──────────────────────────────────────────────────────────────────
#  Tabela: guild_config
#  Armazena configurações por servidor, incluindo IDs de canais.
#
#  Canais disponíveis:
#    canal_economia   — onde o bot posta desafios de digitação
#    canal_call       — onde o bot avisa quando uma call enche
#    canal_mal        — onde o bot posta relatórios do MAL
#    canal_anonimo    — onde o bot expõe o autor de mensagens anônimas
# ──────────────────────────────────────────────────────────────────

CANAIS_VALIDOS = {
    "canal_economia": "Desafios de digitação (economia)",
    "canal_call":     "Aviso de call cheia",
    "canal_mal":      "Relatórios do MAL Tracker",
    "canal_anonimo":  "Exposição de mensagens anônimas",
}


def criar_tabela_config():
    conn = sqlite3.connect("usuarios.db")
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id         INTEGER NOT NULL,
            chave            TEXT    NOT NULL,
            valor            TEXT    NOT NULL,
            PRIMARY KEY (guild_id, chave)
        )
    """)
    conn.commit()
    conn.close()


def get_config(guild_id: int, chave: str) -> int | None:
    """Retorna o valor inteiro de uma configuração, ou None se não definida."""
    conn = sqlite3.connect("usuarios.db")
    c    = conn.cursor()
    c.execute(
        "SELECT valor FROM guild_config WHERE guild_id = ? AND chave = ?",
        (guild_id, chave)
    )
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else None


def set_config(guild_id: int, chave: str, valor: int):
    conn = sqlite3.connect("usuarios.db")
    c    = conn.cursor()
    c.execute("""
        INSERT INTO guild_config (guild_id, chave, valor)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, chave) DO UPDATE SET valor = excluded.valor
    """, (guild_id, chave, str(valor)))
    conn.commit()
    conn.close()


def get_all_config(guild_id: int) -> dict[str, int]:
    """Retorna todas as configurações de um servidor."""
    conn = sqlite3.connect("usuarios.db")
    c    = conn.cursor()
    c.execute("SELECT chave, valor FROM guild_config WHERE guild_id = ?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    return {chave: int(valor) for chave, valor in rows}


class ConfigCog(commands.Cog):
    """Comandos de configuração do bot por servidor."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="config_canal",
        description="[Admin] Define o canal usado para uma função do bot."
    )
    @app_commands.describe(
        funcao="Qual função configurar",
        canal="Canal de texto de destino"
    )
    @app_commands.choices(funcao=[
        app_commands.Choice(name=descricao, value=chave)
        for chave, descricao in CANAIS_VALIDOS.items()
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def config_canal(
        self,
        interaction: discord.Interaction,
        funcao: str,
        canal: discord.TextChannel
    ):
        set_config(interaction.guild_id, funcao, canal.id)
        descricao = CANAIS_VALIDOS.get(funcao, funcao)
        await interaction.response.send_message(
            f"✅ **{descricao}** configurado para {canal.mention}.",
            ephemeral=True
        )
        print(f"[Config] Guild {interaction.guild_id}: '{funcao}' = {canal.id} (por {interaction.user})")

    @app_commands.command(
        name="config_ver",
        description="[Admin] Mostra as configurações de canais do servidor."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def config_ver(self, interaction: discord.Interaction):
        configs = get_all_config(interaction.guild_id)

        embed = discord.Embed(
            title="⚙️ Configurações do Servidor",
            color=0x2E51A2
        )

        for chave, descricao in CANAIS_VALIDOS.items():
            canal_id = configs.get(chave)
            if canal_id:
                valor = f"<#{canal_id}>"
            else:
                valor = "⚠️ Não configurado"
            embed.add_field(name=descricao, value=valor, inline=False)

        embed.set_footer(text="Use /config_canal para definir cada canal.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))