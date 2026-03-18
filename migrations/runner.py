"""
Migration runner — inspirado no Flyway.

Convenção de nomes:
    V001__descricao.py   (V + número com zeros + __ + descrição)

Cada arquivo de migration deve expor uma função:
    def upgrade(conn: sqlite3.Connection) -> None

O runner executa as migrations pendentes em ordem e registra
cada uma na tabela `schema_migrations` após o sucesso.
"""

import os
import re
import sqlite3
import importlib.util
from datetime import datetime

DB_PATH         = "usuarios.db"
MIGRATIONS_DIR  = os.path.dirname(__file__)
VERSION_PATTERN = re.compile(r"^V(\d+)__(.+)\.py$")


def _criar_tabela_controle(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT    NOT NULL PRIMARY KEY,
            descricao   TEXT    NOT NULL,
            aplicada_em TEXT    NOT NULL
        )
    """)
    conn.commit()


def _versoes_aplicadas(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def _registrar(conn: sqlite3.Connection, version: str, descricao: str):
    conn.execute(
        "INSERT INTO schema_migrations (version, descricao, aplicada_em) VALUES (?, ?, ?)",
        (version, descricao, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()


def _carregar_migrations() -> list[tuple[str, str, str]]:
    """
    Retorna lista de (version, descricao, filepath) ordenada por version.
    """
    arquivos = []
    for nome in os.listdir(MIGRATIONS_DIR):
        m = VERSION_PATTERN.match(nome)
        if m:
            version   = m.group(1).zfill(3)
            descricao = m.group(2).replace("_", " ")
            filepath  = os.path.join(MIGRATIONS_DIR, nome)
            arquivos.append((version, descricao, filepath))
    return sorted(arquivos, key=lambda x: x[0])


def rodar_migrations():
    conn = sqlite3.connect(DB_PATH)

    _criar_tabela_controle(conn)
    aplicadas = _versoes_aplicadas(conn)
    migrations = _carregar_migrations()

    pendentes = [(v, d, f) for v, d, f in migrations if v not in aplicadas]

    if not pendentes:
        print("[Migrations] Banco de dados atualizado, nenhuma migration pendente.")
        conn.close()
        return

    for version, descricao, filepath in pendentes:
        print(f"[Migrations] Aplicando V{version}: {descricao}...")
        try:
            spec   = importlib.util.spec_from_file_location(f"migration_{version}", filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade(conn)
            _registrar(conn, version, descricao)
            print(f"[Migrations] ✅ V{version} aplicada com sucesso.")
        except Exception as e:
            conn.close()
            raise RuntimeError(f"[Migrations] ❌ Falha na V{version} ({descricao}): {e}") from e

    conn.close()
    print(f"[Migrations] {len(pendentes)} migration(s) aplicada(s).")