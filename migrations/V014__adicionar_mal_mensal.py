"""
V0XX — Adiciona colunas de progresso mensal em mal_snapshots.

Em vez de uma tabela de histórico separada, o progresso mensal (usado pelo
/mal_mensal) é acumulado direto na própria linha do item em mal_snapshots.
O acumulador é reiniciado sempre que a linha é tocada num mês diferente do
guardado em `mes_referencia` — então mesmo progresso parcial (sem terminar
o anime/mangá) é contabilizado, não só conclusões.

Colunas novas:
  - watched_at:      timestamp (ISO UTC) da última mudança de progresso
                      detectada para este item (episódio/capítulo/volume ou
                      conclusão). Vazio ('') se o item nunca teve progresso
                      detectado (ex.: acabou de entrar na sincronização inicial).
  - mes_referencia:   "YYYY-MM" do mês a que os acumuladores abaixo se referem.
  - episodios_mes:    episódios assistidos acumulados em mes_referencia (anime).
  - capitulos_mes:    capítulos lidos acumulados em mes_referencia (mangá).
  - volumes_mes:      volumes lidos acumulados em mes_referencia (mangá).
  - completou_mes:    1 se o item foi marcado "completed" durante mes_referencia
                      (permanece 1 até o mês virar, mesmo que o status mude de
                      novo depois).

Renomeie este arquivo pro próximo número de versão sequencial do seu sistema
de migrations (ex.: V012_progresso_mensal_snapshots.py) antes de rodar.

SQLite não suporta ADD COLUMN com NOT NULL sem default, então usamos DEFAULT 0/''.
"""
import sqlite3

_COLUNAS_NOVAS = [
    ("watched_at",     "TEXT NOT NULL DEFAULT ''"),
    ("mes_referencia", "TEXT NOT NULL DEFAULT ''"),
    ("episodios_mes",  "INTEGER NOT NULL DEFAULT 0"),
    ("capitulos_mes",  "INTEGER NOT NULL DEFAULT 0"),
    ("volumes_mes",    "INTEGER NOT NULL DEFAULT 0"),
    ("completou_mes",  "INTEGER NOT NULL DEFAULT 0"),
]


def upgrade(conn: sqlite3.Connection):
    cur = conn.execute("PRAGMA table_info(mal_snapshots)")
    colunas_existentes = {row[1] for row in cur.fetchall()}

    for nome, definicao in _COLUNAS_NOVAS:
        if nome in colunas_existentes:
            print(f"[Migrations] Coluna {nome} já existe em mal_snapshots, pulando.")
            continue
        conn.execute(f"ALTER TABLE mal_snapshots ADD COLUMN {nome} {definicao}")
        print(f"[Migrations] Coluna {nome} adicionada a mal_snapshots.")

    conn.commit()