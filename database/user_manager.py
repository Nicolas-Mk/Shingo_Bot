import sqlite3
from datetime import datetime

class UserManager:
    @staticmethod
    def criar_tabela():
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY,
                nome TEXT,
                discriminator TEXT,
                data_registro TEXT,
                descricao TEXT DEFAULT '',
                xp INTEGER DEFAULT 0,
                nivel INTEGER DEFAULT 1,
                ultimo_xp REAL DEFAULT 0,
                flingers INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()

    @staticmethod
    def atualizar_tabela():
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        columns_to_add = [
            ("descricao", "TEXT DEFAULT ''"),
            ("xp", "INTEGER DEFAULT 0"),
            ("nivel", "INTEGER DEFAULT 1"),
            ("ultimo_xp", "REAL DEFAULT 0"),
            ("flingers", "INTEGER DEFAULT 0")
        ]

        for column, definition in columns_to_add:
            try:
                c.execute(f"ALTER TABLE usuarios ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                pass

        try:
            c.execute("ALTER TABLE filmes ADD COLUMN data_assistido TEXT")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        conn.close()

    @staticmethod
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

    @staticmethod
    def buscar_usuario(id):
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute('SELECT nome, discriminator, data_registro, descricao FROM usuarios WHERE id = ?', (id,))
        dados = c.fetchone()
        conn.close()
        return dados

    @staticmethod
    def adicionar_flingers(usuario_id, quantidade):
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute("SELECT flingers FROM usuarios WHERE id = ?", (usuario_id,))
        row = c.fetchone()
        if row:
            flingers_atual = row[0] if row[0] is not None else 0
            novo_flingers = flingers_atual + quantidade
            c.execute("UPDATE usuarios SET flingers = ? WHERE id = ?", (novo_flingers, usuario_id))
            conn.commit()
        conn.close()