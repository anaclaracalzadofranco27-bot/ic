# Smart Matrix Studio v42

Software de controle da **Smart 3D Matrix**, desenvolvido para representar imagens em relevo em uma malha tátil de **100 × 100 pontos**.

O SmartV42 reúne processamento de imagens, geração e importação de G-code, movimentação dos eixos X/Y pelo GRBL, acionamento dos oito níveis Z pela Raspberry Pi e uma interface web de operação.

> **Comece pela simulação.** A execução com motores e solenoides reais ainda exige validação elétrica e mecânica na bancada. Os testes automáticos do GitHub não substituem esses ensaios.

## Neste README

1. [Como a máquina funciona](#1-como-a-máquina-funciona)
2. [Instalar e abrir a versão final](#2-instalar-e-abrir-a-versão-final)
3. [Usar o SmartV42, passo a passo](#3-usar-o-smartv42-passo-a-passo)
4. [Entender os controles da interface](#4-entender-os-controles-da-interface)
5. [Importar G-code sem confundir XY e Z](#5-importar-g-code-sem-confundir-xy-e-z)
6. [Onde fica cada parte do código](#6-onde-fica-cada-parte-do-código)
7. [Problemas comuns: como diagnosticar e corrigir](#7-problemas-comuns-como-diagnosticar-e-corrigir)
8. [Editar, reconstruir e testar](#8-editar-reconstruir-e-testar)
9. [Segurança e documentação técnica](#9-segurança-e-documentação-técnica)

---

## 1. Como a máquina funciona

A Raspberry Pi executa o programa `smartv42.py` e coordena três sistemas:

| Sistema | Hardware | Função |
|---|---|---|
| **XY** | Controladora CNC com GRBL, Arduino Mega e motores de passo | Posicionar o cabeçote sobre cada ponto |
| **Z1–Z8** | GPIOs da Raspberry Pi e módulos de relés/atuadores | Pressionar ou elevar o ponto no nível selecionado |
| **Reset mecânico** | Arduino de reset separado, quando instalado | Reposição mecânica da matriz |

O fluxo é:

```text
Imagem ou padrão ──> Matriz 100 × 100 ─┐
                                       ├─> Preparar job ─> Executor único
G-code importado ──────────────────────┘                       │
                                               ┌───────────────┴───────────────┐
                                               ▼                               ▼
                                         GRBL: motores XY              GPIO: níveis Z1–Z8
```

**Atenção:** Z1–Z8 são **comandos discretos da aplicação**, não um eixo Z cartesiano do GRBL. Não conecte o cabeçote Z à lógica de `G1 Z...`.

### Dimensões e coordenadas

| Característica | Valor |
|---|---|
| Pontos físicos | 100 × 100 = 10.000 |
| Índices lógicos | 0 a 99 em cada eixo |
| Espaçamento nominal entre centros | 3 mm |
| Distância entre os centros extremos | 297 mm |
| Faixa nominal de movimento informada para a bancada | 0 a 299 mm |
| Área de plotagem padrão | Matriz inteira; sem compressão |

Os **299 mm não significam 300 pontos**. A área de centros físicos é de aproximadamente 297 × 297 mm. Uma janela de plotagem opcional apenas **seleciona pontos**; não altera o passo de 3 mm.

Os quatro cantos `A/B/C/D` definem a transformação entre ponto da matriz e coordenada física no **G54**. Na calibração padrão, A = (0, 0), B = (297, 0), C = (297, −297) e D = (0, −297). Essas posições precisam corresponder à **bancada real**.

---

## 2. Instalar e abrir a versão final

### Requisitos

- Raspberry Pi com Python 3.11 ou versão compatível;
- Git, Python e bibliotecas `numpy` e `Pillow`;
- `pyserial` para conversar com a CNC real;
- `gpiozero` e suporte GPIO compatível com a Raspberry Pi para comandar os relés reais;
- CNC/GRBL e sensores X/Y configurados para os testes físicos.

`psutil`, `scipy` e `onnxruntime` são opcionais para recursos adicionais. O modelo `deeplabv3plus_int8.onnx` não está incluído no repositório.

### Instalação sugerida

No terminal da Raspberry Pi:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv

git clone https://github.com/anaclaracalzadofranco27-bot/ic.git
cd ic

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install numpy Pillow pyserial psutil

# Para a Raspberry Pi com cabeçote físico, após preparar o suporte GPIO:
# python -m pip install gpiozero
```

O driver de GPIO adequado depende da versão da Raspberry Pi e do sistema operacional. Instale e valide esse suporte **antes** de energizar os atuadores; a instalação da biblioteca, sozinha, não comprova que os relés estão seguros.

### Primeiro teste: somente simulação

```bash
python3 tools/build_smartv42.py --check
python3 -m unittest discover -s tests -v
python3 smartv42.py --simulado
```

Abra no navegador o endereço informado pelo terminal, semelhante a:

```text
http://127.0.0.1:PORTA_INFORMADA
```

A porta HTTP é escolhida automaticamente por padrão. Para usar uma porta fixa:

```bash
python3 smartv42.py --simulado --http-port 8000
```

Nesse caso, abra `http://127.0.0.1:8000` **na mesma máquina**. O servidor usa `127.0.0.1`: a interface não fica automaticamente disponível em outro computador da rede.

**Na simulação, não é necessário conectar a CNC.** Os movimentos XY e os níveis Z são virtuais.

### Iniciar com a CNC real

**Somente após verificar a proteção elétrica, os sensores e a direção dos motores:**

```bash
python3 smartv42.py --porta /dev/ttyACM0
```

A porta padrão é `/dev/ttyACM0`, mas pode ser outra, como `/dev/ttyUSB0`. Confira em `ls /dev/ttyACM* /dev/ttyUSB*`, se os dispositivos existirem, e selecione a porta correta também na interface.

Para configurar um Arduino de reset físico separado, o programa aceita a variável de ambiente `SMART_MATRIX_PORTA_RESET`; por padrão, o reset começa simulado.

---

## 3. Usar o SmartV42, passo a passo

### Etapa A — Aprender no modo simulado

1. Inicie com `python3 smartv42.py --simulado`.
2. No painel direito, selecione um padrão como **Flor**, **Domo** ou **Rampa X**; ou use **Carregar Imagem IA (ONNX)**.
3. Escolha a representação de profundidade: **Distância**, **Luminância** ou **Binário**. O programa usa processamento alternativo quando o modelo ONNX opcional não está disponível.
4. Confira a matriz 2D/3D e a prévia do G-code.
5. Na seção **Fonte da próxima plotagem**, selecione **Matriz 100 × 100** e clique em **Iniciar**.
6. Acompanhe o progresso. Teste **Hold**, **Resume** e **Abortar**.

O objetivo dessa etapa é entender a interface e o sequenciamento sem movimentar a bancada.

### Etapa B — Preparar a CNC real, com Z desabilitado

1. **Desenergize os atuadores Z** e verifique a parada elétrica de emergência independente.
2. Conecte a CNC/GRBL à Raspberry Pi pela USB; escolha a porta correta no painel **Portas USB**.
3. Abra **Terminal UGS** e envie `?`. O estado normal de repouso deve indicar `Idle`.
4. Com os motores sem potência, confirme o reconhecimento de cada sensor: pressionar X deve indicar `Pn:X`; pressionar Y, `Pn:Y`. Solte as chaves depois.
5. Confira as configurações usando `$$`. A rotina de Home XY por software exige **`$22=0` e `$21=0`**, e verifica também a condição de `$20`.
6. Verifique a direção física real de X e Y antes de permitir uma busca automática.

**Aviso de segurança:** `$21=0` desabilita os limites rígidos do GRBL. Quando essa configuração é usada, as chaves X/Y **não protegem automaticamente contra ultrapassagem de curso**. O Home por USB/Linux não é uma função de segurança. **Não execute com motores energizados sem proteção mecânica e parada elétrica independente.** O programa não altera esses parâmetros automaticamente.

### Etapa C — Fazer o Home XY pelas chaves

1. Clique em **Home XY (chaves)**.
2. Selecione os sentidos **X+/X−** e **Y+/Y−** conforme a movimentação física comprovada.
3. Configure um **curso máximo** dentro da região realmente livre da máquina. Comece com curso e velocidade reduzidos.
4. Escolha o passo da busca (**0,1 a 0,5 mm**) e o avanço (**20 a 150 mm/min**).
5. Informe as coordenadas **G54 X/Y da posição final após o recuo**.
6. Depois de confirmar fisicamente os intertravamentos, marque a confirmação na interface e clique em **Iniciar Home X e Y — recuo 2 mm**.
7. Aguarde a indicação **Home XY concluído**.

O programa procura primeiro a chave **X** e depois a **Y**, observa `Pn:` e comanda um **recuo de 2 mm** após detectar cada sensor. Não utiliza `$H` nem movimenta Z.

**Importante:** a posição do cabeçote após recuar 2 mm **não é automaticamente o ponto A da matriz**. Só use G54 = (0, 0) se essa posição realmente coincidir com A; caso contrário, informe a posição medida. A repetibilidade do disparo e a precisão dos recuos precisam ser testadas mecanicamente.

Se o Home falhar, interrompa a operação e consulte o motivo no painel. Não substitua o Home por zeramento manual com `G92`.

### Etapa D — Testar XY sem movimentar Z

1. Mantenha o modo **Somente XY (bloquear Z)**.
2. Verifique que o Home está válido e que as chaves foram liberadas.
3. Faça deslocamentos pequenos com **Jog Manual**, observando o sentido efetivo.
4. Compare dois centros adjacentes conhecidos: devem estar separados por aproximadamente **3 mm**.
5. Confira pontos e cantos apenas dentro de percursos livres já verificados.

Esse modo é destinado ao comissionamento dos motores; trabalhos que contenham acionamentos Z ficam bloqueados enquanto Z estiver desabilitado.

### Etapa E — Habilitar Z e realizar a primeira plotagem física

**Execute esta etapa somente após validar os sistemas elétrico, mecânico e XY, com alimentação e proteção adequadas.**

1. Verifique a ordem dos GPIOs, a polaridade dos relés e o desligamento seguro em repouso.
2. Confirme **Home XY concluído**, GRBL em `Idle` e sensores liberados.
3. No painel **Cabeçote Z**, clique em **Habilitar Z físico** e confirme a ação.
4. Ajuste a quantidade de pulsos e os tempos de ligação/desligamento.
5. Faça primeiro um **teste individual controlado**, sem carga mecânica perigosa.
6. Escolha um padrão pequeno previamente conferido, selecione sua fonte e clique em **Iniciar**.
7. Observe o progresso; use **Hold** para pausar e **Abortar** se houver comportamento inesperado.

**Ao reconectar a USB, reiniciar o GRBL ou iniciar outro Home XY, o SmartV42 volta a bloquear Z.** É necessário refazer as verificações e habilitá-lo novamente.

### Etapa F — Trabalhar com uma imagem ou G-code importado

**Para uma imagem:** clique em **Carregar Imagem IA (ONNX)**, selecione o modo de profundidade, confira o resultado, escolha **Matriz 100 × 100** como fonte e inicie somente após validar o posicionamento.

**Para um G-code:** clique em **Importar G-code X/Y + Z**, selecione um arquivo `.gcode`, `.nc`, `.tap` ou `.txt`, revise a prévia, escolha **G-code importado** e inicie.

- **Z do arquivo desmarcado:** pulsos/tempos Z vêm da configuração do painel registrada no início do trabalho.
- **Z do arquivo marcado:** valores Z explicitamente presentes no arquivo prevalecem; os ausentes usam a configuração registrada.
- O arquivo importado **não é apagado** ao alterar uma imagem ou gerar padrão. Para apagá-lo, use **Remover G-code**.
- O arquivo importado fica **somente na memória do servidor**; após reiniciar o SmartV42, importe-o novamente.
- A fonte, o programa e os parâmetros são copiados/congelados ao iniciar o job. Se a interface estiver desatualizada, o servidor poderá recusar a execução até atualizar o estado.

---

## 4. Entender os controles da interface

| Painel ou controle | O que faz | Quando usar |
|---|---|---|
| **Portas USB → Conectar** | Seleciona o GRBL e o Arduino de reset | Ao iniciar ou reconectar dispositivos |
| **Visor WPos / MPos** | Mostra posição de trabalho / máquina | Antes e durante movimentos |
| **Terminal UGS** | Consulta estado, alarmes e parâmetros do GRBL | Diagnóstico e configuração |
| **Home XY (chaves)** | Busca X/Y e recua 2 mm em cada eixo | Antes de movimentação automática real |
| **Jog Manual** | Move X/Y em pequenos incrementos | Verificar sentidos e calibração |
| **Somente XY (bloquear Z)** | Proíbe pulsos nos GPIOs Z | Testes de movimento |
| **Habilitar Z físico** | Autoriza o cabeçote real com validações | Somente após comissionamento e Home |
| **Padrões / Carregar Imagem** | Gera a matriz de relevo | Produzir um trabalho |
| **Importar G-code** | Lê e valida instruções híbridas | Utilizar trajetória externa |
| **Fonte da próxima plotagem** | Escolhe matriz ou arquivo importado | Antes de pressionar Iniciar |
| **Iniciar / Hold / Resume / Abortar** | Controla a execução | Durante jobs |

O botão **Iniciar** usa a fonte indicada em **Fonte da próxima plotagem**; não presume que o último arquivo visualizado seja automaticamente a fonte ativa.

---

## 5. Importar G-code sem confundir XY e Z

O SmartV42 lê um **subconjunto próprio de G-code híbrido**, não qualquer programa CNC.

Exemplo didático com uma posição central válida na calibração padrão:

```gcode
G21
G90
G54
G1 X3 Y-297 F120
Z4
G1 X6 Y-297 F120
M42 P1 ; Z_LEVEL=2 GPIO=6 PULSOS=2 ON_MS=10 OFF_MS=10
```

- `G1 X... Y... F...`: movimentação XY pelo GRBL.
- `Z4`: solicita o nível 4 do cabeçote GPIO, **não** o movimento cartesiano Z.
- `M42 P1 ; Z_LEVEL=2 ...`: instrução interpretada pelo **SmartV42**, nunca encaminhada diretamente ao GRBL.
- Um acionamento Z precisa ser precedido por uma posição XY válida, próxima do **centro de um ponto real** (tolerância de validação do código: 0,35 mm).
- O arquivo é verificado antes de movimentar; comandos não suportados, coordenadas fora dos cantos e GPIOs incompatíveis são recusados.

**Não envie esse arquivo diretamente ao Universal Gcode Sender ou a outro transmissor genérico.** Use o importador do SmartV42. Não substitua `Z4` por `G1 Z4`.

---

## 6. Onde fica cada parte do código

O código editável está em `smartv42_src/`. As **dez partes** são fragmentos `.pyfrag` concatenados em uma ordem fixa para gerar `smartv42.py`.

| Parte | Arquivo | Responsabilidade — e onde corrigir |
|---|---|---|
| **01 — Config** | `01_config/config.pyfrag` | Dimensões, cantos A/B/C/D, parâmetros, portas e mapeamento de coordenadas |
| **02 — Telemetria** | `02_telemetry/logging.pyfrag` | Logs, mensagens de erro, histórico e informações de operação |
| **03 — Atuadores Z** | `03_actuators/gpio_z.pyfrag` | Relés, pulsos Z1–Z8, inicialização e bloqueios dos GPIOs |
| **04 — GRBL XY** | `04_grbl/controller.pyfrag` | Serial da CNC, jog, Home XY, leitura de chaves e alarmes |
| **05 — Reset** | `05_mechanics/reset.pyfrag` | Comunicação com Arduino de reset e sequência mecânica |
| **06 — Imagem** | `06_vision/pipeline.pyfrag` | Segmentação, processamento e profundidade da imagem |
| **07 — Trajetória** | `07_trajectory/matrix_job.pyfrag` | Pontos da matriz, ordem de visita e compilação da trajetória |
| **08 — G-code** | `08_gcode/hybrid_runner.pyfrag` | Parser, validação, snapshot de parâmetros e executor único |
| **09 — Interface** | `09_web/interface.pyfrag` | HTML, CSS, botões, visualizações e JavaScript |
| **10 — Servidor** | `10_server/application.pyfrag` | API HTTP, importações, seleção de fonte, jobs e inicialização |

**Como as partes se relacionam:** o servidor (10) recebe ações da interface (09); o repositório de projeto separa matriz e arquivo importado; o preparador cria um plano imutável; o executor (08) envia XY ao controlador GRBL (04) e Z aos atuadores (03). Imagens são tratadas em (06), e a trajetória da matriz nasce em (07).

**Arquitetura atual:** `RepositorioProjetoStudio`, `PlanoJobStudio`, `PreparadorJobStudio`, `ConfiguracaoExecucao` e `MotorExecucaoGCode` organizam preparação e execução. Algumas variáveis globais permanecem como adaptadores de compatibilidade.

**Regra de manutenção:** edite a parte responsável pela função desejada. Não faça correções permanentes diretamente em `smartv42.py`, porque ele é gerado automaticamente.

---

## 7. Problemas comuns: como diagnosticar e corrigir

| Sintoma ou erro | O que verificar / como corrigir | Arquivo responsável |
|---|---|---|
| `ModuleNotFoundError: numpy` ou `PIL` | Ative o ambiente virtual e instale `numpy Pillow` | Instalação |
| A CNC não conecta ou dá `Permission denied` | Confira porta USB, cabo, acesso à serial e se outro programa mantém a porta aberta | `04_grbl` / `01_config` |
| O servidor inicia, mas a interface não abre de outro computador | Está vinculado a `127.0.0.1`; abra no próprio host ou configure um acesso remoto apropriado separadamente | `10_server` |
| GRBL não responde a `?` | Confira porta correta, baud `115200`, alimentação e firmware | `04_grbl` |
| Home XY anda pouco no X e acusa ausência de sensores/status | A busca move até 0,5 mm por vez; a nova rotina tolera duas leituras `?` perdidas. Se persistir, teste USB/alimentação, `Pn:X` e `Pn:Y` sem potência nos motores | `04_grbl` / `docs/HOME_XY_CHAVES.md` |
| Botão Abortar responde lentamente | Abortar envia `!` e `Ctrl+X` pela USB sem aguardar status; se não parar, use a **parada elétrica física**. O Home deve ser refeito | `04_grbl` / `10_server` |
| `Pn:X` / `Pn:Y` não aparece ao pressionar a chave | Verifique o conector, estado elétrico, polaridade e relatório de status **com motores desenergizados** | Ligação / `04_grbl` |
| Home XY não inicia com aviso de `$21`, `$22` ou `$20` | Verifique `$$` e a configuração exigida pelo Home; não desative limites sem parada elétrica independente | `04_grbl` |
| O Home procura no sentido errado | Interrompa; confira físicamente o sentido X+/X− ou Y+/Y− antes de tentar novamente | `04_grbl` / interface |
| A chave é detectada, mas não libera após 2 mm | Investigue mecânica do sensor, posição, folga e sentido de recuo; não force movimentos | `04_grbl` |
| Job bloqueado por ausência de Home | Execute Home XY pelas duas chaves; um reset/reconexão invalida a referência | `04_grbl` / `08_gcode` |
| Cabeçote Z não responde | Confira se está em **Teste físico XY** e se o modo físico completo foi habilitado após Home | `03_actuators` |
| GPIO não inicializa | Verifique `gpiozero`, suporte GPIO, pinagem BCM, permissões e polaridade | `03_actuators` / `01_config` |
| Relé aciona quando não deveria | **Corte a alimentação física**; verifique módulo ativo em nível baixo, polarização e circuito independente de desenergização | Elétrica / `03_actuators` |
| A cabeça pressiona ponto errado | Confira Home, G54 real após recuo, quatro cantos, 3 mm entre centros e orientação visual | `01_config` / `07_trajectory` |
| Calibração antiga recusada | Meça novamente A/B/C/D; a versão atual rejeita perfis incompatíveis com ~297 mm | `01_config` |
| Z fora da janela ou longe de centro físico | Corrija XY do arquivo para o centro de um dos 10.000 pontos e revise o recorte | `01_config` / `08_gcode` |
| `G1 Z...` ou comando desconhecido é rejeitado | Utilize o formato híbrido `Z1`–`Z8` ou `M42 P1 ; Z_LEVEL=N` | `08_gcode` |
| GPIO mencionado no arquivo não coincide com Z | Corrija o mapa/arquivo e importe novamente; o programa bloqueia acionamento incompatível | `03_actuators` / `08_gcode` |
| A plotagem usa matriz em vez do arquivo | Confira **Fonte da próxima plotagem** e selecione explicitamente **G-code importado** | `09_web` / `10_server` |
| O arquivo importado desapareceu após reiniciar | O G-code externo fica em **memória temporária**; importe-o novamente | `10_server` |
| Job recusado porque o projeto mudou | Atualize a página/estado, confira fonte e parâmetros e inicie novamente | `10_server` / `08_gcode` |
| Imagem ou profundidade não corresponde ao esperado | Revise modo Distância/Luminância/Binário e a imagem original | `06_vision` |
| Reset mecânico não responde ou não confirma fim | Confira porta do Arduino de reset, firmware e resposta de conclusão | `05_mechanics` |
| `smartv42.py` está desatualizado | Execute o gerador e depois o modo `--check` | `tools/build_smartv42.py` |
| Testes falham após uma alteração | Consulte qual teste e arquivo falharam; corrija o fragmento correspondente antes de operar | `tests/` + módulo afetado |

**Ordem prática de diagnóstico:** leia o erro na interface → consulte o Terminal UGS e os logs → identifique a linha/comando que falhou → encontre o módulo na tabela acima → corrija e execute os testes. **Não contorne bloqueios de segurança só para fazer o movimento acontecer.**

---

## 8. Editar, reconstruir e testar

O repositório mantém **dez fragmentos** e gera um único `smartv42.py`.

### Depois de alterar qualquer `.pyfrag`

```bash
python3 tools/build_smartv42.py
python3 -m py_compile smartv42.py
python3 -m unittest discover -s tests -v
python3 tools/build_smartv42.py --check
```

- `build_smartv42.py`: junta os dez fragmentos, na ordem correta.
- `--check`: verifica se o arquivo final corresponde aos fragmentos, **sem alterá-lo**.
- `unittest`: executa os testes automatizados sem CNC ou relés reais.
- **GitHub Actions**: ao enviar alterações de código/testes à `main`, reconstrói, valida e, se necessário, publica o `smartv42.py` gerado. Mudanças apenas no README não acionam esse workflow.
- Arquivos `.pyfrag` **não são scripts executáveis isolados**.

### Estrutura essencial

```text
ic/
├── README.md
├── smartv42.py                  # Programa final gerado
├── smartv42_src/                # Dez partes editáveis
├── tools/build_smartv42.py     # Montador
├── tests/                       # Testes automatizados
├── docs/                        # Explicações técnicas detalhadas
└── .github/workflows/           # Reconstrução e testes no GitHub
```

---

## 9. Segurança e documentação técnica

A máquina trabalha com motores, relés e atuadores que podem causar movimentos inesperados. O código foi testado em simulação, mas **não é uma função de segurança industrial**. Antes de usar o hardware, providencie e valide parada elétrica independente, proteções mecânicas, limites de percurso, alimentação, polaridade dos relés e funcionamento das chaves.

- [Home XY por chaves: configuração, limites e recuo de 2 mm](docs/HOME_XY_CHAVES.md)
- [Geometria e coordenadas: 100 × 100, 3 mm, 297 mm úteis](docs/MODELO_COORDENADAS_297.md)
- [Separação entre simulação, XY físico e relés Z](docs/ISOLAMENTO_Z_SIMULACAO.md)
- [Matriz, G-code importado e parâmetros de cada execução](docs/ESTADOS_ISOLADOS_MATRIZ_GCODE.md)
- [Arquitetura: repositório, plano imutável e executor único](docs/ARQUITETURA_SMARTV42.md)
- [Histórico de correções técnicas A–H](docs/CORRECOES_A_H.md)

**Resumo:** use `smartv42.py` para executar, `smartv42_src/` para modificar, `tests/` para verificar e este README para operar. Comece em **simulação**, valide **Home XY**, teste **XY com Z bloqueado** e só depois habilite o **cabeçote real**, com proteção elétrica independente.
