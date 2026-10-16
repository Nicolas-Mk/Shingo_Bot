"""
V00Y — Cria a tabela anime_quest_recusas (contador de quests recusadas por servidor).

Renomeie este arquivo para o próximo número da sua sequência de migrations.

O contador aumenta a recompensa da próxima quest oferecida no servidor e volta
a zero quando alguém aceita uma quest. Se a versão anterior guardou o contador
em guild_config (chave 'anime_quest_recusas'), o valor é copiado para cá e a
chave antiga é removida.
"""
import sqlite3
from datetime import datetime, timezone


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anime_quest_recusas (
            guild_id       INTEGER PRIMARY KEY,
            recusas        INTEGER NOT NULL DEFAULT 0,
            atualizado_em  TEXT    NOT NULL
        )
    """)

    # traz o contador da versão anterior (guild_config), se existir
    tem_config = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'guild_config'"
    ).fetchone()
    if tem_config:
        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT OR IGNORE INTO anime_quest_recusas (guild_id, recusas, atualizado_em) "
            "SELECT guild_id, CAST(valor AS INTEGER), ? FROM guild_config "
            "WHERE chave = 'anime_quest_recusas'",
            (agora,),
        )
        conn.execute("DELETE FROM guild_config WHERE chave = 'anime_quest_recusas'")

    conn.commit()