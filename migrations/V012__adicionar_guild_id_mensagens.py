"""
V012 — Adiciona guild_id à tabela mensagens.
Necessário para o MarkovCog separar o aprendizado por servidor.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    # ALTER TABLE no SQLite não levanta erro se a coluna já existir em versões
    # antigas, mas levanta OperationalError se existir — por isso usamos try/except.
    try:
        conn.execute("ALTER TABLE mensagens ADD COLUMN guild_id INTEGER")
    except Exception:
        pass  # Coluna já existe, sem problema

    # Índice para acelerar as queries do MarkovCog (SELECT por guild_id)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mensagens_guild
        ON mensagens (guild_id)
    """)
    conn.commit()