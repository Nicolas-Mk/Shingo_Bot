"""
V011 — Adiciona coluna `is_favorite` na tabela mal_snapshots.

A coluna indica se o anime está na lista de favoritos públicos do usuário
no MyAnimeList (consultada via Jikan). O campo é populado pelo novo
comando /mal_top_servidor e atualizado pelo loop horário do MalTrackerCog.

SQLite não suporta ADD COLUMN com restrição NOT NULL sem default, então
usamos DEFAULT 0 (False).
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    cur = conn.execute("PRAGMA table_info(mal_snapshots)")
    colunas = {row[1] for row in cur.fetchall()}

    if "is_favorite" in colunas:
        print("[Migrations] Coluna is_favorite já existe em mal_snapshots, nada a fazer.")
        return

    conn.execute("""
        ALTER TABLE mal_snapshots
        ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0
    """)
    conn.commit()
    print("[Migrations] Coluna is_favorite adicionada a mal_snapshots.")