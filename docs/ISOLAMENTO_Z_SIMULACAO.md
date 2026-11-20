# Isolamento dos relés Z no SmartV42

## Estado operacional

O SmartV42 não usa mais o valor da variável global SIMULADO como autorização para ativar saídas físicas. O modo efetivo de Z é derivado de:
- conexão do GRBL (real ou virtual);
- conflito entre a configuração global e a conexão efetiva;
- autorização explícita e temporária de Z físico.

**SIMULAÇÃO** — GRBL virtual selecionado por --simulado ou pela UI. G-code e testes Z não acionam OutputDevice.on(). Objetos antigos podem existir na memória, mas o ramo de simulação jamais os usa. Ao conectar uma porta simulada, os objetos físicos são desligados e fechados.

**TESTE FÍSICO XY** — padrão após conexão real. Somente os motores X/Y têm permissão de mover; os oito GPIOs Z não são inicializados nem energizados. Trabalhos G-code contendo Z são bloqueados antes de iniciar.

**OPERAÇÃO FÍSICA COMPLETA** — botão de habilitação com confirmação explícita. Só é aceita com GRBL realmente conectado, Home XY válido, estado Idle e sensores X/Y liberados. A habilitação é revogada quando o GRBL é reiniciado, é reconectado, inicia um Home XY, ou a referência XY é invalidada.

## Testar na prática

1. **Sem potência no cabeçote**, abra o SmartV42. A barra de Z deve indicar que está bloqueado em conexão real.
2. Selecione GRBL SIMULADO. Use Z1 e Teste 't': a interface deverá relatar simulação sem energizar relés físicos.
3. Volte à conexão física. O programa deve voltar a Teste físico XY, ainda sem inicializar GPIOs Z.
4. Faça Home XY pelas chaves (não usa $H; recuo 2 mm em cada eixo). O modo Z continuará bloqueado.
5. Após validar o Home e a proteção elétrica de emergência, use **Habilitar Z físico**. Nesse momento o programa pode configurar saídas GPIO. Faça comissionamento com circuitos de potência isolados inicialmente.
6. Reinicie GRBL/USB e confirme que o programa desabilita Z novamente.

A autorização pode ser consultada em GET /api/status, campo modo_z.

## Limites de segurança

Software no Linux e troca de mensagens USB **não são parada de segurança**. Com módulos de relé ativos em nível baixo, o estado de alta impedância após fechar GPIO requer resistores físicos adequados de polarização. Use corte elétrico independente dos atuadores, fonte dimensionada e botão de emergência de verdade. Os testes de regressão simulam relés e não substituem ensaio com multímetro, verificações das polaridades reais ou teste em bancada sob supervisão.
