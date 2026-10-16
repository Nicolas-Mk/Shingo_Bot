"""
V0XX — Separa o dinheiro em carteira e banco na tabela usuarios.

Colunas novas:
  - banco: flingers guardados no banco do usuário. Esse saldo é protegido
           e não pode ser roubado. A coluna `flingers` passa a representar
           apenas a carteira (dinheiro "solto", sujeito a roubo).

Transição:
  Na mesma execução em que a coluna `banco` é criada, TODO o saldo atual de
  `flingers` é movido para `banco` e `flingers` é zerado. Assim ninguém fica
  com dinheiro exposto na carteira quando o sistema de roubo for ativado.

A transferência só acontece se a coluna foi criada NESTA execução. Se a
migration rodar de novo (coluna já existe), nada é movido — senão o dinheiro
que as pessoas ganharam depois na carteira iria parar no banco sem querer.

Renomeie este arquivo pro próximo número de versão sequencial do seu sistema
de migrations (ex.: V013_adiciona_banco_usuarios.py) antes de rodar.

SQLite não suporta ADD COLUMN com NOT NULL sem default, então usamos DEFAULT 0.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    cur = conn.execute("PRAGMA table_info(usuarios)")
    colunas_existentes = {row[1] for row in cur.fetchall()}

    if "banco" in colunas_existentes:
        print("[Migrations] Coluna banco já existe em usuarios, pulando.")
        return

    conn.execute("ALTER TABLE usuarios ADD COLUMN banco INTEGER NOT NULL DEFAULT 0")
    print("[Migrations] Coluna banco adicionada a usuarios.")

    # Move todo o dinheiro atual da carteira para o banco (evita roubo em massa)
    cur = conn.execute(
        "UPDATE usuarios SET banco = banco + flingers, flingers = 0 WHERE flingers > 0"
    )
    print(f"[Migrations] Saldo de {cur.rowcount} usuário(s) transferido da carteira para o banco.")

    conn.commit()