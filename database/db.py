import sqlite3
from datetime import datetime

import sqlite3

def criar_tabela():
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY,
            nome TEXT,
            discriminator TEXT,
            data_registro TEXT
        )
    ''')
    conn.commit()
    conn.close()

def criar_tabela_cinecringe():
    # Você já tem a função definida acima
    conn = sqlite3.connect("usuarios.db")
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS filmes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            tipo TEXT CHECK(tipo IN ('anime', 'nao_anime')) NOT NULL,
            link_letterboxd TEXT,
            assistido INTEGER DEFAULT 0 CHECK(assistido IN (0, 1)),
            data_assistido TEXT,
            nota INTEGER DEFAULT NULL,
            UNIQUE(usuario_id, titulo, tipo),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS avaliacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            avaliador_id INTEGER NOT NULL,
            filme_id INTEGER NOT NULL,
            nota INTEGER NOT NULL CHECK(nota >= 1 AND nota <= 10),
            comentario TEXT,
            data_avaliacao TEXT,
            UNIQUE(avaliador_id, filme_id),
            FOREIGN KEY (avaliador_id) REFERENCES usuarios(id),
            FOREIGN KEY (filme_id) REFERENCES filmes(id)
        )
    ''')

    conn.commit()
    conn.close()


def atualizar_tabela():
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    try: c.execute("ALTER TABLE usuarios ADD COLUMN descricao TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN xp INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN nivel INTEGER DEFAULT 1")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN ultimo_xp REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: c.execute("ALTER TABLE usuarios ADD COLUMN flingers INTEGER DEFAULT 0")
    except: pass
    conn.commit()
    conn.close()

def registrar_usuario(id, nome, discriminator):
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    c.execute('SELECT id FROM usuarios WHERE id = ?', (id,))
    if not c.fetchone():
        data_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('INSERT INTO usuarios (id, nome, discriminator, data_registro) VALUES (?, ?, ?, ?)',
                  (id, nome, discriminator, data_registro))
        conn.commit()
        conn.close()
        return True
    else:
        conn.close()
        return False
    
def buscar_usuario(id):
    conn = sqlite3.connect('usuarios.db')
    c = conn.cursor()
    c.execute('SELECT nome, discriminator, data_registro, descricao FROM usuarios WHERE id = ?', (id,))
    dados = c.fetchone()
    conn.close()
    return dados



