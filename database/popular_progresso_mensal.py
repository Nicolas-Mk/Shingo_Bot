"""
popular_progresso_mensal.py
────────────────────────────
Script avulso para popular mal_progresso_mensal com uma linha "baseline"
(zerada, sem mes_referencia) para TODO item já sincronizado em
mal_snapshots — sem precisar chamar a API do MAL de novo, já que os dados
já estão no banco local.

A partir daí, é o loop horário do MalTrackerCog (monitorar_loop) que vai
preenchendo os acumuladores de mal_progresso_mensal conforme detecta
mudanças reais de progresso.

Execute uma única vez após aplicar a migration V0XY (que cria a tabela
mal_progresso_mensal):
    python popular_progresso_mensal.py

Idempotente (INSERT OR IGNORE) — seguro rodar mais de uma vez; itens que já
têm linha na tabela não são tocados (não sobrescreve progresso já acumulado).
"""
import sqlite3

DB_PATH = "usuarios.db"


def popular():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mal_progresso_mensal (
                username        TEXT NOT NULL,
                tipo            TEXT NOT NULL,
                item_id         TEXT NOT NULL,
                watched_at      TEXT NOT NULL DEFAULT '',
                mes_referencia  TEXT NOT NULL DEFAULT '',
                episodios_mes   INTEGER NOT NULL DEFAULT 0,
                capitulos_mes   INTEGER NOT NULL DEFAULT 0,
                volumes_mes     INTEGER NOT NULL DEFAULT 0,
                completou_mes   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (username, tipo, item_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_progresso_mensal_username_tipo_mes "
            "ON mal_progresso_mensal (username, tipo, mes_referencia)"
        )

        total_snapshots = conn.execute("SELECT COUNT(*) FROM mal_snapshots").fetchone()[0]

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO mal_progresso_mensal
                (username, tipo, item_id, watched_at, mes_referencia,
                 episodios_mes, capitulos_mes, volumes_mes, completou_mes)
            SELECT username, tipo, item_id, '', '', 0, 0, 0, 0
            FROM mal_snapshots
            """
        )
        novas_linhas = cur.rowcount
        conn.commit()

        print(
            f"[Popular Progresso Mensal] {novas_linhas} linha(s) nova(s) criada(s) "
            f"(de {total_snapshots} itens em mal_snapshots)."
        )


if __name__ == "__main__":
    popular()