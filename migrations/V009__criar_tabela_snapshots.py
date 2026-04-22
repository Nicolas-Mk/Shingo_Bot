"""
V009 — Cria a tabela mal_snapshots para persistir o estado de cada item
        monitorado por usuário, eliminando a dependência de memória RAM.

Snapshots são por username (sem guild_id), pois o mesmo usuário pode estar
registrado em múltiplas guilds e não faz sentido duplicar sua lista inteira.
A aplicação dos filtros por guild acontece no momento de gerar o relatório.

Isso resolve o bug em que reinicializações do bot fazem o estado ser perdido,
causando detecções incorretas (ex: "completed" sendo reportado novamente ao
invés de uma troca de nota).
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_snapshots (
            username   TEXT NOT NULL,
            tipo       TEXT NOT NULL,  -- 'anime' ou 'manga'
            item_id    TEXT NOT NULL,
            snapshot   TEXT NOT NULL,  -- JSON do _snapshot()
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username, tipo, item_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mal_snapshots_lookup
        ON mal_snapshots (username, tipo)
    """)
    conn.commit()
    print("[Migrations] Tabela mal_snapshots criada.")