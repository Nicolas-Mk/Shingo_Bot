"""
V006 — Adiciona coluna 'icon' na tabela mal_usuarios para armazenar avatares em cache.

Armazena a imagem do Jikan já recortada em BLOB, evitando chamadas
repetidas à API que costuma falhar.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    conn.execute("""
        ALTER TABLE mal_usuarios
        ADD COLUMN icon BLOB DEFAULT NULL
    """)
    
    conn.commit()
    print("[Migrations] Coluna 'icon' adicionada à tabela mal_usuarios.")