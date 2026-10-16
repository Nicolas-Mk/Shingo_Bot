"""
V00Z — Adiciona a coluna cooldown_pago em anime_quests.

Renomeie este arquivo para o próximo número da sua sequência de migrations.

Quando o usuário compra o cooldown de 12h entre pedidos de quest, a última quest
dele é marcada com cooldown_pago = 1 e o cooldown passa a contar como zerado. O
pedido seguinte cria uma quest nova (cooldown_pago = 0) e o limite volta a valer.
"""
import sqlite3


def upgrade(conn: sqlite3.Connection):
    colunas = {row[1] for row in conn.execute("PRAGMA table_info(anime_quests)").fetchall()}
    if "cooldown_pago" not in colunas:
        conn.execute(
            "ALTER TABLE anime_quests ADD COLUMN cooldown_pago INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()