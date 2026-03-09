import discord
from discord.ext import commands
import sqlite3
from datetime import datetime

class VoiceXPCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.call_entradas = {}  # {user_id: {'entrada': datetime, 'mutado_desde': datetime or None}}

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        # Entrou em call
        if before.channel is None and after.channel is not None:
            self.call_entradas[member.id] = {
                'entrada': datetime.now(),
                'mutado_desde': datetime.now() if after.self_mute or after.mute else None,
                'tempo_mutado_total': 0  # acumula o tempo mutado em segundos
            }

        # Saiu da call
        elif before.channel is not None and after.channel is None:
            dados = self.call_entradas.pop(member.id, None)
            if dados:
                # Se estava mutado ao sair, contabiliza o tempo final
                if dados['mutado_desde']:
                    tempo_mutado = (datetime.now() - dados['mutado_desde']).total_seconds()
                    dados['tempo_mutado_total'] += tempo_mutado

                tempo_total = datetime.now() - dados['entrada']
                minutos_total = tempo_total.total_seconds() / 60
                minutos_mutado = dados['tempo_mutado_total'] / 60
                minutos_validos = minutos_total - minutos_mutado

                # Log para debug (opcional)
                print(f"{member.name}: {minutos_total:.1f}min total, {minutos_mutado:.1f}min mutado, {minutos_validos:.1f}min válidos")

                # Só ganha XP se ficou desmutado por pelo menos 15 minutos
                if minutos_validos >= 15:
                    xp_ganho = int(minutos_validos // 15) * 3
                    conn = sqlite3.connect('usuarios.db')
                    c = conn.cursor()
                    c.execute("SELECT xp, nivel FROM usuarios WHERE id = ?", (member.id,))
                    row = c.fetchone()

                    if row:
                        xp_atual, nivel = row
                        novo_xp = xp_atual + xp_ganho

                        xp_necessario = 50 * (1.1 ** (nivel - 1))
                        dificuldade = 1 + (nivel // 10) * 0.05

                        while novo_xp >= xp_necessario * dificuldade:
                            novo_xp -= int(xp_necessario * dificuldade)
                            nivel += 1
                            xp_necessario = 50 * (1.1 ** (nivel - 1))
                            dificuldade = 1 + (nivel // 10) * 0.05

                            if member.guild.system_channel:
                                await member.guild.system_channel.send(
                                    f"🎉 Parabéns, {member.mention}, você subiu para o nível {nivel} de fracasso! 💀"
                                )

                        c.execute("UPDATE usuarios SET xp = ?, nivel = ? WHERE id = ?", (int(novo_xp), nivel, member.id))
                        conn.commit()

                    conn.close()

        # Mudou estado de mute (mas continua na call)
        elif before.channel is not None and after.channel is not None:
            if member.id in self.call_entradas:
                dados = self.call_entradas[member.id]
                
                # Estava desmutado e agora mutou
                if not (before.self_mute or before.mute) and (after.self_mute or after.mute):
                    dados['mutado_desde'] = datetime.now()
                
                # Estava mutado e agora desmutou
                elif (before.self_mute or before.mute) and not (after.self_mute or after.mute):
                    if dados['mutado_desde']:
                        tempo_mutado = (datetime.now() - dados['mutado_desde']).total_seconds()
                        dados['tempo_mutado_total'] += tempo_mutado
                        dados['mutado_desde'] = None

async def setup(bot):
    await bot.add_cog(VoiceXPCog(bot))