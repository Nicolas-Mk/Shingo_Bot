"""
V005 — Cria as tabelas da loja por servidor.

Tabelas:
  loja_itens      — itens criados pelos admins por servidor
  loja_compras    — registro de compras por usuário/servidor
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS loja_itens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            nome        TEXT    NOT NULL,
            descricao   TEXT    NOT NULL,
            preco       INTEGER NOT NULL,
            estoque     INTEGER NOT NULL DEFAULT -1,
            ativo       INTEGER NOT NULL DEFAULT 1,
            icone       TEXT    DEFAULT NULL,
            UNIQUE(guild_id, nome)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS loja_compras (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            item_id     INTEGER NOT NULL,
            quantidade  INTEGER NOT NULL DEFAULT 1,
            comprado_em TEXT    NOT NULL,
            FOREIGN KEY (item_id) REFERENCES loja_itens(id)
        )
    """)

    conn.commit()