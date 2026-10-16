import asyncio
import json
import math
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ----------------------------- CONFIGURAÇÃO -----------------------------
DB_PATH = "usuarios.db"       # mesmo banco do resto do bot
TABLE = "mal_snapshots"       # tabela dos snapshots do MAL
ANILIST_URL = "https://graphql.anilist.co"
COR_EMBED = 0x2E51A2
COR_PEDIDO = 0xE67E22

FLINGERS_POR_MINUTO = 6       # recompensa = minutos totais do anime * isso
COOLDOWN_HORAS = 12           # intervalo entre pedidos de quest (conta desde o pedido)
TEMPO_PARA_ACEITAR = 600      # segundos até a oferta expirar (renova a cada clique)
INTERVALO_VERIFICACAO_MIN = 60 # de quanto em quanto tempo checa quests concluídas/vencidas

# Quantas quests aceitas (em andamento) cada usuário pode ter ao mesmo tempo por servidor.
# Vale para quests pessoais e pedidos da guilda juntos.
MAX_QUESTS_ATIVAS = 2

# Bônus "cata-lixo": abaixo da nota de corte, +0.05x por ponto (0.1 na escala de 10).
# Ex.: nota 55 -> 1 + (65 - 55) * 0.05 = 1.5x
NOTA_CORTE = 65
BONUS_POR_PONTO = 0.05

# Cada quest recusada aumenta a recompensa da próxima oferta do servidor (composto)
# até alguém aceitar uma quest; aí o contador zera (tabela anime_quest_recusas).
# (Pedidos da guilda não mexem nesse contador e não usam esse multiplicador.)
AUMENTO_POR_RECUSA = 1.01

# Canal onde o aviso de quest concluída e os pedidos da guilda são postados
# (mesmo do /config_canal "MAL Tracker").
CHAVE_CANAL_MAL = "canal_mal"

# Filtros pagos do /anime_aleatorio: cada um custa o valor abaixo (os dois juntos somam).
# O débito acontece quando a oferta é criada (anime encontrado); se nenhum anime servir,
# ou se o AniList falhar, nada é cobrado. Não há reembolso se a oferta for recusada/expirar.
CUSTO_FILTRO_GENERO = 100
CUSTO_FILTRO_DECADA = 100

# Reroll da oferta (botão 🎲 na mensagem da quest): o n-ésimo reroll custa
# CUSTOS_REROLL[n-1], então são no máximo len(CUSTOS_REROLL) rerolls por oferta.
# Os filtros pagos continuam valendo no reroll, sem cobrar de novo. O reroll não
# conta como recusa e não mexe no cooldown. Só cobra se achar outro anime.
CUSTOS_REROLL = (200, 300, 400)

# Comprar o cooldown de 12h entre pedidos (botão na mensagem de cooldown).
CUSTO_COMPRAR_COOLDOWN = 500

# ---- Pedido da guilda ----
# A cada PEDIDO_MIN_HORAS~PEDIDO_MAX_HORAS (sorteado) o bot publica no canal do MAL Tracker
# um anime que QUALQUER pessoa do servidor pode aceitar (primeiro a clicar leva). Sem reroll.
# Só um pedido fica aberto por servidor: quando sai o próximo, o anterior expira.
# O pedido NÃO conta no cooldown de 12h e não mexe no contador de recusas.
PEDIDO_MIN_HORAS = 4
PEDIDO_MAX_HORAS = 6
PEDIDO_RETRY_MIN = 30          # se não achar anime/publicar, tenta de novo em tantos minutos
INTERVALO_PEDIDOS_MIN = 5      # de quanto em quanto tempo o loop vê se algum servidor está na hora
MULT_PEDIDO_GUILDA = 1.0       # multiplicador extra da recompensa (1.0 = igual às quests normais)

# Chave de guild_config do módulo (o ConfigCog guarda "modulo_<nome>_ativo"; sem registro = ativo).
CHAVE_MODULO_PEDIDO = "modulo_pedido_guilda_ativo"

# Tipos sorteados para o pedido: (formato, duração, peso). duração = chave de DURACOES (só TV).
PEDIDO_SORTEIO = [
    ("TV",    "one_cour", 4),
    ("TV",    "two_cour", 3),
    ("TV",    "longo",    1),
    ("MOVIE", None,       3),
    ("OVA",   None,       1),
    ("ONA",   None,       1),
]

# Gêneros do AniList: rótulo no Discord -> valor da API (máx. 25 opções no Discord).
GENEROS = {
    "Ação": "Action",
    "Aventura": "Adventure",
    "Comédia": "Comedy",
    "Drama": "Drama",
    "Ecchi": "Ecchi",
    "Fantasia": "Fantasy",
    "Terror": "Horror",
    "Garota mágica": "Mahou Shoujo",
    "Mecha": "Mecha",
    "Música": "Music",
    "Mistério": "Mystery",
    "Psicológico": "Psychological",
    "Romance": "Romance",
    "Ficção científica": "Sci-Fi",
    "Slice of Life": "Slice of Life",
    "Esportes": "Sports",
    "Sobrenatural": "Supernatural",
    "Suspense": "Thriller",
}

# Década -> (rótulo, estreia mínima, estreia máxima), datas no formato FuzzyDateInt
# (YYYYMMDD) do AniList; None = sem limite. Os dois limites são exclusivos:
#   1970s = 19691231 < data de estreia < 19800000.
# A chave "60" vale para 1969 ou antes (sem limite inferior).
DECADAS = {
    "60":   ("Anos 60 ou antes", None,     19700000),
    "70":   ("Anos 70",          19691231, 19800000),
    "80":   ("Anos 80",          19791231, 19900000),
    "90":   ("Anos 90",          19891231, 20000000),
    "2000": ("Anos 2000",        19991231, 20100000),
    "2010": ("Anos 2010",        20091231, 20200000),
    "2020": ("Anos 2020",        20191231, None),
}

# Prazo da quest: 2 dias por episódio, com mínimo de 30 dias
# (coloque PRAZO_MINIMO_DIAS = 0 para remover o mínimo).
PRAZO_MINIMO_DIAS = 15
DIAS_POR_EPISODIO = 2

# Piso de popularidade (nº de usuários com o anime na lista do AniList).
# Se não houver resultado com o piso, o código tenta de novo sem ele.
POPULARIDADE_MIN = {"TV": 5000, "MOVIE": 3000, "OVA": 1500, "ONA": 1500, "SPECIAL": 1500}

# duração -> (mínimo, máximo) de episódios, ambos inclusivos
DURACOES = {
    "one_cour":    (1, 15),
    "two_cour":    (16, 30),
    "longo":       (31, 50),
    "muito_longo": (101, 100000),
}

POR_PAGINA = 50               # candidatos por requisição ao AniList (máx. do AniList)
PAGINA_MAXIMA = 100           # o AniList não entrega além de ~5000 resultados (100 x 50)
MAX_CONSULTAS = 8             # nº máx. de requisições ao AniList por faixa de popularidade

# Relações que exigem que o anime relacionado já tenha sido visto.
RELACOES_EXIGIDAS = {"PREQUEL", "PARENT"}

# plan_to_watch fora da equação: pode ser recomendado
STATUS_IGNORADOS = {"plan_to_watch"}

# Tabela onde o /mal_entrar guarda quem está registrado em cada servidor.
# Preencha com o nome da tabela e das colunas para que o autocomplete (e a
# validação) mostrem só usuários do servidor atual. Com REGISTRO_TABELA = None
# valem todos os usuários do MAL que estão em mal_snapshots (sem filtro de servidor).
REGISTRO_TABELA = "mal_usuarios"
REGISTRO_COL_USERNAME = "username"
REGISTRO_COL_GUILD = "guild_id"
# Opcional: coluna da REGISTRO_TABELA com o id do Discord de quem registrou o usuário do MAL.
# Se preenchida, quem aceita um pedido da guilda não precisa digitar o usuário do MAL
# (o bot descobre sozinho). Com None, o botão abre um formulário pedindo o usuário.
REGISTRO_COL_DISCORD = None

# `pageInfo.total` e `lastPage` do AniList NÃO são precisos (ver docs.anilist.co, seção
# "PageInfo Degradation"), então a query só pede `hasNextPage`.
# $genre, $startMin e $startMax só são enviados quando o usuário usa o filtro pago
# (variável não enviada = filtro ignorado pelo AniList).
QUERY = """
query ($ids: [Int], $format: MediaFormat, $minEp: Int, $maxEp: Int, $minPop: Int, $genre: String, $startMin: FuzzyDateInt, $startMax: FuzzyDateInt, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(
      type: ANIME
      format: $format
      episodes_greater: $minEp
      episodes_lesser: $maxEp
      idMal_not_in: $ids
      popularity_greater: $minPop
      genre: $genre
      startDate_greater: $startMin
      startDate_lesser: $startMax
      sort: POPULARITY_DESC
      isAdult: false
    ) {
      id
      idMal
      title { romaji english }
      format
      episodes
      duration
      averageScore
      genres
      siteUrl
      coverImage { large }
      relations {
        edges {
          relationType
          node { idMal type }
        }
      }
    }
  }
}
"""


# ------------------------------- BANCO ---------------------------------
# As tabelas/colunas são criadas pelas migrations:
#   - anime_quests (V00X), coluna cooldown_pago (V00Z) e coluna origem (V010)
#   - anime_quest_recusas (V00Y)
#   - anime_pedidos_guilda e anime_pedido_agenda (V010)
#
# status: oferecida -> aceita -> completa | vencida | abandonada
#                    -> recusada | expirada (oferta sem resposta)
#
# Limite de 12h entre pedidos vale por usuário em cada servidor (os flingers também são
# por servidor). Até MAX_QUESTS_ATIVAS quests aceitas ao mesmo tempo.
#
# anime_quest_recusas: contador por servidor de quests recusadas desde a última
# aceita. Sobe em _fechar_quest (recusada) e zera em _aceitar_quest.
#
# anime_quests.cooldown_pago: 1 na última quest do usuário quando ele compra o
# cooldown (ver _comprar_cooldown); zera a espera até o próximo pedido.
#
# anime_quests.origem: 'pessoal' (/anime_aleatorio) ou 'guilda' (pedido da guilda).
# Só as 'pessoal' contam no cooldown.
#
# anime_pedidos_guilda: aberto -> aceito | expirado. anime_pedido_agenda guarda quando sai
# o próximo pedido de cada servidor.

def _agora() -> str:
    """Timestamp UTC no mesmo formato do banco (2026-04-06T14:36:04+00:00)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _prazo_dias(episodios: int) -> int:
    return max(PRAZO_MINIMO_DIAS, episodios * DIAS_POR_EPISODIO)


def _mult_nota(nota: int | None) -> float:
    """Multiplicador cata-lixo: nota 55 -> 1.5x, nota >= 65 (ou sem nota) -> 1.0x."""
    if not nota or nota >= NOTA_CORTE:
        return 1.0
    return 1 + (NOTA_CORTE - nota) * BONUS_POR_PONTO


def _calcular_oferta(anime: dict, recusas: int) -> tuple[int, int, int, float]:
    """(minutos, recompensa, prazo_dias, mult_nota) de um anime sorteado."""
    minutos = anime["episodes"] * anime["duration"]
    mult_nota = _mult_nota(anime["averageScore"])
    recompensa = round(
        minutos * FLINGERS_POR_MINUTO * mult_nota * (AUMENTO_POR_RECUSA ** recusas)
    )
    return minutos, recompensa, _prazo_dias(anime["episodes"]), mult_nota


def _recusas_seguidas(guild_id: int) -> int:
    """Quantas quests foram recusadas neste servidor desde a última aceita."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT recusas FROM anime_quest_recusas WHERE guild_id = ?", (guild_id,)
        ).fetchone()
    conn.close()
    return row[0] if row else 0


def _canal_mal(guild_id: int) -> int | None:
    """ID do canal configurado para o MAL Tracker neste servidor (guild_config)."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT valor FROM guild_config WHERE guild_id = ? AND chave = ?",
            (guild_id, CHAVE_CANAL_MAL),
        ).fetchone()
    conn.close()
    return int(row[0]) if row else None


def _saldo(discord_id: int, guild_id: int) -> int:
    """Flingers do usuário neste servidor (0 se não estiver registrado)."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT flingers FROM usuarios WHERE id = ? AND guild_id = ?",
            (discord_id, guild_id),
        ).fetchone()
    conn.close()
    return int(row[0]) if row and row[0] else 0


def _status_do_snapshot(raw) -> str | None:
    """Extrai o status do JSON do snapshot, tolerando estruturas diferentes."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "status" in data:
        return data["status"]
    for chave in ("list_status", "my_list_status"):
        if isinstance(data.get(chave), dict):
            return data[chave].get("status")
    return None


def _buscar_usernames(prefixo: str, guild_id: int) -> list[str]:
    """Usernames do MAL para o autocomplete, só os registrados neste servidor."""
    with sqlite3.connect(DB_PATH) as conn:
        if REGISTRO_TABELA:
            rows = conn.execute(
                f"SELECT DISTINCT {REGISTRO_COL_USERNAME} FROM {REGISTRO_TABELA} "
                f"WHERE {REGISTRO_COL_GUILD} = ? AND {REGISTRO_COL_USERNAME} LIKE ? LIMIT 25",
                (guild_id, f"{prefixo}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT DISTINCT username FROM {TABLE} "
                "WHERE tipo = 'anime' AND username LIKE ? LIMIT 25",
                (f"{prefixo}%",),
            ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _mal_registrado_no_servidor(username: str, guild_id: int) -> bool:
    """O username do MAL está registrado neste servidor? (sempre True sem REGISTRO_TABELA)"""
    if not REGISTRO_TABELA:
        return True
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            f"SELECT 1 FROM {REGISTRO_TABELA} "
            f"WHERE {REGISTRO_COL_GUILD} = ? AND {REGISTRO_COL_USERNAME} = ? COLLATE NOCASE",
            (guild_id, username),
        ).fetchone()
    conn.close()
    return row is not None


def _mal_do_discord(discord_id: int, guild_id: int) -> str | None:
    """Usuário do MAL registrado por este usuário do Discord (None se não der para descobrir)."""
    if not (REGISTRO_TABELA and REGISTRO_COL_DISCORD):
        return None
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            f"SELECT {REGISTRO_COL_USERNAME} FROM {REGISTRO_TABELA} "
            f"WHERE {REGISTRO_COL_GUILD} = ? AND {REGISTRO_COL_DISCORD} = ? LIMIT 1",
            (guild_id, discord_id),
        ).fetchone()
    conn.close()
    return row[0] if row else None


def _ids_vistos(username: str) -> tuple[list[int], int, str | None]:
    """
    Retorna (ids_excluidos, total_na_lista, ultima_atualizacao).
    Função bloqueante: chame via asyncio.to_thread.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT item_id, snapshot, updated_at FROM {TABLE} "
            "WHERE username = ? COLLATE NOCASE AND tipo = 'anime'",
            (username,),
        ).fetchall()
    conn.close()

    ids = []
    ultima = None
    for item_id, snapshot, updated_at in rows:
        if updated_at and (ultima is None or updated_at > ultima):
            ultima = updated_at
        if _status_do_snapshot(snapshot) in STATUS_IGNORADOS:
            continue
        try:
            ids.append(int(item_id))
        except (TypeError, ValueError):
            continue
    return ids, len(rows), ultima


def _usuario_registrado(discord_id: int, guild_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM usuarios WHERE id = ? AND guild_id = ?",
            (discord_id, guild_id),
        ).fetchone()
    conn.close()
    return row is not None


def _quests_ativas(discord_id: int, guild_id: int) -> list[tuple]:
    """Quests aceitas e dentro do prazo: (id, titulo, mal_id, recompensa, expira_em, origem)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, titulo, mal_id, recompensa, expira_em, origem FROM anime_quests "
            "WHERE discord_id = ? AND guild_id = ? AND status = 'aceita' "
            "AND (expira_em IS NULL OR expira_em > ?) ORDER BY aceita_em",
            (discord_id, guild_id, _agora()),
        ).fetchall()
    conn.close()
    return rows


def _quests_do_servidor(guild_id: int) -> list[tuple]:
    """Quests ativas do servidor: (discord_id, titulo, mal_id, recompensa, expira_em)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT discord_id, titulo, mal_id, recompensa, expira_em FROM anime_quests "
            "WHERE guild_id = ? AND status = 'aceita' "
            "AND (expira_em IS NULL OR expira_em > ?) ORDER BY aceita_em",
            (guild_id, _agora()),
        ).fetchall()
    conn.close()
    return rows


def _segundos_restantes(criada_em: str) -> int:
    """Segundos que faltam de cooldown para uma quest criada em `criada_em` (ISO)."""
    criada = datetime.fromisoformat(criada_em)
    restante = COOLDOWN_HORAS * 3600 - (datetime.now(timezone.utc) - criada).total_seconds()
    return max(0, int(restante))


def _segundos_ate_liberar(discord_id: int, guild_id: int) -> int:
    """Segundos até o usuário poder pedir outra quest neste servidor (0 = já pode).
    Só quests pessoais contam; aceitar um pedido da guilda não inicia cooldown."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT criada_em, cooldown_pago FROM anime_quests "
            "WHERE discord_id = ? AND guild_id = ? AND origem = 'pessoal' "
            "ORDER BY criada_em DESC, id DESC LIMIT 1",
            (discord_id, guild_id),
        ).fetchone()
    conn.close()
    if not row or not row[0] or row[1]:
        return 0  # nunca pediu, ou já pagou pra pular a espera
    return _segundos_restantes(row[0])


def _comprar_cooldown(discord_id: int, guild_id: int) -> str:
    """
    Paga CUSTO_COMPRAR_COOLDOWN para zerar a espera até o próximo pedido.
    Retorna "ok", "sem_cooldown" (não há espera para pagar) ou "sem_saldo".
    O débito e a marcação da quest acontecem na mesma transação.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, criada_em, cooldown_pago FROM anime_quests "
            "WHERE discord_id = ? AND guild_id = ? AND origem = 'pessoal' "
            "ORDER BY criada_em DESC, id DESC LIMIT 1",
            (discord_id, guild_id),
        ).fetchone()
        if not row:
            return "sem_cooldown"
        quest_id, criada_em, pago = row
        if pago or not criada_em or _segundos_restantes(criada_em) <= 0:
            return "sem_cooldown"

        cur = conn.execute(
            "UPDATE usuarios SET flingers = flingers - ? "
            "WHERE id = ? AND guild_id = ? AND flingers >= ?",
            (CUSTO_COMPRAR_COOLDOWN, discord_id, guild_id, CUSTO_COMPRAR_COOLDOWN),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return "sem_saldo"
        conn.execute("UPDATE anime_quests SET cooldown_pago = 1 WHERE id = ?", (quest_id,))
        conn.commit()
        return "ok"
    finally:
        conn.close()


def _criar_quest(discord_id, guild_id, mal_username, anime, minutos, recompensa, prazo,
                 custo: int = 0) -> int | None:
    """
    Cria a oferta e devolve o id. Se houver custo (filtros pagos), debita os flingers na
    MESMA transação, só se o saldo ainda cobrir; sem saldo nada é criado e devolve None.
    """
    titulo = anime["title"]["english"] or anime["title"]["romaji"]
    quest_id = None
    with sqlite3.connect(DB_PATH) as conn:
        pago = True
        if custo:
            cur = conn.execute(
                "UPDATE usuarios SET flingers = flingers - ? "
                "WHERE id = ? AND guild_id = ? AND flingers >= ?",
                (custo, discord_id, guild_id, custo),
            )
            pago = cur.rowcount == 1
        if pago:
            cur = conn.execute(
                "INSERT INTO anime_quests "
                "(discord_id, guild_id, mal_username, mal_id, titulo, formato, minutos, "
                " recompensa, prazo_dias, criada_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (discord_id, guild_id, mal_username, anime["idMal"], titulo, anime["format"],
                 minutos, recompensa, prazo, _agora()),
            )
            quest_id = cur.lastrowid
    conn.close()
    return quest_id


def _trocar_quest(quest_id: int, discord_id: int, guild_id: int, anime: dict,
                  minutos: int, recompensa: int, prazo: int, custo: int) -> bool:
    """
    Reroll: troca o anime de uma oferta ainda 'oferecida' e debita o custo, tudo na mesma
    transação. Se a oferta já não estiver disponível ou o saldo não cobrir, nada muda.
    O criada_em não é alterado, então o cooldown continua contando do pedido original.
    """
    titulo = anime["title"]["english"] or anime["title"]["romaji"]
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE anime_quests SET mal_id = ?, titulo = ?, formato = ?, minutos = ?, "
            "recompensa = ?, prazo_dias = ? WHERE id = ? AND status = 'oferecida'",
            (anime["idMal"], titulo, anime["format"], minutos, recompensa, prazo, quest_id),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        cur = conn.execute(
            "UPDATE usuarios SET flingers = flingers - ? "
            "WHERE id = ? AND guild_id = ? AND flingers >= ?",
            (custo, discord_id, guild_id, custo),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    finally:
        conn.close()


def _aceitar_quest(quest_id: int) -> tuple[str | None, str | None]:
    """
    Aceita a oferta e zera o contador de recusas.
    Retorna (data_limite_iso, None) se aceitou, ou (None, motivo) com motivo
    "indisponivel" (oferta não está mais aberta) ou "limite" (já tem MAX_QUESTS_ATIVAS).
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")  # serializa com o aceite de pedidos da guilda
        row = conn.execute(
            "SELECT discord_id, guild_id, prazo_dias FROM anime_quests "
            "WHERE id = ? AND status = 'oferecida'", (quest_id,)
        ).fetchone()
        if not row:
            return None, "indisponivel"
        discord_id, guild_id, prazo = row

        # limite de quests ativas por usuário
        ativas = conn.execute(
            "SELECT COUNT(*) FROM anime_quests WHERE discord_id = ? AND guild_id = ? "
            "AND status = 'aceita' AND (expira_em IS NULL OR expira_em > ?)",
            (discord_id, guild_id, _agora())
        ).fetchone()[0]
        if ativas >= MAX_QUESTS_ATIVAS:
            return None, "limite"

        agora = datetime.now(timezone.utc)
        expira = (agora + timedelta(days=prazo)).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE anime_quests SET status = 'aceita', aceita_em = ?, expira_em = ? "
            "WHERE id = ?",
            (agora.isoformat(timespec="seconds"), expira, quest_id),
        )
        # alguém aceitou: o preço volta ao normal (mesma transação do aceite)
        conn.execute(
            "UPDATE anime_quest_recusas SET recusas = 0, atualizado_em = ? WHERE guild_id = ?",
            (_agora(), guild_id),
        )
        conn.commit()
        return expira, None
    finally:
        conn.close()


def _fechar_quest(quest_id: int, novo_status: str) -> bool:
    """Marca uma oferta ainda 'oferecida' como recusada/expirada.
    Se for recusa, soma 1 ao contador do servidor (mesma transação)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT guild_id FROM anime_quests WHERE id = ? AND status = 'oferecida'",
            (quest_id,),
        ).fetchone()
        if not row:
            return False
        cur = conn.execute(
            "UPDATE anime_quests SET status = ? WHERE id = ? AND status = 'oferecida'",
            (novo_status, quest_id),
        )
        if cur.rowcount != 1:
            return False
        if novo_status == "recusada":
            conn.execute(
                "INSERT INTO anime_quest_recusas (guild_id, recusas, atualizado_em) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET "
                "recusas = recusas + 1, atualizado_em = excluded.atualizado_em",
                (row[0], _agora()),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def _abandonar_quest(discord_id: int, guild_id: int, quest_id: int) -> str | None:
    """Abandona uma quest aceita do usuário neste servidor. Retorna o título, ou None."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT titulo FROM anime_quests WHERE id = ? AND discord_id = ? AND guild_id = ? "
            "AND status = 'aceita'", (quest_id, discord_id, guild_id)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE anime_quests SET status = 'abandonada' WHERE id = ? AND status = 'aceita'",
            (quest_id,),
        )
        conn.commit()
        return row[0]
    finally:
        conn.close()


def _verificar_quests() -> tuple[list[dict], list[dict]]:
    """
    Confere as quests aceitas:
      - anime 'completed' no snapshot (atualizado depois de aceitar e dentro do
        prazo): marca como completa e credita os flingers na mesma transação;
      - prazo estourado: marca como vencida.
    Retorna (pagas, vencidas). Bloqueante: use asyncio.to_thread.
    """
    pagas, vencidas = [], []
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        quests = conn.execute(
            "SELECT id, discord_id, guild_id, mal_username, mal_id, titulo, recompensa, "
            "aceita_em, expira_em, origem FROM anime_quests WHERE status = 'aceita'"
        ).fetchall()
        agora = _agora()

        for (qid, discord_id, guild_id, username, mal_id, titulo, recompensa,
             aceita_em, expira_em, origem) in quests:
            row = conn.execute(
                f"SELECT snapshot, updated_at FROM {TABLE} "
                "WHERE username = ? COLLATE NOCASE AND tipo = 'anime' AND item_id = ?",
                (username, int(mal_id)),
            ).fetchone()

            completo, atualizado = False, None
            if row:
                snapshot, atualizado = row
                completo = _status_do_snapshot(snapshot) == "completed"
                # ignora "completed" anterior ao aceite da quest
                if completo and atualizado and aceita_em and atualizado < aceita_em:
                    completo = False
            no_prazo = not (expira_em and atualizado and atualizado > expira_em)

            if completo and no_prazo:
                cur = conn.execute(
                    "UPDATE anime_quests SET status = 'completa', concluida_em = ? "
                    "WHERE id = ? AND status = 'aceita'", (agora, qid)
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    continue
                cur = conn.execute(
                    "UPDATE usuarios SET flingers = flingers + ? WHERE id = ? AND guild_id = ?",
                    (recompensa, discord_id, guild_id),
                )
                if cur.rowcount != 1:
                    # usuário não existe nesse servidor: desfaz tudo e tenta na próxima rodada
                    conn.rollback()
                    print(f"[AnimeQuest] Quest {qid}: usuário {discord_id} não encontrado "
                          f"na guild {guild_id}, flingers não creditados")
                    continue
                conn.commit()
                pagas.append({"discord_id": discord_id, "guild_id": guild_id,
                              "titulo": titulo, "recompensa": recompensa, "origem": origem})

            elif expira_em and agora > expira_em:
                cur = conn.execute(
                    "UPDATE anime_quests SET status = 'vencida' WHERE id = ? AND status = 'aceita'",
                    (qid,),
                )
                conn.commit()
                if cur.rowcount == 1:
                    vencidas.append({"discord_id": discord_id, "titulo": titulo})
    finally:
        conn.close()
    return pagas, vencidas


# ---- Pedido da guilda: banco ----
def _delay_normal() -> timedelta:
    """Intervalo sorteado até o próximo pedido (PEDIDO_MIN_HORAS a PEDIDO_MAX_HORAS)."""
    return timedelta(hours=random.uniform(PEDIDO_MIN_HORAS, PEDIDO_MAX_HORAS))


def _reagendar_pedido(guild_id: int, delay: timedelta):
    """Define quando sai o próximo pedido deste servidor."""
    proximo = (datetime.now(timezone.utc) + delay).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO anime_pedido_agenda (guild_id, proximo_em) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET proximo_em = excluded.proximo_em",
            (guild_id, proximo),
        )
    conn.close()


def _guildas_com_pedido_devido() -> list[tuple[int, int]]:
    """
    (guild_id, canal_id) dos servidores com canal_mal configurado, módulo ativo e pedido
    na hora. Servidores que ainda não têm agenda ganham uma (primeiro pedido daqui a
    4~6h, sem disparar de cara no deploy). Bloqueante: use asyncio.to_thread.
    """
    agora = _agora()
    devidos = []
    conn = sqlite3.connect(DB_PATH)
    try:
        canais = conn.execute(
            "SELECT guild_id, CAST(valor AS INTEGER) FROM guild_config WHERE chave = ?",
            (CHAVE_CANAL_MAL,),
        ).fetchall()
        for guild_id, canal_id in canais:
            if not canal_id:
                continue
            modulo = conn.execute(
                "SELECT valor FROM guild_config WHERE guild_id = ? AND chave = ?",
                (guild_id, CHAVE_MODULO_PEDIDO),
            ).fetchone()
            if modulo is not None and int(modulo[0]) == 0:
                continue  # módulo desativado neste servidor
            row = conn.execute(
                "SELECT proximo_em FROM anime_pedido_agenda WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if row is None:
                proximo = (datetime.now(timezone.utc) + _delay_normal()).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO anime_pedido_agenda (guild_id, proximo_em) VALUES (?, ?)",
                    (guild_id, proximo),
                )
                continue
            if row[0] <= agora:
                devidos.append((guild_id, canal_id))
        conn.commit()
    finally:
        conn.close()
    return devidos


def _mal_ids_pedidos(guild_id: int) -> list[int]:
    """mal_ids dos pedidos recentes do servidor (para não repetir anime)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT mal_id FROM anime_pedidos_guilda WHERE guild_id = ? "
            "ORDER BY id DESC LIMIT 200",
            (guild_id,),
        ).fetchall()
    conn.close()
    return [int(r[0]) for r in rows]


def _registrar_pedido(guild_id: int, canal_id: int, message_id: int, anime: dict,
                      minutos: int, recompensa: int, prazo: int) -> list[tuple[int, int]]:
    """
    Registra o pedido novo como 'aberto' e expira os anteriores ainda abertos do servidor
    (só um fica aberto por vez). Retorna [(canal_id, message_id)] dos expirados, para o
    bot atualizar as mensagens antigas.
    """
    titulo = anime["title"]["english"] or anime["title"]["romaji"]
    conn = sqlite3.connect(DB_PATH)
    try:
        anteriores = conn.execute(
            "SELECT canal_id, message_id FROM anime_pedidos_guilda "
            "WHERE guild_id = ? AND status = 'aberto'", (guild_id,)
        ).fetchall()
        conn.execute(
            "UPDATE anime_pedidos_guilda SET status = 'expirado' "
            "WHERE guild_id = ? AND status = 'aberto'", (guild_id,)
        )
        conn.execute(
            "INSERT INTO anime_pedidos_guilda "
            "(guild_id, canal_id, message_id, mal_id, titulo, formato, minutos, recompensa, "
            " prazo_dias, status, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'aberto', ?)",
            (guild_id, canal_id, message_id, anime["idMal"], titulo, anime["format"],
             minutos, recompensa, prazo, _agora()),
        )
        conn.commit()
        return anteriores
    finally:
        conn.close()


def _pedido_aberto(message_id: int) -> int | None:
    """mal_id do pedido aberto ligado a esta mensagem (None se já foi aceito/expirou)."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT mal_id FROM anime_pedidos_guilda WHERE message_id = ? AND status = 'aberto'",
            (message_id,),
        ).fetchone()
    conn.close()
    return int(row[0]) if row else None


def _aceitar_pedido_db(message_id: int, discord_id: int, guild_id: int,
                       mal_username: str) -> tuple[str, dict | None]:
    """
    Aceita o pedido: marca como 'aceito' e cria a quest do usuário (já 'aceita', origem
    'guilda') na mesma transação. Só um usuário consegue (BEGIN IMMEDIATE + status 'aberto').
    Retorna ("ok", info) ou (motivo, None) com motivo "indisponivel", "limite" ou "repetida".
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        p = conn.execute(
            "SELECT id, mal_id, titulo, formato, minutos, recompensa, prazo_dias "
            "FROM anime_pedidos_guilda "
            "WHERE message_id = ? AND guild_id = ? AND status = 'aberto'",
            (message_id, guild_id),
        ).fetchone()
        if not p:
            return "indisponivel", None
        pid, mal_id, titulo, formato, minutos, recompensa, prazo = p

        ativas = conn.execute(
            "SELECT mal_id FROM anime_quests WHERE discord_id = ? AND guild_id = ? "
            "AND status = 'aceita' AND (expira_em IS NULL OR expira_em > ?)",
            (discord_id, guild_id, _agora()),
        ).fetchall()
        if len(ativas) >= MAX_QUESTS_ATIVAS:
            return "limite", None
        if any(int(a[0]) == mal_id for a in ativas):
            return "repetida", None

        agora = datetime.now(timezone.utc)
        agora_iso = agora.isoformat(timespec="seconds")
        expira = (agora + timedelta(days=prazo)).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE anime_pedidos_guilda SET status = 'aceito', aceito_por = ?, aceito_em = ? "
            "WHERE id = ?",
            (discord_id, agora_iso, pid),
        )
        conn.execute(
            "INSERT INTO anime_quests "
            "(discord_id, guild_id, mal_username, mal_id, titulo, formato, minutos, "
            " recompensa, prazo_dias, status, criada_em, aceita_em, expira_em, origem) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'aceita', ?, ?, ?, 'guilda')",
            (discord_id, guild_id, mal_username, mal_id, titulo, formato, minutos,
             recompensa, prazo, agora_iso, agora_iso, expira),
        )
        conn.commit()
        return "ok", {"titulo": titulo, "recompensa": recompensa, "expira": expira}
    finally:
        conn.close()


# ------------------------------ EMBEDS ---------------------------------
def _fmt_minutos(m: int) -> str:
    h, mi = divmod(m, 60)
    if h and mi:
        return f"{h}h {mi}min"
    return f"{h}h" if h else f"{mi}min"


def _fmt_data(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y")


def _fmt_venc(expira_em: str | None) -> str:
    """' (vence <t:...:R>)' para mensagens, ou '' se não houver prazo."""
    if not expira_em:
        return ""
    return f" (vence <t:{int(datetime.fromisoformat(expira_em).timestamp())}:R>)"


def _embed_quest(anime: dict, minutos: int, recompensa: int, prazo: int,
                 ultima: str | None, mult_nota: float = 1.0, recusas: int = 0,
                 filtros: list[str] | None = None, custo: int = 0,
                 rerolls: int = 0) -> discord.Embed:
    titulo = anime["title"]["english"] or anime["title"]["romaji"]
    romaji = anime["title"]["romaji"]
    nota = anime["averageScore"]
    link_mal = f"https://myanimelist.net/anime/{anime['idMal']}"

    embed = discord.Embed(title=f"📜 Quest: {titulo}", url=link_mal, color=COR_EMBED)
    if romaji and romaji != titulo:
        embed.description = f"*{romaji}*"
    embed.add_field(name="Formato", value=anime["format"], inline=True)
    embed.add_field(name="Episódios", value=f"{anime['episodes']} × {anime['duration']}min", inline=True)
    embed.add_field(name="Tempo total", value=_fmt_minutos(minutos), inline=True)
    embed.add_field(name="Nota (AniList)", value=f"{nota}/100" if nota else "Sem nota", inline=True)
    embed.add_field(name="🏆 Recompensa", value=f"**{recompensa:,}** flingers".replace(",", "."), inline=True)
    embed.add_field(name="⏳ Prazo", value=f"{prazo} dias", inline=True)

    bonus = []
    if mult_nota > 1:
        bonus.append(f"⭐ Cata-lixo (nota {nota}): ×{mult_nota:.2f}")
    if recusas:
        bonus.append(f"📈 {recusas} recusa(s) seguida(s): ×{AUMENTO_POR_RECUSA ** recusas:.2f}")
    if bonus:
        embed.add_field(name="Bônus", value="\n".join(bonus), inline=False)

    if filtros:
        embed.add_field(
            name="💸 Filtros pagos",
            value="\n".join(filtros) + f"\nCusto: **{custo}** flingers",
            inline=False,
        )

    if anime.get("genres"):
        embed.add_field(name="Gêneros", value=", ".join(anime["genres"][:5]), inline=False)
    embed.add_field(
        name="Links",
        value=f"[MyAnimeList]({link_mal}) • [AniList]({anime['siteUrl']})",
        inline=False
    )
    if anime["coverImage"]["large"]:
        embed.set_thumbnail(url=anime["coverImage"]["large"])

    rodape = "Aceite e marque como Completed no MAL para receber."
    if ultima:
        rodape += f" • lista sincronizada em {ultima[:10]}"
    if rerolls:
        rodape += f" • rerolls: {rerolls}/{len(CUSTOS_REROLL)}"
    embed.set_footer(text=rodape)
    return embed


def _embed_pedido(anime: dict, minutos: int, recompensa: int, prazo: int,
                  mult_nota: float) -> discord.Embed:
    """Embed do pedido da guilda (mesmo layout da quest, com título/rodapé próprios)."""
    titulo = anime["title"]["english"] or anime["title"]["romaji"]
    embed = _embed_quest(anime, minutos, recompensa, prazo, None, mult_nota, 0)
    embed.title = f"🏛️ Pedido da guilda: {titulo}"
    embed.color = COR_PEDIDO
    if MULT_PEDIDO_GUILDA != 1:
        embed.add_field(name="🏛️ Bônus da guilda", value=f"×{MULT_PEDIDO_GUILDA:.2f}", inline=True)
    embed.set_footer(
        text="Qualquer pessoa do servidor pode aceitar • vale até o próximo pedido"
    )
    return embed


def _paginas_mural(rows: list[tuple], por_pagina: int = 10) -> list[discord.Embed]:
    """Monta as páginas do mural (uma embed por página)."""
    if not rows:
        return [discord.Embed(
            title="📜 Mural de quests",
            description="Nenhuma quest ativa neste servidor.",
            color=COR_EMBED
        )]

    paginas = []
    total_paginas = math.ceil(len(rows) / por_pagina)
    for i in range(total_paginas):
        linhas = []
        for n, (discord_id, titulo, mal_id, recompensa, expira_em) in enumerate(
            rows[i * por_pagina:(i + 1) * por_pagina], start=i * por_pagina + 1
        ):
            titulo = titulo.replace("[", "(").replace("]", ")")
            venc = ""
            if expira_em:
                venc = f" • vence <t:{int(datetime.fromisoformat(expira_em).timestamp())}:R>"
            linhas.append(
                f"**{n}.** <@{discord_id}> — [{titulo}](https://myanimelist.net/anime/{mal_id})"
                f" • 🏆 {recompensa}{venc}"
            )
        embed = discord.Embed(title="📜 Mural de quests", description="\n".join(linhas), color=COR_EMBED)
        embed.set_footer(text=f"Página {i + 1}/{total_paginas} • {len(rows)} quest(s) ativa(s)")
        paginas.append(embed)
    return paginas


# -------------------------------- VIEW ---------------------------------
class QuestView(discord.ui.View):
    """
    Oferta de quest: Aceitar / Recusar / Reroll.

    `ctx` guarda o que o reroll precisa para sortear de novo com os mesmos critérios:
    usuario, formato, min_eps, max_eps, genero, decada, ids, ultima, filtros,
    custo_filtros e oferecidos (mal_ids já mostrados nesta oferta, para não repetir).
    """

    def __init__(self, cog: "AnimeAleatorioCog", quest_id: int, discord_id: int,
                 guild_id: int, embed: discord.Embed, ctx: dict):
        super().__init__(timeout=TEMPO_PARA_ACEITAR)
        self.cog = cog
        self.quest_id = quest_id
        self.discord_id = discord_id
        self.guild_id = guild_id
        self.embed = embed
        self.ctx = ctx
        self.rerolls = 0
        self.ocupado = False      # True enquanto um reroll busca outro anime
        self.message: discord.Message | None = None
        self.reroll.label = f"Reroll ({CUSTOS_REROLL[0]})"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Essa quest não é sua.", ephemeral=True)
            return False
        return True

    async def _bloqueado(self, interaction: discord.Interaction) -> bool:
        """Durante um reroll os outros botões esperam (evita aceitar no meio da troca)."""
        if self.ocupado:
            await interaction.response.send_message(
                "🎲 Estou procurando outro anime, aguarde um instante.", ephemeral=True
            )
            return True
        return False

    async def _encerrar(self, interaction: discord.Interaction, ok: bool,
                        cor: int, rodape: str):
        if not ok:
            await interaction.response.send_message(
                "Essa quest não está mais disponível.", ephemeral=True
            )
            return
        self.embed.color = cor
        self.embed.set_footer(text=rodape)
        self.stop()
        await interaction.response.edit_message(embed=self.embed, view=None)

    @discord.ui.button(label="Aceitar", style=discord.ButtonStyle.success, emoji="✅")
    async def aceitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._bloqueado(interaction):
            return
        expira, motivo = await asyncio.to_thread(_aceitar_quest, self.quest_id)
        if expira is None and motivo == "limite":
            await interaction.response.send_message(
                f"📜 Você já tem {MAX_QUESTS_ATIVAS} quests ativas. "
                "Termine ou abandone uma (`/anime_abandonar`) antes de aceitar esta.",
                ephemeral=True
            )
            return
        await self._encerrar(
            interaction, expira is not None, 0x57F287,
            f"✅ Quest aceita! Marque como Completed no MAL até {_fmt_data(expira)}."
            if expira else ""
        )

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="✖️")
    async def recusar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._bloqueado(interaction):
            return
        ok = await asyncio.to_thread(_fechar_quest, self.quest_id, "recusada")
        await self._encerrar(interaction, ok, 0xED4245, "✖️ Quest recusada.")

    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.secondary, emoji="🎲")
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._bloqueado(interaction):
            return
        if self.rerolls >= len(CUSTOS_REROLL):
            await interaction.response.send_message(
                "Você já usou todos os rerolls desta oferta.", ephemeral=True
            )
            return

        custo = CUSTOS_REROLL[self.rerolls]
        saldo = await asyncio.to_thread(_saldo, self.discord_id, self.guild_id)
        if saldo < custo:
            await interaction.response.send_message(
                f"💸 O reroll custa **{custo}** flingers e você tem **{saldo}**.", ephemeral=True
            )
            return

        self.ocupado = True
        try:
            await interaction.response.defer()  # a busca no AniList pode passar de 3s
            try:
                anime = await self.cog._sortear(
                    self.ctx["ids"], self.ctx["formato"],
                    self.ctx["min_eps"], self.ctx["max_eps"],
                    self.ctx["genero"], self.ctx["decada"],
                    excluir=self.ctx["oferecidos"],
                )
            except Exception as e:
                print(f"[AnimeQuest] Erro AniList (reroll): {e}")
                await interaction.followup.send(
                    "⚠️ Não consegui falar com o AniList agora. Nada foi cobrado.",
                    ephemeral=True
                )
                return

            if anime is None:
                await interaction.followup.send(
                    "😕 Não achei outro anime com esses critérios. Nada foi cobrado.",
                    ephemeral=True
                )
                return

            recusas = await asyncio.to_thread(_recusas_seguidas, self.guild_id)
            minutos, recompensa, prazo, mult_nota = _calcular_oferta(anime, recusas)

            ok = await asyncio.to_thread(
                _trocar_quest, self.quest_id, self.discord_id, self.guild_id,
                anime, minutos, recompensa, prazo, custo
            )
            if not ok:
                await interaction.followup.send(
                    "⚠️ Não deu para trocar: a oferta não está mais disponível ou o saldo "
                    "não cobre o reroll. Nada foi cobrado.",
                    ephemeral=True
                )
                return

            self.rerolls += 1
            self.ctx["oferecidos"].append(anime["idMal"])
            self.embed = _embed_quest(
                anime, minutos, recompensa, prazo, self.ctx["ultima"], mult_nota, recusas,
                self.ctx["filtros"], self.ctx["custo_filtros"], self.rerolls
            )
            if self.rerolls >= len(CUSTOS_REROLL):
                self.remove_item(self.reroll)
            else:
                self.reroll.label = f"Reroll ({CUSTOS_REROLL[self.rerolls]})"
            await interaction.edit_original_response(embed=self.embed, view=self)
        finally:
            self.ocupado = False

    async def on_timeout(self):
        fechou = await asyncio.to_thread(_fechar_quest, self.quest_id, "expirada")
        if fechou and self.message:
            self.embed.color = 0x95A5A6
            self.embed.set_footer(text="⌛ Quest expirada (sem resposta).")
            try:
                await self.message.edit(embed=self.embed, view=None)
            except discord.HTTPException:
                pass


class ComprarCooldownView(discord.ui.View):
    """Botão da mensagem de cooldown: paga CUSTO_COMPRAR_COOLDOWN para pedir já."""

    def __init__(self, discord_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.discord_id = discord_id
        self.guild_id = guild_id
        self.interaction: discord.Interaction | None = None
        self.comprar.label = f"Pular espera ({CUSTO_COMPRAR_COOLDOWN})"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("Esse botão não é seu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pular espera", style=discord.ButtonStyle.primary, emoji="⏩")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        resultado = await asyncio.to_thread(_comprar_cooldown, self.discord_id, self.guild_id)
        if resultado == "sem_saldo":
            saldo = await asyncio.to_thread(_saldo, self.discord_id, self.guild_id)
            await interaction.response.send_message(
                f"💸 Pular a espera custa **{CUSTO_COMPRAR_COOLDOWN}** flingers e você tem **{saldo}**.",
                ephemeral=True
            )
            return

        self.stop()
        if resultado == "ok":
            texto = (f"✅ Espera paga (−{CUSTO_COMPRAR_COOLDOWN} flingers)! "
                     "Já pode usar `/anime_aleatorio` de novo.")
        else:  # sem_cooldown: a espera acabou enquanto a mensagem estava aberta
            texto = "Você já pode pedir uma nova quest com `/anime_aleatorio`. Nada foi cobrado."
        await interaction.response.edit_message(content=texto, view=None)

    async def on_timeout(self):
        if self.interaction:
            try:
                await self.interaction.edit_original_response(view=None)
            except discord.HTTPException:
                pass


class PedidoGuildaView(discord.ui.View):
    """
    Botão do pedido da guilda. É PERSISTENTE (timeout=None + custom_id fixo, registrado em
    cog_load com bot.add_view), então continua funcionando depois que o bot reinicia. O
    pedido é identificado pelo id da mensagem em que o botão foi clicado.
    """

    def __init__(self, cog: "AnimeAleatorioCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Aceitar pedido", style=discord.ButtonStyle.success,
                       emoji="🏛️", custom_id="anime_pedido:aceitar")
    async def aceitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog._clicou_pedido(interaction)


class UsuarioMalModal(discord.ui.Modal, title="Aceitar pedido da guilda"):
    """Pergunta o usuário do MAL de quem está aceitando (quando o bot não sabe sozinho)."""

    usuario = discord.ui.TextInput(
        label="Seu usuário do MAL",
        placeholder="O mesmo do /mal_entrar",
        max_length=64,
    )

    def __init__(self, cog: "AnimeAleatorioCog", message: discord.Message):
        super().__init__()
        self.cog = cog
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog._aceitar_pedido(interaction, self.message, self.usuario.value.strip())


class MuralView(discord.ui.View):
    """Navegação entre as páginas do mural."""

    def __init__(self, paginas: list[discord.Embed]):
        super().__init__(timeout=120)
        self.paginas = paginas
        self.indice = 0
        self.message: discord.Message | None = None

    async def _ir_para(self, interaction: discord.Interaction, novo: int):
        self.indice = novo % len(self.paginas)
        await interaction.response.edit_message(embed=self.paginas[self.indice], view=self)

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ir_para(interaction, self.indice - 1)

    @discord.ui.button(label="Próxima", style=discord.ButtonStyle.secondary, emoji="▶️")
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ir_para(interaction, self.indice + 1)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


# -------------------------------- COG ----------------------------------
class AnimeAleatorioCog(commands.Cog):
    """Quests de anime: recomendação aleatória que rende flingers."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.view_pedido: PedidoGuildaView | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        # view persistente: o botão dos pedidos antigos continua valendo após reiniciar
        self.view_pedido = PedidoGuildaView(self)
        self.bot.add_view(self.view_pedido)
        self.verificar_quests.start()
        self.pedidos_guilda.start()

    async def cog_unload(self):
        self.verificar_quests.cancel()
        self.pedidos_guilda.cancel()
        if self.session:
            await self.session.close()

    async def _dm(self, discord_id: int, texto: str):
        try:
            user = self.bot.get_user(discord_id) or await self.bot.fetch_user(discord_id)
            await user.send(texto)
        except discord.HTTPException:
            pass  # DM fechada

    async def _canal(self, canal_id: int):
        """Canal pelo id (cache ou API); None se não existir/sem acesso."""
        canal = self.bot.get_channel(canal_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(canal_id)
            except discord.HTTPException:
                return None
        return canal

    async def _avisar_canal(self, guild_id: int, texto: str):
        """Posta no canal configurado como canal_mal (se houver)."""
        canal_id = await asyncio.to_thread(_canal_mal, guild_id)
        if not canal_id:
            return  # canal_mal não configurado
        canal = await self._canal(canal_id)
        if canal is None:
            return
        try:
            await canal.send(texto)
        except discord.HTTPException:
            pass  # sem permissão para falar no canal

    # ---- Verificação periódica ----
    @tasks.loop(minutes=INTERVALO_VERIFICACAO_MIN)
    async def verificar_quests(self):
        try:
            pagas, vencidas = await asyncio.to_thread(_verificar_quests)
        except Exception as e:
            print(f"[AnimeQuest] Erro na verificação: {e}")
            return
        for q in pagas:
            print(f"[AnimeQuest] {q['discord_id']} completou '{q['titulo']}' (+{q['recompensa']})")
            await self._dm(
                q["discord_id"],
                f"🎉 **Quest concluída!** Você completou **{q['titulo']}** "
                f"e ganhou **{q['recompensa']}** flingers."
            )
            if q["origem"] == "guilda":
                aviso = (f"🏛️ <@{q['discord_id']}> concluiu o pedido da guilda **{q['titulo']}** "
                         f"e ganhou **{q['recompensa']}** flingers!")
            else:
                aviso = (f"🎉 <@{q['discord_id']}> concluiu a quest **{q['titulo']}** "
                         f"e ganhou **{q['recompensa']}** flingers!")
            await self._avisar_canal(q["guild_id"], aviso)
        for q in vencidas:
            print(f"[AnimeQuest] {q['discord_id']} perdeu o prazo de '{q['titulo']}'")
            await self._dm(
                q["discord_id"],
                f"⌛ O prazo da quest **{q['titulo']}** acabou. Você já pode aceitar outra "
                f"quando o limite de 12h entre pedidos permitir."
            )

    @verificar_quests.before_loop
    async def _antes_da_verificacao(self):
        await self.bot.wait_until_ready()

    # ---- Pedido da guilda ----
    @tasks.loop(minutes=INTERVALO_PEDIDOS_MIN)
    async def pedidos_guilda(self):
        try:
            devidos = await asyncio.to_thread(_guildas_com_pedido_devido)
        except Exception as e:
            print(f"[AnimeQuest] Erro ao ver pedidos da guilda: {e}")
            return
        for guild_id, canal_id in devidos:
            try:
                await self._publicar_pedido(guild_id, canal_id)
            except Exception as e:
                print(f"[AnimeQuest] Erro ao publicar pedido (guild {guild_id}): {e}")

    @pedidos_guilda.before_loop
    async def _antes_dos_pedidos(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _sortear_tipo_pedido() -> tuple[str, int, int]:
        """(formato, min_eps, max_eps) do próximo pedido, pelos pesos de PEDIDO_SORTEIO."""
        formato, duracao, _ = random.choices(
            PEDIDO_SORTEIO, weights=[p for _, _, p in PEDIDO_SORTEIO]
        )[0]
        if duracao:
            min_eps, max_eps = DURACOES[duracao]
        else:
            min_eps, max_eps = 0, 100000  # sem filtro de episódios
        return formato, min_eps, max_eps

    async def _publicar_pedido(self, guild_id: int, canal_id: int) -> bool:
        """
        Sorteia um anime e publica o pedido no canal. Retorna True se publicou.
        Sucesso: o próximo sai em 4~6h. Falha (sem anime, canal inacessível): tenta de novo
        em PEDIDO_RETRY_MIN minutos.
        """
        delay = timedelta(minutes=PEDIDO_RETRY_MIN)
        try:
            canal = await self._canal(canal_id)
            if canal is None:
                print(f"[AnimeQuest] Pedido da guilda: canal {canal_id} inacessível (guild {guild_id})")
                return False

            # Sem lista de "vistos": o pedido é pra qualquer um. Com vistos vazio, o filtro de
            # prequel descarta sequências, então só saem animes que dá pra começar do zero.
            # Quem aceita é validado depois contra a própria lista do MAL.
            excluir = await asyncio.to_thread(_mal_ids_pedidos, guild_id)
            anime = None
            for _ in range(3):
                formato, min_eps, max_eps = self._sortear_tipo_pedido()
                anime = await self._sortear([], formato, min_eps, max_eps, excluir=excluir)
                if anime:
                    break
            if anime is None:
                print(f"[AnimeQuest] Pedido da guilda: nenhum anime encontrado (guild {guild_id})")
                return False

            minutos, recompensa, prazo, mult_nota = _calcular_oferta(anime, 0)
            recompensa = round(recompensa * MULT_PEDIDO_GUILDA)
            embed = _embed_pedido(anime, minutos, recompensa, prazo, mult_nota)

            try:
                msg = await canal.send(embed=embed, view=self.view_pedido)
            except discord.HTTPException as e:
                print(f"[AnimeQuest] Pedido da guilda: não consegui postar em {canal_id}: {e}")
                return False

            anteriores = await asyncio.to_thread(
                _registrar_pedido, guild_id, canal_id, msg.id, anime, minutos, recompensa, prazo
            )
            delay = _delay_normal()
            print(f"[AnimeQuest] Pedido da guilda publicado (guild {guild_id}): "
                  f"{anime['idMal']} '{embed.title}', próximo em ~{delay}")

            for canal_ant, msg_ant in anteriores:
                await self._marcar_pedido_expirado(canal_ant, msg_ant)
            return True
        finally:
            await asyncio.to_thread(_reagendar_pedido, guild_id, delay)

    async def _marcar_pedido_expirado(self, canal_id: int, message_id: int):
        """Atualiza a mensagem de um pedido que ninguém aceitou (tira o botão)."""
        canal = await self._canal(canal_id)
        if canal is None:
            return
        try:
            msg = await canal.fetch_message(message_id)
            kwargs = {"view": None}
            if msg.embeds:
                embed = msg.embeds[0].copy()
                embed.color = 0x95A5A6
                embed.set_footer(text="⌛ Pedido expirado (ninguém aceitou).")
                kwargs["embed"] = embed
            await msg.edit(**kwargs)
        except discord.HTTPException:
            pass  # mensagem apagada ou sem permissão

    async def _clicou_pedido(self, interaction: discord.Interaction):
        """Clique no botão do pedido: usa o MAL registrado ou pergunta por um formulário."""
        username = await asyncio.to_thread(_mal_do_discord, interaction.user.id, interaction.guild_id)
        if username:
            await self._aceitar_pedido(interaction, interaction.message, username)
        else:
            await interaction.response.send_modal(UsuarioMalModal(self, interaction.message))

    async def _aceitar_pedido(self, interaction: discord.Interaction,
                              message: discord.Message, username: str):
        discord_id = interaction.user.id
        guild_id = interaction.guild_id
        await interaction.response.defer(ephemeral=True)

        async def responder(texto: str):
            await interaction.followup.send(texto, ephemeral=True)

        if not await asyncio.to_thread(_usuario_registrado, discord_id, guild_id):
            await responder("⚠️ Você precisa estar registrado neste servidor. Use `/registrar`.")
            return
        if not await asyncio.to_thread(_mal_registrado_no_servidor, username, guild_id):
            await responder(f"❌ **{username}** não está registrado neste servidor. Use o `/mal_entrar`.")
            return

        mal_id = await asyncio.to_thread(_pedido_aberto, message.id)
        if mal_id is None:
            await responder("⌛ Esse pedido já foi aceito por alguém ou expirou.")
            return

        ids, total_lista, _ = await asyncio.to_thread(_ids_vistos, username)
        if total_lista == 0:
            await responder(
                f"❌ Não encontrei a lista de **{username}** no banco. "
                "Ele está registrado no `/mal_entrar`?"
            )
            return
        if mal_id in ids:
            await responder(
                "📺 Esse anime já está na sua lista do MAL (fora do plan to watch), "
                "então não vale como pedido. Deixe pra outra pessoa!"
            )
            return

        resultado, info = await asyncio.to_thread(
            _aceitar_pedido_db, message.id, discord_id, guild_id, username
        )
        if resultado == "indisponivel":
            await responder("⌛ Alguém foi mais rápido: esse pedido já foi aceito ou expirou.")
            return
        if resultado == "limite":
            await responder(
                f"📜 Você já tem {MAX_QUESTS_ATIVAS} quests ativas. "
                "Termine ou abandone uma (`/anime_abandonar`) para aceitar este pedido."
            )
            return
        if resultado == "repetida":
            await responder("📜 Você já tem uma quest ativa com esse mesmo anime.")
            return

        # aceitou: mostra no canal quem pegou e tira o botão
        try:
            embed = message.embeds[0].copy() if message.embeds else discord.Embed()
            embed.color = 0x57F287
            embed.add_field(name="✅ Aceito por", value=interaction.user.mention, inline=False)
            embed.set_footer(text=f"Pedido aceito • prazo até {_fmt_data(info['expira'])}")
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

        await responder(
            f"✅ Pedido aceito! **{info['titulo']}** vale **{info['recompensa']}** flingers. "
            f"Marque como Completed no MAL até {_fmt_data(info['expira'])}."
        )

    # ---- AniList ----
    async def _consulta(self, variables: dict) -> dict:
        """
        Uma página da busca. Tenta até 3 vezes em: 429 (respeita Retry-After),
        5xx, timeout e erro de conexão. Erros 4xx e erros de GraphQL (query
        inválida) falham na hora — repetir não adianta.
        """
        ultimo_erro: Exception | None = None
        for tentativa in range(1, 4):
            try:
                async with self.session.post(
                    ANILIST_URL,
                    json={"query": QUERY, "variables": variables},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 429:
                        try:
                            espera = int(float(resp.headers.get("Retry-After", 5)))
                        except ValueError:
                            espera = 5
                        ultimo_erro = RuntimeError("AniList em rate limit")
                        await asyncio.sleep(espera)
                        continue
                    if resp.status >= 500:
                        ultimo_erro = RuntimeError(f"AniList HTTP {resp.status}")
                        await asyncio.sleep(1.5 * tentativa)
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"AniList HTTP {resp.status}")  # não repete
                    body = await resp.json()
                    if "errors" in body:
                        raise RuntimeError(body["errors"])                 # não repete
                    return body["data"]["Page"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                ultimo_erro = e
                await asyncio.sleep(1.5 * tentativa)
        raise RuntimeError(f"AniList indisponível: {ultimo_erro!r}")

    @staticmethod
    def _prequel_pendente(anime: dict, vistos: set[int]) -> bool:
        """True se o anime tem prequel/série principal que o usuário não viu."""
        edges = (anime.get("relations") or {}).get("edges") or []
        for edge in edges:
            if edge["relationType"] not in RELACOES_EXIGIDAS:
                continue
            node = edge["node"]
            if node.get("type") != "ANIME":
                continue
            id_mal = node.get("idMal")
            if id_mal is None:
                continue  # não dá pra verificar, deixa passar
            if id_mal not in vistos:
                return True
        return False

    async def _sortear(self, ids: list[int], formato: str, min_eps: int, max_eps: int,
                       genero: str | None = None, decada: str | None = None,
                       excluir: list[int] | None = None):
        """
        Retorna o anime sorteado ou None. `genero` é o valor do AniList (ex. "Action") e
        `decada` a chave de DECADAS (ex. "90"); quando None, o filtro não é aplicado.
        `excluir` são mal_ids já oferecidos (reroll/pedidos): saem da busca, mas NÃO contam
        como "vistos" na checagem de prequel (rejeitar um anime não é ter assistido).
        """
        vistos = set(ids)
        base = {
            "ids": ids + (excluir or []),
            "format": formato,
            "minEp": min_eps - 1,  # episodes_greater = "maior que"
            "maxEp": max_eps + 1,  # episodes_lesser  = "menor que"
            "perPage": POR_PAGINA,
        }
        # Só envia as variáveis dos filtros pagos quando usados (ausente = sem filtro).
        if genero:
            base["genre"] = genero
        if decada:
            _, inicio, fim = DECADAS[decada]
            if inicio is not None:
                base["startMin"] = inicio
            if fim is not None:
                base["startMax"] = fim

        # 1º com o piso de popularidade; se não render nenhum anime VÁLIDO, sem o piso.
        # dict.fromkeys tira o duplicado quando o piso do formato já é 0.
        for piso in dict.fromkeys((POPULARIDADE_MIN.get(formato, 0), 0)):
            anime = await self._sortear_com_piso(base, piso, vistos, formato, min_eps, max_eps)
            if anime:
                return anime
        return None

    def _escolher(self, media: list[dict], vistos: set[int], descartes: dict):
        """Primeiro anime válido da lista (em ordem aleatória) ou None."""
        random.shuffle(media)
        for anime in media:
            # sem idMal não dá pra checar se já viu
            if anime.get("idMal") is None:
                descartes["sem_idMal"] += 1
                continue
            # sem duração/episódios não dá pra calcular a recompensa
            if not anime.get("duration") or not anime.get("episodes"):
                descartes["sem_duracao"] += 1
                continue
            if self._prequel_pendente(anime, vistos):
                descartes["prequel_nao_visto"] += 1
                continue
            return anime
        return None

    async def _sortear_com_piso(self, base: dict, piso: int, vistos: set[int],
                                formato: str, min_eps: int, max_eps: int):
        """
        Sorteia um anime válido numa faixa de popularidade SEM confiar no total do AniList
        (que não é preciso): descobre o fim real das páginas pelas próprias respostas.
        """
        vars_ = {**base, "minPop": piso}
        descartes = {"sem_idMal": 0, "sem_duracao": 0, "prequel_nao_visto": 0}
        cache: dict[int, list[dict]] = {}   # página -> candidatos já baixados
        anime_escolhido = None
        consultas = 0
        limite = PAGINA_MAXIMA     # palpite do fim; o AniList não informa o total real
        filtros_log = "".join(f", {k}={base[k]}" for k in ("genre", "startMin", "startMax") if k in base)

        def log(resultado: str):
            print(f"[AnimeQuest] {formato} {min_eps}-{max_eps} eps, popularidade>{piso}{filtros_log}, "
                  f"{len(vistos)} excluídos -> {consultas} consulta(s), fim real <= página {limite}, "
                  f"{resultado}, descartes: {descartes}")

        # 1ª consulta: página 1. Vazia = nenhum candidato nesta faixa; sem próxima = página única.
        resp = await self._consulta({**vars_, "page": 1})
        consultas += 1
        if not resp["media"]:
            limite = 0
            log("faixa vazia")
            return None
        cache[1] = resp["media"]
        if not resp["pageInfo"]["hasNextPage"]:
            limite = 1

        tentadas: set[int] = set()
        while True:
            disponiveis = [p for p in range(1, limite + 1) if p not in tentadas]
            if not disponiveis:
                break
            pagina = random.choice(disponiveis)
            tentadas.add(pagina)

            if pagina in cache:                       # página 1 já foi baixada
                media = cache[pagina]
            else:
                if consultas >= MAX_CONSULTAS:
                    break
                vars_["page"] = pagina
                resp = await self._consulta(vars_)
                consultas += 1
                media = resp["media"]
                if not media:
                    limite = min(limite, pagina - 1)  # passou do fim: a última página real é anterior
                    continue
                if not resp["pageInfo"]["hasNextPage"]:
                    limite = min(limite, pagina)      # esta é a última página real

            anime_escolhido = self._escolher(media, vistos, descartes)
            if anime_escolhido:
                break

        # Orçamento de consultas esgotado sem achar nada: a página 1 já está em mãos,
        # então usa ela como último recurso em vez de falhar com candidatos disponíveis.
        if not anime_escolhido and 1 not in tentadas:
            anime_escolhido = self._escolher(cache[1], vistos, descartes)

        log("OK" if anime_escolhido else "sem candidato válido")
        return anime_escolhido

    # ---- Autocomplete ----
    async def _autocomplete_usuario(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        nomes = await asyncio.to_thread(_buscar_usernames, current, interaction.guild_id)
        return [app_commands.Choice(name=n, value=n) for n in nomes]

    async def _autocomplete_quest(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        ativas = await asyncio.to_thread(_quests_ativas, interaction.user.id, interaction.guild_id)
        return [
            app_commands.Choice(name=titulo[:100], value=str(qid))
            for qid, titulo, *_ in ativas
            if current.lower() in titulo.lower()
        ][:25]

    # ---- /anime_aleatorio ----
    @app_commands.command(
        name="anime_aleatorio",
        description="Receba uma quest de anime e ganhe flingers ao completá-lo (1 pedido a cada 12h)."
    )
    @app_commands.describe(
        usuario="Seu usuário do MAL (o mesmo do /mal_entrar)",
        formato="Formato do anime",
        duracao="Quantidade de episódios (só para TV)",
        genero=f"Filtrar por gênero (+{CUSTO_FILTRO_GENERO} flingers)",
        decada=f"Filtrar pela década de estreia (+{CUSTO_FILTRO_DECADA} flingers)",
    )
    @app_commands.choices(
        formato=[
            app_commands.Choice(name="TV",       value="TV"),
            app_commands.Choice(name="Filme",    value="MOVIE"),
            app_commands.Choice(name="OVA",      value="OVA"),
            app_commands.Choice(name="ONA",      value="ONA"),
            app_commands.Choice(name="Especial", value="SPECIAL"),
        ],
        duracao=[
            app_commands.Choice(name="One cour (1–15 eps)",           value="one_cour"),
            app_commands.Choice(name="Two cour (16–30 eps)",          value="two_cour"),
            app_commands.Choice(name="Anime longo (31–50 eps)",       value="longo"),
            app_commands.Choice(name="Muito longo (mais de 100 eps)", value="muito_longo"),
        ],
        genero=[app_commands.Choice(name=rotulo, value=valor) for rotulo, valor in GENEROS.items()],
        decada=[app_commands.Choice(name=rotulo, value=chave) for chave, (rotulo, _, _) in DECADAS.items()],
    )
    @app_commands.autocomplete(usuario=_autocomplete_usuario)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def anime_aleatorio(
        self,
        interaction: discord.Interaction,
        usuario: str,
        formato: app_commands.Choice[str],
        duracao: app_commands.Choice[str] | None = None,
        genero: app_commands.Choice[str] | None = None,
        decada: app_commands.Choice[str] | None = None,
    ):
        discord_id = interaction.user.id
        guild_id = interaction.guild_id

        # a duração só é usada em TV; OVA, filme, ONA e especial não filtram episódios
        if formato.value == "TV" and duracao is None:
            await interaction.response.send_message(
                "📺 Para animes de TV, escolha também a **duração**.", ephemeral=True
            )
            return

        # checagens rápidas, antes do defer, para responder só pra pessoa
        ativas = await asyncio.to_thread(_quests_ativas, discord_id, guild_id)
        if len(ativas) >= MAX_QUESTS_ATIVAS:
            lista = "\n".join(f"• **{a[1]}**{_fmt_venc(a[4])}" for a in ativas)
            await interaction.response.send_message(
                f"📜 Você já tem {len(ativas)} quests ativas (o limite é {MAX_QUESTS_ATIVAS}):\n"
                f"{lista}\n"
                "Termine uma ou use `/anime_abandonar` para liberar uma vaga "
                "(o limite de 12h entre pedidos continua valendo).",
                ephemeral=True
            )
            return

        restante = await asyncio.to_thread(_segundos_ate_liberar, discord_id, guild_id)
        if restante > 0:
            liberar = int(time.time()) + restante
            view_cd = ComprarCooldownView(discord_id, guild_id)
            await interaction.response.send_message(
                f"⏳ Você já pediu uma quest recentemente. Próxima disponível <t:{liberar}:R>.\n"
                f"Quer pedir agora? Pular a espera custa **{CUSTO_COMPRAR_COOLDOWN}** flingers.",
                ephemeral=True,
                view=view_cd,
            )
            view_cd.interaction = interaction
            return

        if not await asyncio.to_thread(_usuario_registrado, discord_id, guild_id):
            await interaction.response.send_message(
                "⚠️ Você precisa estar registrado neste servidor. Use `/registrar`.",
                ephemeral=True
            )
            return

        if not await asyncio.to_thread(_mal_registrado_no_servidor, usuario, guild_id):
            await interaction.response.send_message(
                f"❌ **{usuario}** não está registrado neste servidor. Use o `/mal_entrar`.",
                ephemeral=True
            )
            return

        # filtros pagos: confere o saldo antes de gastar chamadas no AniList
        custo = (CUSTO_FILTRO_GENERO if genero else 0) + (CUSTO_FILTRO_DECADA if decada else 0)
        if custo:
            saldo = await asyncio.to_thread(_saldo, discord_id, guild_id)
            if saldo < custo:
                await interaction.response.send_message(
                    f"💸 Os filtros escolhidos custam **{custo}** flingers e você tem **{saldo}**.",
                    ephemeral=True
                )
                return

        await interaction.response.defer()

        ids, total_lista, ultima = await asyncio.to_thread(_ids_vistos, usuario)
        if total_lista == 0:
            await interaction.followup.send(
                f"❌ Não encontrei a lista de **{usuario}** no banco. "
                "Ele está registrado no `/mal_entrar`?"
            )
            return

        try:
            if formato.value == "TV":
                min_eps, max_eps = DURACOES[duracao.value]
            else:
                min_eps, max_eps = 0, 100000  # sem filtro de episódios
            anime = await self._sortear(
                ids, formato.value, min_eps, max_eps,
                genero.value if genero else None,
                decada.value if decada else None,
            )
        except Exception as e:
            print(f"[AnimeQuest] Erro AniList: {e}")
            await interaction.followup.send(
                "⚠️ Não consegui falar com o AniList agora. Tente de novo em instantes."
            )
            return

        filtros = []
        if genero:
            filtros.append(f"🎭 Gênero: {genero.name}")
        if decada:
            filtros.append(f"📅 Década: {decada.name}")

        if anime is None:
            nomes = [n for n in (genero and genero.name, decada and decada.name) if n]
            com_filtros = f" com {' e '.join(nomes)}" if nomes else ""
            cobranca = " Seus flingers não foram cobrados." if custo else ""
            await interaction.followup.send(
                f"😕 Não achei nenhum anime **{formato.name}**"
                f"{f' ({duracao.name})' if formato.value == 'TV' else ''}{com_filtros} "
                f"que **{usuario}** ainda não tenha visto. Seu limite diário não foi gasto.{cobranca}"
            )
            return

        recusas = await asyncio.to_thread(_recusas_seguidas, guild_id)
        minutos, recompensa, prazo, mult_nota = _calcular_oferta(anime, recusas)

        quest_id = await asyncio.to_thread(
            _criar_quest, discord_id, guild_id, usuario, anime, minutos, recompensa, prazo, custo
        )
        if quest_id is None:
            # o saldo caiu entre a checagem e a criação (ex.: gastou em outro lugar)
            await interaction.followup.send(
                f"💸 Seu saldo não cobre mais os filtros (**{custo}** flingers). Nada foi cobrado."
            )
            return

        embed = _embed_quest(anime, minutos, recompensa, prazo, ultima, mult_nota, recusas,
                             filtros, custo)
        ctx = {
            "usuario": usuario,
            "formato": formato.value,
            "min_eps": min_eps,
            "max_eps": max_eps,
            "genero": genero.value if genero else None,
            "decada": decada.value if decada else None,
            "ids": ids,
            "ultima": ultima,
            "filtros": filtros,
            "custo_filtros": custo,
            "oferecidos": [anime["idMal"]],
        }
        view = QuestView(self, quest_id, discord_id, guild_id, embed, ctx)
        view.message = await interaction.followup.send(embed=embed, view=view, wait=True)

    @anime_aleatorio.error
    async def anime_aleatorio_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ Aguarde {error.retry_after:.0f}s para usar de novo.",
                ephemeral=True
            )
        else:
            raise error

    # ---- /anime_quests ----
    @app_commands.command(
        name="anime_quests",
        description="Mostra suas quests de anime ativas e quando poderá pedir outra."
    )
    @app_commands.guild_only()
    async def anime_quests(self, interaction: discord.Interaction):
        ativas = await asyncio.to_thread(_quests_ativas, interaction.user.id, interaction.guild_id)
        restante = await asyncio.to_thread(_segundos_ate_liberar, interaction.user.id, interaction.guild_id)

        embed = discord.Embed(title="📜 Suas quests de anime", color=COR_EMBED)
        if ativas:
            for _, titulo, mal_id, recompensa, expira_em, origem in ativas:
                venc = ""
                if expira_em:
                    venc = f"\n⏳ Vence <t:{int(datetime.fromisoformat(expira_em).timestamp())}:R>"
                tag = "🏛️ Pedido da guilda • " if origem == "guilda" else ""
                embed.add_field(
                    name=titulo,
                    value=f"{tag}🏆 {recompensa} flingers • "
                          f"[MAL](https://myanimelist.net/anime/{mal_id}){venc}",
                    inline=False
                )
            embed.description = f"{len(ativas)}/{MAX_QUESTS_ATIVAS} quests ativas."
        else:
            embed.description = "Nenhuma quest ativa."

        if restante > 0:
            embed.add_field(
                name="Próximo pedido",
                value=f"<t:{int(time.time()) + restante}:R>",
                inline=False
            )
        else:
            embed.set_footer(text="Você já pode pedir uma nova quest com /anime_aleatorio.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- /anime_abandonar ----
    @app_commands.command(
        name="anime_abandonar",
        description="Abandona uma das suas quests de anime ativas (o limite de 12h entre pedidos continua)."
    )
    @app_commands.describe(quest="Qual quest abandonar (obrigatório se você tiver mais de uma)")
    @app_commands.autocomplete(quest=_autocomplete_quest)
    @app_commands.guild_only()
    async def anime_abandonar(self, interaction: discord.Interaction, quest: str | None = None):
        discord_id = interaction.user.id
        guild_id = interaction.guild_id

        ativas = await asyncio.to_thread(_quests_ativas, discord_id, guild_id)
        if not ativas:
            await interaction.response.send_message(
                "Você não tem nenhuma quest ativa.", ephemeral=True
            )
            return

        if quest is None:
            if len(ativas) > 1:
                lista = "\n".join(f"• **{a[1]}**" for a in ativas)
                await interaction.response.send_message(
                    f"Você tem {len(ativas)} quests ativas:\n{lista}\n"
                    "Escolha qual abandonar no campo `quest` do comando.",
                    ephemeral=True
                )
                return
            quest_id = ativas[0][0]
        else:
            try:
                quest_id = int(quest)
            except ValueError:
                await interaction.response.send_message(
                    "Escolha uma das suas quests na lista do campo `quest`.", ephemeral=True
                )
                return

        titulo = await asyncio.to_thread(_abandonar_quest, discord_id, guild_id, quest_id)
        if not titulo:
            await interaction.response.send_message(
                "Não encontrei essa quest entre as suas ativas.", ephemeral=True
            )
            return

        restante = await asyncio.to_thread(_segundos_ate_liberar, discord_id, guild_id)
        if restante > 0:
            extra = f"Você poderá pedir outra <t:{int(time.time()) + restante}:R>."
        else:
            extra = "Você já pode pedir outra com `/anime_aleatorio`."
        await interaction.response.send_message(
            f"🏳️ Quest **{titulo}** abandonada. {extra}", ephemeral=True
        )

    # ---- /anime_mural ----
    @app_commands.command(
        name="anime_mural",
        description="Mostra as quests de anime ativas de todos neste servidor."
    )
    @app_commands.guild_only()
    async def anime_mural(self, interaction: discord.Interaction):
        rows = await asyncio.to_thread(_quests_do_servidor, interaction.guild_id)
        paginas = _paginas_mural(rows)

        if len(paginas) == 1:
            await interaction.response.send_message(embed=paginas[0])
            return

        view = MuralView(paginas)
        await interaction.response.send_message(embed=paginas[0], view=view)
        view.message = await interaction.original_response()

    # ---- /anime_pedido_agora (admin, útil para testar) ----
    @app_commands.command(
        name="anime_pedido_agora",
        description="[Admin] Publica um pedido da guilda agora no canal do MAL Tracker."
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def anime_pedido_agora(self, interaction: discord.Interaction):
        canal_id = await asyncio.to_thread(_canal_mal, interaction.guild_id)
        if not canal_id:
            await interaction.response.send_message(
                "⚠️ Configure o canal do MAL Tracker com `/config_canal` primeiro.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        ok = await self._publicar_pedido(interaction.guild_id, canal_id)
        await interaction.followup.send(
            "✅ Pedido publicado no canal do MAL Tracker. O próximo sai em 4~6h."
            if ok else
            "⚠️ Não consegui publicar o pedido (sem anime, canal inacessível ou sem permissão). "
            "Veja o log do bot.",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AnimeAleatorioCog(bot))