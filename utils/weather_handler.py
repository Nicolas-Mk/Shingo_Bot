import unicodedata
import aiohttp
import discord
from config import BOT_CONFIG, is_weather_api_configured

GEO_URL     = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"


def _normalizar(texto: str) -> str:
    """Remove acentos e coloca em minúsculo para comparação flexível."""
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode().lower().strip()


async def _geocode(
    session: aiohttp.ClientSession,
    cidade: str,
    estado: str | None,
    api_key: str,
) -> tuple[float, float, str, str] | None:
    """
    Usa a Geocoding API da OpenWeatherMap para resolver cidade → (lat, lon).

    Quando `estado` é informado, busca até 10 resultados e filtra pelo
    campo `state` retornado pela API, aceitando tanto sigla (SC) quanto
    nome completo (Santa Catarina), sem distinção de acentos/maiúsculas.

    Retorna (lat, lon, nome_cidade, nome_estado) ou None se não encontrar.
    """
    params = {
        "q": f"{cidade},BR",   # limita ao Brasil; remova ",BR" para busca global
        "limit": 10,
        "appid": api_key,
    }

    async with session.get(GEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            return None
        resultados = await resp.json()

    if not resultados:
        return None

    if not estado:
        # Sem filtro de estado — usa o primeiro resultado
        r = resultados[0]
        return r["lat"], r["lon"], r.get("name", cidade), r.get("state", "")

    estado_norm = _normalizar(estado)

    for r in resultados:
        state_api = _normalizar(r.get("state", ""))
        # Aceita correspondência parcial: "sc" bate em "santa catarina" e vice-versa
        if estado_norm in state_api or state_api in estado_norm:
            return r["lat"], r["lon"], r.get("name", cidade), r.get("state", estado)

    return None


async def get_weather(cidade: str, estado: str | None = None):
    """
    Busca informações de clima para uma cidade, com filtro opcional de estado.

    Fluxo:
      1. Geocoding API → resolve cidade+estado para lat/lon dentro do Brasil.
      2. Weather API   → busca clima pelas coordenadas (sem ambiguidade de país).

    Args:
        cidade (str): Nome da cidade.
        estado (str | None): Estado/UF para desambiguar (ex: "SC", "Santa Catarina").

    Returns:
        tuple: (discord.Embed, None) em caso de sucesso, (None, str) em caso de erro.
    """
    if not is_weather_api_configured():
        return None, "API de clima não configurada. Configure a chave WEATHER_API_KEY no .env."

    api_key = BOT_CONFIG["WEATHER_API_KEY"]

    try:
        async with aiohttp.ClientSession() as session:

            # ── Etapa 1: Geocoding ────────────────────────────────────
            geo = await _geocode(session, cidade, estado, api_key)

            if geo is None:
                if estado:
                    return None, (
                        f'Não encontrei **{cidade}** no estado **{estado}**. '
                        f'Verifique o nome da cidade e do estado e tente novamente.'
                    )
                else:
                    return None, (
                        f'Cidade **{cidade}** não encontrada. '
                        f'Tente incluir o estado (ex: `/clima Laguna SC`).'
                    )

            lat, lon, nome_cidade, nome_estado = geo

            # ── Etapa 2: Clima pelas coordenadas ─────────────────────
            params = {
                "lat": lat,
                "lon": lon,
                "appid": api_key,
                "units": "metric",
                "lang": "pt_br",
            }

            async with session.get(WEATHER_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

                if resp.status != 200:
                    erro = data.get("message", "Erro desconhecido")
                    return None, f"Erro ao buscar clima: {erro}"

                temp     = data["main"]["temp"]
                sensacao = data["main"]["feels_like"]
                umidade  = data["main"]["humidity"]
                desc     = data["weather"][0]["description"].capitalize()
                vento    = data["wind"]["speed"]
                pais     = data["sys"]["country"]

                # Título mostra estado se disponível
                if nome_estado:
                    titulo = f"🌤️ Clima em {nome_cidade} — {nome_estado}, {pais}"
                else:
                    titulo = f"🌤️ Clima em {nome_cidade}, {pais}"

                embed = discord.Embed(
                    title=titulo,
                    description=desc,
                    color=discord.Color.teal(),
                )
                embed.add_field(name="🌡️ Temperatura", value=f"{temp:.1f}°C",    inline=True)
                embed.add_field(name="🤔 Sensação",    value=f"{sensacao:.1f}°C", inline=True)
                embed.add_field(name="💧 Umidade",     value=f"{umidade}%",       inline=True)
                embed.add_field(name="💨 Vento",       value=f"{vento} m/s",      inline=True)
                return embed, None

    except Exception as e:
        return None, f"Erro ao buscar informações de clima: {e}"