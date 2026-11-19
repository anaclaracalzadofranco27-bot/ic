#!/usr/bin/env python3
"""Reconstroi smartv42.py usando os fragmentos na ordem original."""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
PARTES = [
    'smartv42_src/01_config/config.pyfrag',
    'smartv42_src/02_telemetry/logging.pyfrag',
    'smartv42_src/03_actuators/gpio_z.pyfrag',
    'smartv42_src/04_grbl/controller.pyfrag',
    'smartv42_src/05_mechanics/reset.pyfrag',
    'smartv42_src/06_vision/pipeline.pyfrag',
    'smartv42_src/07_trajectory/matrix_job.pyfrag',
    'smartv42_src/08_gcode/hybrid_runner.pyfrag',
    'smartv42_src/09_web/interface.pyfrag',
    'smartv42_src/10_server/application.pyfrag',
]


def gerar():
    trechos = []
    for nome in PARTES:
        texto = (ROOT / nome).read_text(encoding='utf-8')
        trechos.append(texto if texto.endswith('\n') else texto + '\n')
    return ''.join(trechos)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--check', action='store_true', help='Verifica se o arquivo final corresponde aos fragmentos')
    args = cli.parse_args()
    texto = gerar()
    destino = ROOT / 'smartv42.py'
    compile(texto, str(destino), 'exec')
    if args.check:
        if not destino.exists() or destino.read_text(encoding='utf-8') != texto:
            print('smartv42.py desatualizado; execute python tools/build_smartv42.py', file=sys.stderr)
            sys.exit(1)
        print('Arquivo final sincronizado. OK')
    else:
        destino.write_text(texto, encoding='utf-8', newline='\n')
        print(f'Gerado smartv42.py ({len(texto.splitlines())} linhas)')


if __name__ == '__main__':
    main()
