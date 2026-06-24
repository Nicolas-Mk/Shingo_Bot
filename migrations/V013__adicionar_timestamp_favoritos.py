"""
V013 — Cria tabela mal_usuarios_meta.
Armazena metadados por usuário do MAL (sem vínculo a guild), como o
timestamp da última sincronização de favoritos via Jikan, usado para
aplicar o cooldown semanal no MalTrackerCog.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_usuarios_meta (
            username          TEXT PRIMARY KEY,
            favoritos_sync_em TEXT
        )
    """)
    conn.commit()