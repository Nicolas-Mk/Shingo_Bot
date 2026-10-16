"""
V010 — Pedidos da guilda (quest aberta a qualquer pessoa do servidor).

Renomeie este arquivo para o próximo número da sua sequência de migrations.

Cria:
  - anime_pedidos_guilda: cada pedido publicado no canal do MAL Tracker
    (status: aberto -> aceito | expirado).
  - anime_pedido_agenda: quando sai o próximo pedido de cada servidor (guarda no banco
    para o intervalo de 4~6h sobreviver a reinício do bot).
  - coluna anime_quests.origem ('pessoal' = pedida com /anime_aleatorio,
    'guilda' = aceita de um pedido da guilda). Só as 'pessoal' contam no cooldown.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_pedidos_guilda (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            canal_id    INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            mal_id      INTEGER NOT NULL,
            titulo      TEXT    NOT NULL,
            formato     TEXT,
            minutos     INTEGER NOT NULL,
            recompensa  INTEGER NOT NULL,
            prazo_dias  INTEGER NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'aberto',
            criado_em   TEXT    NOT NULL,
            aceito_por  INTEGER,
            aceito_em   TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_pedidos_guild_status "
        "ON anime_pedidos_guilda (guild_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_pedidos_message "
        "ON anime_pedidos_guilda (message_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_pedido_agenda (
            guild_id    INTEGER PRIMARY KEY,
            proximo_em  TEXT    NOT NULL
        )
    """)

    colunas = {row[1] for row in conn.execute("PRAGMA table_info(anime_quests)").fetchall()}
    if "origem" not in colunas:
        conn.execute(
            "ALTER TABLE anime_quests ADD COLUMN origem TEXT NOT NULL DEFAULT 'pessoal'"
        )

    conn.commit()