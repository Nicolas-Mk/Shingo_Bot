"""
Framework de "easter eggs" reativos a eventos de progresso do MAL Tracker.

Um easter egg é uma reação especial do bot (hoje: duas imagens enviadas em
sequência) disparada quando um usuário monitorado atualiza um item
ESPECÍFICO de um jeito específico — por exemplo, completar um mangá
específico com uma certa nota.

Para adicionar um novo easter egg, sem tocar em mal_tracker_cog.py:
  1. Crie uma subclasse de `EasterEgg` neste arquivo (ou em outro módulo).
  2. Implemente `aplica_ao_item`, `detectar` e `gerar_arquivos`.
  3. Instancie-a e adicione à lista `EASTER_EGGS_REGISTRADOS`, no final
     deste arquivo.

O cog principal só conhece `EasterEggManager` — ele chama
`manager.detectar(...)` item a item durante a comparação de snapshots, e
`manager.enviar(...)` para efetivamente postar no Discord. Nenhuma lógica
específica de um easter egg individual (Shingo ou outro) vaza para o cog.
"""

from __future__ import annotations

import os
import asyncio
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import discord
from PIL import Image, ImageDraw, ImageOps

from cogs.mal_visual import (
    _SS, _PAD, _CARD_W,
    _BG, _CARD_BG, _TEXT_MAIN, _TEXT_MUTED,
    COR_ANIME,
    _fonte, _quebrar_texto, _mascara_arredondada,
    _abrir_imagem, _placeholder_avatar, _finalizar_canvas,
)


def _arquivo_png(png_bytes: bytes, filename: str) -> discord.File:
    buffer = BytesIO(png_bytes)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


# ══════════════════════════════════════════════
#  Framework — classes-base
# ══════════════════════════════════════════════

@dataclass
class EventoEasterEgg:
    """Uma ocorrência detectada de um easter egg, pronta para ser enviada."""
    egg: "EasterEgg"
    usuario: str
    dados: dict[str, Any]


class EasterEgg(ABC):
    """
    Classe-base para um easter egg. Cada implementação decide, a partir da
    comparação de snapshot anterior x atual de UM item, se deve disparar —
    e sabe gerar seus próprios arquivos de imagem para enviar no Discord.
    """

    # Nome curto usado em logs — sobrescreva em cada subclasse.
    nome: str = "easter_egg"

    @abstractmethod
    def aplica_ao_item(self, tipo: str, item_id: str) -> bool:
        """
        Filtro barato, chamado para TODO item processado no ciclo (anime e
        mangá, de todo mundo), antes de qualquer coisa mais cara. Deve ser
        rápido — normalmente só compara tipo e item_id. É o que evita gastar
        tempo comparando snapshots pra itens que não tem nada a ver com este
        easter egg específico.
        """
        raise NotImplementedError

    @abstractmethod
    def detectar(
        self,
        tipo: str,
        item_id: str,
        ant: dict,
        atu: dict,
        completou_agora: bool,
    ) -> dict[str, Any] | None:
        """
        Chamado só para itens que passaram em `aplica_ao_item`. Recebe os
        snapshots anterior/atual (já com json.loads aplicado) e
        `completou_agora` (True se a transição para status="completed"
        aconteceu NESTA comparação, e não em algum momento anterior).

        Retorna um dict com os dados do evento (ex.: {"nota": 8}) se o
        easter egg deve disparar, ou None caso contrário.
        """
        raise NotImplementedError

    @abstractmethod
    async def gerar_arquivos(self, usuario: str, dados: dict[str, Any]) -> list[discord.File]:
        """
        Gera a(s) mensagem(ns) de imagem para este evento, na ordem em que
        devem ser enviadas — um discord.File por mensagem.
        """
        raise NotImplementedError


class EasterEggManager:
    """
    Ponto único de integração entre mal_tracker_cog.py e os easter eggs
    registrados. O cog não conhece Shingo nem qualquer outro easter egg
    específico — só fala com esta classe.
    """

    def __init__(self, eggs: list[EasterEgg] | None = None):
        self.eggs: list[EasterEgg] = eggs if eggs is not None else list(EASTER_EGGS_REGISTRADOS)

    def detectar(
        self,
        tipo: str,
        item_id: str,
        ant: dict,
        atu: dict,
        completou_agora: bool,
        username: str = "",
    ) -> list[EventoEasterEgg]:
        """
        Roda todos os easter eggs registrados contra este item e retorna os
        que dispararam (normalmente 0 ou 1, mas fica como lista pra suportar
        vários easter eggs coincidindo no mesmo item, caso algum dia
        aconteça). Um erro em um easter egg específico é isolado (logado e
        ignorado) e não impede os demais de rodar nem quebra o ciclo.
        """
        eventos: list[EventoEasterEgg] = []
        for egg in self.eggs:
            try:
                if not egg.aplica_ao_item(tipo, item_id):
                    continue
                dados = egg.detectar(tipo, item_id, ant, atu, completou_agora)
            except Exception as e:
                print(f"[EasterEggManager] ❌ Erro ao rodar detecção de '{egg.nome}' em {tipo}/{item_id} ({username}): {e}")
                traceback.print_exc()
                continue
            if dados is not None:
                print(f"[EasterEggManager] 🎉 Easter egg '{egg.nome}' disparado para '{username}' em {tipo}/{item_id}: {dados}")
                eventos.append(EventoEasterEgg(egg=egg, usuario=username, dados=dados))
        return eventos

    async def enviar(self, canal: discord.TextChannel, evento: EventoEasterEgg):
        """Gera e envia, em sequência, os arquivos deste evento no canal."""
        try:
            arquivos = await evento.egg.gerar_arquivos(evento.usuario, evento.dados)
        except Exception as e:
            print(f"[EasterEggManager] ❌ Erro ao gerar arquivos do easter egg '{evento.egg.nome}' de '{evento.usuario}': {e}")
            traceback.print_exc()
            return

        for arquivo in arquivos:
            try:
                await canal.send(file=arquivo)
            except Exception as e:
                print(f"[EasterEggManager] ❌ Erro ao enviar arquivo do easter egg '{evento.egg.nome}' de '{evento.usuario}' no canal #{canal}: {e}")
                traceback.print_exc()
            await asyncio.sleep(0.3)


# ══════════════════════════════════════════════
#  Easter egg — "Watashi wa Shingo"
# ══════════════════════════════════════════════
#
# Dispara quando alguém completa o mangá "Watashi wa Shingo" no MAL, em
# homenagem ao nome do próprio bot.

SHINGO_MANGA_ID = "24646"

SHINGO_IMAGENS = {
    "baixa":          os.path.join("assets", "shingo 3-.jpg"),
    "media":          os.path.join("assets", "shingo 4-6.jpg"),
    "neutra":         os.path.join("assets", "shingo 7.jpg"),
    "elogio":         os.path.join("assets", "shingo 8-9.jpg"),
    "transcendencia": os.path.join("assets", "shingo 10.jpg"),
}

# Borda do card de evento do Shingo: uma versão escurecida de COR_ANIME (em
# vez do vermelho vivo de COR_MANGA), pra manter uma identidade visual
# própria sem competir com a atenção que deve ir pra imagem em si.
COR_SHINGO_BORDA = tuple(int(c * 0.45) for c in COR_ANIME)  # (44, 69, 114)

# Proporção original das imagens do easter egg (2133x1530 px), usada para
# redimensionar sem distorcer.
_RAZAO_SHINGO = 2133 / 1530

# Altura-alvo do CARD INTEIRO da arte (não da imagem isolada) — escolhida
# pra ficar no mesmo patamar dos outros cards do mal_tracker. O Discord
# limita a altura de exibição de cada anexo, e quando a altura passa desse
# teto a largura visual encolhe proporcionalmente — foi o que causava o
# card do Shingo aparecer mais estreito que os outros mesmo com a mesma
# largura em pixels.
_ALVO_ALTURA_CARD_SHINGO = 460


class ShingoEasterEgg(EasterEgg):
    nome = "shingo"

    def __init__(self):
        # Cache em memória dos bytes de cada imagem (uma por faixa de nota),
        # carregados de assets/ uma única vez.
        self._img_cache: dict[str, bytes | None] = {}

    # ── Detecção ──────────────────────────────

    def aplica_ao_item(self, tipo: str, item_id: str) -> bool:
        return tipo == "manga" and item_id == SHINGO_MANGA_ID

    def detectar(
        self,
        tipo: str,
        item_id: str,
        ant: dict,
        atu: dict,
        completou_agora: bool,
    ) -> dict[str, Any] | None:
        # Só dispara na transição REAL pra "completed", com base na
        # comparação snapshot anterior x atual — evita falso positivo em
        # relidas ou mudanças de nota sem trocar status.
        if not completou_agora:
            return None
        nota = atu.get("score") or 0
        if nota <= 0:
            print(f"[EasterEgg:{self.nome}] completou sem nota — não disparado (aguardando nota).")
            return None
        return {"nota": nota}

    # ── Reação / conteúdo ─────────────────────

    def _reacao(self, nota: int) -> tuple[str, str, str]:
        """
        Retorna (chave_imagem, titulo_embed, texto_com_{nota}) de acordo com
        a nota dada pelo usuário ao completar o mangá.
        """
        # Sem emoji nos títulos de propósito: eles vão desenhados dentro do
        # PNG do card (mesma fonte DejaVu usada em mal_visual._fonte), que
        # não tem glifos de emoji — apareceria como retângulo vazio ("tofu").
        if nota <= 3:
            return (
                "baixa",
                "SHINGO NÃO GOSTOU DISSO",
                "Nota {nota}/10 Shingo te despreza. Pense bem antes de me subestimar."
            )
        elif nota <= 6:
            return (
                "media",
                "SHINGO ERGUE UMA SOBRANCELHA",
                "Nota {nota}/10... Shingo espera mais de você. Tente novamente."
            )
        elif nota == 7:
            return (
                "neutra",
                "SHINGO APROVA, COM MODERAÇÃO",
                "Nota {nota}/10 Shingo reconhece o esforço. É um começo razoável."
            )
        elif nota <= 9:
            return (
                "elogio",
                "SHINGO ESTÁ IMPRESSIONADO",
                "Nota {nota}/10! Shingo reconhece sua sabedoria e o seu bom gosto."
            )
        else:  # 10
            return (
                "transcendencia",
                "VOCÊ TRANSCENDEU",
                "Nota 10/10. Você não apenas leu — você entendeu. Shingo vê em você um igual."
            )

    def _carregar_imagem(self, chave: str) -> bytes | None:
        """Lê a imagem correspondente de assets/ uma única vez e mantém em cache em memória."""
        if chave not in self._img_cache:
            caminho = SHINGO_IMAGENS[chave]
            if os.path.isfile(caminho):
                try:
                    with open(caminho, "rb") as f:
                        self._img_cache[chave] = f.read()
                except Exception as e:
                    print(f"[EasterEgg:{self.nome}] ⚠️  Não foi possível carregar '{caminho}': {e}")
                    self._img_cache[chave] = None
            else:
                print(f"[EasterEgg:{self.nome}] ⚠️  Imagem '{caminho}' não encontrada.")
                self._img_cache[chave] = None
        return self._img_cache[chave]

    # ── Geração dos cards (mesmo estilo visual do resto do mal_tracker) ──

    def _gerar_imagem_texto(self, usuario: str, nota: int, titulo: str, texto: str) -> bytes:
        """
        Card com a parte textual da reação (título + subtítulo + texto).
        Mandado como a PRIMEIRA de duas mensagens — separar texto e arte em
        cards distintos evita que um dispute espaço vertical com o outro,
        então a arte pode ocupar uma altura bem maior sem estourar a altura
        total segura que mantém a largura de exibição igual à dos outros
        cards do mal_tracker.
        """
        s = _SS
        pad, card_w = _PAD * s, _CARD_W * s

        f_titulo = _fonte(30 * s, negrito=True)
        f_sub    = _fonte(19 * s)
        f_texto  = _fonte(21 * s)

        medidor = ImageDraw.Draw(Image.new("RGB", (card_w, 10)))

        subtitulo = f"@{usuario}  ·  Watashi wa Shingo  ·  Nota {nota}/10"
        largura_texto_max = card_w - pad * 4
        linhas_texto = _quebrar_texto(medidor, texto, f_texto, largura_texto_max)

        titulo_y = pad + 10 * s
        sub_y    = titulo_y + 42 * s
        texto_y  = sub_y + 36 * s
        linha_h  = 30 * s
        altura   = texto_y + len(linhas_texto) * linha_h + pad

        canvas = Image.new("RGB", (card_w, altura), _BG)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

        titulo_w = draw.textlength(titulo, font=f_titulo)
        draw.text(((card_w - titulo_w) // 2, titulo_y), titulo, font=f_titulo, fill=(255, 255, 255))

        sub_w = draw.textlength(subtitulo, font=f_sub)
        draw.text(((card_w - sub_w) // 2, sub_y), subtitulo, font=f_sub, fill=_TEXT_MUTED)

        y_linha = texto_y
        for linha in linhas_texto:
            linha_w = draw.textlength(linha, font=f_texto)
            draw.text(((card_w - linha_w) // 2, y_linha), linha, font=f_texto, fill=_TEXT_MAIN)
            y_linha += linha_h

        return _finalizar_canvas(canvas)

    def _gerar_imagem_arte(self, imagem_bytes: bytes | None) -> bytes:
        """
        Card com só a arte (shingo X.jpg), mandado como a SEGUNDA mensagem,
        depois do card de texto. Como não compete por espaço com
        título/subtítulo/texto, a imagem pode usar quase toda a
        altura-alvo do card (_ALVO_ALTURA_CARD_SHINGO) pra si, mantendo a
        proporção original (2133x1530 ≈ 1.394:1) sem distorcer.
        """
        s = _SS
        pad, card_w = _PAD * s, _CARD_W * s

        altura_total_alvo = _ALVO_ALTURA_CARD_SHINGO * s
        altura_minima = 160 * s
        altura_img = max(altura_minima, altura_total_alvo - pad * 2)
        largura_img = int(altura_img * _RAZAO_SHINGO)
        largura_max_disponivel = card_w - pad * 2
        if largura_img > largura_max_disponivel:
            largura_img = largura_max_disponivel
            altura_img = int(largura_img / _RAZAO_SHINGO)

        altura = altura_img + pad * 2

        canvas = Image.new("RGB", (card_w, altura), _BG)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((0, 0, card_w - 1, altura - 1), radius=14 * s, fill=_CARD_BG)

        icone_img = _abrir_imagem(imagem_bytes, _placeholder_avatar())
        icone = ImageOps.fit(icone_img, (largura_img, altura_img))
        icone_x = (card_w - largura_img) // 2
        icone_y = pad
        canvas.paste(icone, (icone_x, icone_y), _mascara_arredondada((largura_img, altura_img), 16 * s))
        draw.rounded_rectangle(
            (icone_x, icone_y, icone_x + largura_img, icone_y + altura_img),
            radius=16 * s, outline=COR_SHINGO_BORDA, width=3 * s
        )

        return _finalizar_canvas(canvas)

    # ── Interface pública (chamada pelo EasterEggManager) ────────────────

    async def gerar_arquivos(self, usuario: str, dados: dict[str, Any]) -> list[discord.File]:
        nota = dados["nota"]
        chave, titulo, texto_modelo = self._reacao(nota)
        img_bytes = self._carregar_imagem(chave)
        texto = texto_modelo.format(nota=nota)

        png_texto = self._gerar_imagem_texto(usuario, nota, titulo, texto)
        png_arte  = self._gerar_imagem_arte(img_bytes)

        return [
            _arquivo_png(png_texto, f"shingo_texto_{usuario}.png"),
            _arquivo_png(png_arte, f"shingo_arte_{usuario}.png"),
        ]


# ══════════════════════════════════════════════
#  Registro de easter eggs disponíveis
# ══════════════════════════════════════════════
#
# Para adicionar um novo easter egg: implemente a subclasse de EasterEgg
# (neste arquivo ou em outro), instancie-a e adicione aqui. Nenhuma outra
# parte do bot precisa saber que ela existe.
EASTER_EGGS_REGISTRADOS: list[EasterEgg] = [
    ShingoEasterEgg(),
]