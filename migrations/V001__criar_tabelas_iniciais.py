"""
V001 — Criação das tabelas iniciais do bot.
Tabelas: usuarios, filmes, avaliacoes, mensagens
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id            INTEGER PRIMARY KEY,
            nome          TEXT,
            discriminator TEXT,
            data_registro TEXT,
            descricao     TEXT    DEFAULT '',
            xp            INTEGER DEFAULT 0,
            nivel         INTEGER DEFAULT 1,
            ultimo_xp     REAL    DEFAULT 0,
            flingers      INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS filmes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id      INTEGER NOT NULL,
            titulo          TEXT    NOT NULL,
            tipo            TEXT    CHECK(tipo IN ('anime', 'nao_anime')) NOT NULL,
            link_letterboxd TEXT,
            assistido       INTEGER DEFAULT 0 CHECK(assistido IN (0, 1)),
            data_assistido  TEXT,
            nota            INTEGER DEFAULT NULL,
            UNIQUE(usuario_id, titulo, tipo),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS avaliacoes (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            avaliador_id   INTEGER NOT NULL,
            filme_id       INTEGER NOT NULL,
            nota           INTEGER NOT NULL CHECK(nota >= 1 AND nota <= 10),
            comentario     TEXT,
            data_avaliacao TEXT,
            UNIQUE(avaliador_id, filme_id),
            FOREIGN KEY (avaliador_id) REFERENCES usuarios(id),
            FOREIGN KEY (filme_id)     REFERENCES filmes(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            mensagem   TEXT    NOT NULL,
            canal      TEXT,
            data_envio TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    """)

    conn.commit()