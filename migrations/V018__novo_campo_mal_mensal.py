"""
V0XZ — Adiciona duracao_ep_min em mal_progresso_mensal.

Duração média (em minutos) de um episódio do anime, usada pelo /mal_mensal
para normalizar a pontuação de episódios. 0 = desconhecida (sem ajuste).

Renomeie este arquivo pro próximo número de versão sequencial do seu sistema
de migrations antes de rodar.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    cur = conn.execute("PRAGMA table_info(mal_progresso_mensal)")
    existentes = {row[1] for row in cur.fetchall()}

    if "duracao_ep_min" in existentes:
        print("[Migrations] Coluna duracao_ep_min já existe, pulando.")
    else:
        conn.execute(
            "ALTER TABLE mal_progresso_mensal "
            "ADD COLUMN duracao_ep_min INTEGER NOT NULL DEFAULT 0"
        )
        print("[Migrations] Coluna duracao_ep_min adicionada a mal_progresso_mensal.")

    conn.commit()