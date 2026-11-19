# Smart Matrix Studio v42

Software de controle da **Smart 3D Matrix**, uma matriz tátil de 100 × 100 pontos para representação de imagens em relevo. O projeto reúne uma interface web inspirada no UGS, processamento de imagens, movimentação dos eixos **X/Y pelo GRBL** e acionamento discreto dos níveis **Z1–Z8 pelos GPIOs da Raspberry Pi**.

> **Versão atual:** `smartv42.py`. O código-fonte editável fica em `smartv42_src/`. O executável é gerado automaticamente; não faça alterações permanentes diretamente nele.
>
> **Estado de validação:** a geração, a compilação Python e os testes automatizados sem CNC passaram no GitHub Actions. Isso **não equivale à validação do hardware real**.

## 1. Visão geral do funcionamento

O sistema possui dois caminhos de entrada:

1. **Imagem ou padrão gerado pela interface:** a imagem é transformada em uma matriz de níveis de relevo, convertida em pontos e executada pelo controlador da matriz.
2. **Arquivo G-code híbrido importado:** um interpretador valida o arquivo antes da execução, envia os movimentos X/Y ao GRBL e converte as instruções Z em pulsos nos GPIOs da Raspberry Pi.

Em ambos os casos, **Z não é uma coordenada cartesiana controlada pelo GRBL**. É uma seleção de um dos oito atuadores/níveis, com parâmetros de pulso.

```text
     IMAGEM / PADRÃO                         ARQUIVO .gcode / .nc / .tap / .txt
            |                                            |
            v                                            v
  06_vision/pipeline.pyfrag                  08_gcode/hybrid_runner.pyfrag
            |                                            |
            v                                            |
  Matriz 100 x 100 (níveis 0 a 8)                       |
            |                                            |
            v                                            |
  07_trajectory/matrix_job.pyfrag                        |
            |                                            |
            +--------------------+-----------------------+
                                 |
                    Comandos e sequência de execução
                                 |
                   +-------------+-------------+
                   |                           |
                   v                           v
         04_grbl/controller.pyfrag   03_actuators/gpio_z.pyfrag
                   |                           |
                   v                           v
            GRBL / X e Y                Raspberry Pi / Z1–Z8
                   |
                   +--------- Interface e telemetria ----------+
                             09_web + 10_server + 02_telemetry
```

## 2. Estrutura completa do repositório

```text
ic/
├── README.md
├── smartv42.py                           # Executável completo, gerado pelo bot
├── smartv42_src/                          # Fonte editável, dividido em 10 áreas
│   ├── 01_config/
│   │   └── config.pyfrag
│   ├── 02_telemetry/
│   │   └── logging.pyfrag
│   ├── 03_actuators/
│   │   └── gpio_z.pyfrag
│   ├── 04_grbl/
│   │   └── controller.pyfrag
│   ├── 05_mechanics/
│   │   └── reset.pyfrag
│   ├── 06_vision/
│   │   └── pipeline.pyfrag
│   ├── 07_trajectory/
│   │   └── matrix_job.pyfrag
│   ├── 08_gcode/
│   │   └── hybrid_runner.pyfrag
│   ├── 09_web/
│   │   └── interface.pyfrag
│   └── 10_server/
│       └── application.pyfrag
├── tools/
│   └── build_smartv42.py                 # Montagem do arquivo completo
├── tests/
│   └── test_smartv42.py                  # Testes automatizados sem hardware
└── .github/
    └── workflows/
        └── build-smartv42.yml            # Bot GitHub Actions
```

### Importante: por que os arquivos terminam em `.pyfrag`?

Os dez arquivos menores são **fragmentos de um mesmo programa Python**, e não módulos Python independentes. O gerador os concatena **na ordem fixa** acima para formar `smartv42.py`. Assim, eles compartilham o mesmo escopo global, inclusive constantes, estados, classes e funções do projeto original.

Consequências práticas:

- **Execute `smartv42.py`, não um `.pyfrag` isolado.**
- Faça as correções no fragmento correspondente à função desejada.
- Não altere nomes/caminhos de fragmentos ou a ordem de montagem sem atualizar `tools/build_smartv42.py`.
- Uma função pode ser definida em uma pasta e utilizada pelas seguintes; a divisão foi feita por **responsabilidade** para facilitar manutenção, preservando a arquitetura atual.
- O bot reconstrói o executável a cada alteração enviada para a `main` que atenda aos gatilhos do workflow. Alterações apenas no computador não acionam o bot.

## 3. O que faz cada pasta, em detalhes

### `smartv42_src/01_config/` — Configurações, coordenadas e calibração

**Arquivo:** `config.pyfrag`

Centraliza os imports, as constantes e os estados globais utilizados nas outras partes. Define, entre outros:

- **Comunicação serial:** `PORTA_GRBL` (padrão `/dev/ttyACM0`), `PORTA_ARDUINO_RESET` (padrão `SIMULADO`), baud rate, timeouts e limites de tentativas.
- **Dimensões e grade:** matriz de `100 × 100`, espaçamento nominal de `3 mm` e limites de X/Y.
- **Coordenadas físicas:** pontos `PONTO_A`, `PONTO_B`, `PONTO_C`, `PONTO_D`, inversão visual dos jogs, origem à direita/esquerda e sentido dos eixos.
- **Janela de plotagem configurada na v42:** `USAR_JANELA_PLOTAGEM = True`, X de **246 a 270 mm** e Y de **−50 a −29 mm**. Esses valores descrevem a configuração atual do software e precisam corresponder à calibração **real** antes de movimentar a bancada.
- **Parâmetros Z:** `PINOS_GPIO_Z = [5, 6, 16, 27, 23, 17, 24, 25]`, `TEMPO_ON_Z_S`, `TEMPO_OFF_Z_S`, `PULSOS_POR_NIVEL`, polaridade do relé e pausas.
- **Estado compartilhado:** flags de simulação, dados da interface, interpretações de status do GRBL e mensagens de erro/alarme.

**Principais funções:** `salvar_calibracao_matriz()`, `carregar_calibracao_matriz()`, `indice_para_mm_fisico()`, `mm_fisico_para_indice()`, `obter_estado_coordenadas()`.

**Edite esta pasta quando:** precisar mudar portas, dimensões, orientação da matriz, posições dos quatro cantos, janela de plotagem ou parâmetros padrão de operação.

**Atenção:** `calibracao_matriz.json`, quando existente no diretório de execução, pode sobrescrever os valores de cantos definidos no código. Uma calibração antiga com Y positivo pode entrar em conflito com a configuração atual.

### `smartv42_src/02_telemetry/` — Logs, histórico e telemetria

**Arquivo:** `logging.pyfrag`

Registra eventos para diagnóstico e acompanhamento da máquina. Cuida de:

- Mensagens de funcionamento, avisos e erros do programa.
- Histórico de comandos e respostas do terminal estilo UGS.
- Leitura das últimas linhas de logs e produção de snapshots.
- Rotinas periódicas de exportação de logs e coleta de telemetria da Raspberry Pi.

**Principais funções:** `registrar_log()`, `registrar_terminal()`, `extrair_logs_recentes()`, `exportar_snapshot_de_logs()`, `monitorar_telemetria()`.

**Arquivos que podem aparecer durante a execução:** `log_operacao.txt`, `log_telemetria_rasp.txt` e `log_snapshot_atual.txt`.

**Edite esta pasta quando:** precisar melhorar mensagens de erro, diagnóstico de falhas, registros do terminal ou monitoramento.

### `smartv42_src/03_actuators/` — Cabeçote Z, GPIOs e pulsos

**Arquivo:** `gpio_z.pyfrag`

É a parte que conversa diretamente com os **oito relés/saídas GPIO** responsáveis pelos níveis de relevo. O sistema associa **Z1, Z2, ..., Z8** aos GPIOs BCM **5, 6, 16, 27, 23, 17, 24 e 25**, respectivamente.

Responsabilidades:

- Inicializar as saídas físicas utilizando `gpiozero.OutputDevice` quando disponível.
- Determinar o número de pulsos de cada nível, com configuração fixa ou individual.
- Aplicar tempos ligados e desligados dos pulsos.
- Acionar um nível específico por `acionar_eixo_z(nivel_z, ...)`.
- Testar relés individualmente, executar sequência Z1–Z8 e permitir calibração do cabeçote.
- Interromper uma sequência por meio do evento de cancelamento.
- Retornar falha quando os GPIOs esperados não estão disponíveis em **modo físico**, em vez de informar sucesso fictício.

**Principais funções:** `inicializar_gpio_z()`, `configurar_parametros_pulsos_z()`, `obter_pulsos_para_nivel()`, `acionar_eixo_z()`, `acionar_sequencia_cabecote()`, `testar_reles_um_a_um()`.

**Edite esta pasta quando:** quiser corrigir problemas de relés, ordem dos GPIOs, duração ou quantidade de pulsos e atuação Z. **Não** implemente aqui movimentos G-code de X/Y.

### `smartv42_src/04_grbl/` — Controlador dos eixos X e Y

**Arquivo:** `controller.pyfrag`

Concentra a comunicação com o firmware **GRBL 1.1** da controladora CNC e os comandos relacionados a movimentação.

Responsabilidades:

- Abrir, manter e fechar a conexão serial com GRBL; também possui porta simulada.
- Enviar comandos e interpretar respostas `ok`, `error` e `ALARM`.
- Ler e explicar status, estados de execução, coordenadas e sinais de limite.
- Executar movimentos X/Y e aguardar a conclusão antes da próxima etapa.
- Disponibilizar jog, homing, zero de trabalho, G28/G30, WCS, configurações e comandos de console compatíveis com o projeto.
- Validar limites e cantos antes dos deslocamentos.
- Interpretar comandos de terminal especiais, inclusive `Z1` a `Z8`, que são desviados para o cabeçote GPIO.

**Elementos principais:** `ControladorGRBL`, `PortaSimulada`, `fora_dos_limites()`, `validar_cantos()`, `executar_comando_terminal_integrado()`.

**Edite esta pasta quando:** ocorrerem problemas de conexão GRBL, travamento em `Idle`, alarmes, jog, home, sentindo de movimentação ou comandos manuais X/Y.

**Separação essencial:** o GRBL não deve receber comandos `M42` do formato híbrido nem comandos `G1 Z...` gerados para um eixo Z cartesiano.

### `smartv42_src/05_mechanics/` — Reset mecânico superior

**Arquivo:** `reset.pyfrag`

Controla um **Arduino Mega 2560 separado**, utilizado no mecanismo de reset superior da matriz: atuadores lineares e movimento do eixo **X'** do sistema de reposição.

Responsabilidades:

- Estabelecer comunicação serial com a placa de reset.
- Enviar comandos individuais para subir/descer atuadores e deslocar/retornar X'.
- Executar o ciclo completo de reset de acordo com o firmware do Arduino.
- Manter estados de simulação e mensagens de execução.

**Classe principal:** `ControladorResetArduino`.

**Comandos previstos no código:** `D`, `D1`, `D2`, `M1_01`, `M1_00`, `M2_01`, `M2_00` e comandos legados de reset. A disponibilidade efetiva depende do firmware instalado no Arduino.

**Edite esta pasta quando:** o ciclo de reposição mecânica, a serial do Arduino ou as etapas de subir/descer e varrer apresentarem problemas.

### `smartv42_src/06_vision/` — IA, segmentação e profundidade

**Arquivo:** `pipeline.pyfrag`

Converte imagens em um mapa numérico de níveis de relevo. Abrange:

- Carregamento opcional do modelo **DeepLabV3+ INT8** por `onnxruntime` (arquivo configurado: `deeplabv3plus_int8.onnx`).
- Tratamento e preparação de imagens para a matriz.
- Segmentação alternativa quando o modelo ONNX não está disponível, utilizando procedimentos de limiarização.
- Conversão do objeto/imagem em níveis discretos de altura, por modos de **distância**, **luminância** ou **binário**.
- Criação de padrões de teste, como flor, domo, rampa e logo USP.

**Principais funções:** `carregar_sessao_onnx()`, `processar_imagem_para_matriz()`, `calcular_niveis_por_distancia()`, `calcular_niveis_por_luminancia()`, `gerar_padrao_teste()`.

**Edite esta pasta quando:** quiser corrigir a interpretação das imagens, a seleção de objetos, os valores de profundidade ou os desenhos de teste. O modelo ONNX não foi incluído no repositório: caso deseje utilizá-lo, forneça um modelo compatível.

### `smartv42_src/07_trajectory/` — Planejamento e execução da matriz

**Arquivo:** `matrix_job.pyfrag`

Transforma a matriz 100 × 100 em uma sequência física de movimentos e acionamentos. Responsabilidades:

- Identificar apenas os pontos com nível acima de zero.
- Ordenar a visita aos pontos por varredura **serpentina**, considerando a configuração de origem.
- Converter os índices da imagem em coordenadas reais X/Y.
- Compilar uma representação textual da trajetória em G-code híbrido.
- Executar o trabalho diretamente a partir da matriz, coordenando movimentos X/Y, acionamento Z e eventual reset.
- Manter o estado compartilhado de job, progresso, pausa e cancelamento.

**Principais funções:** `montar_pontos()`, `compilar_matriz_para_estudio()`, `executar_job_em_background()`.

**Edite esta pasta quando:** precisar corrigir ordem de plotagem, mapeamento dos pontos, planejamento de trajetória, progresso da execução por imagem ou retorno à origem.

**Importante:** este é o caminho de execução para **imagens e padrões gerados**. Arquivos G-code importados são tratados pela próxima pasta.

### `smartv42_src/08_gcode/` — Leitor e executor G-code híbrido

**Arquivo:** `hybrid_runner.pyfrag`

Implementa a correção que separa os movimentos cartesianos do acionamento físico Z. É usado ao **importar um arquivo G-code** pela interface.

Etapas:

1. **Análise:** `analisar_gcode_hibrido(texto)` lê o conteúdo inteiro e verifica a sintaxe. Comandos desconhecidos são rejeitados antes do início do job.
2. **Classificação:** separa configurações, movimentos X/Y, marcadores e comandos Z discretos.
3. **Movimento:** `executar_bloco_hibrido()` envia X/Y ao controlador GRBL.
4. **Sincronização:** antes de acionar Z, confirma que o deslocamento terminou e que o GRBL está em `Idle`.
5. **Atuação:** para Z1–Z8, chama o mecanismo do arquivo `03_actuators/gpio_z.pyfrag`, sem enviar Z ao GRBL.
6. **Execução:** `executar_job_gcode_em_background()` processa os blocos em uma thread, compartilhando a trava de jobs e verificando pausa/cancelamento.

**Principais funções:** `validar_janela_calibrada()`, `analisar_gcode_hibrido()`, `executar_bloco_hibrido()`, `executar_job_gcode_em_background()`.

**Comandos aceitos:** o subconjunto implementado do formato Smart Matrix (G21, G90, G54, movimentos G0/G1 com X/Y/F, G4 P0, marcadores M3 S1000/M5, `Z1`–`Z8` e `M42 P1 ; Z_LEVEL=N`). **Não é um interpretador CNC genérico.** Em particular, `G1 Z4` e `G91` são rejeitados.

**Edite esta pasta quando:** arquivos G-code não forem reconhecidos, houver erro de sintaxe, problemas de sincronização entre XY e Z ou necessidade de adicionar suporte explícito a um formato seguro.

### `smartv42_src/09_web/` — Interface gráfica

**Arquivo:** `interface.pyfrag`

Contém o HTML/CSS/JavaScript da interface web do Studio, embutido na variável `HTML_CONCEITUAL`, além da classe de servidor HTTP com suporte a threads.

Na interface, o usuário pode:

- Acompanhar os estados da máquina e as coordenadas.
- Executar jog X/Y, acessar comandos de GRBL e o terminal estilo UGS.
- Testar os oito níveis Z e configurar duração/quantidade de pulsos.
- Calibrar a área de trabalho e a origem.
- Gerar padrões, carregar imagens, escolher a representação de profundidade.
- **Importar G-code híbrido** e executar jobs.
- Visualizar o mapa tátil em 2D/3D e uma prévia das instruções.
- Exportar o **G-code completo**, não somente as linhas exibidas na prévia.
- Enviar comandos ao mecanismo de reset.

**Edite esta pasta quando:** quiser mudar botões, layout, aparência, textos, gráficos, eventos JavaScript e formulários. Alterações de lógica física de motores ou relés devem ficar nos respectivos controladores.

### `smartv42_src/10_server/` — Servidor HTTP, API e inicialização

**Arquivo:** `application.pyfrag`

Conecta o frontend ao restante do sistema. Responsabilidades:

- Inicializar as conexões GRBL, GPIO e Arduino de reset.
- Carregar calibração existente e um padrão inicial.
- Publicar a interface em um servidor HTTP local.
- Oferecer os endpoints `GET /api/matrix`, `GET /api/status` e `POST` de ações da interface.
- Receber uploads de imagens e de G-code híbrido.
- Selecionar o executor certo para o botão de iniciar: **matriz sintetizada** ou **G-code importado**.
- Atualizar o estado global, conectar portas, encaminhar comandos de console e reset.
- Encerrar conexões ao finalizar o programa.

**Elementos principais:** `StudioRequestHandler`, `atualizar_matriz_global()`, `main()`.

**Ações relevantes na API:** `carregar_gcode`, `iniciar_job`, `testar_z`, `jog`, `terminal_cmd`, `upload_imagem`, `config_coordenadas`, `config_cabecote`, `reset_mecanico`, `gerar_padrao`.

**Edite esta pasta quando:** um botão chamar a função errada, houver falhas nos endpoints, erros ao importar dados, problemas ao iniciar jobs ou dificuldades na inicialização e no encerramento do servidor.

## 4. Outras pastas e arquivos

### `tools/` — Gerador do programa final

`tools/build_smartv42.py` lê a lista fixa `PARTES` e concatena os dez fragmentos na ordem esperada. Depois, compila o texto gerado para verificar sintaxe e grava `smartv42.py`.

Comandos:

```bash
python3 tools/build_smartv42.py          # Gera/atualiza smartv42.py
python3 tools/build_smartv42.py --check  # Confere se o gerado está sincronizado
python3 -m py_compile smartv42.py       # Verifica sintaxe, sem executar motores
```

A opção `--check` **não** altera o executável: ela falha se detectar divergência.

### `tests/` — Testes automatizados

`tests/test_smartv42.py` usa um controlador GRBL fictício e mocks para testar o interpretador híbrido sem hardware conectado. Os testes cobrem, entre outros:

- Interpretação das instruções `M42` emitidas pelo compilador.
- Rejeição de Z cartesiano e comandos não suportados.
- Garantia de que `M42` **não seja transmitido ao GRBL**.
- Bloqueio de atuação Z fora da janela permitida.
- Detecção de calibração incompatível com Y negativo.

Execute:

```bash
python3 -m unittest discover -s tests -v
```

**Limitação:** os testes não comprovam a eletrônica, a fiação, os sensores nem a cinemática reais.

### `.github/workflows/` — Bot automático do GitHub

`build-smartv42.yml` é um fluxo do **GitHub Actions**. Sempre que houver um push para `main` alterando `smartv42_src/**`, o próprio gerador, testes ou o workflow, ele:

1. Baixa a revisão do repositório.
2. Prepara Python.
3. Reconstrói `smartv42.py`.
4. Verifica a sintaxe.
5. Instala dependências mínimas para testar.
6. Executa a suíte de testes sem hardware.
7. Se tudo passar e o executável tiver mudado, cria um commit com `github-actions[bot]`.

O workflow também permite disparo manual em **GitHub → Actions → Bot Smart Matrix 42**.

O bot precisa de **Actions habilitado** e permissão `contents: write`. O commit automático usa `[skip ci]` para evitar um loop de execuções. **Se os testes falharem, a etapa de publicação não será executada.**

### `smartv42.py` — Programa para executar

É a concatenação gerada dos dez fragmentos, não uma implementação independente. A execução normal usa este arquivo:

```bash
python3 smartv42.py --simulado
```

Não remova este arquivo e não faça correções permanentes nele. Para modificá-lo, edite `smartv42_src/` e reconstrua.

## 5. Instalação e primeira execução

Recomendado: Raspberry Pi com Python 3.11 ou versão compatível com as bibliotecas utilizadas. O controlador real depende da configuração da bancada.

```bash
git clone https://github.com/anaclaracalzadofranco27-bot/ic.git
cd ic

# Instale as dependências básicas de análise e simulação
python3 -m pip install numpy Pillow pyserial psutil

# Somente na Raspberry Pi com GPIO configurado:
# python3 -m pip install gpiozero

# Opcionais para IA e cálculo de distância:
# python3 -m pip install onnxruntime scipy

# Verifique geração e testes antes de operar
python3 tools/build_smartv42.py --check
python3 -m py_compile smartv42.py
python3 -m unittest discover -s tests -v

# Primeira execução, sem acionamento físico
python3 smartv42.py --simulado
```

A interface é servida por padrão em `127.0.0.1` (endereço local da máquina). O navegador pode abrir automaticamente; o endereço exato aparece no log. Em uma Raspberry Pi acessada remotamente, a interface **não ficará automaticamente acessível pela rede**, pois está vinculada ao loopback local.

**Parâmetros CLI disponíveis:**

| Opção | Objetivo |
|---|---|
| `--simulado` | Não acionar a bancada física durante testes |
| `--porta /dev/ttyACM0` | Escolher a porta serial do GRBL |
| `--origem-esquerda` | Selecionar a origem X na esquerda |
| `--sinal-x -1` | Inverter o sinal de X usado pelo programa |
| `--http-port 8000` | Escolher a porta local do servidor HTTP (0 = automática) |

Para uso físico, depois de revisar a calibração, os comandos GPIO e a segurança mecânica, um exemplo é `python3 smartv42.py --porta /dev/ttyACM0`. **Isso não constitui autorização para energizar os atuadores sem comissionamento.**

## 6. Como editar sem perder a atualização automática

Exemplo: corrigir o tempo de pulso de Z.

1. Abra `smartv42_src/03_actuators/gpio_z.pyfrag` no GitHub.
2. Faça a alteração e salve com um commit na branch `main`.
3. Abra a aba **Actions** do repositório.
4. Aguarde o workflow `Bot Smart Matrix 42 - reconstruir codigo` terminar.
5. Confira o resultado dos testes e o commit automático de `smartv42.py`.

Exemplo: mudar um botão da interface. Edite `smartv42_src/09_web/interface.pyfrag` e use a mesma rotina; não altere o HTML embutido no executável gerado.

**Se o bot não reconstruir**, confira: a alteração foi enviada para `main`? O GitHub Actions está habilitado? O workflow concluiu com sucesso? Ele tem permissão de escrita? Há falhas em `tests/`?

## 7. Exemplos de G-code e tratamento correto do Z

Trecho compatível com o interpretador híbrido:

```gcode
G21
G90
G54
G1 X258 Y-39.5 F900
G4 P0
M42 P1 ; Z_LEVEL=4 GPIO=27 PULSOS=4 ON_MS=10 OFF_MS=10
G1 X261 Y-39.5 F900
G4 P0
Z2
M5
G0 X0 Y0
```

Interpretação:

- `G1 X258 Y-39.5 F900`: posição X/Y a ser atingida pelo GRBL.
- `G4 P0`: marcador de sincronização do formato.
- `M42 P1 ; Z_LEVEL=4 ...`: **instrução de aplicação**, não comando para transmitir ao firmware GRBL. O interpretador escolhe o nível Z4 e aciona o GPIO correspondente.
- `Z2`: comando abreviado para o atuador do nível 2.
- `G0 X0 Y0`: movimento XY de retorno; **não aciona Z**.

O leitor valida o arquivo antes do primeiro movimento; as coordenadas e as condições operacionais são verificadas também durante a execução. Z só deve disparar depois de XY terminar e o GRBL estar em `Idle`.

Não envie diretamente o arquivo híbrido exportado a um transmissor G-code convencional esperando que `M42` funcione no GRBL 1.1 padrão. Use o importador do próprio Studio. Nunca substitua os acionamentos por `G1 Z...` sem redesenhar a arquitetura de hardware.

## 8. Diagnóstico rápido: onde corrigir cada tipo de problema

| Problema observado | Primeiro local para investigar |
|---|---|
| Porta serial, limites, Y invertido, calibração antiga | `01_config/config.pyfrag` |
| Logs insuficientes ou mensagens confusas | `02_telemetry/logging.pyfrag` |
| Z não pulsa, relé incorreto ou tempo de pulso errado | `03_actuators/gpio_z.pyfrag` |
| Movimento X/Y não inicia, homing, alarme GRBL, jog | `04_grbl/controller.pyfrag` |
| Atuadores de reset ou eixo X' não respondem | `05_mechanics/reset.pyfrag` |
| Imagem não segmenta ou profundidade incorreta | `06_vision/pipeline.pyfrag` |
| Trajetória serpentina ou ordem dos pontos incorreta | `07_trajectory/matrix_job.pyfrag` |
| G-code importado rejeitado ou Z tratado como coordenada | `08_gcode/hybrid_runner.pyfrag` |
| Erro visual, botão, gráfico, texto ou layout | `09_web/interface.pyfrag` |
| Upload, endpoint, inicialização ou seleção de job | `10_server/application.pyfrag` |
| Arquivo gerado desatualizado | `tools/build_smartv42.py` e workflow |
| Teste automatizado falhando | `tests/test_smartv42.py` e módulo sob teste |

## 9. Segurança e limitações conhecidas

- A aplicação controla equipamentos que podem exercer força e movimentação reais. Use simulação e testes com atuadores desenergizados antes de operar a bancada.
- Confirme o mapeamento BCM e a polaridade dos oito relés, a alimentação, a proteção elétrica, as condições de fim de curso e uma parada de emergência física independente.
- Verifique os quatro pontos de calibração e a **janela X=246..270 mm, Y=−50..−29 mm** antes de mover. Os limites de software não substituem barreiras físicas.
- O formato G-code híbrido é propositalmente restrito. Não use arquivos CNC arbitrários sem adaptação e revisão.
- O modo de reset pode estar simulado quando a porta do Arduino de reset não estiver configurada: confirme a conexão real antes de considerar o reset concluído fisicamente.
- O modelo ONNX de IA é opcional e precisa ser fornecido/configurado separadamente.
- Testes automatizados concluídos com sucesso comprovam apenas os cenários implementados na suíte — **não validam funcionamento, segurança ou desempenho físico**.

## 10. Manutenção e histórico

A divisão atual facilita corrigir trechos específicos, mas os fragmentos ainda compartilham estado global. Uma futura refatoração pode convertê-los em módulos Python importáveis com interfaces explícitas; isso exige testes de regressão mais abrangentes.

Os arquivos históricos `smartv41.py` e `smaartvmatriz-main.zip` foram retirados da **versão atual** do repositório após a geração e validação da Smart Matrix v42. As versões anteriores continuam recuperáveis pelo histórico Git.
