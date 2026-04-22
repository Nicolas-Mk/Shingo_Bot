import sqlite3
from datetime import datetime

import discord
from discord.ext import commands
from discord import app_commands


# ──────────────────────────────────────────────
#  Helpers de banco
# ──────────────────────────────────────────────

def _get_item(guild_id: int, item_id: int) -> dict | None:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
        "SELECT id, nome, descricao, preco, estoque, ativo, icone FROM loja_itens WHERE id = ? AND guild_id = ?",
        (item_id, guild_id)
        )
        row = c.fetchone()
    if not row:
        return None
    return {"id": row[0], "nome": row[1], "descricao": row[2], "preco": row[3], "estoque": row[4], "ativo": row[5], "icone": row[6]}


def _estoque_vendido(item_id: int) -> int:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(quantidade), 0) FROM loja_compras WHERE item_id = ?", (item_id,))
        total = c.fetchone()[0]
    return total


def _quantidade_no_inventario(user_id: int, guild_id: int, item_id: int) -> int:
    """Retorna a quantidade disponível no inventário (compras - usos)."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute(
        "SELECT COALESCE(SUM(quantidade), 0) FROM loja_compras WHERE user_id = ? AND guild_id = ? AND item_id = ?",
        (user_id, guild_id, item_id)
        )
        total = c.fetchone()[0]
    return total


async def _autocomplete_loja(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("""
        SELECT i.nome, i.icone, i.preco, i.estoque,
               COALESCE((SELECT SUM(c.quantidade) FROM loja_compras c WHERE c.item_id = i.id), 0)
        FROM loja_itens i
        WHERE i.guild_id = ? AND i.ativo = 1
        ORDER BY i.preco
        """, (interaction.guild_id,))
        itens = c.fetchall()

    choices = []
    for nome, icone, preco, estoque, vendidos in itens:
        disponivel = (estoque - vendidos) if estoque != -1 else None
        if disponivel is not None and disponivel <= 0:
            continue
        estoque_str  = f"{disponivel} restantes" if disponivel is not None else "∞"
        nome_exibido = f"{icone + ' ' if icone else ''}{nome} — {preco} 💵 ({estoque_str})"
        if current.lower() in nome.lower():
            choices.append(app_commands.Choice(name=nome_exibido, value=nome))

    return choices[:25]


async def _autocomplete_loja_admin(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Inclui itens esgotados — para uso de admins."""
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("""
        SELECT i.nome, i.icone, i.preco, i.estoque,
               COALESCE((SELECT SUM(c.quantidade) FROM loja_compras c WHERE c.item_id = i.id), 0)
        FROM loja_itens i
        WHERE i.guild_id = ? AND i.ativo = 1
        ORDER BY i.nome
        """, (interaction.guild_id,))
        itens = c.fetchall()

    choices = []
    for nome, icone, preco, estoque, vendidos in itens:
        disponivel   = (estoque - vendidos) if estoque != -1 else None
        estoque_str  = f"{disponivel} restantes" if disponivel is not None else "∞"
        nome_exibido = f"{icone + ' ' if icone else ''}{nome} — {preco} 💵 ({estoque_str})"
        if current.lower() in nome.lower():
            choices.append(app_commands.Choice(name=nome_exibido, value=nome))

    return choices[:25]


async def _autocomplete_inventario(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    with sqlite3.connect("usuarios.db") as conn:
        c = conn.cursor()
        c.execute("""
        SELECT i.nome, i.icone, SUM(c.quantidade)
        FROM loja_compras c
        JOIN loja_itens i ON i.id = c.item_id
        WHERE c.user_id = ? AND c.guild_id = ?
        GROUP BY c.item_id
        HAVING SUM(c.quantidade) > 0
        ORDER BY i.nome
        """, (interaction.user.id, interaction.guild_id))
        itens = c.fetchall()

    return [
        app_commands.Choice(
            name=f"{icone + ' ' if icone else ''}{nome} (x{quantidade})",
            value=nome
        )
        for nome, icone, quantidade in itens
        if current.lower() in nome.lower()
    ][:25]


# ──────────────────────────────────────────────
#  Cog
# ──────────────────────────────────────────────

class LojaCog(commands.Cog):
    """Sistema de loja por servidor com itens personalizados."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────
    #  Comandos — admin
    # ──────────────────────────────────────────

    @app_commands.command(name="adicionar_item", description="[Admin] Cria um novo item na loja.")
    @app_commands.describe(
        nome="Nome do item",
        descricao="Descrição do item",
        preco="Preço em flingers",
        estoque="Quantidade disponível (-1 para ilimitado)",
        icone="Emote do Discord ou emoji (ex: 🎯 ou :nome_do_emote:)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def adicionar_item(
        self,
        interaction: discord.Interaction,
        nome: str,
        descricao: str,
        preco: int,
        estoque: int = -1,
        icone: str = None,
    ):
        if preco <= 0:
            await interaction.response.send_message("❌ O preço deve ser maior que zero.", ephemeral=True)
            return
        if estoque == 0:
            await interaction.response.send_message("❌ Estoque não pode ser zero. Use -1 para ilimitado.", ephemeral=True)
            return

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            try:
                c.execute(
                    "INSERT INTO loja_itens (guild_id, nome, descricao, preco, estoque, icone) VALUES (?, ?, ?, ?, ?, ?)",
                    (interaction.guild_id, nome, descricao, preco, estoque, icone)
                )
                conn.commit()
            except sqlite3.IntegrityError:
                await interaction.response.send_message(f"❌ Já existe um item chamado **{nome}**.", ephemeral=True)
                return

        estoque_str = "Ilimitado" if estoque == -1 else str(estoque)
        nome_exibido = f"{icone} {nome}" if icone else nome
        embed = discord.Embed(title="✅ Item criado", color=0x2E51A2)
        embed.add_field(name="Nome",      value=nome_exibido,        inline=True)
        embed.add_field(name="Preço",     value=f"{preco} flingers", inline=True)
        embed.add_field(name="Estoque",   value=estoque_str,         inline=True)
        embed.add_field(name="Descrição", value=descricao,           inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Loja] Item '{nome}' criado na guild {interaction.guild_id} por {interaction.user}.")

    @app_commands.command(name="item_remover", description="[Admin] Remove um item da loja.")
    @app_commands.describe(nome="Item a remover")
    @app_commands.autocomplete(nome=_autocomplete_loja_admin)
    @app_commands.checks.has_permissions(administrator=True)
    async def item_remover(self, interaction: discord.Interaction, nome: str):
        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
            "SELECT id, nome FROM loja_itens WHERE guild_id = ? AND LOWER(nome) = LOWER(?)",
            (interaction.guild_id, nome)
            )
            row = c.fetchone()
        item = {"id": row[0], "nome": row[1]} if row else None

        if not item:
            await interaction.response.send_message("❌ Item não encontrado.", ephemeral=True)
            return

        with sqlite3.connect("usuarios.db") as conn:
                    conn.execute("UPDATE loja_itens SET ativo = 0 WHERE id = ?", (item["id"],))
        conn.commit()

        await interaction.response.send_message(
            f"✅ Item **{item['nome']}** removido da loja.", ephemeral=True
        )

    @app_commands.command(name="loja_editar_estoque", description="[Admin] Altera o estoque de um item.")
    @app_commands.describe(nome="Item a editar", estoque="Novo estoque (-1 para ilimitado)")
    @app_commands.autocomplete(nome=_autocomplete_loja_admin)
    @app_commands.checks.has_permissions(administrator=True)
    async def loja_editar_estoque(self, interaction: discord.Interaction, nome: str, estoque: int):
        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
            "SELECT id, nome FROM loja_itens WHERE guild_id = ? AND LOWER(nome) = LOWER(?)",
            (interaction.guild_id, nome)
            )
            row = c.fetchone()
        item = {"id": row[0], "nome": row[1]} if row else None

        if not item:
            await interaction.response.send_message("❌ Item não encontrado.", ephemeral=True)
            return

        with sqlite3.connect("usuarios.db") as conn:
                    conn.execute("UPDATE loja_itens SET estoque = ? WHERE id = ?", (estoque, item["id"]))
        conn.commit()

        estoque_str = "Ilimitado" if estoque == -1 else str(estoque)
        await interaction.response.send_message(
            f"✅ Estoque de **{item['nome']}** atualizado para **{estoque_str}**.", ephemeral=True
        )

    # ──────────────────────────────────────────
    #  Comandos — usuários
    # ──────────────────────────────────────────

    @app_commands.command(name="loja", description="Veja os itens disponíveis na loja.")
    async def loja(self, interaction: discord.Interaction):
        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
            "SELECT id, nome, descricao, preco, estoque, icone FROM loja_itens WHERE guild_id = ? AND ativo = 1 ORDER BY preco",
            (interaction.guild_id,)
            )
            itens = c.fetchall()

        if not itens:
            await interaction.response.send_message("🛒 A loja está vazia por enquanto.", ephemeral=True)
            return

        embed = discord.Embed(title="🛒 Loja", color=0x2E51A2)
        embed.set_footer(text="Use /loja_comprar <id> para comprar um item.")

        for item_id, nome, descricao, preco, estoque, icone in itens:
            vendidos    = _estoque_vendido(item_id)
            disponivel  = (estoque - vendidos) if estoque != -1 else None
            estoque_str = f"{disponivel} restantes" if disponivel is not None else "Ilimitado"
            nome_exibido = f"{icone} {nome}" if icone else nome

            embed.add_field(
                name=f"[#{item_id}] {nome_exibido} — {preco} 💵",
                value=f"{descricao}\n`Estoque: {estoque_str}`",
                inline=False
            )

        await interaction.response.send_message(embed=embed)



    @app_commands.command(name="loja_comprar", description="Compra um item da loja.")
    @app_commands.describe(nome="Item a comprar")
    @app_commands.autocomplete(nome=_autocomplete_loja)
    async def loja_comprar(self, interaction: discord.Interaction, nome: str):
        guild_id  = interaction.guild_id
        user_id   = interaction.user.id
        quantidade = 1

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
            "SELECT id, nome, descricao, preco, estoque, ativo, icone FROM loja_itens WHERE guild_id = ? AND LOWER(nome) = LOWER(?) AND ativo = 1",
            (guild_id, nome)
            )
            row = c.fetchone()
        item = {"id": row[0], "nome": row[1], "descricao": row[2], "preco": row[3], "estoque": row[4], "ativo": row[5], "icone": row[6]} if row else None

        if not item:
            await interaction.response.send_message("❌ Item não encontrado.", ephemeral=True)
            return

        custo_total = item["preco"] * quantidade

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()

            # Verifica flingers suficientes
            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (user_id, guild_id))
            row = c.fetchone()
            if not row or (row[0] or 0) < custo_total:
                await interaction.response.send_message(
                    f"❌ Flingers insuficientes. Você precisa de **{custo_total}** e tem **{row[0] if row else 0}**.",
                    ephemeral=True
                )
                return

            # Verifica estoque dentro da mesma transação, contando compras já registradas
            if item["estoque"] != -1:
                c.execute(
                    "SELECT COALESCE(SUM(quantidade), 0) FROM loja_compras WHERE item_id = ?",
                    (item["id"],)
                )
                vendidos   = c.fetchone()[0]
                disponivel = item["estoque"] - vendidos
                if disponivel <= 0:
                    await interaction.response.send_message("❌ Este item está esgotado.", ephemeral=True)
                    return

            c.execute(
            "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ?",
            (custo_total, user_id, guild_id)
            )
            c.execute(
            "INSERT INTO loja_compras (guild_id, user_id, item_id, quantidade, comprado_em) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, item["id"], quantidade, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()

        embed = discord.Embed(title="✅ Compra realizada!", color=discord.Color.green())
        embed.add_field(name="Item",       value=item["nome"],        inline=True)
        embed.add_field(name="Total pago", value=f"{custo_total} 💵", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        print(f"[Loja] {interaction.user} comprou '{item['nome']}' na guild {guild_id}.")



    @app_commands.command(name="usar_item", description="Usa um item do seu inventário.")
    @app_commands.describe(nome="Item a usar")
    @app_commands.autocomplete(nome=_autocomplete_inventario)
    async def usar_item(self, interaction: discord.Interaction, nome: str):
        guild_id = interaction.guild_id
        user_id  = interaction.user.id

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute(
            "SELECT id, nome, descricao, preco, estoque, ativo, icone FROM loja_itens WHERE guild_id = ? AND LOWER(nome) = LOWER(?)",
            (guild_id, nome)
            )
            row = c.fetchone()
        item = {"id": row[0], "nome": row[1], "descricao": row[2], "preco": row[3], "estoque": row[4], "ativo": row[5], "icone": row[6]} if row else None

        if not item:
            await interaction.response.send_message("❌ Item não encontrado.", ephemeral=True)
            return

        no_inventario = _quantidade_no_inventario(user_id, guild_id, item["id"])
        if no_inventario < 1:
            await interaction.response.send_message(
                f"❌ Você não tem **{item['nome']}** no inventário.", ephemeral=True
            )
            return

        with sqlite3.connect("usuarios.db") as conn:
                    conn.execute(
            "INSERT INTO loja_compras (guild_id, user_id, item_id, quantidade, comprado_em) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, item["id"], -1, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()

        embed = discord.Embed(
            title="🎁 Item usado!",
            description=f"{interaction.user.mention} usou **{item['nome']}**.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Descrição", value=item["descricao"], inline=False)
        await interaction.response.send_message(embed=embed)
        print(f"[Loja] {interaction.user} usou '{item['nome']}' na guild {guild_id}.")

    @app_commands.command(name="inventario", description="Veja os itens que você possui.")
    async def inventario(self, interaction: discord.Interaction):
        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()
            c.execute("""
            SELECT i.id, i.nome, i.descricao, i.icone, SUM(c.quantidade)
            FROM loja_compras c
            JOIN loja_itens i ON i.id = c.item_id
            WHERE c.user_id = ? AND c.guild_id = ?
            GROUP BY c.item_id
            HAVING SUM(c.quantidade) > 0
            ORDER BY i.nome
            """, (interaction.user.id, interaction.guild_id))
            itens = c.fetchall()

        if not itens:
            await interaction.response.send_message("🎒 Seu inventário está vazio.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎒 Inventário de {interaction.user.display_name}",
            color=0x2E51A2
        )
        embed.set_footer(text="Use /usar_item <id> para usar um item.")
        for item_id, nome, descricao, icone, quantidade in itens:
            nome_exibido = f"{icone} {nome}" if icone else nome
            embed.add_field(
                name=f"[#{item_id}] {nome_exibido} (x{quantidade})",
                value=descricao,
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="transferir_flingers", description="Transfere flingers para outro usuário.")
    @app_commands.describe(destinatario="Usuário que receberá os flingers", quantidade="Quantidade a transferir")
    async def transferir_flingers(
        self,
        interaction: discord.Interaction,
        destinatario: discord.Member,
        quantidade: int
    ):
        guild_id  = interaction.guild_id
        remetente = interaction.user

        if destinatario.id == remetente.id:
            await interaction.response.send_message("❌ Você não pode transferir flingers para si mesmo.", ephemeral=True)
            return
        if destinatario.bot:
            await interaction.response.send_message("❌ Não é possível transferir flingers para um bot.", ephemeral=True)
            return
        if quantidade <= 0:
            await interaction.response.send_message("❌ A quantidade deve ser maior que zero.", ephemeral=True)
            return

        with sqlite3.connect("usuarios.db") as conn:
            c = conn.cursor()

            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (remetente.id, guild_id))
            row_rem = c.fetchone()
            if not row_rem or (row_rem[0] or 0) < quantidade:
                await interaction.response.send_message(
                    f"❌ Flingers insuficientes. Você tem **{row_rem[0] if row_rem else 0}** e tentou transferir **{quantidade}**.",
                    ephemeral=True
                )
                return

            c.execute("SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?", (destinatario.id, guild_id))
            row_dest = c.fetchone()
            if not row_dest:
                await interaction.response.send_message(
                    f"❌ {destinatario.mention} não está registrado neste servidor.", ephemeral=True
                )
                return

            c.execute(
            "UPDATE usuarios SET flingers = flingers - ? WHERE id = ? AND guild_id = ?",
            (quantidade, remetente.id, guild_id)
            )
            c.execute(
            "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
            (quantidade, destinatario.id, guild_id)
            )
            conn.commit()

        embed = discord.Embed(title="💸 Transferência realizada!", color=discord.Color.green())
        embed.add_field(name="De",         value=remetente.mention,    inline=True)
        embed.add_field(name="Para",       value=destinatario.mention, inline=True)
        embed.add_field(name="Quantidade", value=f"{quantidade} 💵",   inline=True)
        await interaction.response.send_message(embed=embed)
        print(f"[Loja] {remetente} transferiu {quantidade} flingers para {destinatario} na guild {guild_id}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(LojaCog(bot))