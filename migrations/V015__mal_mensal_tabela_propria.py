"""
V0XY — Move o progresso mensal de mal_snapshots para uma tabela própria.

A migration anterior guardava o progresso mensal como colunas extras direto
em mal_snapshots. Isso misturava dois conceitos diferentes na mesma tabela
(o snapshot ATUAL do item vs. um acumulador temporal com regra de reset por
mês), então esta migration separa isso:

  1. Cria mal_progresso_mensal, com o mesmo papel das colunas removidas
     abaixo, mas numa tabela própria (PRIMARY KEY (username, tipo, item_id)).
  2. Copia os dados existentes dessas colunas de mal_snapshots pra lá.
  3. Remove as colunas de mal_snapshots.

Depois de rodar esta migration, rode também `popular_progresso_mensal.py`
uma vez: ela garante que TODOS os itens já sincronizados (não só os que já
tinham progresso registrado antes) tenham uma linha baseline na tabela nova,
pronta pra ser preenchida pelo loop horário dali em diante.
"""
import sqlite3

_COLUNAS_ANTIGAS = [
    "watched_at", "mes_referencia", "episodios_mes",
    "capitulos_mes", "volumes_mes", "completou_mes",
]


def _tabela_tem_colunas(conn: sqlite3.Connection, tabela: str, colunas: list[str]) -> bool:
    cur = conn.execute(f"PRAGMA table_info({tabela})")
    existentes = {row[1] for row in cur.fetchall()}
    return all(c in existentes for c in colunas)


def upgrade(conn: sqlite3.Connection):
    # ── 1. Cria a tabela nova ──
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mal_progresso_mensal (
            username        TEXT NOT NULL,
            tipo            TEXT NOT NULL,   -- 'anime' ou 'manga'
            item_id         TEXT NOT NULL,
            watched_at      TEXT NOT NULL DEFAULT '',
            mes_referencia  TEXT NOT NULL DEFAULT '',  -- "YYYY-MM"
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

    # ── 2. Copia os dados existentes (se as colunas antigas ainda existirem) ──
    if _tabela_tem_colunas(conn, "mal_snapshots", _COLUNAS_ANTIGAS):
        conn.execute(
            """
            INSERT OR IGNORE INTO mal_progresso_mensal
                (username, tipo, item_id, watched_at, mes_referencia,
                 episodios_mes, capitulos_mes, volumes_mes, completou_mes)
            SELECT username, tipo, item_id, watched_at, mes_referencia,
                   episodios_mes, capitulos_mes, volumes_mes, completou_mes
            FROM mal_snapshots
            """
        )
        print("[Migrations] Dados de progresso mensal copiados de mal_snapshots para mal_progresso_mensal.")

        # ── 3. Remove as colunas antigas de mal_snapshots ──
        try:
            for coluna in _COLUNAS_ANTIGAS:
                conn.execute(f"ALTER TABLE mal_snapshots DROP COLUMN {coluna}")
            print("[Migrations] Colunas de progresso mensal removidas de mal_snapshots (via DROP COLUMN).")
        except sqlite3.OperationalError as e:
            # SQLite < 3.35.0 não suporta ALTER TABLE ... DROP COLUMN — reconstrói a tabela.
            print(f"[Migrations] DROP COLUMN não suportado ({e}); reconstruindo mal_snapshots manualmente...")
            _reconstruir_mal_snapshots_sem_colunas_antigas(conn)
    else:
        print("[Migrations] mal_snapshots já não tem as colunas de progresso mensal, nada a remover.")

    conn.commit()


def _reconstruir_mal_snapshots_sem_colunas_antigas(conn: sqlite3.Connection):
    """
    Fallback para SQLite antigo (sem suporte a DROP COLUMN): recria
    mal_snapshots só com as colunas restantes, copia os dados e troca a
    tabela antiga pela nova. Recria também o índice único usado pelo
    ON CONFLICT(username, tipo, item_id) em salvar_snapshots_em_lote.
    """
    cur = conn.execute("PRAGMA table_info(mal_snapshots)")
    todas_colunas = [row[1] for row in cur.fetchall()]
    colunas_mantidas = [c for c in todas_colunas if c not in _COLUNAS_ANTIGAS]
    lista_colunas = ", ".join(colunas_mantidas)

    conn.execute("ALTER TABLE mal_snapshots RENAME TO mal_snapshots_old")
    conn.execute(f"CREATE TABLE mal_snapshots AS SELECT {lista_colunas} FROM mal_snapshots_old")
    conn.execute("DROP TABLE mal_snapshots_old")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mal_snapshots_unico "
        "ON mal_snapshots (username, tipo, item_id)"
    )