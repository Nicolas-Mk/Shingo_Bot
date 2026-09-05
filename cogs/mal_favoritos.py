"""
popular_favoritos.py
────────────────────
Script avulso para popular o campo `is_favorite` em mal_snapshots
para TODOS os usuários já cadastrados no banco — cobrindo tanto
animes quanto mangás.

Execute uma única vez após aplicar a migration V011:
    python popular_favoritos.py

O script:
  1. Busca todos os usernames distintos em mal_usuarios.
  2. Para cada usuário, consulta a API do Jikan:
       GET https://api.jikan.moe/v4/users/{username}/favorites
     (essa resposta já traz `data.anime` e `data.manga` juntos,
     então uma única chamada cobre os dois tipos)
  3. Marca is_favorite = 1 nos animes/mangás que estão nos favoritos.
  4. Zera is_favorite = 0 nos animes/mangás que NÃO estão nos favoritos
     (para corrigir dados obsoletos em re-execuções).
  5. Respeita o rate limit do Jikan (~3 req/s) com pausa entre usuários.
"""

import asyncio
import sqlite3
import aiohttp

DB_PATH = "usuarios.db"
JIKAN_BASE = "https://api.jikan.moe/v4"
PAUSA_ENTRE_USUARIOS = 1.5   # segundos — Jikan permite ~3 req/s

TIPOS = ("anime", "manga")


def carregar_todos_usuarios() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT DISTINCT username FROM mal_usuarios").fetchall()
    return [r[0] for r in rows]


def atualizar_favoritos_db(username: str, ids_favoritos: dict[str, set[str]]):
    """
    Atualiza is_favorite para todos os animes/mangás do usuário no banco:
      - 1 se o item_id está no conjunto de favoritos daquele tipo
      - 0 caso contrário

    ids_favoritos: {"anime": {...ids...}, "manga": {...ids...}}
    """
    with sqlite3.connect(DB_PATH) as conn:
        for tipo in TIPOS:
            ids = ids_favoritos.get(tipo, set())

            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"""
                    UPDATE mal_snapshots
                       SET is_favorite = 1
                     WHERE username = ?
                       AND tipo = ?
                       AND item_id IN ({placeholders})
                    """,
                    (username, tipo, *ids),
                )
                conn.execute(
                    f"""
                    UPDATE mal_snapshots
                       SET is_favorite = 0
                     WHERE username = ?
                       AND tipo = ?
                       AND item_id NOT IN ({placeholders})
                    """,
                    (username, tipo, *ids),
                )
            else:
                # Usuário não tem favoritos desse tipo — zera tudo
                conn.execute(
                    "UPDATE mal_snapshots SET is_favorite = 0 WHERE username = ? AND tipo = ?",
                    (username, tipo),
                )
        conn.commit()


async def buscar_favoritos_jikan(
    session: aiohttp.ClientSession, username: str
) -> dict[str, set[str]]:
    """
    Retorna {"anime": {ids...}, "manga": {ids...}} com os favoritos
    do usuário no MAL. Retorna sets vazios em caso de erro ou lista privada.
    """
    url = f"{JIKAN_BASE}/users/{username}/favorites"
    resultado = {"anime": set(), "manga": set()}
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                favoritos = data.get("data", {})
                resultado["anime"] = {
                    str(a["mal_id"]) for a in favoritos.get("anime", []) if "mal_id" in a
                }
                resultado["manga"] = {
                    str(m["mal_id"]) for m in favoritos.get("manga", []) if "mal_id" in m
                }
            elif resp.status == 404:
                print(f"  ⚠️  Usuário '{username}' não encontrado no Jikan.")
            elif resp.status == 403:
                print(f"  ⚠️  Favoritos de '{username}' são privados.")
            else:
                print(f"  ⚠️  Jikan retornou {resp.status} para '{username}'.")
    except asyncio.TimeoutError:
        print(f"  ⚠️  Timeout ao buscar favoritos de '{username}'.")
    except Exception as e:
        print(f"  ⚠️  Erro ao buscar favoritos de '{username}': {e}")
    return resultado


async def main():
    usuarios = carregar_todos_usuarios()

    if not usuarios:
        print("Nenhum usuário encontrado no banco. Nada a fazer.")
        return

    print(f"Populando favoritos (anime + mangá) para {len(usuarios)} usuário(s)...\n")

    async with aiohttp.ClientSession() as session:
        for i, username in enumerate(usuarios, start=1):
            print(f"[{i}/{len(usuarios)}] {username}")
            ids_favoritos = await buscar_favoritos_jikan(session, username)
            atualizar_favoritos_db(username, ids_favoritos)

            n_anime = len(ids_favoritos["anime"])
            n_manga = len(ids_favoritos["manga"])

            if n_anime or n_manga:
                print(f"  ✅ {n_anime} anime(s) e {n_manga} mangá(s) marcados como favorito.")
            else:
                print(f"  — Sem favoritos (ou lista privada).")

            if i < len(usuarios):
                await asyncio.sleep(PAUSA_ENTRE_USUARIOS)

    print("\n✅ Concluído! Todos os favoritos (anime + mangá) foram populados.")


if __name__ == "__main__":
    asyncio.run(main())