"""
V002 — Adiciona guild_id à tabela usuarios para suporte multi-servidor.

Como o SQLite não suporta DROP COLUMN ou recriar PRIMARY KEY diretamente,
a estratégia é:
  1. Renomear a tabela atual para usuarios_backup
  2. Criar a nova tabela com PRIMARY KEY (id, guild_id)
  3. Copiar os dados, usando guild_id = 0 para registros existentes
  4. Remover o backup
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    # Verifica se a migração já foi aplicada manualmente (guild_id já existe)
    colunas = [row[1] for row in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
    if "guild_id" in colunas:
        # Garante que a PRIMARY KEY seja composta — se não for, refaz
        # (caso migrar_tabela() tenha sido chamado antes do runner)
        indices = conn.execute("PRAGMA index_list(usuarios)").fetchall()
        nomes   = [i[1] for i in indices]
        if any("guild_id" in n for n in nomes) or _pk_composta(conn):
            return  # já está correto

    conn.execute("ALTER TABLE usuarios RENAME TO usuarios_backup")

    conn.execute("""
        CREATE TABLE usuarios (
            id            INTEGER NOT NULL,
            guild_id      INTEGER NOT NULL,
            nome          TEXT,
            discriminator TEXT,
            data_registro TEXT,
            descricao     TEXT    DEFAULT '',
            xp            INTEGER DEFAULT 0,
            nivel         INTEGER DEFAULT 1,
            ultimo_xp     REAL    DEFAULT 0,
            flingers      INTEGER DEFAULT 0,
            PRIMARY KEY (id, guild_id)
        )
    """)

    conn.execute("""
        INSERT INTO usuarios (id, guild_id, nome, discriminator, data_registro,
                              descricao, xp, nivel, ultimo_xp, flingers)
        SELECT id, 0, nome, discriminator, data_registro,
               COALESCE(descricao, ''),
               COALESCE(xp, 0),
               COALESCE(nivel, 1),
               COALESCE(ultimo_xp, 0),
               COALESCE(flingers, 0)
        FROM usuarios_backup
    """)

    conn.execute("DROP TABLE usuarios_backup")
    conn.commit()


def _pk_composta(conn: sqlite3.Connection) -> bool:
    """Verifica se a PRIMARY KEY de usuarios já inclui guild_id."""
    rows = conn.execute("PRAGMA table_info(usuarios)").fetchall()
    pk_cols = [row[1] for row in rows if row[5] > 0]  # row[5] = pk index
    return "guild_id" in pk_cols