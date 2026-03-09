# Gerador de Texto Aleatório - Atualizações

## Mudanças Implementadas

### 1. Intervalo de Tempo Alterado
- **Antes**: Intervalo aleatório entre 5-10 minutos (300-600 segundos)
- **Agora**: Intervalo fixo de 20 minutos (1200 segundos)

### 2. Controle de Ativação/Desativação
- Adicionada variável `gerador_ativo` para controlar se o gerador está funcionando
- O gerador pode ser pausado e retomado conforme necessário

### 3. Novos Comandos

#### `/gerador`
- **Descrição**: Habilita ou desabilita o gerador de texto aleatório
- **Permissão**: Apenas administradores podem usar
- **Funcionamento**: 
  - Se ativo → desabilita
  - Se desabilitado → habilita
- **Resposta**: Mensagem de confirmação (ephemeral)

#### `/status_gerador`
- **Descrição**: Mostra o status atual do gerador de texto
- **Permissão**: Qualquer usuário pode usar
- **Informações exibidas**:
  - Status (Ativo/Desabilitado)
  - Intervalo de tempo
  - Próximo texto
- **Visualização**: Embed colorido (verde para ativo, vermelho para desabilitado)

## Como Usar

1. **Para desabilitar o gerador**:
   ```
   /gerador
   ```

2. **Para verificar o status**:
   ```
   /status_gerador
   ```

3. **Para reativar o gerador**:
   ```
   /gerador
   ```

## Comportamento

- Quando **ativo**: Gera textos a cada 20 minutos exatos
- Quando **desabilitado**: Para de gerar textos, mas mantém o contador interno
- Ao **reativar**: Reinicia imediatamente o contador de 20 minutos
- **Permissões**: Apenas administradores podem controlar o gerador

## Arquivo Modificado
- `cogs/economy.py` - Adicionadas as novas funcionalidades 