# import sqlite3
# from datetime import datetime

# class FilmeManager:
#     @staticmethod
#     def criar_tabela():
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         c.execute('''
#             CREATE TABLE IF NOT EXISTS filmes (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 usuario_id INTEGER NOT NULL,
#                 titulo TEXT NOT NULL,
#                 tipo TEXT CHECK(tipo IN ('anime', 'nao_anime')) NOT NULL,
#                 link_letterboxd TEXT,
#                 assistido INTEGER DEFAULT 0 CHECK(assistido IN (0, 1)),
#                 data_assistido TEXT,
#                 nota INTEGER DEFAULT NULL,
#                 UNIQUE(usuario_id, titulo, tipo),
#                 FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
#             )
#         ''')

#         c.execute('''
#             CREATE TABLE IF NOT EXISTS avaliacoes (
#                 id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 avaliador_id INTEGER NOT NULL,
#                 filme_id INTEGER NOT NULL,
#                 nota INTEGER NOT NULL CHECK(nota >= 1 AND nota <= 10),
#                 comentario TEXT,
#                 data_avaliacao TEXT,
#                 UNIQUE(avaliador_id, filme_id),
#                 FOREIGN KEY (avaliador_id) REFERENCES usuarios(id),
#                 FOREIGN KEY (filme_id) REFERENCES filmes(id)
#             )
#         ''')

#         conn.commit()
#         conn.close()

#     @staticmethod
#     def adicionar_filme(usuario_id: int, titulo: str, tipo: str, link_letterboxd: str = None):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         try:
#             c.execute('''
#                 INSERT INTO filmes (usuario_id, titulo, tipo, link_letterboxd)
#                 VALUES (?, ?, ?, ?)
#             ''', (usuario_id, titulo, tipo, link_letterboxd))
#             conn.commit()
#             return True, f"🎬 Filme **{titulo}** adicionado com sucesso!"
#         except sqlite3.IntegrityError:
#             return False, "❌ Esse filme já está na sua lista!"
#         finally:
#             conn.close()

#     @staticmethod
#     def remover_filme(usuario_id: int, titulo: str, tipo: str):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         c.execute('''
#             DELETE FROM filmes
#             WHERE usuario_id = ? AND titulo = ? AND tipo = ?
#         ''', (usuario_id, titulo, tipo))

#         if c.rowcount == 0:
#             conn.close()
#             return False, "❌ Filme não encontrado na sua lista."
#         else:
#             conn.commit()
#             conn.close()
#             return True, f"🗑️ Filme **{titulo}** removido com sucesso."

#     @staticmethod
#     def listar_filmes(usuario_id: int):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         c.execute('''
#             SELECT tipo, titulo, link_letterboxd
#             FROM filmes
#             WHERE usuario_id = ?
#             ORDER BY assistido ASC, titulo ASC
#         ''', (usuario_id,))
#         filmes = c.fetchall()
#         conn.close()

#         animes = [f for f in filmes if f[0] == "anime"]
#         nao_animes = [f for f in filmes if f[0] == "nao_anime"]

#         return animes, nao_animes

#     @staticmethod
#     def marcar_filme_como_assistido(usuario_id: int, titulo: str, tipo: str):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT id, assistido FROM filmes
#             WHERE usuario_id = ? AND titulo = ? AND tipo = ?
#         ''', (usuario_id, titulo, tipo))

#         row = c.fetchone()
#         if not row:
#             conn.close()
#             return False, "❌ Filme não encontrado na sua lista."

#         if row[1] == 1:
#             conn.close()
#             return False, "📼 Esse filme já está marcado como assistido."

#         data_assistido = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#         c.execute('''
#             UPDATE filmes SET assistido = 1, data_assistido = ?
#             WHERE id = ?
#         ''', (data_assistido, row[0]))

#         conn.commit()
#         conn.close()
#         return True, f"✅ Filme **{titulo}** foi marcado como assistido em {data_assistido}!"

#     @staticmethod
#     def listar_nao_assistidos_por_tipo(usuario_id: int, tipo: str) -> list[str]:
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT titulo FROM filmes
#             WHERE usuario_id = ? AND tipo = ? AND assistido = 0
#             ORDER BY titulo ASC
#         ''', (usuario_id, tipo))
#         titulos = [row[0] for row in c.fetchall()]
#         conn.close()
#         return titulos
    
#     @staticmethod
#     def listar_assistidos_por_tipo(usuario_id: int, tipo: str) -> list[str]:
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT titulo FROM filmes
#             WHERE usuario_id = ? AND tipo = ? AND assistido = 1
#             ORDER BY titulo ASC
#         ''', (usuario_id, tipo))
#         titulos = [row[0] for row in c.fetchall()]
#         conn.close()
#         return titulos

#     @staticmethod
#     def avaliar_filme_assistido(usuario_id: int, titulo: str, tipo: str, nota: int):
#         if nota < 1 or nota > 10:
#             return False, "❌ A nota deve estar entre 1 e 10."

#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT id, assistido FROM filmes
#             WHERE usuario_id = ? AND titulo = ? AND tipo = ?
#         ''', (usuario_id, titulo, tipo))

#         row = c.fetchone()
#         if not row:
#             conn.close()
#             return False, "❌ Filme não encontrado na sua lista."

#         if row[1] != 1:
#             conn.close()
#             return False, "📼 Você só pode avaliar filmes que já foram marcados como assistidos."

#         c.execute('''
#             UPDATE filmes SET nota = ? WHERE id = ?
#         ''', (nota, row[0]))

#         conn.commit()
#         conn.close()
#         return True, f"⭐ Você avaliou o filme **{titulo}** com nota **{nota}/10**."

#     @staticmethod
#     def ranking_melhores_filmes(limit=10):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT f.titulo, f.tipo, f.link_letterboxd, ROUND(AVG(a.nota), 2) AS media, COUNT(a.id) AS num_avaliacoes
#             FROM filmes f
#             JOIN avaliacoes a ON f.id = a.filme_id
#             WHERE f.assistido = 1
#             GROUP BY f.id
#             HAVING COUNT(a.id) > 1
#             ORDER BY media DESC
#             LIMIT ?
#         ''', (limit,))
#         resultado = c.fetchall()
#         conn.close()
#         return resultado

#     @staticmethod
#     def ranking_recomendadores(limit=10):
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()
#         c.execute('''
#             SELECT u.nome, u.discriminator, ROUND(AVG(a.nota), 2) AS media
#             FROM usuarios u
#             JOIN filmes f ON u.id = f.usuario_id
#             JOIN avaliacoes a ON f.id = a.filme_id
#             WHERE f.assistido = 1
#             GROUP BY u.id
#             HAVING COUNT(a.id) > 1
#             ORDER BY media DESC
#             LIMIT ?
#         ''', (limit,))
#         resultado = c.fetchall()
#         conn.close()
#         return resultado

#     @staticmethod
#     def avaliar_filme_assistido(avaliador_id: int, titulo: str, tipo: str, nota: int):
#         if nota < 1 or nota > 10:
#             return False, "❌ A nota deve estar entre 1 e 10."

#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         # Buscar o filme assistido de qualquer usuário
#         c.execute('''
#             SELECT id FROM filmes
#             WHERE titulo = ? AND tipo = ? AND assistido = 1
#             ORDER BY data_assistido DESC
#         ''', (titulo, tipo))

#         row = c.fetchone()
#         if not row:
#             conn.close()
#             return False, "❌ Nenhum filme assistido com esse título foi encontrado."

#         filme_id = row[0]

#         # Verificar se o usuário já avaliou esse filme
#         c.execute('''
#             SELECT 1 FROM avaliacoes
#             WHERE avaliador_id = ? AND filme_id = ?
#         ''', (avaliador_id, filme_id))

#         if c.fetchone():
#             conn.close()
#             return False, "📼 Você já avaliou esse filme antes."

#         data_avaliacao = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#         c.execute('''
#             INSERT INTO avaliacoes (avaliador_id, filme_id, nota, data_avaliacao)
#             VALUES (?, ?, ?, ?)
#         ''', (avaliador_id, filme_id, nota, data_avaliacao))

#         conn.commit()
#         conn.close()

#         return True, f"⭐ Você avaliou o filme **{titulo}** com nota **{nota}/10**."


#     @staticmethod
#     def listar_filmes_assistidos_nao_avaliados(usuario_id: int, tipo: str) -> list[str]:
#         conn = sqlite3.connect("usuarios.db")
#         c = conn.cursor()

#         c.execute('''
#             SELECT f.titulo
#             FROM filmes f
#             LEFT JOIN avaliacoes a
#             ON f.id = a.filme_id AND a.avaliador_id = ?
#             WHERE f.tipo = ? AND f.assistido = 1 AND a.id IS NULL
#             GROUP BY f.id
#             ORDER BY f.titulo ASC
#         ''', (usuario_id, tipo))

#         titulos = [row[0] for row in c.fetchall()]
#         conn.close()
#         return titulos

