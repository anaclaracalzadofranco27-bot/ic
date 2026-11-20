# Arquitetura interna do SmartV42

## Objetivo desta refatoração

Manter o comportamento já validado do Smart Matrix Studio e tornar explícitas as responsabilidades e interfaces entre edição, preparação, execução e hardware. Esta etapa é **interna**, não uma substituição do firmware, da interface web nem dos comandos de controle da máquina.

O projeto continua com **dez seções `smartv42_src/`** e continua montando o arquivo executável único **`smartv42.py`** a partir das seções. A interface HTML (`09_web/interface.pyfrag`) não foi alterada nesta refatoração.

## Organização preservada

| Seção | Responsabilidade |
|---|---|
| `01_config` | Parâmetros de geometria, estado de configuração e importações compartilhadas |
| `02_telemetry` | Logs e telemetria |
| `03_actuators` | GPIOs, pulsos Z1..Z8 e intertravamentos |
| `04_grbl` | GRBL, movimentos XY, limites e Home pelas chaves |
| `05_mechanics` | Controlador do Arduino de reset |
| `06_vision` | Processamento de imagens |
| `07_trajectory` | Compilação da matriz 100x100 em G-code híbrido |
| `08_gcode` | Parser, validação, configuração de execução e **um único motor de jobs** |
| `09_web` | Interface web já existente |
| `10_server` | Estado do projeto, preparação de plano e endpoints HTTP |

## Componentes e contratos

### `RepositorioProjetoStudio` — estado editável

Local: `10_server/application.pyfrag`.

Tem responsabilidade exclusiva por transações de:
- edição da matriz e atualização atômica de seu preview;
- importação, substituição e remoção explícita de um G-code externo;
- preferência “Z do arquivo”;
- seleção da fonte ativa (**matriz** ou **G-code**);
- revisão de estado exibida pela UI.

A **fonte selecionada e o contador de revisão** agora pertencem ao próprio objeto. Operações usam uma única trava reentrante para evitar intercalar importação, seleção e preparação de job.

O dicionário público de importação e as variáveis antigas `_matriz_atual`, `_gcode_atual` e `_trajetoria_atual` permanecem temporariamente como adaptadores para funções e testes legados, mas a atualização da fonte e a publicação de matrizes são centralizadas no repositório. A eliminação do restante dos globais exigirá outra etapa com revisão de telemetria, terminal e HTTP.

### `PlanoJobStudio` — trabalho sem referências mutáveis

Local: `10_server/application.pyfrag`.

É um `@dataclass(frozen=True)` contendo:
- nome e origem do trabalho;
- opção de parâmetros Z do arquivo;
- blocos de G-code serializados como tuplas imutáveis;
- configuração do job.

`RepositorioProjetoStudio.preparar_plano()` cria o plano sob a mesma trava da revisão da interface. Depois de criado, editar matriz, importar outro G-code ou mudar a fonte não altera os blocos já preparados.

### `ConfiguracaoExecucao` — snapshot imutável

Local: `08_gcode/hybrid_runner.pyfrag`.

É um `@dataclass(frozen=True)` com feed de referência, pulsos por nível Z, ON/OFF/pausa, auto-reset e assinatura da calibração/janela/mapa de GPIO. Os oito números de pulsos são uma **tupla**, não a lista de configuração editável no painel.

A classe admite leitura por chave (`config["feed_rate"]`) como adaptador para os comandos já existentes e aceita converter snapshots legados do tipo dicionário sem manter referências às listas originais.

A geometria e o mapa de GPIO ainda são verificados contra o snapshot antes de movimentar e entre as instruções. Isso é uma checagem de consistência, não uma função elétrica de segurança.

### `PreparadorJobStudio` — seleção, validação e reserva

Local: `10_server/application.pyfrag`.

Trabalha com o repositório e não acessa motores diretamente. Na ação “Iniciar job” ele:
1. verifica **fonte e revisão esperadas**;
2. prepara os blocos e o snapshot;
3. entrega o plano ao motor único;
4. devolve uma resposta clara de aceitação ou rejeição à interface.

O lock do projeto é mantido até a tentativa de reserva do executor, impedindo troca de fonte entre preparação e partida. O mecanismo não usa um segundo executor exclusivo para matrizes.

### `MotorExecucaoGCode` — único sequenciador

Local: `08_gcode/hybrid_runner.pyfrag`.

Tem três métodos principais: `validar_antes_de_reservar()`, `iniciar()` e `executar()`. O objeto mantém uma **cópia profunda** dos blocos, a configuração congelada, o nome da fonte e referências aos controladores.

Ele executa os mesmos passos de antes: preflight de geometria e de Z, referência XY válida, reserva de operação, preparo do GRBL, comandos em sequência, pausas, abortamento, atualização de progresso, auto-reset condicional e liberação da reserva.

`executar_job_gcode_em_background()` permanece como adaptador simples que cria um `MotorExecucaoGCode` e inicia uma única thread. `executar_job_em_background()` continua convertendo matrizes para o mesmo motor. Isso mantém os caminhos atuais e facilita o uso de testes e de integrações existentes.

## Limites físicos preservados

- **100 × 100 pontos**, aproximadamente **3 mm** entre centros, **297 mm** entre extremos;
- faixa nominal de movimento 0..299 separada da área efetiva dos centros;
- Home XY automático por `Pn:X`/`Pn:Y`, **recuo comandado de 2 mm**, sem enviar `$H` e sem comandar Z;
- separação de simulação, teste físico XY com Z bloqueado e operação física completa com Z habilitado;
- GPIOs e relés Z, comunicação GRBL e Arduino de reset **sem alteração nesta refatoração**.

**Atenção:** testes de software não homologam hardware, leitura de fim de curso por USB não fornece segurança em tempo real e a alimentação dos atuadores deve dispor de parada elétrica independente.

## Compatibilidade e verificações

- Endpoints HTTP e nomes das funções antigas são preservados.
- A interface HTML é byte a byte igual à anterior.
- O compilador continua gerando um `smartv42.py` único a partir das dez seções.
- Testes verificam planos imutáveis, snapshots sem alias, revisão, reserva única, liberação após erro, ausência de movimento ao falhar o preflight, execução por ambos os caminhos e manutenção do Home XY.

A migração dos indicadores de execução (`_job_*`) e dos globais restantes de visualização pode ocorrer em uma etapa futura, com testes de concorrência próprios; eles **não foram apresentados como eliminados** nesta entrega.
