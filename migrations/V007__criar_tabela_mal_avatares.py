"""
V007 — Cria tabela 'mal_avatares' para armazenar avatares deduplicated por username.

Em vez de armazenar a imagem em cada linha de mal_usuarios,
usamos uma tabela separada que armazena por username (chave global),
evitando duplicação quando o mesmo usuário está em múltiplos servidores.

Estrutura:
    mal_avatares:
        - username (TEXT PRIMARY KEY): Nome único do usuário MAL
        - icon (BLOB): Imagem PNG comprimida (128x128, quality=60)
        - tamanho (INTEGER): Bytes da imagem comprimida
        - atualizado_em (TEXT): ISO format timestamp
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mal_avatares (
            username TEXT PRIMARY KEY,
            icon BLOB NOT NULL,
            tamanho INTEGER NOT NULL,
            atualizado_em TEXT NOT NULL
        )
    """)
    
    conn.commit()
    print("[Migrations] Tabela 'mal_avatares' criada com sucesso.")