"""
V003 — Cria a tabela guild_config para armazenar configurações por servidor.
Usada pelo ConfigCog para canais e outras preferências por guild.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id  INTEGER NOT NULL,
            chave     TEXT    NOT NULL,
            valor     TEXT    NOT NULL,
            PRIMARY KEY (guild_id, chave)
        )
    """)
    conn.commit()