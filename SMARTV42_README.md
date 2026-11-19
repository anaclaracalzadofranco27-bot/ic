# Smart Matrix Studio v42 - projeto modular

A versao 42 preserva **smartv37.py e smartv41.py sem modificacoes**. `smartv42.py` e gerado automaticamente usando partes menores em `smartv42_src/`.

## Organizacao

| Pasta | Funcao |
|---|---|
| `01_config` | Coordenadas fisicas, calibracao, configuracao |
| `02_telemetry` | Logs e telemetria |
| `03_actuators` | GPIO, rele e pulsos Z |
| `04_grbl` | Comunicacao com GRBL, comandos de XY e jog |
| `05_mechanics` | Reset mecanico com Arduino |
| `06_vision` | Leitura e conversao de imagens |
| `07_trajectory` | Matriz 100x100 e trajetoria |
| `08_gcode` | Interpretador hibrido X/Y com Z discreto |
| `09_web` | Interface grafica web |
| `10_server` | Servidor HTTP, endpoints e inicializacao |

Estes arquivos `*.pyfrag` sao **fragmentos** de um unico programa Python. Eles compartilham as mesmas variaveis globais na concatenacao. Nao execute os arquivos pequenos isoladamente.

## Como modificar e reconstruir

Edite **somente** a pasta `smartv42_src/`, execute `python tools/build_smartv42.py` e depois `python -m py_compile smartv42.py`. O bot de GitHub Actions executa a mesma reconstrucao em todo push para `main` que modifique `smartv42_src/**`, validando testes antes de commitar automaticamente o arquivo gerado.

O bot requer que **Actions esteja habilitado e o GITHUB_TOKEN tenha permissao Contents: write**. Configure em Settings > Actions > General > Workflow permissions quando necessario. O bot nao funciona em alteracoes locais que ainda nao foram enviadas ao GitHub.

## Executar

```bash
python3 smartv42.py --simulado
# Apenas depois de testar sem movimento real:
python3 smartv42.py --porta /dev/ttyACM0
```

A Raspberry Pi precisa das bibliotecas do programa original (`numpy`, `Pillow`, `pyserial`, `gpiozero` e dependencias opcionais de IA). O firmware GRBL continua responsavel somente por **X e Y**.

## Arquivos G-code

Clique em **Importar G-code X/Y + Z** na interface. O interpretador reconhece os comandos do formato Smart Matrix:

```gcode
G21
G90
G54
G1 X258 Y-39.5 F900
G4 P0
M42 P1 ; Z_LEVEL=4 GPIO=27 PULSOS=4 ON_MS=10 OFF_MS=10
G0 X0 Y0
```

`Z1` a `Z8` podem substituir o `M42`, mas `G1 Z4` nao e permitido porque Z **nao e coordenada**. O arquivo completo e validado **antes** de mover; o executor aguarda `Idle` apos o movimento XY e so entao aciona o GPIO na Raspberry Pi. O comando M42 nunca vai para o GRBL.

A janela padrao da v41 e **X=246..270 mm, Y=-50..-29 mm**. Z e bloqueado fora dela. Arquivos antigos `calibracao_matriz.json` podem conter cantos positivos de Y, e precisam ser recalibrados.

## Seguranca

O codigo nao foi testado nesta conversa com CNC real. Teste primeiro `--simulado`, depois com os atuadores desenergizados. Confirme o mapeamento dos GPIOs, sinal dos reles, finais de curso, fixacao dos cabos e parada de emergencia antes de qualquer movimento fisico. Este interpretador nao e um UGS generico: nao aceita G-code arbitrario de outros programas CNC.
