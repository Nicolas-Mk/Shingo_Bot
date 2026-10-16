"""
V00X — Cria a tabela anime_quests (quests de anime que rendem flingers).

Renomeie este arquivo para o próximo número da sua sequência de migrations.

Se a tabela já existir (versão anterior do comando, criada pelo próprio cog),
apenas adiciona as colunas novas.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_quests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id    INTEGER NOT NULL,
            guild_id      INTEGER NOT NULL DEFAULT 0,
            mal_username  TEXT    NOT NULL,
            mal_id        INTEGER NOT NULL,
            titulo        TEXT    NOT NULL,
            formato       TEXT,
            minutos       INTEGER NOT NULL,
            recompensa    INTEGER NOT NULL,
            prazo_dias    INTEGER NOT NULL DEFAULT 30,
            status        TEXT    NOT NULL DEFAULT 'oferecida',
            criada_em     TEXT    NOT NULL,
            aceita_em     TEXT,
            expira_em     TEXT,
            concluida_em  TEXT
        )
    """)

    # tabela criada pela versão anterior: acrescenta o que falta
    colunas = {row[1] for row in conn.execute("PRAGMA table_info(anime_quests)").fetchall()}
    novas = {
        "guild_id":   "INTEGER NOT NULL DEFAULT 0",
        "prazo_dias": "INTEGER NOT NULL DEFAULT 30",
        "expira_em":  "TEXT",
    }
    for nome, definicao in novas.items():
        if nome not in colunas:
            conn.execute(f"ALTER TABLE anime_quests ADD COLUMN {nome} {definicao}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_quests_usuario "
        "ON anime_quests (discord_id, status)"
    )
    conn.commit()