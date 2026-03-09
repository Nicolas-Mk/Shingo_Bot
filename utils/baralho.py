import random

def criar_baralho(qtd_baralhos=1):
    """Cria um baralho realista com naipes."""
    valores = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    naipes = ['♠', '♥', '♦', '♣']
    baralho = []

    for _ in range(qtd_baralhos):
        for valor in valores:
            for naipe in naipes:
                baralho.append(f"{valor}{naipe}")

    random.shuffle(baralho)
    return baralho