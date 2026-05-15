"""
popular_favoritos.py
────────────────────
Script avulso para popular o campo `is_favorite` em mal_snapshots
para TODOS os usuários já cadastrados no banco.

Execute uma única vez após aplicar a migration V011:
    python popular_favoritos.py

O script:
  1. Busca todos os usernames distintos em mal_usuarios.
  2. Para cada usuário, consulta a API do Jikan:
       GET https://api.jikan.moe/v4/users/{username}/favorites
  3. Marca is_favorite = 1 nos animes que estão nos favoritos.
  4. Zera is_favorite = 0 nos animes que NÃO estão nos favoritos
     (para corrigir dados obsoletos em re-execuções).
  5. Respeita o rate limit do Jikan (~3 req/s) com pausa entre usuários.
"""

import asyncio
import sqlite3
import aiohttp

DB_PATH = "usuarios.db"
JIKAN_BASE = "https://api.jikan.moe/v4"
PAUSA_ENTRE_USUARIOS = 1.5   # segundos — Jikan permite ~3 req/s


def carregar_todos_usuarios() -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT DISTINCT username FROM mal_usuarios").fetchall()
    return [r[0] for r in rows]


def atualizar_favoritos_db(username: str, ids_favoritos: set[str]):
    """
    Atualiza is_favorite para todos os animes do usuário no banco:
      - 1 se o anime_id está em ids_favoritos
      - 0 caso contrário
    """
    with sqlite3.connect(DB_PATH) as conn:
        # Marca favoritos
        if ids_favoritos:
            placeholders = ",".join("?" * len(ids_favoritos))
            conn.execute(
                f"""
                UPDATE mal_snapshots
                   SET is_favorite = 1
                 WHERE username = ?
                   AND tipo = 'anime'
                   AND item_id IN ({placeholders})
                """,
                (username, *ids_favoritos),
            )

        # Zera os que não são mais favoritos
        if ids_favoritos:
            placeholders = ",".join("?" * len(ids_favoritos))
            conn.execute(
                f"""
                UPDATE mal_snapshots
                   SET is_favorite = 0
                 WHERE username = ?
                   AND tipo = 'anime'
                   AND item_id NOT IN ({placeholders})
                """,
                (username, *ids_favoritos),
            )
        else:
            # Usuário não tem favoritos — zera tudo
            conn.execute(
                "UPDATE mal_snapshots SET is_favorite = 0 WHERE username = ? AND tipo = 'anime'",
                (username,),
            )
        conn.commit()


async def buscar_favoritos_jikan(
    session: aiohttp.ClientSession, username: str
) -> set[str]:
    """
    Retorna o conjunto de anime_ids que estão nos favoritos do usuário no MAL.
    Retorna set vazio em caso de erro ou lista privada.
    """
    url = f"{JIKAN_BASE}/users/{username}/favorites"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                animes = data.get("data", {}).get("anime", [])
                return {str(a["mal_id"]) for a in animes if "mal_id" in a}
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
    return set()


async def main():
    usuarios = carregar_todos_usuarios()

    if not usuarios:
        print("Nenhum usuário encontrado no banco. Nada a fazer.")
        return

    print(f"Populando favoritos para {len(usuarios)} usuário(s)...\n")

    async with aiohttp.ClientSession() as session:
        for i, username in enumerate(usuarios, start=1):
            print(f"[{i}/{len(usuarios)}] {username}")
            ids_favoritos = await buscar_favoritos_jikan(session, username)
            atualizar_favoritos_db(username, ids_favoritos)

            if ids_favoritos:
                print(f"  ✅ {len(ids_favoritos)} favorito(s) marcado(s).")
            else:
                print(f"  — Sem favoritos (ou lista privada).")

            if i < len(usuarios):
                await asyncio.sleep(PAUSA_ENTRE_USUARIOS)

    print("\n✅ Concluído! Todos os favoritos foram populados.")


if __name__ == "__main__":
    asyncio.run(main())