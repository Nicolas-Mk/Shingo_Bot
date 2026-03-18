import sqlite3
from datetime import datetime


class UserManager:

    # ──────────────────────────────────────────
    #  CRUD
    # ──────────────────────────────────────────

    @staticmethod
    def registrar_usuario(user_id: int, guild_id: int, nome: str, discriminator: str) -> bool:
        """Registra o usuário no servidor. Retorna True se criado, False se já existia."""
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute('SELECT id FROM usuarios WHERE id = ? AND guild_id = ?', (user_id, guild_id))
        if not c.fetchone():
            data_registro = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            c.execute(
                'INSERT INTO usuarios (id, guild_id, nome, discriminator, data_registro) VALUES (?, ?, ?, ?, ?)',
                (user_id, guild_id, nome, discriminator, data_registro)
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False

    @staticmethod
    def buscar_usuario(user_id: int, guild_id: int):
        """Retorna (nome, discriminator, data_registro, descricao) ou None."""
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute(
            'SELECT nome, discriminator, data_registro, descricao FROM usuarios WHERE id = ? AND guild_id = ?',
            (user_id, guild_id)
        )
        dados = c.fetchone()
        conn.close()
        return dados

    @staticmethod
    def adicionar_flingers(user_id: int, guild_id: int, quantidade: int):
        conn = sqlite3.connect('usuarios.db')
        c = conn.cursor()
        c.execute('SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?', (user_id, guild_id))
        row = c.fetchone()
        if row:
            novo = (row[0] or 0) + quantidade
            c.execute(
                'UPDATE usuarios SET flingers = ? WHERE id = ? AND guild_id = ?',
                (novo, user_id, guild_id)
            )
            conn.commit()
        conn.close()