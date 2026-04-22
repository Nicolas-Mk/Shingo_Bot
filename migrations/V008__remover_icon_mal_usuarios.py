"""
V008 — Remove a coluna 'icon' de 'mal_usuarios' após migração para 'mal_avatares'.

NOTA: Esta migração é OPCIONAL. Apenas execute se tiver certeza de ter
executado a migração de dados de V006 para V007.

Se você quiser manter a coluna icon por compatibilidade retroativa,
simplesmente não execute esta migração.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    # SQLite tem limitações com ALTER TABLE DROP COLUMN
    # Verificamos se a coluna ainda existe antes de tentar remover
    conn.execute("PRAGMA foreign_keys=OFF")
    
    # Verifica se a coluna 'icon' existe
    cursor = conn.execute("PRAGMA table_info(mal_usuarios)")
    colunas = {row[1] for row in cursor.fetchall()}
    
    if "icon" in colunas:
        # Cria tabela temporária sem a coluna 'icon'
        conn.execute("""
            CREATE TABLE mal_usuarios_temp (
                guild_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                PRIMARY KEY (guild_id, username)
            )
        """)
        
        # Copia dados da tabela antiga (sem a coluna icon)
        conn.execute("""
            INSERT INTO mal_usuarios_temp (guild_id, username)
            SELECT guild_id, username FROM mal_usuarios
        """)
        
        # Remove tabela antiga
        conn.execute("DROP TABLE mal_usuarios")
        
        # Renomeia tabela temporária para o nome original
        conn.execute("ALTER TABLE mal_usuarios_temp RENAME TO mal_usuarios")
        
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        print("[Migrations] Coluna 'icon' removida de 'mal_usuarios'.")
    else:
        print("[Migrations] Coluna 'icon' não encontrada em 'mal_usuarios', nada a fazer.")
        conn.commit()