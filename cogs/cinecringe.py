# import discord
# from discord.ext import commands
# from discord import app_commands, Interaction
# from database.db_manager import FilmeManager
# from typing import Literal


# class CineCringeCog(commands.Cog):
#     def __init__(self, bot):
#         self.bot = bot


#     @app_commands.command(name="adicionarcine", description="Adiciona um filme à sua lista do CineCringe")
#     @app_commands.describe(
#         tipo="Escolha o tipo do filme",
#         titulo="Título do filme",
#         link="Link opcional do Letterboxd"
#     )
#     async def adicionarcine(
#         self,
#         interaction: Interaction,
#         tipo: Literal["anime", "nao_anime"],
#         titulo: str,
#         link: str = None
#     ):
#         sucesso, mensagem = FilmeManager.adicionar_filme(interaction.user.id, titulo, tipo, link)
#         await interaction.response.send_message(mensagem, ephemeral=True)


#     @app_commands.command(name="removercine", description="Remove um filme da sua lista CineCringe")
#     async def removercine(self, interaction: Interaction, tipo: str, titulo: str):
#         sucesso, mensagem = FilmeManager.remover_filme(interaction.user.id, tipo.lower(), titulo)
#         await interaction.response.send_message(mensagem, ephemeral=True)

#     @app_commands.command(name="listarcine", description="Veja os filmes da sua lista CineCringe")
#     async def listarcine(self, interaction: Interaction):
#         animes, nao_animes = FilmeManager.listar_filmes(interaction.user.id)
#         embed = discord.Embed(title="🎬 Sua lista do CineCringe", color=discord.Color.blue())

#         if animes:
#             anime_str = "\n".join([f"[{titulo}]({link})" if link else titulo for _, titulo, link in animes])
#             embed.add_field(name="🎌 Filmes Anime", value=anime_str, inline=False)
#         else:
#             embed.add_field(name="🎌 Filmes Anime", value="(nenhum adicionado)", inline=False)

#         if nao_animes:
#             nao_anime_str = "\n".join([f"[{titulo}]({link})" if link else titulo for _, titulo, link in nao_animes])
#             embed.add_field(name="🎥 Filmes Não-Anime", value=nao_anime_str, inline=False)
#         else:
#             embed.add_field(name="🎥 Filmes Não-Anime", value="(nenhum adicionado)", inline=False)

#         await interaction.response.send_message(embed=embed)

#     @app_commands.command(name="marcarassistido", description="Marque um dos seus filmes do CineCringe como assistido")
#     @app_commands.describe(
#         tipo="anime ou nao_anime",
#         titulo="Título do filme (autocomplete)"
#     )
#     async def marcarassistido(
#         self,
#         interaction: Interaction,
#         tipo: Literal["anime", "nao_anime"],
#         titulo: str
#     ):
#         sucesso, mensagem = FilmeManager.marcar_filme_como_assistido(interaction.user.id, titulo, tipo)
#         await interaction.response.send_message(mensagem, ephemeral=True)

#     @marcarassistido.autocomplete("titulo")
#     async def autocomplete_titulo_filme(
#         self,
#         interaction: Interaction,
#         current: str
#     ) -> list[app_commands.Choice[str]]:
#         tipo = interaction.namespace.tipo
#         usuario_id = interaction.user.id

#         filmes = FilmeManager.listar_nao_assistidos_por_tipo(usuario_id, tipo)
#         opcoes = [
#             app_commands.Choice(name=titulo, value=titulo)
#             for titulo in filmes if current.lower() in titulo.lower()
#         ]
#         return opcoes[:25]  # limite do Discord



#     @app_commands.command(name="avaliarcine", description="Avalie um filme já assistido da sua lista CineCringe")
#     @app_commands.describe(
#         tipo="Tipo do filme (anime ou nao_anime)",
#         titulo="Título do filme assistido",
#         nota="Nota de 1 a 10"
#     )
#     async def avaliarcine(
#         self,
#         interaction: Interaction,
#         tipo: Literal["anime", "nao_anime"],
#         titulo: str,
#         nota: int
#     ):
#         if nota < 1 or nota > 10:
#             await interaction.response.send_message("❌ A nota deve estar entre 1 e 10.", ephemeral=True)
#             return

#         sucesso, mensagem = FilmeManager.avaliar_filme_assistido(interaction.user.id, titulo, tipo, nota)
#         await interaction.response.send_message(mensagem, ephemeral=True)

#     @app_commands.command(name="rankingfilmes", description="Veja os melhores filmes do CineCringe por média de nota")
#     async def rankingfilmes(self, interaction: Interaction):
#         filmes = FilmeManager.ranking_melhores_filmes()
#         if not filmes:
#             await interaction.response.send_message("📭 Nenhum filme com avaliações suficientes ainda.", ephemeral=True)
#             return

#         embed = discord.Embed(title="🎬 Ranking dos Melhores Filmes", color=discord.Color.orange())
#         for i, (titulo, tipo, link, media, total) in enumerate(filmes, start=1):
#             tipo_emoji = "🎌" if tipo == "anime" else "🎥"
#             titulo_formatado = f"[{titulo}]({link})" if link else titulo
#             embed.add_field(
#                 name=f"{i}. {tipo_emoji} {titulo_formatado}",
#                 value=f"Média: **{media}** ({total} avaliações)",
#                 inline=False
#             )
#         await interaction.response.send_message(embed=embed)
    
#     @avaliarcine.autocomplete("titulo")
#     async def autocomplete_titulos_assistidos(
#         self,
#         interaction: Interaction,
#         current: str
#     ) -> list[app_commands.Choice[str]]:
#         tipo = interaction.namespace.tipo
#         usuario_id = interaction.user.id

#         filmes = FilmeManager.listar_assistidos_por_tipo(usuario_id, tipo)
#         return [
#             app_commands.Choice(name=t, value=t)
#             for t in filmes if current.lower() in t.lower()
#         ][:25]
    
#     @avaliarcine.autocomplete("titulo")
#     async def autocomplete_filmes_disponiveis_para_avaliar(
#         self,
#         interaction: Interaction,
#         current: str
#     ) -> list[app_commands.Choice[str]]:
#         tipo = interaction.namespace.tipo
#         usuario_id = interaction.user.id

#         filmes = FilmeManager.listar_filmes_assistidos_nao_avaliados(usuario_id, tipo)
#         return [
#             app_commands.Choice(name=titulo, value=titulo)
#             for titulo in filmes if current.lower() in titulo.lower()
#         ][:25]


#     @app_commands.command(name="rankingrecomendadores", description="Veja os melhores recomendadores do CineCringe")
#     async def rankingrecomendadores(self, interaction: Interaction):
#         users = FilmeManager.ranking_recomendadores()
#         if not users:
#             await interaction.response.send_message("📭 Nenhum recomendador com filmes avaliados ainda.", ephemeral=True)
#             return

#         embed = discord.Embed(title="🌟 Ranking de Recomendadores", color=discord.Color.gold())
#         for i, (nome, discriminator, media) in enumerate(users, start=1):
#             embed.add_field(
#                 name=f"{i}. {nome}#{discriminator}",
#                 value=f"Média de avaliação dos filmes: **{media}**",
#                 inline=False
#             )
#         await interaction.response.send_message(embed=embed)

# async def setup(bot):
#     await bot.add_cog(CineCringeCog(bot))
