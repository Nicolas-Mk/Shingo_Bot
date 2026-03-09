def calculate_xp_needed(nivel: int) -> int:
    """
    Calcula quanto de XP é necessário para subir de nível,
    baseado no nível atual.
    """
    base = 50
    xp_total = base
    for i in range(1, nivel):
        mult = 0.10 + ((i - 1) // 10) * 0.05
        xp_total += xp_total * mult
    return int(xp_total)

def calculate_level_up(current_xp: int, current_level: int) -> tuple:
    """
    Calcula se o usuário sobe de nível e retorna novo XP e nível.
    
    :param current_xp: XP atual do usuário
    :param current_level: Nível atual do usuário
    :return: Tuple (novo_xp, novo_nivel, upou)
    """
    xp = current_xp
    nivel = current_level
    upou = False

    xp_necessario = calculate_xp_needed(nivel)
    while xp >= xp_necessario:
        xp -= xp_necessario
        nivel += 1
        xp_necessario = calculate_xp_needed(nivel)
        upou = True

    return xp, nivel, upou