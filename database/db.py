import sqlite3
from datetime import datetime

# ──────────────────────────────────────────────────────────────────
#  ATENÇÃO: a gestão principal de usuários foi migrada para
#  database/user_manager.py (classe UserManager).
#  Este arquivo mantém apenas as tabelas auxiliares (filmes,
#  avaliações, mensagens) e funções legadas que ainda são usadas
#  diretamente por alguns cogs.
# ──────────────────────────────────────────────────────────────────


def criar_tabela_cinecringe():
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS filmes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id       INTEGER NOT NULL,
            titulo           TEXT    NOT NULL,
            tipo             TEXT    CHECK(tipo IN ('anime', 'nao_anime')) NOT NULL,
            link_letterboxd  TEXT,
            assistido        INTEGER DEFAULT 0 CHECK(assistido IN (0, 1)),
            data_assistido   TEXT,
            nota             INTEGER DEFAULT NULL,
            UNIQUE(usuario_id, titulo, tipo),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    ''')

    c.execute('''
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
    ''')

    conn.commit()
    conn.close()


def criar_tabela_mensagens():
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS mensagens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id  INTEGER NOT NULL,
            mensagem    TEXT    NOT NULL,
            canal       TEXT,
            data_envio  TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    ''')
    conn.commit()
    conn.close()


def registrar_mensagem(usuario_id: int, mensagem: str, canal: str):
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    data_envio = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.execute(
        'INSERT INTO mensagens (usuario_id, mensagem, canal, data_envio) VALUES (?, ?, ?, ?)',
        (usuario_id, mensagem, canal, data_envio)
    )
    conn.commit()
    conn.close()