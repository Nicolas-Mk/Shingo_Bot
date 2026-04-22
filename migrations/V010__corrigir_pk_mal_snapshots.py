"""
V010 — Corrige a PRIMARY KEY da tabela mal_snapshots.

A V009 pode ter criado a tabela com guild_id na PK (versão antiga), ou com
a PK correta (username, tipo, item_id). Esta migration garante que a tabela
final sempre terá a PK sem guild_id, preservando os dados existentes.

SQLite não suporta ALTER TABLE para remover colunas da PK, então a estratégia
é: renomear a tabela antiga → recriar com a PK correta → copiar os dados
deduplicando por (username, tipo, item_id) → dropar a antiga.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    # Verifica se a tabela já existe com a estrutura correta
    cur = conn.execute("PRAGMA table_info(mal_snapshots)")
    colunas = {row[1] for row in cur.fetchall()}

    if not colunas:
        # Tabela não existe ainda — cria direto com a PK correta
        conn.execute("""
            CREATE TABLE mal_snapshots (
                username   TEXT NOT NULL,
                tipo       TEXT NOT NULL,
                item_id    TEXT NOT NULL,
                snapshot   TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (username, tipo, item_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mal_snapshots_lookup
            ON mal_snapshots (username, tipo)
        """)
        conn.commit()
        print("[Migrations] mal_snapshots criada com PK correta (sem guild_id).")
        return

    if "guild_id" not in colunas:
        # Já está com a estrutura correta
        print("[Migrations] mal_snapshots já está com PK correta, nada a fazer.")
        return

    # guild_id existe na tabela — precisa migrar
    print("[Migrations] Migrando mal_snapshots: removendo guild_id da PK...")

    conn.execute("ALTER TABLE mal_snapshots RENAME TO mal_snapshots_old")

    conn.execute("""
        CREATE TABLE mal_snapshots (
            username   TEXT NOT NULL,
            tipo       TEXT NOT NULL,
            item_id    TEXT NOT NULL,
            snapshot   TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username, tipo, item_id)
        )
    """)

    # Copia dados deduplicando — mantém o registro com updated_at mais recente
    conn.execute("""
        INSERT INTO mal_snapshots (username, tipo, item_id, snapshot, updated_at)
        SELECT username, tipo, item_id, snapshot, updated_at
        FROM (
            SELECT username, tipo, item_id, snapshot, updated_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY username, tipo, item_id
                       ORDER BY updated_at DESC
                   ) AS rn
            FROM mal_snapshots_old
        )
        WHERE rn = 1
    """)

    conn.execute("DROP TABLE mal_snapshots_old")

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mal_snapshots_lookup
        ON mal_snapshots (username, tipo)
    """)

    conn.commit()
    print("[Migrations] mal_snapshots migrada com sucesso (guild_id removido da PK).")