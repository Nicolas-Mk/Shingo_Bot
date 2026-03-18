"""
V004 — Cria as tabelas do MAL Tracker por servidor.
Substitui os arquivos mal_usuarios.json e mal_filtros.json.

Se os arquivos JSON existirem, migra os dados para o banco
usando guild_id = 0 como placeholder e renomeia os arquivos
para .migrado para evitar reprocessamento.
"""
import os
import json
import sqlite3

USUARIOS_FILE = "mal_usuarios.json"
FILTROS_FILE  = "mal_filtros.json"

TODOS_STATUS_ANIME = ["watching", "completed", "on_hold", "dropped", "plan_to_watch"]
TODOS_STATUS_MANGA = ["reading",  "completed", "on_hold", "dropped", "plan_to_read"]


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_usuarios (
            guild_id INTEGER NOT NULL,
            username TEXT    NOT NULL,
            PRIMARY KEY (guild_id, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_filtros (
            guild_id INTEGER NOT NULL,
            tipo     TEXT    NOT NULL,
            status   TEXT    NOT NULL,
            PRIMARY KEY (guild_id, tipo, status)
        )
    """)

    conn.commit()

    # Migra dados do JSON se existirem
    if os.path.exists(USUARIOS_FILE):
        with open(USUARIOS_FILE, "r") as f:
            usuarios = json.load(f)
        for u in usuarios:
            conn.execute(
                "INSERT OR IGNORE INTO mal_usuarios (guild_id, username) VALUES (?, ?)",
                (0, u)
            )
        conn.commit()
        os.rename(USUARIOS_FILE, USUARIOS_FILE + ".migrado")
        print(f"[Migrations] {len(usuarios)} usuário(s) MAL migrados do JSON (guild_id=0).")

    if os.path.exists(FILTROS_FILE):
        with open(FILTROS_FILE, "r") as f:
            filtros = json.load(f)
        for tipo, statuses in filtros.items():
            for status in statuses:
                conn.execute(
                    "INSERT OR IGNORE INTO mal_filtros (guild_id, tipo, status) VALUES (?, ?, ?)",
                    (0, tipo, status)
                )
        conn.commit()
        os.rename(FILTROS_FILE, FILTROS_FILE + ".migrado")
        print("[Migrations] Filtros MAL migrados do JSON (guild_id=0).")