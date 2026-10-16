"""
V00X — Cria as tabelas de estúdios de anime (ranking de estúdios do /mal_estudios).

Renomeie este arquivo para o próximo número da sua sequência de migrations.

Tabelas:
  - mal_estudios:             estúdios (id = mal_id do estúdio no MAL/Jikan)
  - mal_anime_estudios:       relação N:N anime <-> estúdio (co-produções têm vários)
  - mal_anime_estudios_sync:  animes já consultados no Jikan (inclusive os sem
                              estúdio ou 404), para o sync não repetir consultas

Idempotente: pode rodar mais de uma vez sem efeito colateral.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_estudios (
            id   INTEGER PRIMARY KEY,
            nome TEXT NOT NULL
        )
    """)

    # anime_id é TEXT para casar com mal_snapshots.item_id (salvo como str)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_anime_estudios (
            anime_id   TEXT    NOT NULL,
            estudio_id INTEGER NOT NULL REFERENCES mal_estudios(id),
            PRIMARY KEY (anime_id, estudio_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_anime_estudios_sync (
            anime_id TEXT PRIMARY KEY,
            sync_em  TEXT NOT NULL
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_anime_estudios_estudio "
        "ON mal_anime_estudios (estudio_id)"
    )
    # acelera o JOIN do ranking (mal_snapshots.item_id -> mal_anime_estudios.anime_id)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_snapshots_tipo_item "
        "ON mal_snapshots (tipo, item_id)"
    )
    conn.commit()