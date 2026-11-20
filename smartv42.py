#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# GERADO AUTOMATICAMENTE: edite apenas smartv42_src/ e rode tools/build_smartv42.py.
# NAO EDITE smartv42.py DIRETAMENTE: GitHub Actions reconstruira o arquivo.
"""
SMART MATRIX STUDIO v1.8 — EDIÇÃO COMPLETA UGS + SMARTV13 (EESC-USP)
Plataforma CNC Tátil, IA Embarcada, Calibração de Home, Console UGS & Reset Mecânico
  1. Eixos X/Y: Placa CNC GRBL 1.1 em /dev/ttyACM0 (G1 + G4 P0 + confirmação Idle + proteção Pn:XY + JOG $J=)
  2. Eixo Z (Atuador de Pinos): 8 Relés via GPIO da Raspberry Pi 5 na sequência oficial da bancada:
     PINOS_GPIO_Z = [5, 6, 16, 27, 23, 17, 24, 25]
     Com duração de pulso (ms) e quantidade de pulsos por nível (Z1..Z8 / NUM_PULSOS_Z) 100% configuráveis pelo usuário.
  3. Sincronização de Origem Física (0,0 na Extremidade Direita ou Esquerda) + Inversão de Eixos ($3) e Coordenadas:
     - Suporta ORIGEM_X_NA_DIREITA (quando o ponto 0,0 físico fica na extremidade direita e a mesa avança para a esquerda)
     - Suporta inverter o sentido físico do Jog X/Y na interface e espelhar a matriz para não desenhar invertido.
  4. Comandos Completos do UGS (Universal Gcode Sender) + Calibração de Home ($H, Home Manual G10/G92,
     G28/G28.1, G30/G30.1, WCS G54-G59, Check Mode $C, Sleep $SLP, Overrides em Tempo Real e EEPROM $$)
  5. Terminal Serial Interativo Embutido (Web + CLI) para envio de comandos G-Code, GRBL e calibração de pinos/pulsos.
"""

import argparse
import base64
import copy
import csv
import http.server
import io
import json
import math
import os
import queue
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np
try:
    import psutil
except ImportError:
    psutil = None
from PIL import Image

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

try:
    from gpiozero import OutputDevice
    GPIO_DISPONIVEL = True
except ImportError:
    GPIO_DISPONIVEL = False

# =============================================================================
# CONFIGURAÇÕES E CONSTANTES GLOBAIS (SINCRONIZADAS COM A FOTO / SMARTV13)
# =============================================================================
PORTA_GRBL = os.environ.get("SMART_MATRIX_PORTA_GRBL", "/dev/ttyACM0")
PORTA_ARDUINO_RESET = os.environ.get("SMART_MATRIX_PORTA_RESET", "SIMULADO")
BAUD = 115200
FEED_RATE = 900.0
JOG_FEED_RATE = 900.0
NOME_MODELO = "deeplabv3plus_int8.onnx"
LIMIAR_IA_PADRAO = 0.42

TAMANHO_MATRIZ = (100, 100)
PASSO_NOMINAL_MALHA_MM = 3.0
# 100 pontos geram 99 intervalos; extremos fisicos separados por 297 mm.
EXTENSAO_UTIL_MALHA_MM = (TAMANHO_MATRIZ[1] - 1) * PASSO_NOMINAL_MALHA_MM
# Faixa nominal 0..299 do mecanismo NAO significa 300 pontos e nao
# autoriza extrapolar o centro dos pinos para alem dos cantos medidos.
CURSO_NOMINAL_DESLOCAMENTO_MM = 299.0
LIMITE_X_MM = CURSO_NOMINAL_DESLOCAMENTO_MM
LIMITE_Y_MM = CURSO_NOMINAL_DESLOCAMENTO_MM

# -----------------------------------------------------------------------------
# SINCRONIZAÇÃO DO SISTEMA DE COORDENADAS COM A BANCADA FÍSICA:
# Se na sua matriz física o ponto (0,0) inicial fica na EXTREMIDADE DIREITA:
#   - MODO_COORD_X = "zero_direita_negativo": O cabeçote começa na extrema direita como X=0.0
#     e se desloca para a esquerda usando coordenadas X negativas (ou positivas se $3 inverter o motor).
#   - ESPELHAR_MATRIZ_X = True: Espelha horizontalmente os índices X da matriz (99 - x) para que a imagem
#     não saia espelhada quando o cabeçote começa pela extremidade direita.
#   - SINAL_EIXO_X = 1 (ou -1): Multiplica as coordenadas X enviadas ao GRBL (use -1 se para ir da direita
#     para a esquerda no seu GRBL for necessário enviar X negativo, ou +1 se X+ já move para a esquerda).
#   - INVERTER_JOG_X = False: Se True, inverte os botões "← X-" e "→ X+" na interface para bater com a visão frontal.
# -----------------------------------------------------------------------------
ORIGEM_X_NA_DIREITA = False
SINAL_EIXO_X = 1.0          # +1.0 = envia X positivo | -1.0 = envia X negativo
SINAL_EIXO_Y = -1.0         # -1.0 = eixo Y invertido com valores negativos (0 a -297)
INVERTER_JOG_X = False      # Se True, inverte apenas os botões visuais ← X- e → X+ do painel
INVERTER_JOG_Y = True       # True = inverte os botões ↑ Y+ / ↓ Y- para acompanhar Y negativo

# =============================================================================
# CALIBRAÇÃO BILINEAR PELOS 4 CANTOS DA MATRIZ 297x297 mm (Y INVERTIDO NEGATIVO)
# Plano com eixo X positivo (0..297) e eixo Y invertido negativo (0..-297):
#   - Ponto A (Canto Inferior Esquerdo | col=0,  lin=99): X = 0.000,   Y = 0.000
#   - Ponto B (Canto Inferior Direito  | col=99, lin=99): X = 297.000, Y = 0.000
#   - Ponto C (Canto Superior Direito  | col=99, lin=0 ): X = 297.000, Y = -297.000
#   - Ponto D (Canto Superior Esquerdo | col=0,  lin=0 ): X = 0.000,   Y = -297.000
# =============================================================================
ARQUIVO_CALIBRACAO_MATRIZ = "calibracao_matriz.json"
USAR_CALIBRACAO_4_PONTOS = True

PONTO_A = [0.000, 0.000]        # Base-Esquerda da matriz (col 0,  lin 99)
PONTO_B = [297.000, 0.000]      # Base-Direita da matriz  (col 99, lin 99)
PONTO_C = [297.000, -297.000]   # Topo-Direita da matriz  (col 99, lin 0)
PONTO_D = [0.000, -297.000]     # Topo-Esquerda da matriz (col 0,  lin 0)

X_INICIO_FISICO = 0.000
Y_INICIO_FISICO = 0.000
X_FIM_FISICO = 297.000
Y_FIM_FISICO = -297.000

# =============================================================================
# JANELA OPCIONAL DE RECORTE: mascara pontos sem reescalar o passo fisico.
# Padrao: 100x100 pinos sobre a area inteira entre os centros 0..297 mm.
# =============================================================================
USAR_JANELA_PLOTAGEM = False
PLOT_X_MIN = 0.0
PLOT_X_MAX = 297.0
PLOT_Y_MIN = -297.0
PLOT_Y_MAX = 0.0

TIMEOUT_OK_GRBL = 5.0
TIMEOUT_IDLE_GRBL = 10.0
FATOR_TIMEOUT_MOV = 1.5
MARGEM_TIMEOUT_MOV_S = 5.0

# Tempos de pulso em segundos (padrão 10 ms = 0.01 s, configuráveis pelo usuário em ms)
PAUSA_ANTES_Z_S = 0.01
TEMPO_ON_Z_S = 0.01
TEMPO_OFF_Z_S = 0.01

# Quantidade padrão de pulsos (configurável via terminal e interface, conforme foto)
NUM_PULSOS_Z = 7
PULSOS_POR_NIVEL = [1, 2, 3, 4, 5, 6, 7, 8]
USAR_PULSOS_FIXOS = False

CONFIRMAR_IDLE = True
INTERVALO_POLL_IDLE_S = 0.02

TIMEOUT_LEITURA_SERIAL_S = 0.05
TIMEOUT_BOOT_GRBL_S = 3.0

VOLTAR_ORIGEM = True
IGNORAR_FIM_DE_CURSO = False
AUTO_RESET_APOS_PLOT = False

RELE_ATIVO_ALTO = False
NIVEIS_MAX = 8
MODO_PROFUNDIDADE = "distancia"
SIMULADO = False
# Armada somente via acao explicita da interface; o padrao da CNC real e teste XY sem Z.
MODO_Z_FISICO_HABILITADO = False

# Sequência EXATA dos pinos GPIO do cabeçote conforme a foto enviada:
PINOS_GPIO_Z = [5, 6, 16, 27, 23, 17, 24, 25]
reles_z = []

fila_de_movimentos = queue.Queue()
sistema_ativo = threading.Event()
sistema_ativo.set()
pausado_evento = threading.Event()
abortar_job_evento = threading.Event()

_sessao_onnx = None
_input_name_onnx = None
_input_hw_onnx = (513, 513)
_processo_atual = psutil.Process(os.getpid()) if psutil else None

RE_MPOS = re.compile(r"MPos:\s*(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)")
RE_WPOS = re.compile(r"WPos:\s*(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)")
RE_WCO = re.compile(r"WCO:\s*(-?\d+\.?\d*),(-?\d+\.?\d*),(-?\d+\.?\d*)")
RE_PINOS_GRBL = re.compile(r"Pn:([A-Za-z]+)")
RE_FS_GRBL = re.compile(r"FS:\s*(\d+\.?\d*),(\d+\.?\d*)")
RE_OV_GRBL = re.compile(r"Ov:\s*(\d+),(\d+),(\d+)")

ALARMES_GRBL = {
    "1": "fim de curso (hard limit) acionado",
    "2": "alvo fora do curso da maquina (soft limit)",
    "3": "reset com a maquina andando (posicao pode ter se perdido)",
    "4": "falha de probe (estado inicial)",
    "5": "falha de probe (nao encostou)",
    "6": "homing falhou: reset durante o homing",
    "7": "homing falhou: porta de seguranca aberta",
    "8": "homing falhou: nao conseguiu soltar o fim de curso",
    "9": "homing falhou: nao achou o fim de curso",
}

ERROS_GRBL = {
    "1": "G-code esperado tem letra invalida",
    "2": "Formato numerico invalido",
    "3": "Comando de sistema '$' nao reconhecido",
    "4": "Valor negativo nao permitido",
    "5": "Ciclo de Homing ($H) nao esta habilitado no GRBL ($22=0)",
    "6": "Pulso de passo minimo deve ser > 3us",
    "7": "Falha ao ler EEPROM — usando padroes",
    "8": "Comando '$' so pode ser usado quando GRBL esta IDLE",
    "9": "G-code bloqueado durante alarme ou jog",
    "10": "Soft limits ($20) exigem Homing ($22=1) habilitado",
    "11": "Maximo de caracteres por linha excedido",
    "12": "Taxa de passo maxima excedida",
    "13": "Porta de seguranca aberta",
    "14": "Comprimento da linha de inicializacao excedido",
    "15": "Alvo do Jog excede o curso da maquina",
    "16": "Comando de Jog invalido (sem '=' ou G-code proibido)",
    "20": "Comando G-code nao suportado",
    "21": "Mais de um comando do mesmo grupo modal na linha",
    "22": "Feed rate ainda nao definido",
}

DICA_FIM_DE_CURSO = (
    "Dica: com a fonte dos motores desligada, empurre o carro pra longe do fim de curso, "
    "ate o canto da origem (0,0), e reinicie a placa do GRBL pra ela zerar ali. Se o Pn: "
    "continuar aparecendo com o carro longe da chave, confira a fiacao e o $5 "
    "(inversao dos fins de curso)."
)

ESTADO_ESTUDIO = {
    "matriz": [],
    "pontos": [],
    "linhas_gcode": [],
    "resumo": {
        "modo_operacao": "Domo Gaussiano 3D",
        "pontos_ativos": 2453,
        "total_grade": 10000,
        "reducao_pinos_pct": 75.5,
        "linhas_gcode_total": 7366,
        "tempo_inferencia_ms": 9.0,
        "tempo_geracao_gcode_ms": 14.2,
        "tempo_total_estimado_s": 540.0,
        "distancia_total_mm": 12450.0,
        "arquivo_gcode": "trajetoria_smart_matrix.gcode",
    },
    "progresso_idx": 0,
    "z_atual": 0,
    "em_reset": False,
    "etapa_reset": 0,
    "logs": deque(maxlen=100),
    "terminal": deque(maxlen=250),
}


def salvar_calibracao_matriz():
    dados = {
        "usar_calibracao_4_pontos": USAR_CALIBRACAO_4_PONTOS,
        "ponto_a": PONTO_A,
        "ponto_b": PONTO_B,
        "ponto_c": PONTO_C,
        "ponto_d": PONTO_D,
        "origem_x_na_direita": ORIGEM_X_NA_DIREITA,
        "inverter_jog_x": INVERTER_JOG_X,
        "inverter_jog_y": INVERTER_JOG_Y,
        "sinal_eixo_x": SINAL_EIXO_X,
        "sinal_eixo_y": SINAL_EIXO_Y,
        "modelo": "100x100_3mm_297mm",
    }
    try:
        with open(ARQUIVO_CALIBRACAO_MATRIZ, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def carregar_calibracao_matriz():
    """Carrega configuracao legada apenas se os quatro cantos cobrem 100x100 a 3 mm.

    Uma calibracao antiga de 24x21/247.5 mm NUNCA substitui silenciosamente os
    pontos de 297 mm. Nao salva nem migra sem intervencao do operador.
    """
    global USAR_CALIBRACAO_4_PONTOS, PONTO_A, PONTO_B, PONTO_C, PONTO_D
    global ORIGEM_X_NA_DIREITA, INVERTER_JOG_X, INVERTER_JOG_Y, SINAL_EIXO_X, SINAL_EIXO_Y
    if not os.path.isfile(ARQUIVO_CALIBRACAO_MATRIZ):
        return False
    try:
        with open(ARQUIVO_CALIBRACAO_MATRIZ, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        nomes = ("ponto_a", "ponto_b", "ponto_c", "ponto_d")
        pontos = [[float(v) for v in dados[nome]] for nome in nomes]
        ok, motivo = validar_calibracao_cantos(pontos)
        if not ok:
            registrar_log(f"Calibracao salva IGNORADA: {motivo} Refaça medicao dos 4 cantos.", "erro")
            return False
        origem_direita = bool(dados.get("origem_x_na_direita", ORIGEM_X_NA_DIREITA))
        sx = -1.0 if float(dados.get("sinal_eixo_x", SINAL_EIXO_X)) < 0 else 1.0
        sy = -1.0 if float(dados.get("sinal_eixo_y", SINAL_EIXO_Y)) < 0 else 1.0
        PONTO_A, PONTO_B, PONTO_C, PONTO_D = pontos
        USAR_CALIBRACAO_4_PONTOS = True
        ORIGEM_X_NA_DIREITA = origem_direita
        INVERTER_JOG_X = bool(dados.get("inverter_jog_x", INVERTER_JOG_X))
        INVERTER_JOG_Y = bool(dados.get("inverter_jog_y", INVERTER_JOG_Y))
        SINAL_EIXO_X, SINAL_EIXO_Y = sx, sy
        return True
    except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
        registrar_log(f"Calibracao salva invalida e ignorada: {exc}", "erro")
        return False


def validar_calibracao_cantos(pontos=None, tolerancia_extensao_mm=12.0):
    """Exige quatro cantos medidos compativeis com a malha 100x100 de ~3 mm."""
    pontos = (PONTO_A, PONTO_B, PONTO_C, PONTO_D) if pontos is None else pontos
    try:
        pts = [(float(p[0]), float(p[1])) for p in pontos]
        if len(pts) != 4 or any(not all(math.isfinite(v) for v in p) for p in pts):
            return False, "Quatro cantos finitos A/B/C/D sao obrigatorios."
    except (ValueError, TypeError, IndexError):
        return False, "Coordenadas XY invalidas nos quatro cantos."
    a, b, c, d = pts
    edges = [(b[0]-a[0], b[1]-a[1]), (c[0]-b[0], c[1]-b[1]),
             (d[0]-c[0], d[1]-c[1]), (a[0]-d[0], a[1]-d[1])]
    distancias = [math.hypot(*e) for e in edges]
    for i, dist in enumerate(distancias):
        if abs(dist - EXTENSAO_UTIL_MALHA_MM) > tolerancia_extensao_mm:
            return False, (
                f"Calibracao antiga/incompativel: aresta {i+1} tem {dist:.2f} mm; "
                f"esperado aproximadamente {EXTENSAO_UTIL_MALHA_MM:.1f} mm "
                "para 100 pinos espacados 3 mm."
            )
    cruzamentos = [edges[i][0]*edges[(i+1)%4][1] -
                   edges[i][1]*edges[(i+1)%4][0] for i in range(4)]
    if any(abs(c) < 1.0 for c in cruzamentos) or not (
        all(c > 0 for c in cruzamentos) or all(c < 0 for c in cruzamentos)
    ):
        return False, "Cantos degenerados, cruzados ou com orientacao inconsistente."
    return True, ""


def _interpolar_cantos(u, v, pontos=None):
    """Coordenadas no G54: topo D->C; base A->B; passo real pelos cantos."""
    a, b, c, d = (PONTO_A, PONTO_B, PONTO_C, PONTO_D) if pontos is None else pontos
    return (
        (1-u)*(1-v)*d[0] + u*(1-v)*c[0] + (1-u)*v*a[0] + u*v*b[0],
        (1-u)*(1-v)*d[1] + u*(1-v)*c[1] + (1-u)*v*a[1] + u*v*b[1],
    )


def _inverter_cantos(x_mm, y_mm):
    """Inversa numerica da mesma bilinear, inclusive para leve desalinhamento."""
    if not math.isfinite(float(x_mm)) or not math.isfinite(float(y_mm)):
        raise ValueError("Coordenadas fisicas nao finitas.")
    a, b, c, d = [(float(p[0]), float(p[1])) for p in (PONTO_A, PONTO_B, PONTO_C, PONTO_D)]
    ex = (c[0]-d[0], c[1]-d[1])
    ey = (a[0]-d[0], a[1]-d[1])
    cruz = (d[0]-c[0]-a[0]+b[0], d[1]-c[1]-a[1]+b[1])
    det = ex[0]*ey[1]-ex[1]*ey[0]
    if abs(det) < 1e-9:
        raise ValueError("Calibracao degenerada: eixo XY nao invertivel.")
    tx, ty = float(x_mm)-d[0], float(y_mm)-d[1]
    u = (tx*ey[1]-ty*ey[0])/det
    v = (ex[0]*ty-ex[1]*tx)/det
    for _ in range(15):
        rx = d[0]+ex[0]*u+ey[0]*v+cruz[0]*u*v-float(x_mm)
        ry = d[1]+ex[1]*u+ey[1]*v+cruz[1]*u*v-float(y_mm)
        ju = (ex[0]+cruz[0]*v, ex[1]+cruz[1]*v)
        jv = (ey[0]+cruz[0]*u, ey[1]+cruz[1]*u)
        determinant = ju[0]*jv[1]-ju[1]*jv[0]
        if abs(determinant) < 1e-9:
            raise ValueError("Calibracao bilinear nao invertivel.")
        du = (rx*jv[1]-ry*jv[0])/determinant
        dv = (ju[0]*ry-ju[1]*rx)/determinant
        u -= du
        v -= dv
        if max(abs(du), abs(dv)) < 1e-12:
            break
    px, py = _interpolar_cantos(u, v)
    if math.hypot(px-float(x_mm),py-float(y_mm)) > 0.001:
        raise ValueError("Coordenada fisica incompativel com bilinear.")
    return u, v


def indice_para_mm_fisico(x_idx, y_idx):
    """Um indice logico [0..99] vira sempre um centro fisico da malha."""
    x_idx, y_idx = float(x_idx), float(y_idx)
    if not (math.isfinite(x_idx) and math.isfinite(y_idx) and
            0 <= x_idx <= TAMANHO_MATRIZ[1]-1 and
            0 <= y_idx <= TAMANHO_MATRIZ[0]-1):
        raise ValueError("Indice fora da matriz 100x100 (0..99).")
    u = x_idx/(TAMANHO_MATRIZ[1]-1)
    v = y_idx/(TAMANHO_MATRIZ[0]-1)
    if ORIGEM_X_NA_DIREITA:
        u = 1.0-u
    return tuple(float(z) for z in _interpolar_cantos(u,v))


def mm_fisico_para_indice(x_grbl, y_grbl):
    """Inversa exata da transformacao de centros, independente do recorte."""
    u, v = _inverter_cantos(x_grbl, y_grbl)
    if ORIGEM_X_NA_DIREITA:
        u = 1.0-u
    return (int(round(max(0, min(1, u))*(TAMANHO_MATRIZ[1]-1))),
            int(round(max(0, min(1, v))*(TAMANHO_MATRIZ[0]-1))))


def ponto_na_janela_plotagem(x_mm, y_mm, tolerancia_mm=1e-6):
    """Janela e uma mascara; nao muda nunca as coordenadas dos 100x100 centros."""
    if not USAR_JANELA_PLOTAGEM:
        return True
    return (min(PLOT_X_MIN,PLOT_X_MAX)-tolerancia_mm <= x_mm <=
            max(PLOT_X_MIN,PLOT_X_MAX)+tolerancia_mm and
            min(PLOT_Y_MIN,PLOT_Y_MAX)-tolerancia_mm <= y_mm <=
            max(PLOT_Y_MIN,PLOT_Y_MAX)+tolerancia_mm)


def desvio_centro_malha_mm(x_mm, y_mm):
    """Distancia ao centro fisico mais proximo para validar pressionamentos Z."""
    u, v = _inverter_cantos(x_mm, y_mm)
    if not (-0.001 <= u <= 1.001 and -0.001 <= v <= 1.001):
        return float("inf")
    idx_x, idx_y = mm_fisico_para_indice(x_mm, y_mm)
    px, py = indice_para_mm_fisico(idx_x, idx_y)
    return math.hypot(x_mm-px, y_mm-py)


def obter_estado_coordenadas():
    x_meio, y_meio = indice_para_mm_fisico(49.5, 49.5)
    curso_x = abs(0.5 * (PONTO_B[0] + PONTO_C[0]) - 0.5 * (PONTO_A[0] + PONTO_D[0]))
    curso_y = abs(0.5 * (PONTO_D[1] + PONTO_C[1]) - 0.5 * (PONTO_A[1] + PONTO_B[1]))
    return {
        "usar_calibracao_4_pontos": USAR_CALIBRACAO_4_PONTOS,
        "ponto_a": [round(float(PONTO_A[0]), 3), round(float(PONTO_A[1]), 3)],
        "ponto_b": [round(float(PONTO_B[0]), 3), round(float(PONTO_B[1]), 3)],
        "ponto_c": [round(float(PONTO_C[0]), 3), round(float(PONTO_C[1]), 3)],
        "ponto_d": [round(float(PONTO_D[0]), 3), round(float(PONTO_D[1]), 3)],
        "x_meio_fisico": round(float(x_meio), 3),
        "y_meio_fisico": round(float(y_meio), 3),
        "curso_x_grbl": round(float(curso_x), 3),
        "curso_y_grbl": round(float(curso_y), 3),
        "origem_x_na_direita": ORIGEM_X_NA_DIREITA,
        "sinal_eixo_x": SINAL_EIXO_X,
        "sinal_eixo_y": SINAL_EIXO_Y,
        "inverter_jog_x": INVERTER_JOG_X,
        "inverter_jog_y": INVERTER_JOG_Y,
        "x_inicio_fisico": round(float(PONTO_A[0]), 3),
        "y_inicio_fisico": round(float(PONTO_A[1]), 3),
        "x_fim_fisico": round(float(PONTO_C[0]), 3),
        "y_fim_fisico": round(float(PONTO_C[1]), 3),
        "limite_x_mm": LIMITE_X_MM,
        "limite_y_mm": LIMITE_Y_MM,
        "passo_nominal_mm": PASSO_NOMINAL_MALHA_MM,
        "extensao_util_mm": EXTENSAO_UTIL_MALHA_MM,
        "curso_nominal_mm": CURSO_NOMINAL_DESLOCAMENTO_MM,
        "modelo": "100x100_3mm_297mm",
        "usar_janela_plotagem": USAR_JANELA_PLOTAGEM,
        "plot_x_min": PLOT_X_MIN,
        "plot_x_max": PLOT_X_MAX,
        "plot_y_min": PLOT_Y_MIN,
        "plot_y_max": PLOT_Y_MAX,
        "avisos": validar_cantos(),
    }


# =============================================================================
# LOGS, CONSOLE UGS, SNAPSHOT E TELEMETRIA DA RASPBERRY PI 5
# =============================================================================
def registrar_terminal(direcao, mensagem, tipo="info"):
    """Registra linhas enviadas (>>>) e recebidas (<) no Console/Terminal estilo UGS."""
    hora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    ESTADO_ESTUDIO["terminal"].appendleft({
        "hora": hora,
        "dir": direcao,
        "msg": str(mensagem),
        "tipo": tipo,
    })


def registrar_log(mensagem, tipo="info", nome_arquivo="log_operacao.txt"):
    hora = datetime.now().strftime("%H:%M:%S")
    timestamp_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha = f"[{timestamp_full}] {mensagem}\n"
    try:
        with open(nome_arquivo, "a", encoding="utf-8") as f:
            f.write(linha)
    except Exception:
        pass
    print(linha.strip())
    if nome_arquivo == "log_operacao.txt":
        ESTADO_ESTUDIO["logs"].appendleft({"hora": hora, "msg": mensagem, "tipo": tipo})


def ler_ultimas_linhas(nome_arquivo, n_linhas, bloco=8192):
    with open(nome_arquivo, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        dados = b""
        while pos > 0 and dados.count(b"\n") <= n_linhas:
            passo = min(bloco, pos)
            pos -= passo
            f.seek(pos)
            dados = f.read(passo) + dados
    return dados.decode("utf-8", errors="replace").splitlines(keepends=True)[-n_linhas:]


def extrair_logs_recentes(n_linhas=50, arquivos=("log_operacao.txt", "log_telemetria_rasp.txt")):
    resultado = {}
    for nome_arquivo in arquivos:
        try:
            resultado[nome_arquivo] = ler_ultimas_linhas(nome_arquivo, n_linhas)
        except FileNotFoundError:
            resultado[nome_arquivo] = []
    return resultado


def exportar_snapshot_de_logs(nome_saida="log_snapshot_atual.txt", n_linhas=50):
    logs = extrair_logs_recentes(n_linhas)
    try:
        with open(nome_saida, "w", encoding="utf-8") as f:
            f.write(f"=== Snapshot de logs em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
            for nome_arquivo, linhas in logs.items():
                f.write(f"--- {nome_arquivo} (ultimas {len(linhas)} linhas) ---\n")
                f.writelines(linhas)
                f.write("\n")
    except Exception as e:
        registrar_log(f"Erro ao exportar snapshot de logs: {e}", "erro")


def monitorar_telemetria():
    while sistema_ativo.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1) if psutil else 0.0
            ram = psutil.virtual_memory().percent if psutil else 0.0
            mem_processo_mb = (_processo_atual.memory_info().rss / (1024 * 1024)) if _processo_atual else 0.0
            try:
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp_c = float(f.read()) / 1000.0
            except Exception:
                temp_c = 0.0

            msg = (
                f"TELEMETRIA | CPU: {cpu}% | RAM: {ram}% | "
                f"Mem proc: {mem_processo_mb:.1f}MB | Temp: {temp_c:.1f}C | "
                f"Fila: {fila_de_movimentos.qsize()}"
            )
            registrar_log(msg, tipo="info", nome_arquivo="log_telemetria_rasp.txt")
            time.sleep(5)
        except Exception as e:
            registrar_log(f"Erro na telemetria: {e}", tipo="erro", nome_arquivo="log_telemetria_rasp.txt")


# =============================================================================
# 1. CONTROLE DO EIXO Z (SEQUÊNCIA EXATA DA FOTO + PULSOS/MS CONFIGURÁVEIS)
# =============================================================================
def obter_pulsos_para_nivel(nivel_z):
    """Retorna a quantidade de pulsos configurada pelo usuário para o nível (1 a 8)."""
    idx = max(0, min(NIVEIS_MAX - 1, int(nivel_z) - 1))
    if USAR_PULSOS_FIXOS:
        return max(1, int(NUM_PULSOS_Z))
    if idx < len(PULSOS_POR_NIVEL):
        return max(1, int(PULSOS_POR_NIVEL[idx]))
    return max(1, int(NUM_PULSOS_Z))


def configurar_parametros_pulsos_z(
    on_ms=None,
    off_ms=None,
    pausa_ms=None,
    num_pulsos_fixo=None,
    pulsos_por_nivel=None,
    usar_fixo=None,
    pinos_gpio=None,
):
    global TEMPO_ON_Z_S, TEMPO_OFF_Z_S, PAUSA_ANTES_Z_S, NUM_PULSOS_Z, PULSOS_POR_NIVEL, USAR_PULSOS_FIXOS, PINOS_GPIO_Z

    # Valida primeiro, para impedir configuracoes parciais ou silenciosamente ignoradas.
    for nome, valor, minimo, maximo in (
        ("ON", on_ms, 1, 2000), ("OFF", off_ms, 0, 2000),
        ("Pausa", pausa_ms, 0, 2000),
    ):
        if valor is not None and (not math.isfinite(float(valor)) or not minimo <= float(valor) <= maximo):
            raise ValueError(f"{nome} deve estar em {minimo}..{maximo} ms")
    if num_pulsos_fixo is not None and not 1 <= int(num_pulsos_fixo) <= 500:
        raise ValueError("Pulsos fixos devem estar em 1..500")
    if pulsos_por_nivel is not None:
        partes_teste = ([int(x.strip()) for x in pulsos_por_nivel.split(",") if x.strip()]
                        if isinstance(pulsos_por_nivel, str) else [int(x) for x in pulsos_por_nivel])
        if len(partes_teste) not in (1, NIVEIS_MAX) or any(not 1 <= x <= 500 for x in partes_teste):
            raise ValueError("Informe 1 ou 8 valores de pulsos, cada um entre 1 e 500")
    if on_ms is not None:
        TEMPO_ON_Z_S = float(on_ms) / 1000.0
    if off_ms is not None:
        TEMPO_OFF_Z_S = float(off_ms) / 1000.0
    if pausa_ms is not None:
        PAUSA_ANTES_Z_S = float(pausa_ms) / 1000.0
    if num_pulsos_fixo is not None:
        NUM_PULSOS_Z = int(num_pulsos_fixo)
    if usar_fixo is not None:
        USAR_PULSOS_FIXOS = bool(usar_fixo)

    if pulsos_por_nivel is not None:
        if isinstance(pulsos_por_nivel, str):
            partes = [int(x.strip()) for x in pulsos_por_nivel.split(",") if x.strip()]
        else:
            partes = [int(x) for x in pulsos_por_nivel]
        if len(partes) == 1:
            NUM_PULSOS_Z = max(1, partes[0])
            PULSOS_POR_NIVEL = [NUM_PULSOS_Z] * NIVEIS_MAX
        elif len(partes) >= NIVEIS_MAX:
            PULSOS_POR_NIVEL = [max(1, p) for p in partes[:NIVEIS_MAX]]

    if pinos_gpio is not None:
        if isinstance(pinos_gpio, str):
            novos_pinos = [int(x.strip()) for x in pinos_gpio.split(",") if x.strip()]
        else:
            novos_pinos = [int(x) for x in pinos_gpio]
        if len(novos_pinos) == NIVEIS_MAX and novos_pinos != PINOS_GPIO_Z:
            PINOS_GPIO_Z = novos_pinos
            inicializar_gpio_z()

    resumo_niveis = ", ".join(f"Z{i+1}={obter_pulsos_para_nivel(i+1)}p(GPIO{PINOS_GPIO_Z[i]})" for i in range(NIVEIS_MAX))
    msg = (
        f"Pulsos Z configurados: ON={TEMPO_ON_Z_S*1000:.1f}ms | OFF={TEMPO_OFF_Z_S*1000:.1f}ms | "
        f"Pausa={PAUSA_ANTES_Z_S*1000:.1f}ms | [{resumo_niveis}]"
    )
    registrar_log(msg, "ok")
    registrar_terminal("SYS", msg, "ok")
    return obter_estado_config_z()


def obter_estado_config_z():
    return {
        "pinos_gpio_z": list(PINOS_GPIO_Z),
        "z_on_ms": round(TEMPO_ON_Z_S * 1000.0, 2),
        "z_off_ms": round(TEMPO_OFF_Z_S * 1000.0, 2),
        "pausa_z_ms": round(PAUSA_ANTES_Z_S * 1000.0, 2),
        "num_pulsos_z": NUM_PULSOS_Z,
        "usar_pulsos_fixos": USAR_PULSOS_FIXOS,
        "pulsos_por_nivel": [obter_pulsos_para_nivel(i + 1) for i in range(NIVEIS_MAX)],
        "pulsos_tabela_custom": list(PULSOS_POR_NIVEL),
    }


# Um unico intertravamento protege criacao, troca de modo, testes e todos os pulsos.
trava_gpio_z = threading.RLock()


def obter_modo_z_efetivo():
    """Estados exclusivos derivados da conexao efetiva; nunca de lista de objetos GPIO."""
    grbl = globals().get("_grbl_global")
    if grbl is None or getattr(grbl, "ser", None) is None:
        return "desconectado"
    virtual = bool(getattr(grbl, "modo_simulacao", False))
    fisico = bool(getattr(grbl, "conexao_real", False))
    porta_virtual = str(getattr(grbl, "porta_atual", "")).upper() == "SIMULADO"
    if (virtual and fisico) or (SIMULADO and fisico) or (porta_virtual and fisico):
        return "inconsistente"
    if (virtual or porta_virtual) and not fisico:
        return "simulacao"
    if SIMULADO and not virtual:
        return "inconsistente"
    if not fisico:
        return "desconectado"
    return "completo" if MODO_Z_FISICO_HABILITADO else "teste_xy"


def obter_estado_modo_z():
    modo = obter_modo_z_efetivo()
    return {
        "modo": modo,
        "fisico_habilitado": modo == "completo",
        "gpio_inicializado": len(reles_z) == len(PINOS_GPIO_Z) and len(reles_z) > 0,
        "simulado": modo == "simulacao",
        "mensagem": {
            "simulacao": "GRBL e Z virtuais: GPIOs nao podem ser acionados.",
            "teste_xy": "Teste fisico XY: relés Z bloqueados.",
            "completo": "Z fisico habilitado: operar somente apos Home XY.",
            "desconectado": "GRBL desconectado; Z fisico bloqueado.",
            "inconsistente": "Modo de simulacao e conexao fisica inconsistentes; Z bloqueado.",
        }[modo],
    }


def _desligar_e_liberar_reles_z():
    """Chamado apenas com trava_gpio_z adquirida."""
    for rele in tuple(reles_z):
        try:
            rele.off()
        except Exception as exc:
            registrar_log(f"Nao foi possivel desligar rele Z: {exc}", "erro")
        try:
            rele.close()
        except Exception as exc:
            registrar_log(f"Nao foi possivel liberar GPIO Z: {exc}", "erro")
    reles_z.clear()


def bloquear_z_fisico(motivo="Modo Teste fisico XY: Z bloqueado"):
    """Desarma e libera objetos fisicos; seguro mesmo se GRBL mudar para simulacao."""
    global MODO_Z_FISICO_HABILITADO
    with trava_gpio_z:
        MODO_Z_FISICO_HABILITADO = False
        _desligar_e_liberar_reles_z()
    registrar_log(f"Z fisico desabilitado: {motivo}", "aviso")
    return True


def inicializar_gpio_z():
    """Nunca configura saidas fisicas para GRBL virtual ou no modo Teste XY."""
    with trava_gpio_z:
        _desligar_e_liberar_reles_z()
        modo = obter_modo_z_efetivo()
        if modo != "completo":
            registrar_log(f"GPIO Z nao inicializado (modo={modo}).", "info")
            return False
        if not GPIO_DISPONIVEL or OutputDevice is None:
            registrar_log("GPIO fisico indisponivel; Z mantido bloqueado.", "erro")
            return False
        try:
            for pino in PINOS_GPIO_Z:
                reles_z.append(OutputDevice(pino, active_high=RELE_ATIVO_ALTO, initial_value=False))
        except Exception as exc:
            _desligar_e_liberar_reles_z()
            registrar_log(f"Falha critica ao inicializar GPIO Z: {exc}", "erro")
            return False
        registrar_log(f"GPIO Z fisico inicializado, {len(reles_z)} reles em repouso.", "ok")
        return True


def habilitar_z_fisico():
    """Arma Z apenas com CNC realmente conectada, Home XY valido e Idle confirmado."""
    global MODO_Z_FISICO_HABILITADO
    with trava_gpio_z:
        grbl = globals().get("_grbl_global")
        if (grbl is None or getattr(grbl, "ser", None) is None
                or not getattr(grbl, "conexao_real", False)
                or getattr(grbl, "modo_simulacao", False)
                or str(getattr(grbl, "porta_atual", "")).upper() == "SIMULADO"
                or SIMULADO):
            return False, "Z fisico proibido sem conexao GRBL fisica nao simulada."
        if not getattr(grbl, "home_xy_referenciado", False):
            return False, "Execute Home XY por chaves antes de habilitar Z fisico."
        if not GPIO_DISPONIVEL or OutputDevice is None:
            return False, "Biblioteca/controlador GPIO indisponivel na Raspberry Pi."
        if abortar_job_evento.is_set() or _job_abortar:
            return False, "Operacao abortada: Z fisico nao habilitado."
        status = grbl.ler_status_grbl(timeout=0.7)
        if not status.startswith("<Idle"):
            return False, f"GRBL precisa estar Idle para habilitar Z ({status})."
        if any(p in pinos_ativos(status) for p in "XY"):
            return False, "Chaves X/Y acionadas: Z fisico nao habilitado."
        MODO_Z_FISICO_HABILITADO = True
        if not inicializar_gpio_z():
            MODO_Z_FISICO_HABILITADO = False
            _desligar_e_liberar_reles_z()
            return False, "Nao foi possivel inicializar fisicamente os reles Z."
        return True, "Modo fisico completo habilitado; Z1..Z8 podem ser acionados com intertravamentos."


def validar_conexao_fisica_z():
    modo = obter_modo_z_efetivo()
    if modo == "simulacao":
        return True, ""
    if modo == "inconsistente":
        return False, "Modo simulado e GRBL fisico inconsistentes; relés Z bloqueados."
    if modo == "desconectado":
        return False, "Bloqueado: GRBL fisico nao conectado."
    if modo == "teste_xy":
        return False, "Teste fisico XY: Z fisico bloqueado; habilite-o expressamente apos Home XY."
    grbl = globals()["_grbl_global"]
    if not getattr(grbl, "home_xy_referenciado", False):
        return False, "Home XY nao referenciado: Z bloqueado."
    status = grbl.ler_status_grbl(timeout=0.7)
    if not status.startswith("<Idle"):
        return False, f"Bloqueado: GRBL nao confirmou Idle ({status})."
    if any(p in pinos_ativos(status) for p in "XY"):
        return False, "Fim de curso X/Y acionado; Z bloqueado."
    if abortar_job_evento.is_set() or _job_abortar:
        return False, "Z bloqueado: operacao abortada."
    return True, ""


def aguardar_liberacao_z():
    """Intertravamento de Z: pausa nao permite iniciar o proximo pulso."""
    while _job_pausado and not _job_abortar and not abortar_job_evento.is_set() and sistema_ativo.is_set():
        time.sleep(0.025)
    return sistema_ativo.is_set() and not _job_abortar and not abortar_job_evento.is_set()


def acionar_eixo_z(nivel_z, pulsos_override=None, on_ms_override=None, off_ms_override=None):
    info = {"pulsos": 0, "tempo_ms": 0, "pino_gpio": None, "erro": ""}
    try:
        sensor = int(nivel_z)
        if not 1 <= sensor <= NIVEIS_MAX:
            raise ValueError(f"nivel fora de 1-{NIVEIS_MAX}: {sensor}")
        num_batidas = int(pulsos_override) if pulsos_override is not None else obter_pulsos_para_nivel(sensor)
        t_on = float(on_ms_override) / 1000.0 if on_ms_override is not None else TEMPO_ON_Z_S
        t_off = float(off_ms_override) / 1000.0 if off_ms_override is not None else TEMPO_OFF_Z_S
    except (TypeError, ValueError, OverflowError) as exc:
        info["erro"] = f"Parametros de Z invalidos: {exc}"
        return False, info
    info["pino_gpio"] = PINOS_GPIO_Z[sensor - 1]
    if not 1 <= num_batidas <= 500 or not 0.001 <= t_on <= 2.0 or not 0 <= t_off <= 2.0:
        info["erro"] = "Pulsos/tempos Z fora da faixa (1..500, ON 1..2000 ms, OFF 0..2000 ms)."
        return False, info

    t_inicio = time.perf_counter()
    with trava_gpio_z:
        permitido, motivo = validar_conexao_fisica_z()
        if not permitido:
            info["erro"] = motivo
            return False, info
        modo = obter_modo_z_efetivo()
        if modo == "completo" and (not GPIO_DISPONIVEL or len(reles_z) != len(PINOS_GPIO_Z)):
            info["erro"] = "GPIO fisico Z nao inicializado: acao bloqueada."
            return False, info
        if modo not in ("simulacao", "completo"):
            info["erro"] = f"Modo Z nao autorizado: {modo}"
            return False, info

        ESTADO_ESTUDIO["z_atual"] = sensor
        try:
            return _acionar_pulsos_z_protegidos(
                sensor, num_batidas, t_on, t_off, info, t_inicio, modo
            )
        finally:
            ESTADO_ESTUDIO["z_atual"] = 0


def _acionar_pulsos_z_protegidos(sensor, num_batidas, t_on, t_off, info, t_inicio, modo):
    """Somente modo COMPLETO possui ramo que chama OutputDevice.on()."""
    pulsos_realizados = 0
    if modo == "simulacao":
        for i in range(num_batidas):
            if not aguardar_liberacao_z():
                break
            time.sleep(t_on)
            pulsos_realizados += 1
            if i < num_batidas - 1:
                time.sleep(t_off)
    elif modo == "completo":
        rele = reles_z[sensor - 1]
        try:
            for i in range(num_batidas):
                if not aguardar_liberacao_z():
                    break
                # Reavaliar autorizacao imediatamente ANTES de cada pulso fisico.
                pronto, motivo = validar_conexao_fisica_z()
                if not pronto:
                    info["erro"] = motivo
                    break
                rele.on()
                try:
                    # Event.wait acorda imediatamente ao clicar Abortar, ao
                    # contrario de time.sleep(t_on), que segurava o rele ligado.
                    interrompeu = abortar_job_evento.wait(t_on)
                finally:
                    rele.off()
                if interrompeu or _job_abortar or not sistema_ativo.is_set():
                    break
                pulsos_realizados += 1
                if i < num_batidas - 1 and abortar_job_evento.wait(t_off):
                    break
        finally:
            rele.off()
    else:
        info["erro"] = "Modo Z invalido: nenhum pulso realizado."
        return False, info

    info["pulsos"] = pulsos_realizados
    info["tempo_ms"] = int((time.perf_counter() - t_inicio) * 1000)
    if pulsos_realizados != num_batidas:
        info["erro"] = info["erro"] or "Z interrompido antes do fim da sequencia de pulsos."
        return False, info
    return True, info


def acionar_sequencia_cabecote(ate_nivel=8, pulsos_por_pino=None, on_ms=None, off_ms=None):
    ate_nivel = max(1, min(NIVEIS_MAX, int(ate_nivel)))
    registrar_log(
        f"[SEQUENCIA CABECOTE] Acionando pinos na ordem {PINOS_GPIO_Z[:ate_nivel]} "
        f"(ON={(on_ms if on_ms is not None else TEMPO_ON_Z_S*1000):.1f}ms)...",
        "info",
    )
    total_pulsos = 0
    falhou = False
    t0 = time.perf_counter()
    for nv in range(1, ate_nivel + 1):
        if abortar_job_evento.is_set():
            break
        ok, info = acionar_eixo_z(nv, pulsos_override=pulsos_por_pino, on_ms_override=on_ms, off_ms_override=off_ms)
        total_pulsos += info.get("pulsos", 0)
        if not ok:
            falhou = True
        registrar_terminal(
            "Z-SEQ",
            f"Ordem #{nv} -> GPIO {PINOS_GPIO_Z[nv-1]} acionado ({info.get('pulsos', 0)} pulsos, {info.get('tempo_ms', 0)} ms)",
            "ok" if ok else "erro",
        )
        if not ok:
            break
    ESTADO_ESTUDIO["z_atual"] = 0
    dt_ms = int((time.perf_counter() - t0) * 1000)
    sucesso = not falhou and not abortar_job_evento.is_set()
    registrar_log(f"[SEQUENCIA CABECOTE] {('Concluida' if sucesso else 'Interrompida')} ({total_pulsos} pulsos em {dt_ms} ms).", "ok" if sucesso else "erro")
    return sucesso


def testar_reles_um_a_um(tempo_ligado_s=0.5):
    """Simulacao jamais percorre uma lista de objetos reais previamente inicializados."""
    if not (0.01 <= float(tempo_ligado_s) <= 2.0):
        registrar_log("Teste Z rejeitado: tempo fora de 0.01..2.0s.", "erro")
        return False
    with trava_gpio_z:
        permitido, erro = validar_conexao_fisica_z()
        if not permitido:
            registrar_log(f"Teste sequencial Z bloqueado: {erro}", "erro")
            return False
        modo = obter_modo_z_efetivo()
        if modo not in ("simulacao", "completo"):
            registrar_log(f"Teste sequencial Z rejeitado: modo {modo}.", "erro")
            return False
        if modo == "completo" and (not GPIO_DISPONIVEL or len(reles_z) != NIVEIS_MAX):
            registrar_log("Teste Z bloqueado: relés fisicos nao inicializados.", "erro")
            return False
        try:
            for i, pino in enumerate(PINOS_GPIO_Z, start=1):
                if not aguardar_liberacao_z():
                    return False
                ESTADO_ESTUDIO["z_atual"] = i
                if modo == "simulacao":
                    registrar_log(f"[TESTE SIMULADO] Z{i} GPIO {pino}: sem energia fisica.", "info")
                    time.sleep(tempo_ligado_s)
                    continue
                pronto, motivo = validar_conexao_fisica_z()
                if not pronto:
                    registrar_log(f"Teste Z interrompido: {motivo}", "erro")
                    return False
                rele = reles_z[i - 1]
                try:
                    rele.on()
                    # Teste manual Z tambem precisa desligar ao receber Abortar.
                    interrompeu = abortar_job_evento.wait(tempo_ligado_s)
                finally:
                    rele.off()
                if interrompeu or _job_abortar or not sistema_ativo.is_set():
                    return False
                if i < NIVEIS_MAX and abortar_job_evento.wait(0.3):
                    return False
        finally:
            if modo == "completo":
                for rele in reles_z:
                    rele.off()
            ESTADO_ESTUDIO["z_atual"] = 0
        registrar_log(f"Teste sequencial Z ({modo}) concluido.", "ok")
        return True


def perguntar_config_pulsos_interativo():
    print("\n" + "=" * 72)
    print("  CONFIGURAÇÃO DO CABEÇOTE Z — SEQUÊNCIA OFICIAL DOS PINOS (DA IMAGEM)")
    print(f"  Ordem exata de ativação GPIO: {PINOS_GPIO_Z}")
    print("=" * 72)
    try:
        in_on = input(f"  1) Duração do pulso LIGADO em ms [Enter = {TEMPO_ON_Z_S*1000:.1f} ms]: ").strip()
        on_ms = float(in_on.replace(",", ".")) if in_on else TEMPO_ON_Z_S * 1000.0

        in_off = input(f"  2) Intervalo DESLIGADO entre pulsos em ms [Enter = {TEMPO_OFF_Z_S*1000:.1f} ms]: ").strip()
        off_ms = float(in_off.replace(",", ".")) if in_off else TEMPO_OFF_Z_S * 1000.0

        in_pausa = input(f"  3) Pausa antes de acionar Z em ms [Enter = {PAUSA_ANTES_Z_S*1000:.1f} ms]: ").strip()
        pausa_ms = float(in_pausa.replace(",", ".")) if in_pausa else PAUSA_ANTES_Z_S * 1000.0

        atual_str = ",".join(str(obter_pulsos_para_nivel(i + 1)) for i in range(NIVEIS_MAX))
        print("  4) Quantidade de pulsos por nível (Z1 a Z8):")
        print("     - Digite 8 valores separados por vírgula (ex: 1,2,3,4,5,6,7,8 ou 2,4,6,8,10,12,14,16)")
        print(f"     - Ou digite 1 único número (ex: {NUM_PULSOS_Z}) para usar esse nº de pulsos em todos os níveis")
        in_pulsos = input(f"     Pulsos [Enter = manter {atual_str}]: ").strip()

        if in_pulsos:
            partes = [int(x.strip()) for x in in_pulsos.split(",") if x.strip()]
            if len(partes) == 1:
                configurar_parametros_pulsos_z(
                    on_ms=on_ms, off_ms=off_ms, pausa_ms=pausa_ms,
                    num_pulsos_fixo=partes[0], pulsos_por_nivel=[partes[0]] * NIVEIS_MAX, usar_fixo=True
                )
            else:
                while len(partes) < NIVEIS_MAX:
                    partes.append(partes[-1])
                configurar_parametros_pulsos_z(
                    on_ms=on_ms, off_ms=off_ms, pausa_ms=pausa_ms,
                    pulsos_por_nivel=partes[:NIVEIS_MAX], usar_fixo=False
                )
        else:
            configurar_parametros_pulsos_z(on_ms=on_ms, off_ms=off_ms, pausa_ms=pausa_ms)
    except (EOFError, KeyboardInterrupt):
        print("\nMantendo configuração padrão de pulsos.")
    except Exception as e:
        registrar_log(f"Aviso ao ler configuração interativa: {e}. Mantendo valores atuais.", "aviso")
# =============================================================================
# 2. DIAGNÓSTICO DE ALARMES, LIMITES E CONTROLADOR GRBL 1.1 + FUNÇÕES UGS
# =============================================================================
def explicar_alarme(linha):
    codigo = linha.split(":", 1)[1].strip() if ":" in linha else ""
    return f"{linha} = {ALARMES_GRBL.get(codigo, 'codigo fora da tabela, veja a doc do firmware')}"


def explicar_erro_grbl(linha):
    codigo = linha.split(":", 1)[1].strip() if ":" in linha else ""
    desc = ERROS_GRBL.get(codigo, "erro GRBL desconhecido")
    return f"{linha} ({desc})"


def pinos_ativos(status):
    m = RE_PINOS_GRBL.search(status or "")
    return m.group(1) if m else ""


def explicar_pinos(status):
    pinos = pinos_ativos(status)
    fins = "/".join(p for p in pinos if p in "XYZABC")
    outros = "".join(p for p in pinos if p not in "XYZABC")
    texto = ""
    if fins:
        texto += f" | fim de curso acionado: {fins}"
    if outros:
        texto += f" | outras entradas ativas: {outros}"
    return texto


def avaliar_status_para_mover(status):
    if not status:
        return False, "GRBL nao respondeu ao '?'"
    if not status.startswith("<Idle"):
        return False, f"GRBL nao esta Idle para movimento: {status}"
    fins_xy = "/".join(p for p in pinos_ativos(status) if p in "XY")
    if fins_xy and not IGNORAR_FIM_DE_CURSO:
        return False, f"fim de curso {fins_xy} acionado com a maquina parada"
    return True, ""


def fora_dos_limites(x_mm, y_mm):
    """Area segura de trabalho limitada pelos cantos medidos de 297 mm.

    A escala nominal de movimentacao 0..299 NAO acrescenta pontos fisicos nem
    libera deslocamentos fora da area medida sem verificacao mecanica.
    """
    try:
        u, v = _inverter_cantos(x_mm, y_mm)
    except (ValueError, TypeError, ZeroDivisionError):
        return True
    margem = 0.0002  # ~0.06 mm para arredondamentos G-code de tres casas
    return not (-margem <= u <= 1.0+margem and -margem <= v <= 1.0+margem)


def validar_cantos(tol_mm=3.0):
    """
    Confere se os 4 cantos medidos formam um retangulo coerente.
    Bordas opostas devem ter coordenadas parecidas: A/C (mesma coluna) com Y igual,
    B/D com Y igual, A/B (mesma linha) com X igual, C/D com X igual.
    Retorna lista de avisos (vazia = coerente).
    """
    avisos = []
    ok, motivo = validar_calibracao_cantos()
    if not ok:
        avisos.append(motivo)
    checks = [
        ("Y de A e B (borda inferior)", PONTO_A[1], PONTO_B[1]),
        ("Y de D e C (borda superior)", PONTO_D[1], PONTO_C[1]),
        ("X de A e D (borda esquerda)", PONTO_A[0], PONTO_D[0]),
        ("X de B e C (borda direita)", PONTO_B[0], PONTO_C[0]),
    ]
    for nome, v1, v2 in checks:
        dif = abs(v1 - v2)
        if dif > tol_mm:
            avisos.append(f"{nome} diferem {dif:.1f} mm (>{tol_mm:.0f} mm): confira a medicao ou se a maquina esta fora de esquadro.")
    return avisos


class PortaSimulada:
    """Simulador fiel ao GRBL 1.1h + todos os comandos e registradores do UGS."""
    TEMPO_MOVIMENTO_S = 0.05

    def __init__(self, papel="grbl"):
        self.papel = papel
        self.fila = deque([("Grbl 1.1h ['$' for help]", 0.0)])
        self.is_open = True
        self.ocupado_ate = 0.0
        self.estado = "Idle"
        self.mpos = [0.0, 0.0, 0.0]
        self.wco = [0.0, 0.0, 0.0]
        self.g28_pos = [0.0, 0.0, 0.0]
        self.g30_pos = [0.0, 0.0, 0.0]
        self.wcs_ativo = "G54"
        self.wcs_offsets = {
            "G54": [0.0, 0.0, 0.0],
            "G55": [0.0, 0.0, 0.0],
            "G56": [0.0, 0.0, 0.0],
            "G57": [0.0, 0.0, 0.0],
            "G58": [0.0, 0.0, 0.0],
            "G59": [0.0, 0.0, 0.0],
        }
        self.modo_distancia = "G90"
        self.unidades = "G21"
        self.feed_atual = FEED_RATE
        self.override_pct = 100
        self.rapid_override_pct = 100
        self.spindle_override_pct = 100
        self.check_mode = False
        # Chaves VIRTUAIS apenas no simulador: testes reproduziveis sem acionar hardware.
        self.home_simulado_x = -3.0
        self.home_simulado_y = 3.0
        self.home_simulado_y_sentido = 1  # -1: chave virtual em Y negativo
        self.settings = {
            0: ("10", "Step pulse time, microseconds"),
            1: ("25", "Step idle delay, milliseconds"),
            2: ("0", "Step pulse invert, mask"),
            3: ("0", "Step direction invert, mask"),
            4: ("0", "Invert step enable pin, boolean"),
            5: ("0", "Invert limit pins, boolean"),
            6: ("0", "Invert probe pin, boolean"),
            10: ("1", "Status report options, mask"),
            11: ("0.010", "Junction deviation, millimeters"),
            12: ("0.002", "Arc tolerance, millimeters"),
            13: ("0", "Report in inches, boolean"),
            20: ("0", "Soft limits enable, boolean"),
            21: ("0", "Hard limits enable, boolean"),
            22: ("1", "Homing cycle enable, boolean"),
            23: ("3", "Homing direction invert, mask"),
            24: ("25.000", "Homing locate feed rate, mm/min"),
            25: ("500.000", "Homing search seek rate, mm/min"),
            26: ("250", "Homing switch debounce delay, ms"),
            27: ("2.000", "Homing switch pull-off distance, mm"),
            30: ("1000", "Maximum spindle speed, RPM"),
            31: ("0", "Minimum spindle speed, RPM"),
            32: ("0", "Laser-mode enable, boolean"),
            100: ("80.000", "X-axis travel resolution, step/mm"),
            101: ("80.000", "Y-axis travel resolution, step/mm"),
            102: ("80.000", "Z-axis travel resolution, step/mm"),
            110: ("2000.000", "X-axis maximum rate, mm/min"),
            111: ("2000.000", "Y-axis maximum rate, mm/min"),
            112: ("1000.000", "Z-axis maximum rate, mm/min"),
            120: ("100.000", "X-axis acceleration, mm/sec^2"),
            121: ("100.000", "Y-axis acceleration, mm/sec^2"),
            122: ("50.000", "Z-axis acceleration, mm/sec^2"),
            130: ("297.000", "X-axis maximum travel, millimeters"),
            131: ("297.000", "Y-axis maximum travel, millimeters"),
            132: ("50.000", "Z-axis maximum travel, millimeters"),
        }

    def write(self, dados):
        agora = time.perf_counter()
        if dados == b"?":
            if self.check_mode:
                self.estado = "Check"
            elif self.estado not in ("Hold", "Alarm", "Sleep"):
                self.estado = "Run" if agora < self.ocupado_ate else "Idle"
            wx = self.mpos[0] - self.wco[0]
            wy = self.mpos[1] - self.wco[1]
            wz = self.mpos[2] - self.wco[2]
            fs = int(self.feed_atual * self.override_pct / 100.0) if self.estado in ("Run", "Jog") else 0
            y_chave_ativa = (
                self.mpos[1] <= self.home_simulado_y
                if self.home_simulado_y_sentido < 0
                else self.mpos[1] >= self.home_simulado_y
            )
            pn_simulado = (
                ("X" if self.mpos[0] <= self.home_simulado_x else "") +
                ("Y" if y_chave_ativa else "")
            )
            campo_pn = f"|Pn:{pn_simulado}" if pn_simulado else ""
            status = (
                f"<{self.estado}|MPos:{self.mpos[0]:.3f},{self.mpos[1]:.3f},{self.mpos[2]:.3f}|"
                f"WPos:{wx:.3f},{wy:.3f},{wz:.3f}|WCO:{self.wco[0]:.3f},{self.wco[1]:.3f},{self.wco[2]:.3f}|"
                f"FS:{fs},0|Ov:{self.override_pct},{self.rapid_override_pct},{self.spindle_override_pct}{campo_pn}>"
            )
            self.fila.append((status, 0.0))
            return
        if dados == b"!":
            self.estado = "Hold"
            self.ocupado_ate = 0.0
            return
        if dados == b"~":
            self.estado = "Check" if self.check_mode else "Idle"
            return
        if dados == b"\x85":
            self.ocupado_ate = 0.0
            if self.estado == "Jog":
                self.estado = "Idle"
            return
        if dados == b"\x90":
            self.override_pct = 100
            return
        if dados == b"\x91":
            self.override_pct = min(200, self.override_pct + 10)
            return
        if dados == b"\x92":
            self.override_pct = max(10, self.override_pct - 10)
            return
        if dados == b"\x93":
            self.override_pct = min(200, self.override_pct + 1)
            return
        if dados == b"\x94":
            self.override_pct = max(10, self.override_pct - 1)
            return
        if dados == b"\x95":
            self.rapid_override_pct = 100
            return
        if dados == b"\x96":
            self.rapid_override_pct = 50
            return
        if dados == b"\x97":
            self.rapid_override_pct = 25
            return
        if dados == b"\x18":
            self.fila.clear()
            self.check_mode = False
            self.estado = "Idle"
            self.ocupado_ate = 0.0
            self.fila.append(("Grbl 1.1h ['$' for help]", 0.0))
            return

        for linha in dados.decode(errors="ignore").splitlines():
            texto = linha.strip()
            if not texto:
                continue
            up = texto.upper()

            if up == "$":
                self.fila.append(("[HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H ~ ! ? ctrl-x]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$$":
                for k in sorted(self.settings.keys()):
                    val, desc = self.settings[k]
                    self.fila.append((f"${k}={val} ({desc})", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$#":
                for wcs_k in ("G54", "G55", "G56", "G57", "G58", "G59"):
                    v = self.wcs_offsets[wcs_k]
                    self.fila.append((f"[{wcs_k}:{v[0]:.3f},{v[1]:.3f},{v[2]:.3f}]", 0.0))
                self.fila.append((f"[G28:{self.g28_pos[0]:.3f},{self.g28_pos[1]:.3f},{self.g28_pos[2]:.3f}]", 0.0))
                self.fila.append((f"[G30:{self.g30_pos[0]:.3f},{self.g30_pos[1]:.3f},{self.g30_pos[2]:.3f}]", 0.0))
                self.fila.append((f"[G92:{self.wco[0]:.3f},{self.wco[1]:.3f},{self.wco[2]:.3f}]", 0.0))
                self.fila.append(("[TLO:0.000]", 0.0))
                self.fila.append(("[PRB:0.000,0.000,0.000:0]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$G":
                self.fila.append((f"[GC:G0 {self.wcs_ativo} G17 {self.unidades} {self.modo_distancia} G94 M5 M9 T0 F{self.feed_atual:g} S0]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$I":
                self.fila.append(("[VER:1.1h.20190825:SMART_MATRIX_EESC_USP]", 0.0))
                self.fila.append(("[OPT:V,15,128]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$N":
                self.fila.append(("$N0=G21 G90", 0.0))
                self.fila.append(("$N1=", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$X":
                self.estado = "Idle"
                self.fila.append(("[MSG:Caution: Unlocked]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$C":
                self.check_mode = not self.check_mode
                self.estado = "Check" if self.check_mode else "Idle"
                self.fila.append((f"[MSG:Check Mode {'Enabled' if self.check_mode else 'Disabled'}]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$SLP":
                self.estado = "Sleep"
                self.fila.append(("[MSG:Sleeping]", 0.0))
                self.fila.append(("ok", 0.0))
                continue
            if up == "$H":
                self.mpos = [0.0, 0.0, 0.0]
                self.wco = [0.0, 0.0, 0.0]
                self.estado = "Idle"
                self.ocupado_ate = agora + 0.1
                self.fila.append(("ok", self.ocupado_ate))
                continue

            m_set = re.match(r"^\$(\d+)\s*=\s*(-?[\d.]+)$", up)
            if m_set:
                cod = int(m_set.group(1))
                val = m_set.group(2)
                desc = self.settings.get(cod, ("", "Custom GRBL setting"))[1]
                self.settings[cod] = (val, desc)
                self.fila.append(("ok", 0.0))
                continue

            if up in ("G54", "G55", "G56", "G57", "G58", "G59"):
                self.wcs_ativo = up
                self.wco = list(self.wcs_offsets[up])
                self.fila.append(("ok", 0.0))
                continue

            if "G90" in up and not up.startswith("$J="):
                self.modo_distancia = "G90"
            if "G91" in up and not up.startswith("$J="):
                self.modo_distancia = "G91"
            if "G20" in up:
                self.unidades = "G20"
            if "G21" in up:
                self.unidades = "G21"

            if up == "G28.1":
                self.g28_pos = list(self.mpos)
                self.fila.append(("ok", 0.0))
                continue
            if up == "G28":
                self.mpos = list(self.g28_pos)
                self.ocupado_ate = agora + self.TEMPO_MOVIMENTO_S
                self.fila.append(("ok", 0.0))
                continue
            if up == "G30.1":
                self.g30_pos = list(self.mpos)
                self.fila.append(("ok", 0.0))
                continue
            if up == "G30":
                self.mpos = list(self.g30_pos)
                self.ocupado_ate = agora + self.TEMPO_MOVIMENTO_S
                self.fila.append(("ok", 0.0))
                continue

            if up.startswith(("G10 P0 L20", "G10 L20 P0", "G10 L20 P1", "G92")):
                mx = re.search(r"X(-?[\d.]+)", up)
                my = re.search(r"Y(-?[\d.]+)", up)
                mz = re.search(r"Z(-?[\d.]+)", up)
                if mx:
                    self.wco[0] = self.mpos[0] - float(mx.group(1))
                if my:
                    self.wco[1] = self.mpos[1] - float(my.group(1))
                if mz:
                    self.wco[2] = self.mpos[2] - float(mz.group(1))
                self.wcs_offsets[self.wcs_ativo] = list(self.wco)
                self.fila.append(("ok", 0.0))
                continue

            if up.startswith("$J="):
                corpo_jog = up[3:]
                inc = "G91" in corpo_jog or (self.modo_distancia == "G91" and "G90" not in corpo_jog)
                mx = re.search(r"X(-?[\d.]+)", corpo_jog)
                my = re.search(r"Y(-?[\d.]+)", corpo_jog)
                mz = re.search(r"Z(-?[\d.]+)", corpo_jog)
                mf = re.search(r"F([\d.]+)", corpo_jog)
                if mf:
                    self.feed_atual = float(mf.group(1))
                if mx:
                    v = float(mx.group(1))
                    self.mpos[0] = (self.mpos[0] + v) if inc else (v + self.wco[0])
                if my:
                    v = float(my.group(1))
                    self.mpos[1] = (self.mpos[1] + v) if inc else (v + self.wco[1])
                if mz:
                    v = float(mz.group(1))
                    self.mpos[2] = (self.mpos[2] + v) if inc else (v + self.wco[2])
                self.ocupado_ate = max(agora, self.ocupado_ate) + self.TEMPO_MOVIMENTO_S
                self.fila.append(("ok", 0.0))
                continue

            if re.search(r"\bG0?[01]\b", up):
                mx = re.search(r"X(-?[\d.]+)", up)
                my = re.search(r"Y(-?[\d.]+)", up)
                mz = re.search(r"Z(-?[\d.]+)", up)
                mf = re.search(r"F([\d.]+)", up)
                if mx or my or mz:
                    inc = "G91" in up or (self.modo_distancia == "G91" and "G90" not in up)
                    if mx:
                        v = float(mx.group(1))
                        self.mpos[0] = (self.mpos[0] + v) if inc else (v + self.wco[0])
                    if my:
                        v = float(my.group(1))
                        self.mpos[1] = (self.mpos[1] + v) if inc else (v + self.wco[1])
                    if mz:
                        v = float(mz.group(1))
                        self.mpos[2] = (self.mpos[2] + v) if inc else (v + self.wco[2])
                    if mf:
                        self.feed_atual = float(mf.group(1))
                    self.ocupado_ate = max(agora, self.ocupado_ate) + self.TEMPO_MOVIMENTO_S

            liberar_em = self.ocupado_ate if up.startswith("G4") else 0.0
            self.fila.append(("ok", liberar_em))

    def readline(self):
        if self.fila:
            texto, liberar_em = self.fila[0]
            espera = liberar_em - time.perf_counter()
            if espera > TIMEOUT_LEITURA_SERIAL_S:
                time.sleep(TIMEOUT_LEITURA_SERIAL_S)
                return b""
            if espera > 0:
                time.sleep(espera)
            self.fila.popleft()
            return (texto + "\r\n").encode()
        time.sleep(TIMEOUT_LEITURA_SERIAL_S)
        return b""

    def reset_input_buffer(self):
        self.fila.clear()

    def close(self):
        self.is_open = False


class ControladorGRBL:
    """Controlador GRBL 1.1 completo com todas as funções do UGS + Calibração de Home + Console Serial."""

    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.estado = "IDLE"
        self.mpos = [0.0, 0.0, 0.0]
        self.wpos = [0.0, 0.0, 0.0]
        self.wco = [0.0, 0.0, 0.0]
        self.wcs_ativo = "G54"
        self.feed_override = 100
        self.rapid_override = 100
        self.spindle_override = 100
        self.fs_atual = [0.0, 0.0]
        self.pinos_pn = ""
        self.ultimo_status_raw = ""
        self.porta_atual = PORTA_GRBL
        self.conexao_real = False
        self.modo_simulacao = False
        # Referencia XY eh valida somente ate reset, alarme ou reconexao da USB.
        self.home_xy_referenciado = False
        self.home_xy_estado = {
            "ativo": False, "concluido": False, "fase": "Nao referenciado",
            "eixo": "", "erro": "", "passos": 0,
        }
        self.config_eeprom = {}
        self.offsets_wcs = {}
        self.estado_parser = "G0 G54 G17 G21 G90 G94"

    def listar_portas_disponiveis(self):
        portas = ["/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyUSB0", "SIMULADO"]
        if serial is not None and hasattr(serial, "tools"):
            try:
                for p in serial.tools.list_ports.comports():
                    if p.device not in portas:
                        portas.insert(0, p.device)
            except Exception:
                pass
        return portas

    def conectar(self, porta=None, baud=None):
        global SIMULADO
        porta = (porta or PORTA_GRBL).strip()
        baud = baud or BAUD
        bloquear_z_fisico("Troca/reconexao da porta GRBL")
        self.invalidar_home_xy("Conexao USB reiniciada")
        with self.lock:
            if self.ser and getattr(self.ser, "is_open", False):
                try:
                    self.ser.close()
                except Exception:
                    pass

            self.conexao_real = False
            self.modo_simulacao = False
            if porta.upper() == "SIMULADO" or SIMULADO:
                self.porta_atual = "SIMULADO"
                self.modo_simulacao = True
                self.ser = PortaSimulada("grbl")
                registrar_log("Conexao GRBL em Modo Simulado (UGS Ready).", "info")
            elif serial is None:
                self.ser = None
                self.porta_atual = "DESCONECTADO"
                registrar_log("pyserial ausente: conexao fisica indisponivel. Sem fallback para simulacao.", "erro")
                return False
            else:
                candidatas = [porta]
                for p_alt in ("/dev/ttyACM0", "/dev/ttyACM1", "/dev/ttyACM2", "/dev/ttyUSB0", "/dev/ttyUSB1"):
                    if p_alt not in candidatas and os.path.exists(p_alt):
                        candidatas.append(p_alt)
                conectou_real = False
                ultimo_erro = ""
                for p_tentativa in candidatas:
                    try:
                        try:
                            self.ser = serial.Serial(p_tentativa, baud, timeout=TIMEOUT_LEITURA_SERIAL_S, exclusive=True)
                        except TypeError:
                            self.ser = serial.Serial(p_tentativa, baud, timeout=TIMEOUT_LEITURA_SERIAL_S)
                        self.porta_atual = p_tentativa
                        self.conexao_real = True
                        conectou_real = True
                        if p_tentativa != porta:
                            registrar_log(f"Porta {porta} mudou para {p_tentativa}! Conectado automaticamente em {p_tentativa}.", "ok")
                        else:
                            registrar_log(f"Porta serial {p_tentativa} aberta ({baud} bps).", "ok")
                        registrar_terminal("SYS", f"Conectado em {p_tentativa} @ {baud} bps", "ok")
                        break
                    except Exception as e:
                        ultimo_erro = str(e)
                if not conectou_real:
                    self.porta_atual = "DESCONECTADO"
                    self.ser = None
                    self.conexao_real = False
                    registrar_log(f"Porta GRBL indisponivel ({ultimo_erro}). JOB BLOQUEADO; reconecte ou execute --simulado.", "erro")
                    registrar_terminal("SYS", "Falha USB: nenhum movimento sera simulado automaticamente.", "erro")
                    return False

            t0 = time.perf_counter()
            if self._aguardar_grbl_pronto_nolock(TIMEOUT_BOOT_GRBL_S):
                registrar_log(f"GRBL respondeu em {time.perf_counter() - t0:.2f}s.", "ok")
            else:
                registrar_log("Aviso: GRBL nao mandou banner nem respondeu '?'. Tentando acordar...", "aviso")
                self.ser.write(b"\r\n\r\n")
                time.sleep(0.5)
            self.ser.reset_input_buffer()

        status_grbl = self.preparar_grbl()
        registrar_log(f"Status do GRBL: {status_grbl or 'sem resposta'}{explicar_pinos(status_grbl)}", "info")
        pronto, motivo = avaliar_status_para_mover(status_grbl)
        if not pronto:
            registrar_log(f"ATENCAO: {motivo}. Do jeito que esta, a trajetoria nao vai comecar.", "aviso")
            if "fim de curso" in motivo:
                registrar_log(DICA_FIM_DE_CURSO, "aviso")
        return bool(pronto and status_grbl.startswith("<Idle"))

    def _ler_linha_nolock(self, limite):
        pedaco = b""
        while sistema_ativo.is_set():
            dados = self.ser.readline()
            if dados:
                pedaco += dados
                if pedaco.endswith(b"\n"):
                    return pedaco.decode(errors="ignore").strip()
            elif time.perf_counter() >= limite:
                break
        return ""

    def _aguardar_grbl_pronto_nolock(self, timeout=TIMEOUT_BOOT_GRBL_S):
        inicio = time.perf_counter()
        limite = inicio + timeout
        prox_sonda = inicio + 0.2
        while sistema_ativo.is_set() and time.perf_counter() < limite:
            linha = self._ler_linha_nolock(min(limite, prox_sonda))
            if "Grbl" in linha or linha.startswith("<"):
                if "Grbl" in linha:
                    registrar_terminal("RX", linha, "ok")
                if linha.startswith("<"):
                    self._parse_status(linha)
                return True
            if time.perf_counter() >= prox_sonda:
                self.ser.write(b"?")
                prox_sonda = time.perf_counter() + 0.25
        return False

    def ler_status_grbl(self, timeout=1.2):
        if not self.ser:
            return ""
        with self.lock:
            return self._ler_status_nolock(timeout)

    def _ler_status_nolock(self, timeout=0.5):
        try:
            self.ser.write(b"?")
            limite = time.perf_counter() + timeout
            while sistema_ativo.is_set() and time.perf_counter() < limite:
                linha = self._ler_linha_nolock(limite)
                if linha.startswith("<"):
                    self._parse_status(linha)
                    return linha
                if linha.startswith("ALARM"):
                    self.invalidar_home_xy(f"GRBL informou {linha}")
                    self.estado = "ALARM"
                    msg = explicar_alarme(linha)
                    registrar_log(f"GRBL avisou: {msg}", "erro")
                    registrar_terminal("RX", msg, "erro")
                    return linha
        except Exception as e:
            if "readiness to read" in str(e) or "Input/output error" in str(e) or "Errno 5" in str(e):
                try:
                    self.ser.close()
                except Exception:
                    pass
        return ""

    def atualizar_status(self):
        self.ler_status_grbl(timeout=0.25)

    def _parse_status(self, linha):
        self.ultimo_status_raw = linha
        if linha.startswith(("<Alarm", "<Sleep")):
            self.invalidar_home_xy(f"GRBL informou {linha.split('|', 1)[0]}")
        partes = linha.strip("<>").split("|")
        if partes:
            self.estado = partes[0].split(":")[0].upper()
        self.pinos_pn = pinos_ativos(linha)
        m_wco = RE_WCO.search(linha)
        if m_wco:
            self.wco = [float(m_wco.group(1)), float(m_wco.group(2)), float(m_wco.group(3))]
        m_mpos = RE_MPOS.search(linha)
        m_wpos = RE_WPOS.search(linha)
        if m_mpos:
            self.mpos = [float(m_mpos.group(1)), float(m_mpos.group(2)), float(m_mpos.group(3))]
            if not m_wpos:
                self.wpos = [self.mpos[i] - self.wco[i] for i in range(3)]
        if m_wpos:
            self.wpos = [float(m_wpos.group(1)), float(m_wpos.group(2)), float(m_wpos.group(3))]
            if not m_mpos:
                self.mpos = [self.wpos[i] + self.wco[i] for i in range(3)]
        m_fs = RE_FS_GRBL.search(linha)
        if m_fs:
            self.fs_atual = [float(m_fs.group(1)), float(m_fs.group(2))]
        m_ov = RE_OV_GRBL.search(linha)
        if m_ov:
            self.feed_override = int(m_ov.group(1))
            self.rapid_override = int(m_ov.group(2))
            self.spindle_override = int(m_ov.group(3))

    def preparar_grbl(self):
        """Nunca desbloqueia alarmes, ativa modo Check ou simula movimento silenciosamente."""
        if not self.ser or (self.conexao_real == self.modo_simulacao) or (SIMULADO and self.conexao_real):
            registrar_log("Conexao GRBL ausente ou estados fisico/simulado inconsistentes.", "erro")
            return ""
        status = self.ler_status_grbl()
        pronto, motivo = avaliar_status_para_mover(status)
        if not pronto:
            registrar_log(f"GRBL nao esta pronto: {motivo}; corrija manualmente.", "erro")
            return ""
        for comando in ("G21", "G90", "G54"):
            ok, msg = self.enviar_comando(comando, logar=False)
            if not ok:
                registrar_log(f"GRBL recusou {comando}: {msg}", "erro")
                return ""
        status = self.ler_status_grbl()
        return status if avaliar_status_para_mover(status)[0] else ""

    def enviar_byte_tempo_real(self, byte_cmd, descricao=""):
        if not self.ser:
            return False
        try:
            self.ser.write(byte_cmd)
            if descricao:
                registrar_log(descricao, "info")
                registrar_terminal("RT", descricao, "info")
            return True
        except Exception:
            return False

    def enviar_override_tempo_real(self, tipo):
        mapa = {
            "feed_100": (b"\x90", "Feed Override: 100% (0x90)"),
            "feed_+10": (b"\x91", "Feed Override: +10% (0x91)"),
            "feed_-10": (b"\x92", "Feed Override: -10% (0x92)"),
            "feed_+1": (b"\x93", "Feed Override: +1% (0x93)"),
            "feed_-1": (b"\x94", "Feed Override: -1% (0x94)"),
            "rapid_100": (b"\x95", "Rapid Override: 100% (0x95)"),
            "rapid_50": (b"\x96", "Rapid Override: 50% (0x96)"),
            "rapid_25": (b"\x97", "Rapid Override: 25% (0x97)"),
            "jog_cancel": (b"\x85", "Jog Cancel em Tempo Real (0x85)"),
        }
        if tipo in mapa:
            b_cmd, desc = mapa[tipo]
            ok = self.enviar_byte_tempo_real(b_cmd, desc)
            self.atualizar_status()
            return ok
        return False

    def cancelar_jog(self):
        return self.enviar_override_tempo_real("jog_cancel")

    def soft_reset(self):
        bloquear_z_fisico("Soft reset do controlador GRBL")
        self.invalidar_home_xy("Soft reset GRBL")
        if not self.ser:
            return False
        registrar_terminal("TX", "Ctrl+X (0x18 Soft-Reset)", "aviso")
        with self.lock:
            try:
                self.ser.write(b"\x18")
                limite = time.perf_counter() + 2.5
                while sistema_ativo.is_set() and time.perf_counter() < limite:
                    linha = self._ler_linha_nolock(limite)
                    if linha:
                        registrar_terminal("RX", linha, "info")
                    if "Grbl" in linha:
                        break
                self.ser.reset_input_buffer()
            except Exception as e:
                registrar_log(f"Erro no Soft-Reset: {e}", "erro")
                return False
        registrar_log("Soft-Reset (Ctrl+X) enviado ao GRBL.", "aviso")
        self.atualizar_status()
        return True

    def parar_grbl(self):
        """Parada por software imediata: prioridade aos bytes em tempo real.

        NAO pega self.lock nem espera serial/status. Pode interromper leitura
        e comandos longos em outra thread. Exige E-stop eletrico independente.
        Ctrl+X invalida toda referencia XY do GRBL.
        """
        self.invalidar_home_xy("Abortar/Reset GRBL por comando em tempo real")
        porta = self.ser
        if porta is None:
            registrar_log("Abortar: GRBL desconectado; use a parada fisica.", "erro")
            return False
        try:
            porta.write(b"!")
            porta.write(b"\x18")
        except Exception as exc:
            registrar_log(f"FALHA ao enviar abortamento serial: {exc}; use a parada fisica.", "erro")
            return False
        registrar_terminal("RT", "Abortar: Feed Hold (!) + Ctrl+X enviados sem espera serial", "aviso")
        return True

    def enviar_comando(self, comando, timeout=None, logar=True, registrar_no_terminal=True):
        if not self.ser:
            return False, "Desconectado"
        cmd_limpo = comando.strip()
        if not cmd_limpo:
            return True, ""
        if cmd_limpo.upper() == "$H":
            return False, "Homing $H exige eixo Z nesse firmware. Use Home XY por chaves no SmartV42."
        # Todas as entradas GRBL (inclusive terminal e jog) obedecem a separacao do Z GPIO.
        if re.search(r"Z\s*[+-]?(?:\d|\.)", cmd_limpo.upper()):
            return False, "GRBL bloqueou coordenada cartesiana Z; use comandos discretos Z1..Z8."

        timeout = TIMEOUT_OK_GRBL if timeout is None else timeout
        if registrar_no_terminal:
            registrar_terminal("TX", f">>> {cmd_limpo}", "cmd")

        linhas_resposta = []
        with self.lock:
            try:
                self.ser.write((cmd_limpo + "\n").encode())
                limite = time.perf_counter() + timeout
                while sistema_ativo.is_set():
                    if time.perf_counter() > limite:
                        msg_to = f"TIMEOUT esperando 'ok' do GRBL para '{cmd_limpo}'"
                        if registrar_no_terminal:
                            registrar_terminal("RX", msg_to, "erro")
                        return False, msg_to
                    linha = self._ler_linha_nolock(limite)
                    if not linha:
                        continue
                    if linha.startswith("<"):
                        self._parse_status(linha)
                        continue
                    if linha == "ok":
                        if re.match(r"^(?:G10\b|G92\b|G92\.1\b|\$X\b|\$2[012]=)", cmd_limpo, re.I):
                            self.invalidar_home_xy("Referencia alterada por comando GRBL externo")
                        if registrar_no_terminal:
                            registrar_terminal("RX", "ok", "ok")
                        if logar:
                            registrar_log(f"Comando {cmd_limpo} executado (ok).", "ok")
                        return True, "\n".join(linhas_resposta)
                    if linha.startswith("ALARM"):
                        self.invalidar_home_xy(f"GRBL retornou {linha}")
                        msg = f"GRBL retornou {explicar_alarme(linha)}"
                        registrar_log(msg, "erro")
                        if registrar_no_terminal:
                            registrar_terminal("RX", msg, "erro")
                        return False, msg
                    if linha.startswith("error"):
                        msg = explicar_erro_grbl(linha)
                        registrar_log(f"GRBL retornou '{msg}' ({cmd_limpo})", "erro")
                        if registrar_no_terminal:
                            registrar_terminal("RX", msg, "erro")
                        return False, msg

                    linhas_resposta.append(linha)
                    if registrar_no_terminal:
                        registrar_terminal("RX", linha, "info")
                    m_cfg = re.match(r"^\$(\d+)=([-\d.]+)", linha)
                    if m_cfg:
                        self.config_eeprom[f"${m_cfg.group(1)}"] = m_cfg.group(2)
                    if linha.startswith("[GC:"):
                        self.estado_parser = linha.strip("[]")[3:]
            except Exception as e:
                if registrar_no_terminal:
                    registrar_terminal("RX", f"Erro serial: {e}", "erro")
                return False, str(e)
        return False, "interrompido"

    def consultar_configuracoes_grbl(self):
        ok, resp = self.enviar_comando("$$", timeout=5.0, logar=True)
        return ok, resp, dict(self.config_eeprom)

    def gravar_parametro_grbl(self, codigo, valor):
        chave = str(codigo).strip()
        if not chave.startswith("$"):
            chave = f"${chave}"
        cmd = f"{chave}={valor}"
        ok, resp = self.enviar_comando(cmd, timeout=5.0, logar=True)
        if ok:
            self.config_eeprom[chave] = str(valor)
        return ok, resp

    def executar_jog_ugs(self, dx=0.0, dy=0.0, dz=0.0, feed=None):
        """
        Executa Jog incremental no padrão oficial do UGS / GRBL 1.1 ($J=G91 G21 X... Y... Z... F...),
        respeitando INVERTER_JOG_X / INVERTER_JOG_Y caso o operador queira inverter o sentido das setas.
        """
        if abs(dz) > 1e-6:
            return False, "Eixo Z nao e cartesiano: use Z1..Z8 pelo GPIO."
        if not self.ser or (self.conexao_real == self.modo_simulacao) or (SIMULADO and self.conexao_real):
            return False, "Conexao GRBL ausente ou inconsistente para jog."
        estado = self.ler_status_grbl()
        if not estado.startswith("<Idle"):
            return False, f"Jog bloqueado; estado GRBL: {estado}"
        vel = JOG_FEED_RATE if feed is None else float(feed)
        dx_real = -dx if INVERTER_JOG_X else dx
        dy_real = -dy if INVERTER_JOG_Y else dy
        partes = ["$J=G91 G21"]
        if abs(dx_real) > 1e-6:
            partes.append(f"X{dx_real:.3f}")
        if abs(dy_real) > 1e-6:
            partes.append(f"Y{dy_real:.3f}")
        if len(partes) == 1:
            return True, "Sem deslocamento"
        partes.append(f"F{vel:g}")
        cmd_jog = " ".join(partes)
        ok, resp = self.enviar_comando(cmd_jog, timeout=5.0, logar=True)
        self.atualizar_status()
        return ok, resp

    def aguardar_grbl_idle(self, timeout=None):
        timeout = TIMEOUT_IDLE_GRBL if timeout is None else timeout
        limite = time.perf_counter() + timeout
        while sistema_ativo.is_set() and not abortar_job_evento.is_set():
            if _job_pausado and _job_em_execucao:
                time.sleep(0.05)
                limite += 0.05
                continue
            if time.perf_counter() > limite:
                registrar_log(f"TIMEOUT ({timeout:.0f}s) esperando GRBL ficar Idle.", "erro")
                return False, "GRBL nao ficou Idle"
            status = self.ler_status_grbl()
            if status.startswith("<Alarm"):
                registrar_log(f"GRBL em alarme: {status}{explicar_pinos(status)}", "erro")
                return False, "GRBL em alarme"
            if status.startswith("<Idle"):
                return True, ""
            if status.startswith(("<Check", "<Hold", "<Door", "<Sleep")):
                return False, f"Estado nao executavel: {status}"
            time.sleep(INTERVALO_POLL_IDLE_S)
        return False, "interrompido"

    def mover_xy_e_aguardar(self, x_mm, y_mm, timeout=None, feed=None):
        if not self.modo_simulacao and not self.home_xy_referenciado:
            return False, "sem referencia XY", "Execute Home XY por chaves antes de movimentos absolutos."
        if not self.ser or (self.conexao_real == self.modo_simulacao) or (SIMULADO and self.conexao_real):
            return False, "Desconectado", "Conexao GRBL fisica/simulada ausente ou inconsistente"
        if fora_dos_limites(x_mm, y_mm):
            return (
                False, "alvo fora dos limites",
                f"Alvo X={x_mm:.3f} Y={y_mm:.3f} fora dos quatro cantos calibrados. Movimento nao enviado.",
            )
        status_antes = self.ler_status_grbl(timeout=0.7)
        pronto, motivo = avaliar_status_para_mover(status_antes)
        if not pronto:
            return False, "GRBL nao pronto", motivo
        vel = FEED_RATE if feed is None else feed
        if timeout is None:
            dist = math.hypot(x_mm - self.wpos[0], y_mm - self.wpos[1])
            t_est = (dist / max(1.0, vel)) * 60.0
            timeout = max(TIMEOUT_IDLE_GRBL, t_est * FATOR_TIMEOUT_MOV + MARGEM_TIMEOUT_MOV_S)

        comando = f"G90 G1 X{x_mm:.3f} Y{y_mm:.3f} F{vel:g}"
        respostas = 0
        erro = ""
        with self.lock:
            try:
                self.ser.write(f"{comando}\nG4 P0\n".encode())
                limite = time.perf_counter() + timeout
                while respostas < 2:
                    if abortar_job_evento.is_set() or not sistema_ativo.is_set():
                        return False, "interrompido", f"Interrompido durante: {comando}"
                    if _job_pausado and _job_em_execucao:
                        time.sleep(0.05)
                        limite += 0.05
                        continue
                    if time.perf_counter() > limite:
                        if respostas == 0:
                            return (
                                False,
                                "GRBL nao aceitou o movimento",
                                f"TIMEOUT esperando 'ok' do GRBL para o comando: {comando}",
                            )
                        return (
                            False,
                            "GRBL nao ficou Idle",
                            f"TIMEOUT ({timeout:.0f}s) esperando o fim do movimento: {comando}",
                        )
                    linha = self._ler_linha_nolock(limite)
                    if not linha:
                        continue
                    if linha.startswith("<"):
                        self._parse_status(linha)
                        continue
                    if linha == "ok":
                        respostas += 1
                    elif linha.startswith("error"):
                        respostas += 1
                        erro = erro or f"GRBL retornou '{explicar_erro_grbl(linha)}' para o comando: {comando}"
                    elif linha.startswith("ALARM"):
                        status_grbl = self._ler_status_nolock()
                        return (
                            False,
                            f"GRBL em alarme ({linha})",
                            f"GRBL em alarme: {explicar_alarme(linha)} | comando: {comando} | "
                            f"status: {status_grbl or 'sem resposta'}{explicar_pinos(status_grbl)}",
                        )
            except Exception as e:
                return False, "GRBL nao aceitou o movimento", f"Erro na comunicacao XY: {e}"

        if erro:
            return False, "GRBL nao aceitou o movimento", erro

        if CONFIRMAR_IDLE:
            ok_idle, st_idle = self.aguardar_grbl_idle(timeout)
            if not ok_idle:
                return False, st_idle, f"Falha ao confirmar Idle apos {comando}: {st_idle}"

        self.wpos[0], self.wpos[1] = x_mm, y_mm
        return True, "ok", ""

    def mover_xy_sincrono(self, x_mm, y_mm, feed=None, timeout=None):
        ok, status, detalhe = self.mover_xy_e_aguardar(x_mm, y_mm, timeout=timeout, feed=feed)
        if not ok and detalhe:
            registrar_log(detalhe, "erro")
        return ok, status

    def voltar_origem(self, pos=None):
        # Retorna ao Ponto A (origem da matriz: 0.000, 0.000).
        alvo_x, alvo_y = float(PONTO_A[0]), float(PONTO_A[1])
        pos_ref = pos or (self.wpos[0], self.wpos[1])
        dist = math.hypot(alvo_x - pos_ref[0], alvo_y - pos_ref[1])
        timeout = max(TIMEOUT_IDLE_GRBL, dist / FEED_RATE * 60.0 * FATOR_TIMEOUT_MOV + MARGEM_TIMEOUT_MOV_S)
        t0 = time.perf_counter()
        registrar_terminal("TX", f">>> Ir ao Ponto A (G90 G1 X{alvo_x:.3f} Y{alvo_y:.3f})", "cmd")
        ok, status, detalhe = self.mover_xy_e_aguardar(alvo_x, alvo_y, timeout=timeout)
        if ok:
            registrar_log(f"Cabecote no Ponto A ({alvo_x:.2f}, {alvo_y:.2f}) em {time.perf_counter() - t0:.2f}s.", "ok")
            return True, ""
        registrar_log(detalhe, "erro")
        return False, status

    def zerar_eixo(self, eixo="XY", valor_x=0.0, valor_y=0.0, valor_z=0.0):
        # Origem de trabalho e definida APENAS apos as chaves X/Y e recuo de 2 mm.
        registrar_log("Zeramento manual/G92 desativado: use Home XY pelas chaves.", "erro")
        return False


    def invalidar_home_xy(self, motivo="Referencia XY invalidada"):
        """Invalida referencia e permissão de Z sem adquirir lock GPIO sob lock serial."""
        global MODO_Z_FISICO_HABILITADO
        self.home_xy_referenciado = False
        MODO_Z_FISICO_HABILITADO = False
        estado = getattr(self, "home_xy_estado", None)
        if estado is not None:
            estado.update({"concluido": False})
            if not estado.get("ativo"):
                estado.update({"fase": "Nao referenciado", "erro": motivo})

    def _estado_home_xy(self, fase, eixo="", passos=None):
        self.home_xy_estado.update({"fase": fase, "eixo": eixo})
        if passos is not None:
            self.home_xy_estado["passos"] = passos

    def _status_home_xy(self):
        """Aceita perda transitoria de status sem confundir Pn vazio com falha.

        Cada jog e limitado em mm; o status somente confirma contato e fim do
        passo. Tres leituras curtas evitam abortar a rotina por uma unica
        resposta USB atrasada, sem ignorar ALARM ou abortamento do operador.
        """
        for tentativa in range(3):
            if _job_abortar or abortar_job_evento.is_set() or not sistema_ativo.is_set():
                raise RuntimeError("Home XY cancelado pelo operador.")
            if _job_pausado:
                raise RuntimeError("Home XY interrompido pelo Hold.")
            status = self.ler_status_grbl(timeout=0.5)
            if status:
                if not status.startswith(("<Idle", "<Jog", "<Run")):
                    raise RuntimeError(f"Estado GRBL nao permite Home XY: {status}")
                return status
            if tentativa < 2:
                time.sleep(0.08)
        raise RuntimeError(
            "Sem resposta do status GRBL apos 3 consultas '?' (ate ~1,7 s). "
            "Confira USB/alimentacao: leitura de Pn:X/Pn:Y nao confirmada. "
            f"Ultimo status valido: {self.ultimo_status_raw or 'nenhum'}."
        )

    def _jog_passo_xy_home(self, deslocamentos, feed, passo_max=5.0,
                          detectar_contato=True):
        """Jog incremental XY; 0x85 cancela ambos os eixos em caso de contato."""
        if not deslocamentos or any(
            eixo not in ("X", "Y") or not math.isfinite(delta)
            or abs(delta) <= 1e-7 or abs(delta) > passo_max + 1e-6
            for eixo, delta in deslocamentos.items()
        ):
            raise RuntimeError("Passo de Home XY invalido ou excede o limite.")
        estado = self._status_home_xy()
        if not estado.startswith("<Idle"):
            raise RuntimeError("GRBL nao esta Idle antes do passo de Home XY.")
        antes = dict(zip(("X", "Y"), self.mpos[:2]))
        partes = [f"{eixo}{deslocamentos[eixo]:.3f}" for eixo in ("X", "Y")
                  if eixo in deslocamentos]
        comando = f"$J=G91 G21 {' '.join(partes)} F{feed:.1f}"
        if len(deslocamentos) == 2 and not getattr(self, "_home_duplo_registrado", False):
            registrar_log(
                "HOME XY COMANDO CONJUNTO: " + comando +
                " (um jog GRBL com X e Y no mesmo bloco)", "info"
            )
            self._home_duplo_registrado = True
        ok, msg = self.enviar_comando(comando, timeout=5.0, logar=False)
        if not ok:
            raise RuntimeError(f"GRBL rejeitou jog de Home XY: {msg}")
        comprimento = math.hypot(deslocamentos.get("X", 0.0),
                                 deslocamentos.get("Y", 0.0))
        prazo = time.monotonic() + max(5.0, comprimento * 60.0 / feed + 4.0)
        contatos = set()
        movimento_observado = False
        cancelado = False
        while time.monotonic() < prazo:
            estado = self._status_home_xy()
            if estado.startswith(("<Jog", "<Run")):
                movimento_observado = True
            if any(abs(self.mpos[0 if eixo == "X" else 1] - antes[eixo]) >= 0.005
                   for eixo in deslocamentos):
                movimento_observado = True
            if detectar_contato:
                novos = set(deslocamentos).intersection(pinos_ativos(estado))
                contatos.update(novos)
                if novos and not cancelado and estado.startswith(("<Jog", "<Run")):
                    if not self.enviar_byte_tempo_real(
                        b"\x85", f"Home XY: contato {','.join(sorted(novos))}, cancelar jog"
                    ):
                        raise RuntimeError("Falhou o cancelamento de jog no contato.")
                    cancelado = True
            if estado.startswith("<Idle") and movimento_observado:
                if detectar_contato:
                    contatos.update(set(deslocamentos).intersection(pinos_ativos(estado)))
                return contatos, estado
            # Consultas mais frequentes durante o segmento longo reduzem a
            # latencia ate o cancelamento por contato. Nao e um hard limit.
            time.sleep(0.05)
        self.enviar_byte_tempo_real(b"\x85", "Home XY: timeout, cancelar jog")
        raise RuntimeError("Timeout esperando parar jog do Home XY.")

    def _jog_passo_home_xy(self, eixo, deslocamento, feed, passo_max=5.0,
                          detectar_contato=True):
        """Jog de um eixo, inclusive recuo de 5 mm."""
        contatos, estado = self._jog_passo_xy_home(
            {eixo: deslocamento}, feed, passo_max, detectar_contato
        )
        return eixo in contatos, estado

    def _confirmar_chave_liberada_home(self, eixo):
        """Confirma chave livre em duas leituras Idle consecutivas.

        Aguarda ate ~2 segundos apenas consultando o GRBL, sem mover nenhum
        motor. Isso tolera status serial atrasado ou bounce do contato, mas
        nunca presume liberacao se Pn continuar ativo.
        """
        amostras = []
        livres_seguidas = 0
        estado = ""
        for tentativa in range(12):
            if tentativa:
                time.sleep(0.15)
            estado = self._status_home_xy()
            if not estado.startswith("<Idle"):
                raise RuntimeError(
                    f"GRBL saiu de Idle durante verificacao da chave {eixo}: {estado}"
                )
            pn = pinos_ativos(estado)
            amostras.append(pn or "livre")
            livres_seguidas = livres_seguidas + 1 if eixo not in pn else 0
            if livres_seguidas >= 2:
                return True, estado, amostras
        return False, estado, amostras

    def _recuar_chave_home(self, eixo, direcao, feed, recuo, fase):
        """Recuo supervisionado: valida Pn e movimento INFORMADO pelo GRBL.

        MPos e posicao de passos comandados, nao medicao de deslocamento
        mecanico por encoder. Mesmo com MPos correto, confira motor, acoplamento
        e chave fisica antes de repetir o Home.
        """
        self._estado_home_xy(fase, eixo)
        self._status_home_xy()
        indice = 0 if eixo == "X" else 1
        posicao_antes = self.mpos[indice]
        deslocamento = -direcao * recuo
        registrar_log(
            f"HOME XY RECUO {eixo}: busca no sentido {direcao:+d}; "
            f"afastamento comandado {eixo}{deslocamento:+.3f} mm, F{feed:.1f}.", "info"
        )
        self._jog_passo_home_xy(eixo, deslocamento, feed, passo_max=recuo,
                                detectar_contato=False)
        liberada, estado, amostras = self._confirmar_chave_liberada_home(eixo)
        deslocamento_grbl = self.mpos[indice] - posicao_antes
        pn = pinos_ativos(estado)
        diagnostico = (f"Comando: {eixo}{deslocamento:+.2f} mm; "
                       f"MPos informado: {deslocamento_grbl:+.2f} mm; "
                       f"Pn final:{pn or 'livre'}; "
                       f"leituras Pn: {' / '.join(amostras)}; "
                       f"estado: {estado.split('|', 1)[0]}.")
        if abs(deslocamento_grbl - deslocamento) > 0.35:
            raise RuntimeError(
                f"Recuo {eixo} nao confirmado pela posicao reportada do GRBL. "
                f"{diagnostico} Pare e verifique comandos, configuracao do eixo e mecanica."
            )
        if not liberada:
            raise RuntimeError(
                f"Chave {eixo} nao liberou apos recuo de {recuo:.1f} mm. "
                f"{diagnostico} O GRBL segue detectando o sensor. Se a chave "
                "solta manualmente, verifique se o acionador ainda a pressiona "
                f"apos os {recuo:.1f} mm (folga, curso de acionamento e posicionamento). "
                "Confira o espaco livre e o sentido antes de repetir. "
                + "Home XY interrompido antes de concluir a referencia."
            )
        registrar_log(f"Home XY: chave {eixo} liberada em leituras consecutivas. {diagnostico}", "ok")

    def _recuar_chaves_home(self, eixos, direcoes, feed, recuos, fase):
        """Recua juntas as duas chaves quando ambas estiverem acionadas.

        Se so uma chave foi detectada, preserva recuo exclusivamente daquele
        eixo. Validacao de Pn e MPos permanece individual e obrigatoria.
        """
        eixos = tuple(e for e in ("X", "Y") if e in eixos)
        if not eixos:
            raise RuntimeError("Recuo Home sem eixo selecionado.")
        if len(eixos) == 1:
            eixo = eixos[0]
            self._recuar_chave_home(eixo, direcoes[eixo], feed, recuos[eixo], fase)
            return
        self._estado_home_xy(fase + " (X e Y juntos)", "XY")
        estado = self._status_home_xy()
        if not estado.startswith("<Idle"):
            raise RuntimeError("GRBL nao esta Idle antes do recuo XY.")
        antes = {"X": self.mpos[0], "Y": self.mpos[1]}
        deslocamentos = {e: -direcoes[e] * recuos[e] for e in eixos}
        registrar_log(
            "HOME XY RECUO CONJUNTO: " +
            " ".join(f"{e}{deslocamentos[e]:+.3f}" for e in eixos) +
            f" F{feed:.1f}", "info",
        )
        self._jog_passo_xy_home(
            deslocamentos, feed, passo_max=max(recuos.values()), detectar_contato=False
        )
        falhas = []
        for eixo in eixos:
            liberada, estado, leituras = self._confirmar_chave_liberada_home(eixo)
            delta = self.mpos[0 if eixo == "X" else 1] - antes[eixo]
            esperado = deslocamentos[eixo]
            registrar_log(
                f"Recuo {eixo}: comando {esperado:+.2f} mm, "
                f"MPos reportado {delta:+.2f} mm, "
                f"Pn: {' / '.join(leituras)}", "info",
            )
            if abs(delta - esperado) > 0.35:
                falhas.append(
                    f"{eixo}: MPos {delta:+.2f} mm, esperado {esperado:+.2f} mm"
                )
            if not liberada:
                falhas.append(
                    f"Chave {eixo} nao liberou apos recuo de {recuos[eixo]:.1f} mm; "
                    f"MPos informado {delta:+.2f} mm; "
                    f"Pn final:{pinos_ativos(estado) or 'livre'}"
                )
        if falhas:
            raise RuntimeError(
                "Recuo conjunto Home XY nao confirmado: " +
                "; ".join(falhas) +
                ". Confira fisicamente os sentidos e o acionamento das chaves. "
                "Home XY interrompido sem referencia valida."
            )
        registrar_log("Home XY: ambas as chaves liberadas apos recuo conjunto.", "ok")

    def _buscar_xy_em_fluxo_home(self, pendentes, direcoes, cursos, percorrido, passo, feed):
        """Jog com fila curta: GRBL planeja blocos sucessivos sem esperar Idle.

        O GRBL 1.1 aceita comandos $J enquanto esta em Jog. A fila mantem
        aproximadamente dois trechos de movimento, sempre sujeitos a Pn
        e cancelamento imediato de toda a fila no primeiro contato.
        """
        pendentes = tuple(pendentes)
        estado = self._status_home_xy()
        if not estado.startswith("<Idle"):
            raise RuntimeError("GRBL precisa estar Idle para iniciar fluxo Home XY.")
        inicio = {e: self.mpos[0 if e == "X" else 1] for e in pendentes}
        saldos = {e: cursos[e] - percorrido[e] for e in pendentes}
        planejado = {e: 0.0 for e in pendentes}
        if any(v <= 1e-6 for v in saldos.values()):
            return set(), estado

        limite_fila = 1.5 * passo
        distancia_maxima = math.hypot(*(saldos[e] for e in pendentes))
        prazo = time.monotonic() + max(
            12.0, 3.0 * distancia_maxima * 60.0 / feed + 12.0
        )
        ultimo_log = 0.0
        algum_jog = False

        while time.monotonic() < prazo:
            estado = self._status_home_xy()
            contatos = set(pendentes).intersection(pinos_ativos(estado))
            if contatos:
                registrar_log(
                    "HOME XY SENSOR EM MOVIMENTO: " + "/".join(sorted(contatos)) +
                    "; cancelando fila de jogs antes do recuo.", "info"
                )
                if not self.enviar_byte_tempo_real(
                    b"\x85", "Home XY: contato detectado, cancelar todos os jogs"
                ):
                    raise RuntimeError("Falha ao cancelar jog do Home no contato.")
                limite_parada = time.monotonic() + 6.0
                while time.monotonic() < limite_parada:
                    estado = self._status_home_xy()
                    if estado.startswith("<Idle"):
                        break
                    time.sleep(0.04)
                else:
                    raise RuntimeError("GRBL nao ficou Idle apos cancelar jog no sensor.")
                confirmados = set(pendentes).intersection(pinos_ativos(estado))
                if not contatos.issubset(confirmados):
                    raise RuntimeError(
                        "Contato de " + "/".join(sorted(contatos)) +
                        " nao persistiu apos cancelar o jog. Verifique "
                        "deceleracao, largura do acionador e leitura Pn."
                    )
                return confirmados, estado

            progresso = {
                e: max(0.0, direcoes[e] *
                       (self.mpos[0 if e == "X" else 1] - inicio[e]))
                for e in pendentes
            }
            pendencia = max(planejado[e] - progresso[e] for e in pendentes)
            planejado_ate_limite = any(
                saldos[e] - planejado[e] <= 1e-6 for e in pendentes
            )
            if planejado_ate_limite and estado.startswith("<Idle") and all(
                progresso[e] >= planejado[e] - 0.06 for e in pendentes
            ):
                return set(), estado

            if not planejado_ate_limite and pendencia <= limite_fila - passo * 0.5:
                deslocamentos = {
                    e: direcoes[e] * min(passo, saldos[e] - planejado[e])
                    for e in pendentes
                }
                comando = "$J=G91 G21 " + " ".join(
                    f"{e}{deslocamentos[e]:.3f}" for e in pendentes
                ) + f" F{feed:.1f}"
                if len(pendentes) == 2 and not getattr(self, "_home_duplo_registrado", False):
                    registrar_log(
                        "HOME XY FLUXO CONJUNTO: " + comando +
                        " (X/Y juntos, sem esperar Idle a cada jog)", "info"
                    )
                    self._home_duplo_registrado = True
                ok, resposta = self.enviar_comando(
                    comando, timeout=5.0, logar=False
                )
                if not ok:
                    raise RuntimeError(
                        f"GRBL rejeitou fluxo de Jog Home XY: {resposta}"
                    )
                algum_jog = True
                for e in pendentes:
                    planejado[e] += abs(deslocamentos[e])
                continue

            if not algum_jog:
                raise RuntimeError("Fluxo Home XY nao iniciou movimentacao.")
            agora = time.monotonic()
            if agora - ultimo_log > 1.5:
                self._estado_home_xy(
                    "Busca continua " + "/".join(pendentes) + ": " +
                    ", ".join(
                        f"{e}={percorrido[e] + progresso[e]:.1f}/{cursos[e]:.1f} mm"
                        for e in pendentes
                    ), "/".join(pendentes)
                )
                ultimo_log = agora
            time.sleep(0.04)

        self.enviar_byte_tempo_real(b"\x85", "Timeout do fluxo Home XY: cancelar jog")
        raise RuntimeError(
            "Timeout no fluxo Home XY: GRBL nao confirmou final/cancelamento."
        )

    def _home_xy_busca_sincronizada(self, direcoes, cursos, passo, feed, recuos):
        """Busca X/Y juntos; ao achar uma chave, recua seu eixo e o retira da busca."""
        estado = self._status_home_xy()
        if not estado.startswith("<Idle"):
            raise RuntimeError("GRBL precisa estar Idle antes da busca XY.")
        pinos_iniciais = pinos_ativos(estado)
        registrar_log(
            "Home XY: inicio em " + estado.split("|", 1)[0] +
            "; chaves iniciais: " + (pinos_iniciais or "livres") +
            ". Comandos conjuntos exigem ambos os eixos sem chave pressionada.",
            "info",
        )
        iniciais_acionadas = {e for e in ("X", "Y") if e in pinos_iniciais}
        if iniciais_acionadas:
            self._recuar_chaves_home(
                iniciais_acionadas, direcoes, feed, recuos,
                "Liberando chaves inicialmente acionadas",
            )
            estado = self._status_home_xy()

        percorrido = {"X": 0.0, "Y": 0.0}
        encontrados = set()
        while len(encontrados) < 2:
            pendentes = tuple(e for e in ("X", "Y") if e not in encontrados)
            for eixo in pendentes:
                if cursos[eixo] - percorrido[eixo] < 1e-6:
                    raise RuntimeError(
                        f"Busca do eixo {eixo} encerrada por limite: "
                        f"{percorrido[eixo]:.1f}/{cursos[eixo]:.1f} mm percorridos "
                        f"sem detectar Pn:{eixo}. "
                        f"Eixos ja encontrados: {('/'.join(sorted(encontrados)) or 'nenhum')}. "
                        f"Confira 'Busca max {eixo}' e o sentido fisico."
                    )
            self._estado_home_xy(
                "Iniciando busca continua " + "/".join(pendentes),
                "/".join(pendentes),
            )
            antes = {e: self.mpos[0 if e == "X" else 1] for e in pendentes}
            contatos, estado = self._buscar_xy_em_fluxo_home(
                pendentes, direcoes, cursos, percorrido, passo, feed
            )
            for eixo in pendentes:
                delta = self.mpos[0 if eixo == "X" else 1] - antes[eixo]
                percorrido[eixo] += min(
                    cursos[eixo] - percorrido[eixo], abs(delta)
                )
            if not contatos:
                eixo = next(
                    (e for e in pendentes if percorrido[e] >= cursos[e] - 0.1),
                    pendentes[0],
                )
                raise RuntimeError(
                    f"Busca do eixo {eixo} encerrada por limite: "
                    f"{percorrido[eixo]:.1f}/{cursos[eixo]:.1f} mm percorridos "
                    f"sem detectar Pn:{eixo}. "
                    f"Eixos ja encontrados: {('/'.join(sorted(encontrados)) or 'nenhum')}. "
                    f"Confira 'Busca max {eixo}' e o sentido fisico."
                )
            self._recuar_chaves_home(
                contatos, direcoes, feed, recuos,
                "Recuando " + "/".join(sorted(contatos)) +
                " apos cancelar e esvaziar a fila de jog",
            )
            encontrados.update(contatos)
            registrar_log(
                "HOME XY: chaves encontradas e liberadas " +
                "/".join(sorted(encontrados)) + "; pendente(s): " +
                ("/".join(e for e in ("X", "Y") if e not in encontrados) or "nenhum"),
                "info",
            )

    def executar_home_xy_chaves(self, direcao_x, direcao_y, curso_x, curso_y,
                               coord_x=0.0, coord_y=0.0, passo=5.0, feed=500.0,
                               recuo=5.0, seguranca_confirmada=False, recuo_y=None):
        """Referencia X e Y por Pn: via USB; nunca executa $H nem move Z."""
        bloquear_z_fisico("Home XY em andamento: Z fisico proibido")
        self.invalidar_home_xy("Iniciando nova referencia XY")
        self._home_duplo_registrado = False
        self._home_duplo_mov_registrado = False
        self.home_xy_estado.update({
            "ativo": True, "concluido": False, "erro": "",
            "fase": "Validando configuracao GRBL", "eixo": "", "passos": 0,
        })
        try:
            if not seguranca_confirmada:
                raise ValueError("Confirme parada de emergencia fisica independente antes de iniciar.")
            if not self.ser or (self.conexao_real == self.modo_simulacao) or (SIMULADO and self.conexao_real):
                raise RuntimeError("Conexao GRBL inconsistente: Home XY bloqueado.")
            if direcao_x not in (-1, 1) or direcao_y not in (-1, 1):
                raise ValueError("Defina expressamente a direcao de busca X e Y em coordenadas GRBL.")
            if recuo_y is None:
                recuo_y = recuo
            for nome, valor in (("curso X", curso_x), ("curso Y", curso_y), ("coordenada X", coord_x),
                                ("coordenada Y", coord_y), ("passo", passo), ("feed", feed),
                                ("recuo X", recuo), ("recuo Y", recuo_y)):
                if not math.isfinite(valor):
                    raise ValueError(f"{nome} deve ser finito.")
            if not 0.1 <= passo <= 10.0 or not 20.0 <= feed <= 600.0:
                raise ValueError("Home requer passo 0.1..10 mm e feed 20..600 mm/min.")
            if not 1.0 <= curso_x <= 300.0 or not 1.0 <= curso_y <= 300.0:
                raise ValueError("Cursos MAXIMOS de BUSCA devem estar em 1..300 mm; recuo apos contato permanece 5 mm.")
            if not 2.0 <= recuo <= 10.0 or not 2.0 <= recuo_y <= 10.0:
                raise ValueError(
                    "Recuos X/Y devem estar entre 2 e 10 mm. "
                    "Verifique fisicamente o espaco livre antes de aumentar."
                )
            recuos = {"X": recuo, "Y": recuo_y}
            if fora_dos_limites(coord_x, coord_y):
                raise ValueError("Posicao declarada apos o recuo nao pertence aos limites A/B/C/D.")

            ok_cfg, msg_cfg, cfg = self.consultar_configuracoes_grbl()
            if not ok_cfg or "$21" not in cfg or "$22" not in cfg:
                raise RuntimeError(f"Nao foi possivel confirmar $21 e $22 do firmware: {msg_cfg}")
            if float(cfg["$22"]) != 0.0:
                raise RuntimeError("Desative o homing do firmware ($22=0), conferindo pelo comando $ antes.")
            if float(cfg["$21"]) != 0.0:
                raise RuntimeError(
                    "$21=1 causaria ALARM nas chaves X/Y. Home por software requer $21=0, "
                    "com parada fisica independente. O programa NAO desabilita limites automaticamente."
                )
            if float(cfg.get("$20", "0")) != 0:
                raise RuntimeError("$20=1 (soft limits) depende de referencia valida do GRBL; revise $20.")
            self._status_home_xy()
            registrar_log(
                f"Home XY CONFIG: X {'+' if direcao_x > 0 else '-'}, "
                f"Y {'+' if direcao_y > 0 else '-'}, "
                f"F{feed:.1f} mm/min, segmento={passo:.1f} mm por eixo, busca max "
                f"X={curso_x:.1f} mm Y={curso_y:.1f} mm, "
                f"recuo apos contato X={recuo:.1f}, Y={recuo_y:.1f} mm.", "info"
            )
            self._home_xy_busca_sincronizada(
                {"X": direcao_x, "Y": direcao_y},
                {"X": curso_x, "Y": curso_y}, passo, feed, recuos
            )
            if _job_abortar or abortar_job_evento.is_set():
                raise RuntimeError("Home cancelado antes de gravar origem.")
            estado = self._status_home_xy()
            if not estado.startswith("<Idle") or any(eixo in pinos_ativos(estado) for eixo in "XY"):
                raise RuntimeError("Home XY nao pode definir origem com fins de curso acionados.")
            self._estado_home_xy("Gravando origem G54 (sem G92)")
            # Limpa offsets temporarios G92 legados, depois registra somente G54.
            for comando in ("G21", "G90", "G54", "G92.1",
                            f"G10 L20 P1 X{coord_x:.3f} Y{coord_y:.3f}"):
                ok, msg = self.enviar_comando(comando, timeout=5.0, logar=False)
                if not ok:
                    raise RuntimeError(f"GRBL recusou {comando}: {msg}")
            estado = self._status_home_xy()
            if abs(self.wpos[0] - coord_x) > 0.15 or abs(self.wpos[1] - coord_y) > 0.15:
                raise RuntimeError(
                    f"Origem nao confirmada pelo GRBL: WPos={self.wpos[:2]}, esperado=({coord_x},{coord_y})."
                )
            self.home_xy_referenciado = True
            self.home_xy_estado.update({"concluido": True, "fase": "Home XY concluido", "erro": "", "eixo": ""})
            msg = (f"Home XY realizado: chaves X/Y detectadas, recuo "
                   f"X={recuo:.1f} mm, Y={recuo_y:.1f} mm; "
                   f"G54 definido em X{coord_x:.3f} Y{coord_y:.3f}.")
            registrar_log(msg, "ok")
            return True, msg
        except Exception as exc:
            self.home_xy_referenciado = False
            erro = str(exc)
            self.home_xy_estado.update({"concluido": False, "fase": "Home XY interrompido", "erro": erro})
            registrar_log(f"HOME XY NAO CONCLUIDO: {erro}", "erro")
            # O jog eh finito; um cancelamento adicional evita que a proxima etapa prossiga.
            try:
                if self.ser and not (_job_abortar or abortar_job_evento.is_set()):
                    self.enviar_byte_tempo_real(b"\x85", "Home XY interrompido: cancelar jog")
            except Exception:
                pass
            return False, erro
        finally:
            self.home_xy_estado["ativo"] = False

    def executar_homing(self):
        registrar_log("Home $H desabilitado nesta instalacao: exige Z. Use Home XY por chaves.", "erro")
        return False

    def calibrar_home(self, *args, **kwargs):
        registrar_log("Calibracao manual e homing $H desativados; execute Home XY automatico.", "erro")
        return False


    def ir_para_g28(self):
        ok, _ = self.enviar_comando("G28")
        if ok:
            self.aguardar_grbl_idle()
            self.atualizar_status()
        return ok

    def salvar_g28(self):
        ok, _ = self.enviar_comando("G28.1")
        if ok:
            registrar_log("Posição atual salva como Home Secundário G28 (G28.1)!", "ok")
        return ok

    def ir_para_g30(self):
        ok, _ = self.enviar_comando("G30")
        if ok:
            self.aguardar_grbl_idle()
            self.atualizar_status()
        return ok

    def salvar_g30(self):
        ok, _ = self.enviar_comando("G30.1")
        if ok:
            registrar_log("Posição atual salva como Estacionamento G30 (G30.1)!", "ok")
        return ok

    def selecionar_wcs(self, wcs="G54"):
        wcs = wcs.strip().upper()
        if wcs in ("G54", "G55", "G56", "G57", "G58", "G59"):
            ok, _ = self.enviar_comando(wcs)
            if ok:
                self.wcs_ativo = wcs
                self.atualizar_status()
            return ok
        return False

    def alternar_modo_check(self):
        ok, _ = self.enviar_comando("$C")
        self.atualizar_status()
        return ok

    def acionar_sleep(self):
        ok, _ = self.enviar_comando("$SLP")
        self.atualizar_status()
        return ok


def configurar_sistema_coordenadas(
    origem_x_na_direita=None, sinal_eixo_x=None, sinal_eixo_y=None,
    inverter_jog_x=None, inverter_jog_y=None, limite_x_mm=None,
    limite_y_mm=None, x_inicio_fisico=None, y_inicio_fisico=None,
    x_fim_fisico=None, y_fim_fisico=None,
    ponto_a=None, ponto_b=None, ponto_c=None, ponto_d=None,
):
    """Calibracao atomica. Mudar sentido de jog nunca altera geometria A/B/C/D."""
    global ORIGEM_X_NA_DIREITA, SINAL_EIXO_X, SINAL_EIXO_Y
    global INVERTER_JOG_X, INVERTER_JOG_Y, LIMITE_X_MM, LIMITE_Y_MM
    global PONTO_A, PONTO_B, PONTO_C, PONTO_D
    global X_INICIO_FISICO, Y_INICIO_FISICO, X_FIM_FISICO, Y_FIM_FISICO

    candidatos = [
        list(PONTO_A if ponto_a is None else ponto_a),
        list(PONTO_B if ponto_b is None else ponto_b),
        list(PONTO_C if ponto_c is None else ponto_c),
        list(PONTO_D if ponto_d is None else ponto_d),
    ]
    ok, motivo = validar_calibracao_cantos(candidatos)
    if not ok:
        raise ValueError(f"Calibracao recusada: {motivo}")
    candidatos = [[float(x), float(y)] for x,y in candidatos]
    if limite_x_mm is not None and (not math.isfinite(float(limite_x_mm)) or float(limite_x_mm) < EXTENSAO_UTIL_MALHA_MM):
        raise ValueError("Limite X precisa cobrir os 297 mm medidos.")
    if limite_y_mm is not None and (not math.isfinite(float(limite_y_mm)) or float(limite_y_mm) < EXTENSAO_UTIL_MALHA_MM):
        raise ValueError("Limite Y precisa cobrir os 297 mm medidos.")
    if any(v is not None for v in (x_inicio_fisico,y_inicio_fisico,x_fim_fisico,y_fim_fisico)):
        raise ValueError("Limites fisicos legados desativados: use somente os quatro cantos A/B/C/D.")
    # Os quatro cantos descrevem diretamente a posicao fisica no sistema G54.
    PONTO_A, PONTO_B, PONTO_C, PONTO_D = candidatos
    X_INICIO_FISICO, Y_INICIO_FISICO = PONTO_A
    X_FIM_FISICO, Y_FIM_FISICO = PONTO_C
    if origem_x_na_direita is not None:
        ORIGEM_X_NA_DIREITA = bool(origem_x_na_direita)
    if sinal_eixo_x is not None:
        SINAL_EIXO_X = -1.0 if float(sinal_eixo_x) < 0 else 1.0
    if sinal_eixo_y is not None:
        SINAL_EIXO_Y = -1.0 if float(sinal_eixo_y) < 0 else 1.0
    if inverter_jog_x is not None:
        INVERTER_JOG_X = bool(inverter_jog_x)
    if inverter_jog_y is not None:
        INVERTER_JOG_Y = bool(inverter_jog_y)
    if limite_x_mm is not None:
        LIMITE_X_MM = float(limite_x_mm)
    if limite_y_mm is not None:
        LIMITE_Y_MM = float(limite_y_mm)

    grbl = globals().get("_grbl_global")
    if grbl is not None:
        bloquear_z_fisico("Calibracao ou orientacao XY alterada")
        grbl.invalidar_home_xy("Calibracao/orientacao modificada: execute Home XY novamente.")
    salvar_calibracao_matriz()
    est = obter_estado_coordenadas()
    registrar_log(
        f"Malha 100x100, passo ~3 mm: cantos A={PONTO_A}, B={PONTO_B}, C={PONTO_C}, D={PONTO_D}",
        "ok",
    )
    return est


def executar_comando_terminal_integrado(texto_cmd, ctrl_grbl, ctrl_reset):
    cmd = (texto_cmd or "").strip()
    if not cmd:
        return {"ok": True, "resposta": ""}

    up = cmd.upper()

    if up in ("HELP", "AJUDA", "?HELP"):
        ajuda = (
            "=== COMANDOS DO TERMINAL INTEGRADO (UGS + SMART MATRIX) ===\n"
            "  [GRBL / UGS]  $$ (Config EEPROM) | $# (Offsets G54/G28/G30) | $G (Modais) | $I (Info) | $N (Startup)\n"
            "                $H (Homing) | $X (Destravar) | $C (Check Mode) | $SLP (Sleep) | RESET / CTRL+X\n"
            "                $3=0..7 (Inverter direcao dos motores X/Y na EEPROM do GRBL)\n"
            "  [ORIGEM 0,0]  ORIGEM_DIREITA (Define ponto 0,0 na extremidade direita da matriz)\n"
            "                ORIGEM_ESQUERDA (Define ponto 0,0 na extremidade esquerda da matriz)\n"
            "                SINAL_X=-1 ou SINAL_X=1 (Define se para ir para a esquerda o GRBL usa X- ou X+)\n"
            "                HOME_CAL (Desativado: use Home XY automatico)\n"
            "  [PINOS Z]     PINOS (Mostra sequencia GPIO [5, 6, 16, 27, 23, 17, 24, 25] e pulsos)\n"
            "                Z1 a Z8 (Dispara nivel Z) | TESTE_Z (Testa reles 1 a 8) | SEQ_Z (Sequencia 1..8)\n"
            "                MS=10 ou MS=15,10,10 (Configura duracao do pulso ON, OFF e Pausa em ms)\n"
            "                PULSOS=7 (Define no de pulsos padrao para todos os niveis)\n"
            "                PULSOS_NIVEL=1,2,3,4,5,6,7,8 (Define quantos pulsos em cada nivel Z1..Z8)"
        )
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        for l in ajuda.splitlines():
            registrar_terminal("SYS", l, "info")
        return {"ok": True, "resposta": ajuda}

    if up in ("ORIGEM_DIREITA", "ORIGEM=DIREITA"):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        cfg_c = configurar_sistema_coordenadas(origem_x_na_direita=True)
        if ESTADO_ESTUDIO["matriz"]:
            compilar_matriz_para_estudio(
                np.array(ESTADO_ESTUDIO["matriz"], dtype=np.uint8),
                ESTADO_ESTUDIO["resumo"].get("modo_operacao", "Matriz Atual"),
            )
        return {"ok": True, "resposta": "Origem (0,0) definida na EXTREMIDADE DIREITA da matriz.", "coordenadas": cfg_c, "atualizar_matriz": True}

    if up in ("ORIGEM_ESQUERDA", "ORIGEM=ESQUERDA"):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        cfg_c = configurar_sistema_coordenadas(origem_x_na_direita=False)
        if ESTADO_ESTUDIO["matriz"]:
            compilar_matriz_para_estudio(
                np.array(ESTADO_ESTUDIO["matriz"], dtype=np.uint8),
                ESTADO_ESTUDIO["resumo"].get("modo_operacao", "Matriz Atual"),
            )
        return {"ok": True, "resposta": "Origem (0,0) definida na EXTREMIDADE ESQUERDA da matriz.", "coordenadas": cfg_c, "atualizar_matriz": True}

    if up.startswith("SINAL_X="):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        val = float(cmd.split("=", 1)[1].strip())
        cfg_c = configurar_sistema_coordenadas(sinal_eixo_x=val)
        if ESTADO_ESTUDIO["matriz"]:
            compilar_matriz_para_estudio(
                np.array(ESTADO_ESTUDIO["matriz"], dtype=np.uint8),
                ESTADO_ESTUDIO["resumo"].get("modo_operacao", "Matriz Atual"),
            )
        return {"ok": True, "resposta": f"Sinal do Eixo X ajustado para {cfg_c['sinal_eixo_x']}.", "coordenadas": cfg_c, "atualizar_matriz": True}

    if up == "PINOS":
        registrar_terminal("TX", ">>> PINOS", "cmd")
        cfg = obter_estado_config_z()
        mapa_str = " | ".join(
            f"Z{i+1}->GPIO {cfg['pinos_gpio_z'][i]} ({cfg['pulsos_por_nivel'][i]} pulsos)"
            for i in range(NIVEIS_MAX)
        )
        msg = (
            f"Ordem Oficial Cabeçote: {cfg['pinos_gpio_z']} | "
            f"Pulso ON={cfg['z_on_ms']}ms, OFF={cfg['z_off_ms']}ms, Pausa={cfg['pausa_z_ms']}ms | "
            f"{mapa_str}"
        )
        registrar_terminal("SYS", msg, "ok")
        return {"ok": True, "resposta": msg, "config_z": cfg}

    if up in ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7", "Z8"):
        nv = int(up[1])
        registrar_terminal("TX", f">>> {up} (GPIO {PINOS_GPIO_Z[nv-1]})", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Z manual bloqueado durante job."}
        abortar_job_evento.clear()
        ok, info = acionar_eixo_z(nv)
        msg = f"Nivel Z{nv} (GPIO {info['pino_gpio']}) acionado: {info['pulsos']} pulsos em {info['tempo_ms']} ms"
        registrar_terminal("RX", msg, "ok" if ok else "erro")
        return {"ok": ok, "resposta": msg}

    if up in ("TESTE_Z", "T"):
        registrar_terminal("TX", f">>> {up}", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Teste Z bloqueado durante job."}
        abortar_job_evento.clear()
        ok_teste = testar_reles_um_a_um()
        return {"ok": bool(ok_teste), "resposta": "Teste dos 8 reles concluido." if ok_teste else "Teste Z bloqueado/interrompido."}

    if up == "SEQ_Z":
        registrar_terminal("TX", ">>> SEQ_Z", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Sequencia Z bloqueada durante job."}
        abortar_job_evento.clear()
        ok_seq = acionar_sequencia_cabecote(ate_nivel=NIVEIS_MAX)
        return {"ok": bool(ok_seq), "resposta": f"Sequencia {PINOS_GPIO_Z} concluida." if ok_seq else "Sequencia Z interrompida."}

    if up.startswith(("MS=", "ON_MS=")):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        val_str = cmd.split("=", 1)[1].strip()
        partes = [float(x.strip()) for x in val_str.split(",") if x.strip()]
        on_ms = partes[0] if len(partes) >= 1 else None
        off_ms = partes[1] if len(partes) >= 2 else on_ms
        pausa_ms = partes[2] if len(partes) >= 3 else None
        cfg = configurar_parametros_pulsos_z(on_ms=on_ms, off_ms=off_ms, pausa_ms=pausa_ms)
        return {"ok": True, "resposta": "Tempo de pulso (ms) atualizado.", "config_z": cfg, "atualizar_matriz": True}

    if up.startswith("OFF_MS="):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        off_ms = float(cmd.split("=", 1)[1].strip())
        cfg = configurar_parametros_pulsos_z(off_ms=off_ms)
        return {"ok": True, "resposta": "Tempo OFF (ms) atualizado.", "config_z": cfg, "atualizar_matriz": True}

    if up.startswith("PAUSA_MS="):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        pausa_ms = float(cmd.split("=", 1)[1].strip())
        cfg = configurar_parametros_pulsos_z(pausa_ms=pausa_ms)
        return {"ok": True, "resposta": "Pausa antes de Z (ms) atualizada.", "config_z": cfg, "atualizar_matriz": True}

    if up.startswith("PULSOS="):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        n_p = int(cmd.split("=", 1)[1].strip())
        cfg = configurar_parametros_pulsos_z(
            num_pulsos_fixo=n_p, pulsos_por_nivel=[n_p] * NIVEIS_MAX, usar_fixo=True
        )
        return {"ok": True, "resposta": f"Pulsos configurados para {n_p} em todos os niveis.", "config_z": cfg, "atualizar_matriz": True}

    if up.startswith("PULSOS_NIVEL="):
        registrar_terminal("TX", f">>> {cmd}", "cmd")
        lista_str = cmd.split("=", 1)[1].strip()
        cfg = configurar_parametros_pulsos_z(pulsos_por_nivel=lista_str, usar_fixo=False)
        return {"ok": True, "resposta": "Tabela de pulsos por nivel atualizada.", "config_z": cfg, "atualizar_matriz": True}

    if up == "HOME_CAL":
        registrar_terminal("TX", ">>> HOME_CAL", "cmd")
        ok = ctrl_grbl.calibrar_home(modo="manual_aqui", salvar_g28=True)
        return {"ok": ok, "resposta": "Home calibrado na posicao atual."}

    if up in ("RESET", "CTRL+X", "CTRL-X", "0X18"):
        ok = ctrl_grbl.soft_reset()
        return {"ok": ok, "resposta": "Soft-Reset enviado."}

    if up == "?":
        registrar_terminal("TX", ">>> ?", "cmd")
        st = ctrl_grbl.ler_status_grbl()
        registrar_terminal("RX", st or "Sem resposta", "info")
        return {"ok": bool(st), "resposta": st}

    if up == "!":
        ctrl_grbl.enviar_byte_tempo_real(b"!", "Feed Hold (!) enviado via Terminal")
        return {"ok": True, "resposta": "Feed Hold (!)"}

    if up == "~":
        ctrl_grbl.enviar_byte_tempo_real(b"~", "Cycle Start (~) enviado via Terminal")
        return {"ok": True, "resposta": "Cycle Start (~)"}

    timeout_cmd = 45.0 if up == "$H" else 8.0
    ok, resp = ctrl_grbl.enviar_comando(cmd, timeout=timeout_cmd, logar=True, registrar_no_terminal=True)
    ctrl_grbl.atualizar_status()
    return {"ok": ok, "resposta": resp}


def iniciar_home_xy_em_background(grbl, **parametros):
    """Reserva a maquina durante todo o Home XY. Abort continua disponivel."""
    global _operacao_manual, _job_abortar, _job_pausado
    with _job_lock:
        if _job_em_execucao or _operacao_manual:
            return False, "Maquina ocupada com job ou comando manual."
        _operacao_manual = True
        _job_abortar = False
        _job_pausado = False
        abortar_job_evento.clear()
    bloquear_z_fisico("Home XY reservado: somente motores X/Y autorizados")
    def _worker():
        global _operacao_manual
        try:
            grbl.executar_home_xy_chaves(**parametros)
        finally:
            with _job_lock:
                _operacao_manual = False
    try:
        threading.Thread(target=_worker, daemon=True, name="home-xy-chaves").start()
    except Exception as exc:
        with _job_lock:
            _operacao_manual = False
        return False, f"Nao foi possivel iniciar Home XY: {exc}"
    return True, "Home XY iniciou; acompanhe o estado e use Abortar para interromper."

# ==============================================================================
# SECAO 3: CONTROLADOR ARDUINO DE RESET SUPERIOR (4 ATUADORES + EIXO X')
# ==============================================================================

class ControladorResetArduino:
    """
    Controlador Serial para o Arduino Mega 2560 responsavel pelos 4 atuadores
    lineares do mecanismo de reset superior e pelo motor de passo do eixo X'.
    Compativel com os comandos de firmware do portal EESC-USP:
      - D / D1 / D2 (ciclo completo ou individual dos modulos 1 e 2)
      - M1_01 (descer Mod 1), M1_00 (subir Mod 1), M2_01 (descer Mod 2), M2_00 (subir Mod 2)
      - FC=NO / FC=NC (logica do fim de curso do portal)
      - Comandos legados RESET_FULL, ACT_DOWN, ACT_UP, X_SWEEP, X_HOME
    """
    def __init__(self):
        self.ser = None
        self.porta = PORTA_ARDUINO_RESET
        self.simulado = True
        self.estado_atuadores = "RECUADOS (TOPO)"
        self.posicao_x_linha = 0.0
        self.em_ciclo = False
        self.lock = threading.Lock()

    def conectar(self, porta=None):
        with self.lock:
            if porta is not None:
                self.porta = str(porta).strip()
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

            if not self.porta or self.porta.upper() == "SIMULADO" or serial is None:
                self.simulado = True
                registrar_log("Arduino de Reset (4 Atuadores + Eixo X') em MODO SIMULADO.")
                return True

            try:
                self.ser = serial.Serial(self.porta, BAUD, timeout=2.0)
                time.sleep(1.5)
                self.ser.reset_input_buffer()
                self.simulado = False
                registrar_log(f"Arduino de Reset conectado em {self.porta} ({BAUD} bps).")
                return True
            except Exception as e:
                self.simulado = True
                self.ser = None
                registrar_log(f"Aviso Reset ({self.porta}): {e}. Operando Reset em modo simulado.")
                return False

    def _enviar_cmd(self, cmd, tempo_simulado=0.5):
        with self.lock:
            if self.simulado or self.ser is None:
                time.sleep(tempo_simulado)
                registrar_log(f"[RESET SIMULADO] Comando '{cmd}' concluido.")
                return True, f"SIM_OK:{cmd}"
            try:
                self.ser.write(f"{cmd}\n".encode("utf-8"))
                self.ser.flush()
                t0 = time.monotonic()
                respostas = []
                while (time.monotonic() - t0) < 12.0:
                    linha = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if linha:
                        respostas.append(linha)
                        if any(k in linha.upper() for k in ("OK", "DONE", "CONCLUIDO", "PAROU")):
                            break
                resp_str = " | ".join(respostas) if respostas else "SEM RESPOSTA"
                confirmado = any(k in resp_str.upper() for k in ("OK", "DONE", "CONCLUIDO"))
                if "PAROU" in resp_str.upper():
                    confirmado = False
                registrar_log(f"[RESET ARDUINO] {cmd} -> {resp_str}", "ok" if confirmado else "erro")
                return confirmado, resp_str
            except Exception as e:
                registrar_log(f"Erro na serial do Arduino de Reset: {e}")
                return False, str(e)

    def descer_atuadores(self):
        self.estado_atuadores = "DESCENDO (PRESSIONANDO)"
        ok, msg = self._enviar_cmd("M1_01", 0.3)
        ok2, msg2 = self._enviar_cmd("M2_01", 0.3)
        if ok and ok2:
            self.estado_atuadores = "AVANCADOS (BASE)"
        return bool(ok and ok2), msg + " | " + msg2

    def subir_atuadores(self):
        self.estado_atuadores = "SUBINDO (RECUANDO)"
        ok, msg = self._enviar_cmd("M1_00", 0.3)
        ok2, msg2 = self._enviar_cmd("M2_00", 0.3)
        if ok and ok2:
            self.estado_atuadores = "RECUADOS (TOPO)"
        return bool(ok and ok2), msg + " | " + msg2

    def varrer_eixo_x_linha(self, destino_mm=250.0):
        self.estado_atuadores = f"VARRENDO EIXO X' -> {destino_mm:.0f}mm"
        ok, msg = self._enviar_cmd(f"X_SWEEP:{destino_mm:.1f}", 0.5)
        if ok:
            self.posicao_x_linha = float(destino_mm)
        return ok, msg

    def home_eixo_x_linha(self):
        self.estado_atuadores = "RETORNANDO X' (HOME)"
        ok, msg = self._enviar_cmd("X_HOME", 0.4)
        if ok:
            self.posicao_x_linha = 0.0
            self.estado_atuadores = "RECUADOS (TOPO)"
        return ok, msg

    def ciclo_completo_reset(self, matriz_ref=None):
        """D e comando de ciclo COMPLETO: nao repete as etapas individuais."""
        if self.em_ciclo:
            return False, "Ciclo de reset ja em andamento."
        if self.simulado and not SIMULADO:
            return False, "Arduino de reset nao conectado fisicamente."
        self.em_ciclo = True
        try:
            registrar_log("Iniciando ciclo completo de reset mecanico (apenas comando D).")
            ok, resposta = self._enviar_cmd("D", 0.4)
            if not ok:
                return False, f"Arduino nao confirmou D: {resposta}"
            # Somente DONE/CONCLUIDO confirma que o ciclo fisico foi finalizado.
            if not self.simulado and not any(chave in resposta.upper() for chave in ("DONE", "CONCLUIDO")):
                return False, f"Arduino recebeu D, mas conclusao nao confirmada: {resposta}"
            if matriz_ref is not None:
                matriz_ref.fill(0)
            registrar_log("Reset mecanico concluido pelo Arduino.", "ok")
            return True, "Reset completo executado."
        finally:
            self.em_ciclo = False

    def fechar(self):
        with self.lock:
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None


# ==============================================================================
# SECAO 4: PIPELINE DE VISAO COMPUTACIONAL E REDE NEURAL DEEPLABV3+ (INT8 ONNX)
# ==============================================================================

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from scipy.ndimage import distance_transform_edt
except ImportError:
    distance_transform_edt = None

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def calcular_niveis_por_distancia(mascara_binaria, niveis_max=NIVEIS_MAX):
    if not np.any(mascara_binaria):
        return np.zeros_like(mascara_binaria, dtype=np.uint8)

    if distance_transform_edt is not None:
        dist = distance_transform_edt(mascara_binaria)
    else:
        dist = mascara_binaria.astype(np.float32)
        for _ in range(8):
            padded = np.pad(dist, 1, mode="constant", constant_values=0)
            vizinhos = np.minimum(
                np.minimum(padded[:-2, 1:-1], padded[2:, 1:-1]),
                np.minimum(padded[1:-1, :-2], padded[1:-1, 2:])
            )
            dist = np.where(mascara_binaria > 0, vizinhos + 1.0, 0.0)

    d_max = float(dist.max())
    if d_max <= 0:
        return np.zeros_like(mascara_binaria, dtype=np.uint8)
    normalizado = dist / d_max
    niveis = np.ceil(normalizado * niveis_max).astype(np.uint8)
    niveis[mascara_binaria == 0] = 0
    return np.clip(niveis, 0, niveis_max)


def calcular_niveis_por_luminancia(img_gray_100x100, mascara_binaria, niveis_max=NIVEIS_MAX):
    if not np.any(mascara_binaria):
        return np.zeros_like(mascara_binaria, dtype=np.uint8)
    pixels_ativos = img_gray_100x100[mascara_binaria > 0].astype(np.float32)
    v_min, v_max = float(pixels_ativos.min()), float(pixels_ativos.max())
    if v_max - v_min < 1e-5:
        out = np.zeros_like(mascara_binaria, dtype=np.uint8)
        out[mascara_binaria > 0] = niveis_max
        return out
    norm = (img_gray_100x100.astype(np.float32) - v_min) / (v_max - v_min)
    niveis = np.ceil(norm * niveis_max).astype(np.uint8)
    niveis[mascara_binaria == 0] = 0
    return np.clip(niveis, 1, niveis_max) * (mascara_binaria > 0).astype(np.uint8)


def carregar_sessao_onnx():
    global _sessao_onnx, _input_name_onnx, _input_hw_onnx
    if _sessao_onnx is not None:
        return _sessao_onnx, _input_name_onnx
    if ort is None or not os.path.isfile(NOME_MODELO):
        return None, None
    try:
        t0 = time.perf_counter()
        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads = 4
        opcoes.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _sessao_onnx = ort.InferenceSession(
            NOME_MODELO,
            sess_options=opcoes,
            providers=["CPUExecutionProvider"],
        )
        inp = _sessao_onnx.get_inputs()[0]
        _input_name_onnx = inp.name
        shape = inp.shape
        if (
            isinstance(shape, (list, tuple))
            and len(shape) == 4
            and isinstance(shape[2], int)
            and isinstance(shape[3], int)
            and shape[2] > 0
            and shape[3] > 0
        ):
            _input_hw_onnx = (int(shape[2]), int(shape[3]))
        dt = (time.perf_counter() - t0) * 1000.0
        registrar_log(f"Modelo ONNX ({_input_hw_onnx[0]}x{_input_hw_onnx[1]}) carregado em {dt:.1f} ms.")
        return _sessao_onnx, _input_name_onnx
    except Exception as e:
        registrar_log(f"Aviso ONNX: {e}. Usando segmentacao geometrica Otsu/Gradiente.")
        _sessao_onnx = None
        _input_name_onnx = None
        return None, None


def _segmentar_fallback_otsu(img_pil, limiar_ia=LIMIAR_IA_PADRAO):
    img_gray = img_pil.convert("L").resize(TAMANHO_MATRIZ, Image.Resampling.BILINEAR)
    arr = np.asarray(img_gray, dtype=np.float32) / 255.0
    borda = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
    fundo_claro = float(np.median(borda)) > 0.5
    saliencia = (1.0 - arr) if fundo_claro else arr
    limiar = float(np.clip(limiar_ia, 0.10, 0.90))
    mascara = (saliencia >= limiar).astype(np.uint8)
    if np.mean(mascara) < 0.01:
        med = float(np.mean(saliencia))
        mascara = (saliencia > med).astype(np.uint8)
    return mascara, (arr * 255.0).astype(np.uint8)


def processar_imagem_para_matriz(
    caminho_ou_pil,
    modo_profundidade=None,
    niveis_max=NIVEIS_MAX,
    limiar_ia=LIMIAR_IA_PADRAO,
    inverter_mascara=False,
):
    if modo_profundidade is None:
        modo_profundidade = MODO_PROFUNDIDADE
    t0 = time.perf_counter()

    if Image is None:
        return gerar_padrao_teste("domo", niveis_max), 1.0

    if isinstance(caminho_ou_pil, str):
        img_pil = Image.open(caminho_ou_pil).convert("RGB")
    else:
        img_pil = caminho_ou_pil.convert("RGB")

    sessao, input_name = carregar_sessao_onnx()
    if sessao is not None and input_name is not None:
        try:
            hw_h, hw_w = _input_hw_onnx
            img_in = img_pil.resize((hw_w, hw_h), Image.Resampling.BILINEAR)
            arr_in = np.asarray(img_in, dtype=np.float32) / 255.0
            arr_in = (arr_in - IMAGENET_MEAN) / IMAGENET_STD
            tensor = np.transpose(arr_in, (2, 0, 1))[np.newaxis, ...].astype(np.float32)
            saida = sessao.run(None, {input_name: tensor})[0]
            if saida.ndim == 4:
                logits = saida[0]
                if logits.shape[0] > 1:
                    exp_l = np.exp(logits - np.max(logits, axis=0, keepdims=True))
                    probs = exp_l / np.sum(exp_l, axis=0, keepdims=True)
                    fg_prob = 1.0 - probs[0]
                    pred = (fg_prob >= float(limiar_ia)).astype(np.uint8)
                else:
                    pred = (logits[0] >= float(limiar_ia)).astype(np.uint8)
            else:
                pred = (saida[0] > 0).astype(np.uint8)

            mask_pil = Image.fromarray((pred * 255).astype(np.uint8)).resize(
                TAMANHO_MATRIZ, Image.Resampling.NEAREST
            )
            mascara = (np.asarray(mask_pil) > 127).astype(np.uint8)
            img_gray_100 = np.asarray(
                img_pil.convert("L").resize(TAMANHO_MATRIZ, Image.Resampling.BILINEAR),
                dtype=np.uint8,
            )
        except Exception as e:
            registrar_log(f"Fallback segmentacao: {e}")
            mascara, img_gray_100 = _segmentar_fallback_otsu(img_pil, limiar_ia)
    else:
        mascara, img_gray_100 = _segmentar_fallback_otsu(img_pil, limiar_ia)

    if inverter_mascara:
        mascara = 1 - mascara

    if modo_profundidade == "luminancia":
        matriz = calcular_niveis_por_luminancia(img_gray_100, mascara, niveis_max)
    elif modo_profundidade == "binario":
        matriz = (mascara > 0).astype(np.uint8) * int(niveis_max)
    else:
        matriz = calcular_niveis_por_distancia(mascara, niveis_max)

    tempo_ms = (time.perf_counter() - t0) * 1000.0
    registrar_log(f"Inferencia IA concluida ({tempo_ms:.1f} ms)")
    return matriz.astype(np.uint8), tempo_ms


def gerar_padrao_teste(nome="domo", niveis_max=NIVEIS_MAX):
    h, w = TAMANHO_MATRIZ
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    nome = (nome or "domo").lower().strip()

    if nome == "flor":
        # Padrao Flor 3D centralizada exatamente no meio (cx=49.5, cy=49.5)
        dx = xx - cx
        dy = yy - cy
        r = np.sqrt(dx ** 2 + dy ** 2)
        theta = np.arctan2(dy, dx)
        # 6 petalas + miolo elevado no centro (50,50)
        raio_petala = 18.0 + 11.0 * np.cos(6.0 * theta)
        out = np.zeros((h, w), dtype=np.uint8)
        mask_petala = (r <= raio_petala) & (r >= 7.5)
        if np.any(mask_petala):
            out = calcular_niveis_por_distancia(mask_petala.astype(np.uint8), max(1, niveis_max - 2))
        # Miolo central exatamente em (50,50) com altura maxima (Nivel 8)
        mask_miolo = r <= 8.5
        out[mask_miolo] = int(niveis_max)
        return out

    if nome == "piramide":
        dx = np.abs(xx - cx) / (w * 0.38)
        dy = np.abs(yy - cy) / (h * 0.38)
        d = np.maximum(dx, dy)
        val = np.clip(1.0 - d, 0.0, 1.0)
        return np.round(val * niveis_max).astype(np.uint8)

    if nome == "ondas":
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        mask = r <= 42.0
        val = (np.cos(r * 0.28) + 1.0) * 0.5
        out = np.round(val * (niveis_max - 1) + 1).astype(np.uint8)
        out[~mask] = 0
        return out

    if nome == "anel":
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        anel = np.exp(-((r - 26.0) ** 2) / (2.0 * (5.5 ** 2)))
        centro = np.exp(-(r ** 2) / (2.0 * (7.0 ** 2)))
        val = np.maximum(anel, centro)
        out = np.round(val * niveis_max).astype(np.uint8)
        out[val < 0.15] = 0
        return out

    if nome == "rampa_x":
        # Padrao de calibracao do Eixo X:
        # Barra alta (Nivel 8) na extremidade DIREITA da imagem (x_idx = 88..95 -> perto de X=0mm!)
        # Barra baixa (Nivel 3) na extremidade ESQUERDA da imagem (x_idx = 5..10 -> perto de X=235mm!)
        out = np.zeros((h, w), dtype=np.uint8)
        out[20:80, 88:95] = int(niveis_max)
        out[35:65, 5:10] = max(1, int(niveis_max // 3))
        for x_col in range(15, 85):
            prop = (x_col - 15) / 70.0
            nv = max(1, min(int(niveis_max), int(round(1 + prop * (niveis_max - 1)))))
            out[47:53, x_col] = nv
        return out

    if nome == "usp":
        out = np.zeros((h, w), dtype=np.uint8)
        out[12:16, 12:88] = 4
        out[84:88, 12:88] = 4
        out[12:88, 12:16] = 4
        out[12:88, 84:88] = 4
        # U
        out[28:68, 20:25] = 8
        out[28:68, 33:38] = 8
        out[63:68, 20:38] = 8
        # S
        out[28:33, 43:61] = 8
        out[28:50, 43:48] = 8
        out[46:51, 43:61] = 8
        out[46:68, 56:61] = 8
        out[63:68, 43:61] = 8
        # P
        out[28:68, 66:71] = 8
        out[28:33, 66:80] = 8
        out[46:51, 66:80] = 8
        out[28:51, 75:80] = 8
        return out

    r2 = (xx - cx) ** 2 + (yy - cy) ** 2
    raio_max = 28.0
    mascara = r2 <= (raio_max ** 2)
    return calcular_niveis_por_distancia(mascara.astype(np.uint8), niveis_max)


# ==============================================================================
# SECAO 5: GERACAO DE TRAJETORIA SERPENTINA (COM ORIGEM NA DIREITA) E EXECUCAO
# ==============================================================================

def montar_pontos(matriz):
    """
    Ordena os pontos ativos (nivel > 0) em varredura serpentina (zig-zag) ao longo de Y.
    CORRECAO ESSENCIAL PARA ORIGEM (0,0) NA EXTREMIDADE DIREITA:
      - Quando ORIGEM_X_NA_DIREITA == True, a coluna da direita da imagem (x_idx = 99)
        corresponde a coordenada fisica X = 0.0 mm (onde o cabecote ja comeca!).
      - Portanto, iteramos x_idx de (cols - 1) ate 0 (da direita para a esquerda),
        para que o cabecote comece a plotar imediatamente perto do (0,0) na direita
        e avance suavemente para a esquerda ate 247.5 mm, sem precisar atravessar
        toda a mesa em vazio no inicio!
      - Quando ORIGEM_X_NA_DIREITA == False, iteramos de 0 ate (cols - 1) normalmente.
    """
    pontos = []
    alt, larg = matriz.shape
    if ORIGEM_X_NA_DIREITA:
        colunas_x = range(larg - 1, -1, -1)
    else:
        colunas_x = range(larg)

    passo_col = 0
    for x in colunas_x:
        ys = range(alt) if (passo_col % 2 == 0) else range(alt - 1, -1, -1)
        algum_na_coluna = False
        for y in ys:
            nivel = int(matriz[y, x])
            if nivel > 0:
                pontos.append((x, y, nivel))
                algum_na_coluna = True
        if algum_na_coluna:
            passo_col += 1
    return pontos


def compilar_matriz_para_estudio(matriz, modo_operacao="Domo Gaussiano 3D"):
    """
    Compila a matriz 100x100 em instrucoes G-Code e pontos de trajetoria,
    usando `indice_para_mm_fisico(x_idx, y_idx)` para que as coordenadas X/Y em mm
    reflitam exatamente a origem fisica configurada (Direita = 0,0 por padrao).
    """
    t0 = time.perf_counter()
    pontos = [
        (xi, yi, nivel) for xi, yi, nivel in montar_pontos(matriz)
        if ponto_na_janela_plotagem(*indice_para_mm_fisico(xi, yi))
    ]
    cfg_z = obter_estado_config_z()
    on_ms = cfg_z["z_on_ms"]
    off_ms = cfg_z["z_off_ms"]

    origem_desc = "DIREITA (colunas espelhadas)" if ORIGEM_X_NA_DIREITA else "ESQUERDA (colunas normais)"
    linhas_gcode = [
        "G21 ; unidades em mm",
        "G90 ; posicionamento absoluto",
        "G54 ; sistema de coordenadas",
    ]

    trajetoria_ui = []
    for x_i, y_i, nivel in pontos:
        x_mm, y_mm = indice_para_mm_fisico(x_i, y_i)
        idx_pino = max(0, min(len(PINOS_GPIO_Z) - 1, int(nivel) - 1))
        pino_bcm = PINOS_GPIO_Z[idx_pino]
        qtd_pulsos = obter_pulsos_para_nivel(nivel)

        linhas_gcode.append(
            f"G1 X{x_mm:.3f} Y{y_mm:.3f} F{FEED_RATE:.0f} ; pino_img=({x_i},{y_i}) nivel={nivel}"
        )
        linhas_gcode.append(
            f"M42 P1 ; Z_LEVEL={nivel} GPIO={pino_bcm} PULSOS={qtd_pulsos} ON_MS={on_ms} OFF_MS={off_ms} IDX=({x_i},{y_i})"
        )
        trajetoria_ui.append({
            "x": int(x_i),
            "y": int(y_i),
            "x_mm": round(float(x_mm), 3),
            "y_mm": round(float(y_mm), 3),
            "nivel": int(nivel),
            "gpio": int(pino_bcm),
            "pulsos": int(qtd_pulsos),
        })

    if VOLTAR_ORIGEM and pontos:
        linhas_gcode.append(f"G0 X{PONTO_A[0]:.3f} Y{PONTO_A[1]:.3f} ; retorna ao Ponto A")

    dt_ms = (time.perf_counter() - t0) * 1000.0
    ESTADO_ESTUDIO["matriz"] = matriz.tolist()
    ESTADO_ESTUDIO["pontos"] = trajetoria_ui
    ESTADO_ESTUDIO["linhas_gcode"] = linhas_gcode
    ESTADO_ESTUDIO["resumo"].update({
        "modo_operacao": modo_operacao,
        "pontos_ativos": len(pontos),
        "linhas_gcode_total": len(linhas_gcode),
        "tempo_geracao_gcode_ms": round(dt_ms, 2),
    })
    registrar_log(f"Compilacao G-Code ({dt_ms:.2f} ms | {len(pontos)} pinos | Origem={origem_desc})")
    return linhas_gcode, trajetoria_ui, dt_ms


# ==============================================================================
# EXECUCAO EM BACKGROUND DA TRAJETORIA COMPLETA
# ==============================================================================

_job_lock = threading.Lock()
_job_em_execucao = False
_operacao_manual = False

def reservar_operacao_manual():
    """Bloqueia jog/reset/calibracao/testes concorrentes com jobs."""
    global _operacao_manual, _job_abortar
    with _job_lock:
        if _job_em_execucao or _operacao_manual:
            return False
        _operacao_manual = True
        _job_abortar = False
        abortar_job_evento.clear()
        return True

def liberar_operacao_manual():
    global _operacao_manual
    with _job_lock:
        _operacao_manual = False

_job_pausado = False
_job_abortar = False
_job_progresso = {
    "ativo": False,
    "pausado": False,
    "atual": 0,
    "total": 0,
    "x_idx": 99,
    "y_idx": 0,
    "x_mm": 0.0,
    "y_mm": 0.0,
    "nivel_z": 0,
    "gpio_atual": 0,
    "pulsos_atuais": 0,
    "inicio_ts": 0.0,
}


def executar_job_em_background(grbl, reset_ctrl, matriz):
    """Converte a matriz em blocos tipados e usa o MESMO motor do G-code importado."""
    if matriz is None or not np.any(matriz):
        registrar_log("Matriz vazia: nao ha pontos para plotar.", "erro")
        return False
    try:
        with _estado_projeto_lock:
            matriz_copia = np.array(matriz, dtype=np.uint8, copy=True)
            linhas, _, _ = compilar_matriz_para_estudio(matriz_copia)
            config_job = capturar_snapshot_parametros_job()
            programa = analisar_gcode_hibrido("\n".join(linhas))
        # Bloqueia todas as coordenadas antes de criar a thread.
        validar_programa_antes_movimento(grbl, programa)
    except (ValueError, RuntimeError) as exc:
        registrar_log(f"Falha ao preparar a matriz: {exc}", "erro")
        return False
    return executar_job_gcode_em_background(
        grbl, programa, reset_ctrl=reset_ctrl,
        origem="matriz", usar_parametros_arquivo=False,
        parametros_job=config_job, nome_programa="Matriz 100x100",
    )
# ==============================================================================
# SMARTV42: Interpretador hibrido G-code XY (GRBL) + Z discreto (GPIO)
# Z1..Z8 sao acionamentos independentes; NUNCA coordenadas cartesianas.
# ==============================================================================


def validar_janela_calibrada():
    """Confere geometria 100x100, passo nominal 3 mm e recorte espacial."""
    ok, motivo = validar_calibracao_cantos()
    if not ok:
        return False, motivo
    if not USAR_JANELA_PLOTAGEM:
        return True, ""
    janela = (float(PLOT_X_MIN), float(PLOT_X_MAX), float(PLOT_Y_MIN), float(PLOT_Y_MAX))
    if not all(math.isfinite(v) for v in janela):
        return False, "Janela de plotagem nao finita."
    if abs(janela[1]-janela[0]) < 1e-6 or abs(janela[3]-janela[2]) < 1e-6:
        return False, "Janela de plotagem deve ter largura e altura positivas."
    # A janela e um recorte opcional: precisa intersectar algum centro da malha.
    encontrou = any(
        ponto_na_janela_plotagem(*indice_para_mm_fisico(i, j))
        for i in range(TAMANHO_MATRIZ[1])
        for j in range(TAMANHO_MATRIZ[0])
    )
    if not encontrou:
        return False, "Janela nao contem nenhum centro da malha (passo de 3 mm)."
    return True, ""


def analisar_gcode_hibrido(texto):
    """Interpreta e valida TODO o arquivo, sem mover a maquina."""
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("Arquivo G-code vazio.")
    if len(texto.encode("utf-8")) > 3_000_000:
        raise ValueError("Arquivo G-code excede 3 MB.")
    linhas = texto.splitlines()
    if len(linhas) > 80_000:
        raise ValueError("Limite de 80.000 linhas excedido.")

    programa = []
    houve_xy = False
    numero = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    padrao_mov = re.compile(r"G0?([01])\s*((?:[XYF]\s*" + numero + r"\s*)+)$")

    for indice, linha in enumerate(linhas, 1):
        corpo, _, comentario = linha.partition(";")
        corpo = re.sub(r"\([^)]*\)", "", corpo).strip().upper()
        if not corpo:
            continue
        bloco = {"linha": indice, "texto": linha.rstrip()}
        if corpo in ("G21", "G90", "G54"):
            bloco.update(tipo="config", comando=corpo)
        elif re.fullmatch(r"G0?4\s*P0(?:\.0+)?", corpo):
            bloco.update(tipo="sync")
        elif corpo in ("M3 S1000", "M3S1000", "M5"):
            bloco.update(tipo="marcador")
        elif re.fullmatch(r"Z[1-8]", corpo):
            bloco.update(tipo="z", nivel=int(corpo[1:]))
        elif re.fullmatch(r"M42\s*P1", corpo):
            nivel_info = re.search(r"\bZ_LEVEL\s*=\s*([1-8])\b", comentario, re.I)
            if not nivel_info:
                raise ValueError(f"Linha {indice}: M42 requer comentario Z_LEVEL=1..8.")
            nivel = int(nivel_info.group(1))
            bloco.update(tipo="z", nivel=nivel)
            for chave, destino in (
                ("GPIO", "gpio"), ("PULSOS", "pulsos"),
                ("ON_MS", "on_ms"), ("OFF_MS", "off_ms"),
            ):
                encontrado = re.search(r"\b" + chave + r"\s*=\s*(\d+(?:\.\d+)?)\b", comentario, re.I)
                if encontrado:
                    valor = float(encontrado.group(1))
                    if destino in ("gpio", "pulsos"):
                        if not valor.is_integer():
                            raise ValueError(f"Linha {indice}: {chave} precisa ser inteiro.")
                        valor = int(valor)
                    bloco[destino] = valor
            if "gpio" in bloco and bloco["gpio"] != PINOS_GPIO_Z[nivel - 1]:
                raise ValueError(f"Linha {indice}: GPIO nao corresponde ao nivel Z{nivel}.")
            if "pulsos" in bloco and not (1 <= bloco["pulsos"] <= 500):
                raise ValueError(f"Linha {indice}: PULSOS fora de 1..500.")
            if "on_ms" in bloco and not (1 <= bloco["on_ms"] <= 2000):
                raise ValueError(f"Linha {indice}: ON_MS fora de 1..2000.")
            if "off_ms" in bloco and not (0 <= bloco["off_ms"] <= 2000):
                raise ValueError(f"Linha {indice}: OFF_MS fora de 0..2000.")
        else:
            mov = padrao_mov.fullmatch(corpo)
            if not mov:
                raise ValueError(
                    f"Linha {indice}: comando nao suportado ({corpo}). "
                    "G0/G1 aceitam somente X,Y,F; para o mecanismo Z use Z1..Z8 "
                    "ou M42 P1 ; Z_LEVEL=N."
                )
            parametros = {}
            for eixo, valor in re.findall(r"([XYF])\s*(" + numero + r")", mov.group(2)):
                if eixo in parametros:
                    raise ValueError(f"Linha {indice}: parametro {eixo} duplicado.")
                numero_float = float(valor)
                if not math.isfinite(numero_float):
                    raise ValueError(f"Linha {indice}: valor nao finito.")
                parametros[eixo] = numero_float
            if "F" in parametros and not (0 < parametros["F"] <= 5000):
                raise ValueError(f"Linha {indice}: Feed deve estar em (0,5000] mm/min.")
            if "X" not in parametros and "Y" not in parametros:
                raise ValueError(f"Linha {indice}: movimento sem X/Y.")
            bloco.update(tipo="xy", x=parametros.get("X"),
                         y=parametros.get("Y"), feed=parametros.get("F"))
            houve_xy = True

        if bloco["tipo"] == "z" and not houve_xy:
            raise ValueError(f"Linha {indice}: Z exige movimento XY anterior.")
        programa.append(bloco)

    if not programa or not any(item["tipo"] == "xy" for item in programa):
        raise ValueError("Arquivo sem movimentos XY.")
    return programa


def validar_programa_antes_movimento(grbl, programa):
    """Valida as coordenadas de todos os blocos antes do primeiro movimento."""
    posicao = None
    wx, wy = grbl.wpos[:2]
    for bloco in programa:
        if bloco["tipo"] == "xy":
            wx = wx if bloco["x"] is None else bloco["x"]
            wy = wy if bloco["y"] is None else bloco["y"]
            if not math.isfinite(wx) or not math.isfinite(wy) or fora_dos_limites(wx, wy):
                raise ValueError(f"Linha {bloco['linha']}: XY fora dos cantos calibrados ({wx}, {wy}).")
            posicao = (wx, wy)
        elif bloco["tipo"] == "z":
            if "gpio" in bloco and bloco["gpio"] != PINOS_GPIO_Z[int(bloco["nivel"]) - 1]:
                raise ValueError(
                    f"Linha {bloco['linha']}: GPIO de Z{bloco['nivel']} nao corresponde ao mapa atual. "
                    "Reimporte o G-code ou restaure o mapa dos pinos."
                )
            if posicao is None:
                raise ValueError(f"Linha {bloco['linha']}: acionamento Z sem deslocamento XY anterior.")
            x, y = posicao
            if not ponto_na_janela_plotagem(x, y):
                raise ValueError(f"Linha {bloco['linha']}: Z fora da janela em ({x}, {y}).")
            if desvio_centro_malha_mm(x, y) > 0.35:
                raise ValueError(
                    f"Linha {bloco['linha']}: Z em ({x:.3f}, {y:.3f}) fora dos centros "
                    "fisicos da matriz (100x100, passo ~3 mm)."
                )
    return True



def assinatura_geometria_execucao():
    """Assinatura da geometria, janela e GPIO usada durante um job."""
    return (
        tuple(tuple(float(v) for v in p) for p in (PONTO_A, PONTO_B, PONTO_C, PONTO_D)),
        bool(ORIGEM_X_NA_DIREITA), bool(USAR_JANELA_PLOTAGEM),
        float(PLOT_X_MIN), float(PLOT_X_MAX), float(PLOT_Y_MIN), float(PLOT_Y_MAX),
        tuple(PINOS_GPIO_Z),
    )


@dataclass(frozen=True)
class ConfiguracaoExecucao:
    """Contrato imutavel do job, independente dos campos editaveis da interface.

    Suporta acesso por chave para compatibilidade com o motor e testes legados.
    O mapeamento de GPIO e a calibracao sao assinaturas de verificacao, nunca
    fontes de movimentacao dinamica.
    """
    feed_rate: float
    pulsos_por_nivel: tuple
    on_ms: float
    off_ms: float
    pausa_ms: float
    auto_reset_apos_plot: bool
    assinatura_geometria: tuple

    def __getitem__(self, chave):
        return getattr(self, chave)

    def get(self, chave, default=None):
        return getattr(self, chave, default)

    def to_dict(self):
        return {
            "feed_rate": self.feed_rate,
            "pulsos_por_nivel": list(self.pulsos_por_nivel),
            "on_ms": self.on_ms,
            "off_ms": self.off_ms,
            "pausa_ms": self.pausa_ms,
            "auto_reset_apos_plot": self.auto_reset_apos_plot,
            "assinatura_geometria": self.assinatura_geometria,
        }

    @classmethod
    def de_snapshot(cls, valores):
        if isinstance(valores, cls):
            return valores
        # Aceita o antigo dicionario como interface de transicao, sem alias.
        assinatura = copy.deepcopy(tuple(valores["assinatura_geometria"]))
        return cls(
            feed_rate=float(valores["feed_rate"]),
            pulsos_por_nivel=tuple(int(v) for v in valores["pulsos_por_nivel"]),
            on_ms=float(valores["on_ms"]), off_ms=float(valores["off_ms"]),
            pausa_ms=float(valores["pausa_ms"]),
            auto_reset_apos_plot=bool(valores["auto_reset_apos_plot"]),
            assinatura_geometria=assinatura,
        )


def capturar_snapshot_parametros_job():
    """Cria uma ConfiguracaoExecucao imutavel antes da reserva do motor."""
    z = obter_estado_config_z()
    return ConfiguracaoExecucao(
        feed_rate=float(FEED_RATE),
        pulsos_por_nivel=tuple(int(v) for v in z["pulsos_por_nivel"]),
        on_ms=float(z["z_on_ms"]), off_ms=float(z["z_off_ms"]),
        pausa_ms=float(z["pausa_z_ms"]),
        auto_reset_apos_plot=bool(AUTO_RESET_APOS_PLOT),
        assinatura_geometria=assinatura_geometria_execucao(),
    )


def validar_snapshot_geometria_job(snapshot):
    if assinatura_geometria_execucao() != snapshot["assinatura_geometria"]:
        raise RuntimeError("Geometria, janela ou pinos GPIO alterados apos preparar o trabalho. Execucao interrompida.")


def executar_bloco_hibrido(grbl, bloco, contexto):
    """Executa instrucoes validadas, sem encaminhar Z/M42 ao GRBL."""
    tipo = bloco["tipo"]
    cfg_job = contexto.get("config_job")
    if cfg_job is None:
        cfg_job = capturar_snapshot_parametros_job()  # chamadas legadas fora do job
    validar_snapshot_geometria_job(cfg_job)
    if tipo == "config":
        ok, resp = grbl.enviar_comando(bloco["comando"], logar=False)
        if not ok:
            raise RuntimeError(f"GRBL nao aceitou {bloco['comando']}: {resp}")
        return
    if tipo in ("sync", "marcador"):
        return
    if tipo == "xy":
        x = grbl.wpos[0] if bloco["x"] is None else bloco["x"]
        y = grbl.wpos[1] if bloco["y"] is None else bloco["y"]
        feed = cfg_job["feed_rate"] if bloco["feed"] is None else bloco["feed"]
        ok, estado, detalhe = grbl.mover_xy_e_aguardar(x, y, feed=feed)
        if not ok:
            raise RuntimeError(f"GRBL rejeitou XY: {estado}; {detalhe}")
        contexto["posicao_xy"] = (x, y)
        xi, yi = mm_fisico_para_indice(x, y)
        _job_progresso.update({"x_mm": x, "y_mm": y, "x_idx": xi, "y_idx": yi})
        return
    if tipo == "z":
        pos = contexto.get("posicao_xy")
        if pos is None:
            raise RuntimeError("Z bloqueado: nenhum movimento XY concluido.")
        x, y = pos
        if not ponto_na_janela_plotagem(x,y):
            raise RuntimeError(f"Z fora da janela em X={x:.3f}, Y={y:.3f}.")
        if desvio_centro_malha_mm(x,y) > 0.35:
            raise RuntimeError("Z recusado fora do centro fisico de um pino (passo ~3 mm).")
        if not aguardar_liberacao_z():
            raise RuntimeError("Z interrompido durante pausa.")
        pronto, motivo = grbl.aguardar_grbl_idle()
        if not pronto:
            raise RuntimeError(f"GRBL nao esta Idle; Z bloqueado ({motivo}).")
        if _job_abortar or abortar_job_evento.is_set():
            raise RuntimeError("Z interrompido pelo operador.")
        if not aguardar_liberacao_z():
            raise RuntimeError("Z bloqueado por pausa/abortamento.")
        if cfg_job["pausa_ms"] > 0:
            time.sleep(cfg_job["pausa_ms"] / 1000.0)
        if not aguardar_liberacao_z():
            raise RuntimeError("Z bloqueado por pausa/abortamento.")
        arquivo_prevalece = bool(contexto.get("usar_parametros_arquivo", False))
        nivel = int(bloco["nivel"])
        pulsos = bloco.get("pulsos") if arquivo_prevalece else None
        on_ms = bloco.get("on_ms") if arquivo_prevalece else None
        off_ms = bloco.get("off_ms") if arquivo_prevalece else None
        ok, info = acionar_eixo_z(
            nivel,
            pulsos_override=pulsos if pulsos is not None else cfg_job["pulsos_por_nivel"][nivel-1],
            on_ms_override=on_ms if on_ms is not None else cfg_job["on_ms"],
            off_ms_override=off_ms if off_ms is not None else cfg_job["off_ms"],
        )
        if not ok:
            raise RuntimeError("Falha no mecanismo Z: " + str(info.get("erro", "erro desconhecido")))
        _job_progresso.update({"nivel_z": bloco["nivel"],
                               "gpio_atual": info["pino_gpio"],
                               "pulsos_atuais": info["pulsos"]})
        return
    raise RuntimeError(f"Tipo de instrucao invalido: {tipo}")


class MotorExecucaoGCode:
    """Executor unico: controla preflight, reserva, progresso, blocos e reset.

    Nunca recebe a matriz ou referencias para programas da interface: somente
    um programa copiado e uma ConfiguracaoExecucao congelada.
    O GRBL e o controlador Z continuam responsaveis pelos intertravamentos.
    """
    def __init__(self, grbl, programa, reset_ctrl=None, origem="gcode",
                 usar_parametros_arquivo=False, parametros_job=None,
                 nome_programa=None):
        self.grbl = grbl
        self.programa = copy.deepcopy(list(programa)) if programa is not None else []
        self.reset_ctrl = reset_ctrl
        self.origem = origem
        self.usar_parametros_arquivo = bool(usar_parametros_arquivo)
        self.config_job = ConfiguracaoExecucao.de_snapshot(
            parametros_job if parametros_job is not None else capturar_snapshot_parametros_job()
        )
        self.nome_programa = nome_programa or origem

    def validar_antes_de_reservar(self):
        if not self.programa:
            return False
        try:
            validar_snapshot_geometria_job(self.config_job)
            if (hasattr(self.grbl, "home_xy_referenciado")
                    and not getattr(self.grbl, "modo_simulacao", False)
                    and not self.grbl.home_xy_referenciado):
                registrar_log("Job bloqueado: primeiro execute Home XY por chaves.", "erro")
                return False
            if (self.config_job["auto_reset_apos_plot"] and self.origem == "matriz"
                    and self.reset_ctrl is not None
                    and bool(getattr(self.grbl, "modo_simulacao", False)) != bool(self.reset_ctrl.simulado)):
                registrar_log("Reset mecanico inconsistente com modo real/simulado do GRBL; job bloqueado.", "erro")
                return False
            if any(b.get("tipo") == "z" for b in self.programa):
                modo_z = obter_modo_z_efetivo()
                if modo_z not in ("simulacao", "completo"):
                    registrar_log(f"Job com Z bloqueado no modo {modo_z}. Habilite Z fisico apos Home XY.", "erro")
                    return False
            validar_programa_antes_movimento(self.grbl, self.programa)
        except (ValueError, RuntimeError) as exc:
            registrar_log(f"Programa rejeitado antes de movimentar: {exc}", "erro")
            return False
        return True

    def iniciar(self):
        """Faz uma unica reserva para todos os tipos de programa."""
        global _job_em_execucao, _job_pausado, _job_abortar
        if not self.validar_antes_de_reservar():
            return False
        with _job_lock:
            if _job_em_execucao or _operacao_manual:
                registrar_log("Ja existe um job ou operacao manual em execucao.", "erro")
                return False
            _job_em_execucao = True
            _job_pausado = False
            _job_abortar = False
            abortar_job_evento.clear()
        try:
            threading.Thread(target=self.executar, daemon=True).start()
        except Exception as exc:
            with _job_lock:
                _job_em_execucao = False
            registrar_log(f"Nao foi possivel iniciar thread do job: {exc}", "erro")
            return False
        return True

    def executar(self):
        """Rotina unica para matriz e G-code externo; nao inicia novas threads."""
        global _job_em_execucao, _job_pausado
        grbl = self.grbl
        programa = self.programa
        reset_ctrl = self.reset_ctrl
        origem = self.origem
        config_job = self.config_job
        usar_parametros_arquivo = self.usar_parametros_arquivo
        _job_progresso.update({
            "ativo": True, "pausado": False, "atual": 0, "total": len(programa),
            "inicio_ts": time.monotonic(), "nivel_z": 0,
            "origem": origem, "nome_programa": self.nome_programa,
            "usar_parametros_arquivo": usar_parametros_arquivo,
            "parametros_job": {
                "feed_rate": config_job["feed_rate"], "on_ms": config_job["on_ms"],
                "off_ms": config_job["off_ms"], "pausa_ms": config_job["pausa_ms"],
                "pulsos_por_nivel": list(config_job["pulsos_por_nivel"]),
                "auto_reset_apos_plot": config_job["auto_reset_apos_plot"],
            },
            "erro": "", "concluido": False,
        })
        try:
            ok_cal, erro_cal = validar_janela_calibrada()
            if not ok_cal:
                raise RuntimeError(erro_cal)
            validar_snapshot_geometria_job(config_job)
            if any(b.get("tipo") == "z" for b in programa) and obter_modo_z_efetivo() not in ("simulacao", "completo"):
                raise RuntimeError("Modo Z alterado antes do job; reles Z bloqueados.")
            if not grbl.preparar_grbl():
                raise RuntimeError("GRBL nao ficou pronto; nenhum comando executado.")
            validar_programa_antes_movimento(grbl, programa)
            validar_snapshot_geometria_job(config_job)
            contexto = {
                "usar_parametros_arquivo": usar_parametros_arquivo,
                "config_job": config_job,
            }
            for pos, bloco in enumerate(programa, 1):
                if _job_abortar or abortar_job_evento.is_set() or not sistema_ativo.is_set():
                    raise RuntimeError("Execucao interrompida pelo operador.")
                while _job_pausado and not _job_abortar and sistema_ativo.is_set():
                    _job_progresso["pausado"] = True
                    time.sleep(0.05)
                if _job_abortar or abortar_job_evento.is_set() or not sistema_ativo.is_set():
                    raise RuntimeError("Trabalho interrompido durante pausa.")
                _job_progresso["pausado"] = False
                _job_progresso["atual"] = pos
                try:
                    executar_bloco_hibrido(grbl, bloco, contexto)
                except Exception as exc:
                    raise RuntimeError(f"Linha {bloco['linha']}: {exc}") from exc
            if config_job["auto_reset_apos_plot"] and origem == "matriz" and reset_ctrl is not None:
                if bool(getattr(grbl, "modo_simulacao", False)) != bool(reset_ctrl.simulado):
                    raise RuntimeError("Reset mecanico cancelado: modo GRBL e Arduino reset diferentes.")
                ok_reset, detalhe_reset = reset_ctrl.ciclo_completo_reset()
                if not ok_reset:
                    raise RuntimeError(f"Reset mecanico falhou: {detalhe_reset}")
            _job_progresso["concluido"] = True
            registrar_log(f"Job {origem} concluido: {len(programa)} blocos.", "ok")
        except Exception as exc:
            _job_progresso["erro"] = str(exc)
            registrar_log(f"JOB {origem} INTERROMPIDO: {exc}", "erro")
        finally:
            _job_progresso.update({"ativo": False, "pausado": False, "nivel_z": 0})
            with _job_lock:
                _job_em_execucao = False


def executar_job_gcode_em_background(grbl, programa, reset_ctrl=None, origem="gcode",
                                      usar_parametros_arquivo=False, parametros_job=None,
                                      nome_programa=None):
    """Interface compativel: ambos os tipos de trabalho usam MotorExecucaoGCode."""
    return MotorExecucaoGCode(
        grbl, programa, reset_ctrl=reset_ctrl, origem=origem,
        usar_parametros_arquivo=usar_parametros_arquivo,
        parametros_job=parametros_job, nome_programa=nome_programa,
    ).iniciar()
# ==============================================================================
# SECAO 6: INTERFACE WEB STUDIO v1.9 (UGS + CALIBRACAO DE HOME & ORIGEM DIREITA)
# ==============================================================================

import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


HTML_CONCEITUAL = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Matrix Studio v1.9 — UGS + Origem Direita (0,0) + GPIO [5,6,16,27,23,17,24,25]</title>
<style>
  :root {
    --bg-main: #0b1020;
    --bg-panel: #131b31;
    --bg-card: #19233f;
    --border: #2a3964;
    --text: #e8eefc;
    --muted: #95a6d1;
    --accent: #38bdf8;
    --emerald: #10b981;
    --amber: #f59e0b;
    --rose: #f43f5e;
    --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: "DejaVu Sans", "Liberation Sans", system-ui, -apple-system, sans-serif;
    background: var(--bg-main);
    color: var(--text);
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  header {
    background: linear-gradient(90deg, #0f172a 0%, #172554 100%);
    border-bottom: 1px solid var(--border);
    padding: 8px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand-badge {
    width: 34px; height: 34px; border-radius: 8px;
    background: linear-gradient(135deg, #3b82f6, #8b5cf6);
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; font-size: 18px; color: #fff;
  }
  .brand h1 { font-size: 15px; font-weight: 700; }
  .brand p { font-size: 11px; color: var(--muted); }
  .header-right { display: flex; gap: 8px; align-items: center; font-size: 11px; }
  .pill {
    padding: 4px 10px; border-radius: 999px; font-weight: 600;
    background: var(--bg-card); border: 1px solid var(--border);
  }
  .workspace {
    display: grid;
    grid-template-columns: 165px 410px 1fr;
    flex: 1;
    min-height: 0;
  }
  .sidebar {
    background: #0d1428;
    border-right: 1px solid var(--border);
    padding: 10px 8px;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .nav-btn {
    width: 100%; text-align: left; padding: 8px 10px;
    background: transparent; border: 1px solid transparent;
    color: var(--text); border-radius: 7px; cursor: pointer;
    font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 8px;
  }
  .nav-btn:hover, .nav-btn.active {
    background: var(--bg-card); border-color: var(--border); color: var(--accent);
  }
  .sidebar-footer {
    margin-top: auto; padding: 8px; border-radius: 7px;
    background: rgba(56, 189, 248, 0.08); border: 1px solid var(--border);
    font-size: 10.5px; color: var(--muted); line-height: 1.35;
  }
  .left-panel {
    background: var(--bg-panel);
    border-right: 1px solid var(--border);
    padding: 10px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 9px;
  }
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 9px;
  }
  .card-title {
    font-size: 11.5px; font-weight: 700; color: #cbd5e1;
    margin-bottom: 7px; display: flex; justify-content: space-between; align-items: center;
  }
  .row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
  .row:last-child { margin-bottom: 0; }
  select, input[type="text"], input[type="number"] {
    background: #0d1428; color: var(--text); border: 1px solid var(--border);
    border-radius: 5px; padding: 5px 7px; font-size: 11.5px; width: 100%;
  }
  .btn {
    border: 1px solid var(--border); background: #223159; color: var(--text);
    padding: 6px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 600;
    cursor: pointer; text-align: center; transition: 0.15s;
  }
  .btn:hover { filter: brightness(1.12); }
  .btn-emerald { background: #059669; border-color: #10b981; color: #fff; }
  .btn-amber { background: #d97706; border-color: #f59e0b; color: #fff; }
  .btn-rose { background: #e11d48; border-color: #f43f5e; color: #fff; }
  .btn-sky { background: #0284c7; border-color: #38bdf8; color: #fff; }
  .btn-purple { background: #7e22ce; border-color: #a855f7; color: #fff; }
  .dro-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    background: #090e1c; padding: 8px; border-radius: 6px; border: 1px solid #1e293b;
  }
  .dro-axis { font-family: "DejaVu Sans Mono", monospace; font-size: 13px; line-height: 1.5; }
  .dro-axis span { color: var(--accent); font-weight: 700; }
  .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px; margin-bottom: 5px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; margin-bottom: 5px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; }
  .jog-container { display: grid; grid-template-columns: 128px 1fr; gap: 8px; align-items: center; }
  .jog-pad { display: grid; grid-template-columns: repeat(3, 38px); gap: 4px; }
  .jog-btn {
    height: 32px; border-radius: 5px; border: 1px solid var(--border);
    background: #1e2b4d; color: #fff; font-weight: 700; font-size: 11px; cursor: pointer;
  }
  .jog-btn:hover { background: #2d4073; }
  .gpio-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; margin-bottom: 6px; }
  .gpio-btn {
    padding: 5px 3px; font-size: 10.5px; border-radius: 5px;
    background: #0f3a36; border: 1px solid #14b8a6; color: #ccfbf1; cursor: pointer; font-weight: 700;
  }
  .pulsos-grid { display: grid; grid-template-columns: repeat(8, 1fr); gap: 3px; margin: 5px 0; }
  .pulsos-cell { text-align: center; font-size: 9.5px; color: var(--muted); }
  .pulsos-cell input { text-align: center; padding: 3px 2px; font-size: 11px; font-weight: 700; margin-top: 2px; }
  .right-panel {
    display: flex; flex-direction: column; padding: 10px; gap: 8px; min-height: 0; overflow: hidden;
  }
  .top-bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
  .viz-split {
    display: grid; grid-template-columns: 1fr 330px; gap: 10px; flex: 1; min-height: 0;
  }
  .canvas-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px; display: flex; flex-direction: column; min-height: 0; position: relative;
  }
  .canvas-wrap {
    flex: 1; display: flex; align-items: center; justify-content: center;
    background: #070b16; border-radius: 6px; overflow: hidden; position: relative;
  }
  canvas { max-width: 100%; max-height: 100%; }
  .side-stack { display: flex; flex-direction: column; gap: 8px; min-height: 0; }
  .code-box, .log-box {
    background: #070b16; border: 1px solid var(--border); border-radius: 6px;
    padding: 7px; font-family: "DejaVu Sans Mono", monospace; font-size: 10.5px;
    color: #a5f3fc; overflow-y: auto; flex: 1; white-space: pre-wrap; line-height: 1.35;
  }
  .log-box { color: #d1fae5; }
  .modal-backdrop {
    display: none; position: fixed; inset: 0; background: rgba(4, 8, 20, 0.78);
    z-index: 100; align-items: center; justify-content: center; padding: 16px;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px;
    width: 100%; max-width: 720px; max-height: 90vh; display: flex; flex-direction: column;
    overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.65);
  }
  .modal-header {
    padding: 12px 16px; background: #0f172a; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center; font-weight: 700;
  }
  .modal-body { padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
  .coord-banner {
    background: rgba(16, 185, 129, 0.12); border: 1px solid #10b981;
    border-radius: 6px; padding: 7px; font-size: 11px; color: #d1fae5; line-height: 1.35;
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-badge">❖</div>
    <div>
      <h1>Smart Matrix Studio v1.9 (UGS + Origem X Direita 0,0 + GPIO [5,6,16,27,23,17,24,25])</h1>
      <p>Plataforma CNC Tatil, Simetria/Origem Configuravel, Console Serial UGS & IA Embarcada (EESC-USP)</p>
    </div>
  </div>
  <div class="header-right">
    <span class="pill" id="badge-origem">⌖ Origem (0,0): DIREITA</span>
    <span class="pill" id="badge-status-porta">Porta: /dev/ttyACM0</span>
    <span class="pill">Projeto IC • Prof. Mario Tronco</span>
  </div>
</header>

<div class="workspace">
  <!-- BARRA LATERAL ESQUERDA -->
  <aside class="sidebar">
    <button class="nav-btn active" onclick="fecharModais()">⊞ Visao Geral</button>
    <button class="nav-btn" onclick="iniciarJob()">▶ Plotar Matriz</button>
    <button class="nav-btn" onclick="abrirModal('modal-home')">⌂ Home XY</button>
    <button class="nav-btn" onclick="abrirModal('modal-terminal')">⌨ Terminal UGS</button>
    <button class="nav-btn" onclick="abrirModal('modal-grbl')">⚙ Config GRBL ($$)</button>
    <button class="nav-btn" onclick="executarResetCompleto()">↻ Resetar Pinos</button>
    <button class="nav-btn" onclick="alternarVista()">❖ Alternar 2D / 3D</button>
    <button class="nav-btn" onclick="exportarGCode()">⬇ Exportar G-Code</button>

    <div class="sidebar-footer">
      <strong>Malha física: 100 × 100 pontos</strong><br>
      Passo nominal 3 mm · extremos separados por 297 mm.<br>
      Home XY por chaves · recuo de 5 mm · Smart Matrix EESC-USP
    </div>
  </aside>

  <!-- PAINEL DE CONTROLE UGS + CABECOTE + ORIENTACAO -->
  <section class="left-panel">
    <!-- PORTAS USB -->
    <div class="card">
      <div class="card-title">
        <span>Portas USB (GRBL X/Y & Arduino Reset)</span>
        <span id="status-porta-pill" style="color:#34d399;font-size:11px;">● Conectado</span>
      </div>
      <div class="row">
        <select id="sel-porta-grbl">
          <option value="/dev/ttyACM0">GRBL: /dev/ttyACM0</option>
          <option value="/dev/ttyACM1">GRBL: /dev/ttyACM1</option>
          <option value="/dev/ttyUSB0">GRBL: /dev/ttyUSB0</option>
          <option value="SIMULADO">GRBL: SIMULADO</option>
        </select>
        <select id="sel-porta-reset">
          <option value="SIMULADO">RESET: SIMULADO</option>
          <option value="/dev/ttyACM1">RESET: /dev/ttyACM1</option>
          <option value="/dev/ttyUSB0">RESET: /dev/ttyUSB0</option>
          <option value="/dev/ttyACM0">RESET: /dev/ttyACM0</option>
        </select>
        <button class="btn btn-sky" onclick="conectarPortas()" style="white-space:nowrap;">↻ Conectar</button>
      </div>
    </div>

    <!-- VISOR DIGITAL UGS (WPos / MPos) -->
    <div class="card">
      <div class="card-title">
        <span>Visor Digital UGS (WPos / MPos)</span>
        <span id="grbl-state-badge" style="color:#34d399;">● IDLE (G54)</span>
      </div>
      <div class="dro-grid">
        <div class="dro-axis">
          <div>X <span id="dro-wx">0.00 mm</span></div>
          <div>Y <span id="dro-wy">0.00 mm</span></div>
          <div>Z <span id="dro-wz" style="color:#fbbf24;">Nivel 0</span></div>
        </div>
        <div class="dro-axis" style="font-size:11px;color:var(--muted);">
          <div>MPos X: <span id="dro-mx" style="color:#cbd5e1;">0.00 mm</span></div>
          <div>MPos Y: <span id="dro-my" style="color:#cbd5e1;">0.00 mm</span></div>
          <div id="dro-pn" style="color:#fbbf24;">Pn: Nenhum | Col:99,Lin:0</div>
        </div>
      </div>
    </div>

    <!-- CALIBRACAO BILINEAR DOS 4 CANTOS FISICOS (A, B, C, D MEDIDOS NA BANCADA) -->
    <div class="card" style="border-color:#10b981;">
      <div class="card-title">
        <span style="color:#34d399;">⌖ 4 Cantos Medidos na Chapa (A, B, C, D)</span>
        <button class="btn btn-sky" style="padding:2px 7px;font-size:10.5px;" onclick="calibrarCantoMatriz('ir_meio')">🎯 Ir ao Centro (50,50)</button>
      </div>
      <div class="row" style="font-size:10px;gap:4px;">
        <span style="width:28px;color:#34d399;font-weight:bold;">A(↙)</span>
        <input type="number" id="inp-xa" value="0.000" step="0.5" title="Ponto A (0,0) - X">
        <input type="number" id="inp-ya" value="0.000" step="0.5" title="Ponto A (0,0) - Y">
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('ir_a')">Ir A</button>
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('gravar_a')">📍A</button>
      </div>
      <div class="row" style="font-size:10px;gap:4px;">
        <span style="width:28px;color:#38bdf8;font-weight:bold;">B(↘)</span>
        <input type="number" id="inp-xb" value="297.000" step="0.5" title="Ponto B (297,0) - X">
        <input type="number" id="inp-yb" value="0.000" step="0.5" title="Ponto B (297,0) - Y">
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('ir_b')">Ir B</button>
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('gravar_b')">📍B</button>
      </div>
      <div class="row" style="font-size:10px;gap:4px;">
        <span style="width:28px;color:#fbbf24;font-weight:bold;">C(↗)</span>
        <input type="number" id="inp-xc" value="297.000" step="0.5" title="Ponto C (297,-297) - X">
        <input type="number" id="inp-yc" value="-297.000" step="0.5" title="Ponto C (297,-297) - Y">
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('ir_c')">Ir C</button>
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('gravar_c')">📍C</button>
      </div>
      <div class="row" style="font-size:10px;gap:4px;">
        <span style="width:28px;color:#f43f5e;font-weight:bold;">D(↖)</span>
        <input type="number" id="inp-xd" value="0.000" step="0.5" title="Ponto D (0,-297) - X">
        <input type="number" id="inp-yd" value="-297.000" step="0.5" title="Ponto D (0,-297) - Y">
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('ir_d')">Ir D</button>
        <button class="btn" style="padding:3px 5px;font-size:9.5px;" onclick="calibrarCantoMatriz('gravar_d')">📍D</button>
      </div>
      <div id="janela-matriz-info" style="font-size:10px;color:#34d399;background:rgba(16,185,129,0.12);border:1px solid #10b981;border-radius:5px;padding:4px 6px;margin-top:4px;font-family:monospace;">
        Matriz 100 × 100 · passo 3 mm · extensão útil 297 × 297 mm · 299 mm é faixa nominal do movimento.
      </div>
      <div id="lbl-avisos" style="display:none;white-space:pre-wrap;font-size:10px;color:#fbbf24;background:rgba(245,158,11,0.12);border:1px solid #f59e0b;border-radius:5px;padding:5px;margin-top:4px;"></div>
      <div class="row" style="justify-content:space-between;font-size:10.5px;margin-top:4px;">
        <span id="lbl-meio-fisico" style="color:#38bdf8;font-family:monospace;">Centro(50,50): X=258.00, Y=-39.50</span>
        <button class="btn btn-emerald" style="padding:3px 8px;font-size:10.5px;" onclick="aplicarConfigCoordenadas()">✓ Salvar A,B,C,D</button>
      </div>
      <div style="font-size:10px;color:var(--muted);margin:5px 0;">
        Os cantos A/B/C/D são os centros extremos no G54. Defina a origem somente pelo Home XY com chaves, não pelo G92.
      </div>
      <div class="row" style="justify-content:space-between;font-size:10px;">
        <button class="btn btn-sky" style="padding:2px 6px;font-size:9.5px;" onclick="resetarQuadrado297()">↺ Padrao 297x297 (A..D)</button>
        <button class="btn" style="padding:2px 6px;font-size:9.5px;" onclick="calibrarCantoMatriz('testar_perimetro')">▢ Perimetro A→B→C→D→A</button>
        <label style="display:flex;align-items:center;gap:3px;cursor:pointer;">
          <input type="checkbox" id="cfg-inv-jog-x" onchange="aplicarConfigCoordenadas()"> Inv. Jog X
        </label>
      </div>
    </div>

    <!-- EXECUCAO & COMANDOS USUAIS DO UGS -->
    <div class="card">
      <div class="card-title">
        <span>Execucao & Comandos Usuais do UGS</span>
        <select id="sel-wcs" style="width:95px;padding:2px 4px;font-size:10.5px;" onchange="mudarWCS(this.value)">
          <option value="G54">WCS: G54</option>
          <option value="G55">WCS: G55</option>
          <option value="G56">WCS: G56</option>
        </select>
      </div>
      <div class="grid-4">
        <button class="btn btn-emerald" onclick="iniciarJob()">▶ Iniciar</button>
        <button class="btn btn-amber" onclick="acaoUGS('hold')">❚❚ Hold (!)</button>
        <button class="btn btn-sky" onclick="acaoUGS('resume')">▶‖ Resume (~)</button>
        <button class="btn btn-rose" onclick="acaoUGS('abort')">■ Abortar</button>
      </div>
      <div class="grid-3">
        <button class="btn btn-purple" onclick="abrirModal('modal-home')">⌂ Home XY</button>
        <button class="btn btn-amber" onclick="acaoUGS('unlock')">🔓 Kill Alarm ($X)</button>
        <button class="btn btn-rose" onclick="acaoUGS('soft_reset')">⚡ Soft Reset</button>
      </div>
      <div class="grid-4" style="margin-bottom:0;">
        <button class="btn" onclick="acaoUGS('ir_g28')">⏎ Ir G28</button>
        <button class="btn" onclick="acaoUGS('salvar_g28')">⌖ Set G28.1</button>
        <button class="btn" onclick="acaoUGS('check_mode')">✓ Check ($C)</button>
        <button class="btn" onclick="acaoUGS('ir_zero_trabalho')">◎ Ir Origem A</button>
      </div>
    </div>

    <!-- JOG MANUAL UGS & CALIBRACAO ZERO -->
    <div class="card">
      <div class="card-title">
        <span>Jog Manual UGS ($J=G91) & Definir (0,0) Aqui</span>
        <button class="btn btn-rose" style="padding:2px 7px;font-size:10px;" onclick="acaoUGS('jog_cancel')">■ Parar Jog</button>
      </div>
      <div class="row" style="font-size:11px;margin-bottom:7px;">
        <span>Passo(mm):</span>
        <label><input type="radio" name="jog-step" value="0.5">0.5</label>
        <label><input type="radio" name="jog-step" value="1.0">1.0</label>
        <label><input type="radio" name="jog-step" value="2.5" checked>2.5</label>
        <label><input type="radio" name="jog-step" value="10">10</label>
        <label><input type="radio" name="jog-step" value="25">25</label>
      </div>
      <div class="jog-container">
        <div class="jog-pad">
          <button class="jog-btn" onclick="jog(-1, 1)">↖</button>
          <button class="jog-btn" onclick="jog(0, 1)">↑ Y+</button>
          <button class="jog-btn" onclick="jog(1, 1)">↗</button>
          <button class="jog-btn" onclick="jog(-1, 0)">← X-</button>
          <button class="jog-btn" style="background:#0284c7;" onclick="acaoUGS('ir_zero_trabalho')">A(0,0)</button>
          <button class="jog-btn" onclick="jog(1, 0)">→ X+</button>
          <button class="jog-btn" onclick="jog(-1, -1)">↙</button>
          <button class="jog-btn" onclick="jog(0, -1)">↓ Y-</button>
          <button class="jog-btn" onclick="jog(1, -1)">↘</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:5px;">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <button class="btn" onclick="acaoUGS('zerar_x')">Zerar X0</button>
            <button class="btn" onclick="acaoUGS('zerar_y')">Zerar Y0</button>
            <button class="btn" onclick="acaoUGS('ir_g30')">Ir G30</button>
            <button class="btn" onclick="acaoUGS('salvar_g30')">Set G30.1</button>
          </div>
        </div>
      </div>
    </div>

    <!-- CABECOTE Z: ORDEM EXATA [5, 6, 16, 27, 23, 17, 24, 25] -->
    <div class="card">
      <div class="card-title">
        <span>Cabecote Z: Ordem [5, 6, 16, 27, 23, 17, 24, 25]</span>
        <span style="font-size:10px;color:var(--accent);">Acionamento protegido</span>
      </div>
      <div style="border:1px solid rgba(148,163,184,.3);padding:8px;border-radius:6px;margin:5px 0 9px;">
        <div id="estado-modo-z" style="font-size:11px;font-weight:700;">Z fisico bloqueado por padrao</div>
        <div style="display:flex;gap:7px;margin-top:7px;flex-wrap:wrap;">
          <button class="btn btn-sky" onclick="alterarModoZ(false)">Somente XY (bloquear Z)</button>
          <button class="btn btn-amber" onclick="alterarModoZ(true)">Habilitar Z fisico</button>
        </div>
        <div style="font-size:10px;color:var(--muted);margin-top:6px;">
          Simulacao GRBL: Z sempre virtual. Teste fisico XY: Z desativado.
          Habilitar Z real exige Home XY e confirmacao explicita; reconectar,
          reiniciar GRBL ou executar Home XY volta a bloquear Z.
        </div>
      </div>
      <div class="gpio-grid">
        <button class="gpio-btn" onclick="testarNivelZ(1)">1:GPIO5</button>
        <button class="gpio-btn" onclick="testarNivelZ(2)">2:GPIO6</button>
        <button class="gpio-btn" onclick="testarNivelZ(3)">3:GPIO16</button>
        <button class="gpio-btn" onclick="testarNivelZ(4)">4:GPIO27</button>
        <button class="gpio-btn" onclick="testarNivelZ(5)">5:GPIO23</button>
        <button class="gpio-btn" onclick="testarNivelZ(6)">6:GPIO17</button>
        <button class="gpio-btn" onclick="testarNivelZ(7)">7:GPIO24</button>
        <button class="gpio-btn" onclick="testarNivelZ(8)">8:GPIO25</button>
        <button class="gpio-btn" style="background:#1e3a8a;border-color:#3b82f6;" onclick="acaoUGS('teste_t')">Teste 't'</button>
        <button class="gpio-btn" style="background:#581c87;border-color:#a855f7;" onclick="acaoUGS('seq_cabecote')">Seq 1..8</button>
      </div>
      <div class="row" style="font-size:11px;">
        <span>ON(ms):</span><input type="number" id="inp-on-ms" value="10" step="1" min="1" max="2000">
        <span>OFF(ms):</span><input type="number" id="inp-off-ms" value="10" step="1" min="1" max="2000">
        <span>PausaZ:</span><input type="number" id="inp-pausa-ms" value="10" step="1" min="0" max="2000">
        <span>Feed:</span><input type="number" id="inp-feed" value="900" step="50" min="50" max="5000">
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:10.5px;margin-top:4px;">
        <span>Quantidade de Pulsos em Cada Nivel (Z1 a Z8):</span>
        <div style="display:flex;gap:3px;align-items:center;">
          <span>Fixo:</span>
          <input type="number" id="inp-pulsos-fixo" value="7" style="width:42px;padding:2px 4px;">
          <button class="btn" style="padding:2px 5px;font-size:10px;" onclick="preencherPulsosFixo()">Todos=N</button>
          <button class="btn" style="padding:2px 5px;font-size:10px;" onclick="preencherPulsosEscada()">1..8</button>
        </div>
      </div>
      <div class="pulsos-grid">
        <div class="pulsos-cell">Z1(P5)<input type="number" id="p-nv-1" value="1" min="1" max="50"></div>
        <div class="pulsos-cell">Z2(P6)<input type="number" id="p-nv-2" value="2" min="1" max="50"></div>
        <div class="pulsos-cell">Z3(P16)<input type="number" id="p-nv-3" value="3" min="1" max="50"></div>
        <div class="pulsos-cell">Z4(P27)<input type="number" id="p-nv-4" value="4" min="1" max="50"></div>
        <div class="pulsos-cell">Z5(P23)<input type="number" id="p-nv-5" value="5" min="1" max="50"></div>
        <div class="pulsos-cell">Z6(P17)<input type="number" id="p-nv-6" value="6" min="1" max="50"></div>
        <div class="pulsos-cell">Z7(P24)<input type="number" id="p-nv-7" value="7" min="1" max="50"></div>
        <div class="pulsos-cell">Z8(P25)<input type="number" id="p-nv-8" value="8" min="1" max="50"></div>
      </div>
      <button class="btn btn-emerald" style="width:100%;margin-top:4px;" onclick="salvarConfigCabecote()">✓ Aplicar Milissegundos do Pulso & Pulsos por Nivel</button>
    </div>

    <!-- RESET SUPERIOR -->
    <div class="card">
      <div class="card-title">
        <span>↻ Reset Superior (4 Atuadores + Eixo X')</span>
        <label style="font-size:10.5px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="chk-auto-reset"> Auto pos-plot
        </label>
      </div>
      <button class="btn btn-purple" style="width:100%;margin-bottom:5px;" onclick="executarResetCompleto()">↻ Executar Ciclo Completo de Reset ('D')</button>
      <div class="grid-4" style="margin-bottom:0;">
        <button class="btn" onclick="acaoReset('descer')">↓ Descer</button>
        <button class="btn" onclick="acaoReset('subir')">↑ Subir</button>
        <button class="btn" onclick="acaoReset('varrer')">→ Varrer X'</button>
        <button class="btn" onclick="acaoReset('home')">← Retornar X' (reset)</button>
      </div>
    </div>
  </section>

  <!-- PAINEL DIREITO: VISUALIZACAO 2D/3D, UPLOAD IA, G-CODE E LOGS -->
  <section class="right-panel">
    <div class="top-bar">
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btn btn-sky" onclick="alternarVista()" id="btn-modo-vista">❖ Mapa Tatil 2D & 3D</button>
        <button class="btn btn-emerald" onclick="carregarPadrao('flor')">✿ Flor no Meio (50,50)</button>
        <button class="btn" onclick="carregarPadrao('domo')">◎ Domo</button>
        <button class="btn" onclick="carregarPadrao('rampa_x')">⇄ Rampa Calibracao X</button>
        <button class="btn" onclick="carregarPadrao('usp')">⊞ Logo USP</button>
      </div>
      <div style="display:flex;gap:6px;align-items:center;">
        <label class="btn btn-emerald" style="cursor:pointer;">
          ⬆ Carregar Imagem IA (ONNX)
          <input type="file" id="inp-file-img" accept="image/*" style="display:none;" onchange="enviarImagemIA(this)">
        </label>
        <label class="btn btn-sky" style="cursor:pointer;" title="XY vai ao GRBL; Z aos GPIOs">
          ⬆ Importar G-code X/Y + Z
          <input type="file" id="inp-file-gcode" accept=".gcode,.nc,.tap,.txt" style="display:none;" onchange="importarGcodeHibrido(this)">
        </label>
        <label style="display:flex;gap:5px;align-items:center;font-size:10px;" title="Desmarcado usa a configuracao Z atual; marcado usa ON/OFF/PULSOS do arquivo.">
          <input id="usar-parametros-gcode" type="checkbox" style="width:auto;" onchange="alterarPreferenciaGcode()"> Z do arquivo
        </label>
        <select id="sel-profundidade" style="width:125px;" onchange="reprocessarProfundidade()">
          <option value="distancia">Prof: Distancia EDT</option>
          <option value="luminancia">Prof: Luminancia</option>
          <option value="binario">Prof: Binario (Z8)</option>
        </select>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 10px;border:1px solid rgba(148,163,184,.32);border-radius:6px;margin-bottom:6px;">
      <strong style="font-size:11px;">Fonte da próxima plotagem:</strong>
      <button id="bot-fonte-matriz" class="btn btn-sky" onclick="selecionarFonteJob('matriz')">Matriz 100 × 100</button>
      <button id="bot-fonte-gcode" class="btn" onclick="selecionarFonteJob('gcode')">G-code importado</button>
      <span id="fonte-job-info" style="font-size:11px;flex:1;min-width:150px;" title="O G-code importado fica salvo na memoria ate ser removido ou o servidor reiniciar.">Matriz selecionada</span>
      <button id="bot-remover-gcode" class="btn btn-rose" onclick="removerGcodeSelecionado()">Remover G-code</button>
    </div>

    <div class="viz-split">
      <div class="canvas-card">
        <div class="card-title">
          <span id="titulo-mapa">Mapa Tatil 2D/3D — Origem Fisica (0,0) na Extremidade DIREITA</span>
          <span id="info-progresso" style="color:var(--accent);">Pronto</span>
        </div>
        <div class="canvas-wrap">
          <canvas id="canvas-matriz" width="620" height="620"></canvas>
        </div>
      </div>

      <div class="side-stack">
        <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0;">
          <div class="card-title">
            <span>⬇ Instrucoes G-CODE (X0 = Direita)</span>
            <span id="badge-qtd-pinos">0 pinos</span>
          </div>
          <div class="code-box" id="box-gcode">Carregando G-Code...</div>
        </div>

        <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0;">
          <div class="card-title">
            <span>⌨ Telemetria & Log de Eventos</span>
            <button class="btn" style="padding:1px 6px;font-size:10px;" onclick="abrirModal('modal-terminal')">Abrir Terminal</button>
          </div>
          <div class="log-box" id="box-logs">Inicializando...</div>
        </div>
      </div>
    </div>
  </section>
</div>

<!-- MODAL 1: CALIBRACAO DE HOME & PONTO (0,0) NA EXTREMIDADE DIREITA -->
<div class="modal-backdrop" id="modal-home">
  <div class="modal">
    <div class="modal-header">
      <span>⌂ Home XY automatico nas chaves — recuo de 5 mm (sem eixo Z)</span>
      <button class="btn btn-rose" onclick="fecharModais()">✕ Fechar</button>
    </div>
    <div class="modal-body">
      <div class="coord-banner">
        <strong>HOME XY AUTOMATICO POR CHAVES — SEM $H / SEM Z</strong><br>
        O SmartV42 busca X e Y simultaneamente em trechos coordenados de ate 10 mm
        por eixo (padrao 5 mm, 10 vezes maior que antes). A leitura do sensor
        continua ativa durante o movimento, para cancelar o jog ao detectar contato.
        Trechos maiores exigem espaco livre e chave funcionando: a USB nao garante
        parada instantanea no batente.
        Quando uma chave aciona, o Home interrompe o jog conjunto,
        confirma o contato, recua o eixo correspondente na distancia configurada
        e continua com o eixo pendente.
        <strong>Home XY so termina quando ambas as chaves estiverem livres.</strong><br>
        <strong>Recuo independente X e Y: 2 a 10 mm. Confira o espaco livre antes de aumentar.</strong><br>
        <strong>ATENCAO: os campos Busca max X e Busca max Y iniciam em apenas 30 mm.</strong>
        Se Y encontrar sua chave e X ainda estiver longe, X continuara ate atingir seu
        proprio limite de busca e parara, mesmo que o teto permitido seja 300 mm.
        O recuo de X/Y apos contato e configurado separadamente.
        Antes de aumentar a busca, confira a distancia
        fisica livre, pois os hard limits estao desativados neste modo.<br>
        A direcao inicial do Y foi sugerida como negativa porque o sentido fisico
        relatado estava invertido; confirme com um movimento curto antes do Home.
        O feed inicial do Home e 500 mm/min, independente do feed de plotagem.<br>
        Diagnostico sem movimento: com a alimentacao dos motores desligada e USB/GRBL ligados,
        abra Terminal UGS, envie ? e pressione/solte as chaves individualmente.
        Pn:X e Pn:Y devem aparecer somente durante cada acionamento.<br>
        <strong>Atencao:</strong> Com $21=1, atingir as chaves causa alarme. Esta rotina exige
        $22=0 e $21=0 confirmados pelo GRBL, sem alterar a EEPROM automaticamente.
        Nesse modo as chaves NAO funcionam como limites rigidos: uma parada de emergencia
        eletrica independente e protecao mecanica sao obrigatorias. O deslocamento
        maximo so limita o software; nao impede colisoes se houver falha de comunicacao.
      </div>
      <div class="card" style="display:flex;flex-direction:column;gap:7px;">
        <div class="card-title">Parametros da busca X/Y</div>
        <div class="row" style="gap:8px;flex-wrap:wrap;">
          <label>Direcao X no GRBL
            <select id="home-dir-x" style="width:95px;">
              <option value="">Escolher</option><option value="-1">X negativo</option><option value="1">X positivo</option>
            </select>
          </label>
          <label>Direcao Y no GRBL (invertido na bancada)
            <select id="home-dir-y" style="width:110px;">
              <option value="-1" selected>Y negativo</option><option value="1">Y positivo</option>
            </select>
          </label>
          <label title="O valor inicial e 30 mm, mesmo que o limite permitido seja 300 mm. Aumente somente com espaco livre confirmado.">Busca max X (mm) <input id="home-curso-x" type="number" min="1" max="300" step="1" value="30" style="width:75px;"></label>
          <label title="O valor inicial e 30 mm, mesmo que o limite permitido seja 300 mm. Aumente somente com espaco livre confirmado.">Busca max Y (mm) <input id="home-curso-y" type="number" min="1" max="300" step="1" value="30" style="width:75px;"></label>
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap;">
          <label title="Distancia de cada segmento da busca, nao o recuo apos o contato. Padrao 5 mm; ate 10 mm se o curso livre for confirmado.">Trecho de busca (mm) <input id="home-passo" type="number" min="0.1" max="10" step="any" value="5" style="width:70px;"></label>
          <label>Feed do Home (mm/min) <input id="home-feed" type="number" min="20" max="600" step="10" value="500" style="width:75px;"></label>
          <label>Recuo X (mm) <input id="home-recuo-x" type="number" min="2" max="10" step="0.5" value="5" style="width:65px;"></label>
          <label>Recuo Y (mm) <input id="home-recuo-y" type="number" min="2" max="10" step="0.5" value="5" style="width:65px;"></label>
          <label>G54 X real apos recuo <input id="home-coord-x" type="number" step="0.1" value="0" style="width:70px;"></label>
          <label>G54 Y real apos recuo <input id="home-coord-y" type="number" step="0.1" value="0" style="width:70px;"></label>
        </div>
        <label style="font-size:11px;display:flex;gap:6px;align-items:flex-start;">
          <input type="checkbox" id="home-confirmacao-seguranca" style="width:auto;">
          Confirmo que ha parada de emergencia fisica independente e protecoes
          que impedem colisao caso a Raspberry Pi ou o GRBL falhem.
        </label>
        <div class="row">
          <button class="btn btn-emerald" onclick="iniciarHomeXY()">⌂ Iniciar Home XY sincronizado</button>
          <button class="btn btn-rose" onclick="acaoUGS('abort')">■ Abortar / Reset GRBL</button>
        </div>
        <div id="home-xy-status" style="font-size:11px;font-family:monospace;">Home XY nao referenciado.</div>
      </div>
      <div class="grid-3">
        <button class="btn btn-sky" onclick="acaoUGS('ir_zero_trabalho')">◎ Ir ao ponto A (apos Home)</button>
        <button class="btn" onclick="abrirModal('modal-terminal')">⌨ Verificar $21, $22 e Pn:</button>
      </div>
      <div class="card">
        <div class="card-title"><span>Sentido dos motores · malha útil ~297 × 297 mm</span></div>
        <div class="row">
          <span>Se ao mandar ir para a esquerda o motor andar para a direita:</span>
          <button class="btn btn-amber" onclick="inverterMotorXGrbl()">⇄ Inverter Direcao do Motor X no GRBL ($3)</button>
          <button class="btn btn-amber" onclick="inverterMotorYGrbl()">⇄ Inverter Direcao do Motor Y no GRBL ($3)</button>
        </div>
        <div class="row" style="font-size:11px;color:var(--muted);">
          As coordenadas enviadas ao GRBL vem somente dos 4 cantos A, B, C, D (painel de calibracao).
          Para mudar o sentido, corrija os cantos ou use $3.
        </div>
      </div>
    </div>
  </div>
</div>

<!-- MODAL 2: TERMINAL SERIAL UGS INTEGRADO -->
<div class="modal-backdrop" id="modal-terminal">
  <div class="modal">
    <div class="modal-header">
      <span>⌨ Terminal Serial Integrado (Universal Gcode Sender + Comandos do Cabecote)</span>
      <button class="btn btn-rose" onclick="fecharModais()">✕ Fechar</button>
    </div>
    <div class="modal-body">
      <div style="font-size:11px;color:var(--muted);">
        Comandos: <code>$</code>, <code>$X</code>, <code>?</code>, <code>Home XY via interface</code>, <code>ORIGEM_DIREITA</code>, <code>ORIGEM_ESQUERDA</code>, <code>SINAL_X=-1</code>, <code>SINAL_X=1</code>, <code>MS=12,10,15</code>, <code>PULSOS_NIVEL=1,2,3,4,5,6,7,8</code>, <code>T</code>, <code>SEQ_Z</code>, <code>Z1..Z8</code>.
      </div>
      <div class="log-box" id="term-historico" style="height:280px;font-size:11.5px;"></div>
      <div class="row">
        <input type="text" id="inp-cmd-term" placeholder="Digite qualquer comando G-Code, GRBL ($3=1, $100=80) ou comando interno..." onkeydown="if(event.key==='Enter') enviarCmdTerminal()">
        <button class="btn btn-emerald" style="white-space:nowrap;" onclick="enviarCmdTerminal()">Enviar ⏎</button>
      </div>
    </div>
  </div>
</div>

<!-- MODAL 3: CONFIGURACAO EEPROM GRBL ($$) -->
<div class="modal-backdrop" id="modal-grbl">
  <div class="modal">
    <div class="modal-header">
      <span>⚙ Configuracao de Parametros do GRBL ($$ — Passos/mm, Direcao $3 e Limites)</span>
      <button class="btn btn-rose" onclick="fecharModais()">✕ Fechar</button>
    </div>
    <div class="modal-body">
      <div class="row">
        <button class="btn btn-sky" onclick="consultarSettingsGrbl()">↻ Ler Parametros Atuais ($$)</button>
        <button class="btn btn-amber" onclick="enviarRawGrbl('$21=0')">Desativar Hard Limits ($21=0)</button>
        <button class="btn btn-emerald" onclick="inverterMotorXGrbl()">⇄ Inverter Eixo X ($3)</button>
        <button class="btn btn-amber" onclick="inverterMotorYGrbl()">⇄ Inverter Eixo Y ($3)</button>
      </div>
      <div class="code-box" id="box-grbl-settings" style="height:260px;">Clique em "Ler Parametros Atuais ($$)" para listar a EEPROM do GRBL.</div>
    </div>
  </div>
</div>

<script>
let estadoMatriz = [];
let modoVista3D = false;
let posCabecote = { x_mm: 0, y_mm: 0, x_idx: 99, y_idx: 0, nivel_z: 0 };
let configCoord = { origem_x_na_direita: true, sinal_eixo_x: 1.0, inverter_jog_x: false };
let fonteJobAtiva = 'matriz';
let revisaoProjeto = null;
let nomeGcodeCarregado = null;

function abrirModal(id) {
  document.querySelectorAll('.modal-backdrop').forEach(m => m.classList.remove('open'));
  document.getElementById(id).classList.add('open');
}
function fecharModais() {
  document.querySelectorAll('.modal-backdrop').forEach(m => m.classList.remove('open'));
}

async function apiPost(acao, payload = {}) {
  const res = await fetch('/api/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: acao, ...payload })
  });
  let dados;
  try { dados = await res.json(); }
  catch (e) { return { ok: false, erro: 'Resposta invalida do servidor: ' + res.status }; }
  if (!res.ok) console.error('API Smart Matrix:', acao, dados);
  return dados;
}

function lerNumeroInput(id, padrao) {
  const v = parseFloat(document.getElementById(id).value);
  return Number.isFinite(v) ? v : padrao;
}

async function aplicarConfigCoordenadas() {
  const invJogX = document.getElementById('cfg-inv-jog-x').checked;
  const pa = [lerNumeroInput('inp-xa', 0.0), lerNumeroInput('inp-ya', 0.0)];
  const pb = [lerNumeroInput('inp-xb', 297.0), lerNumeroInput('inp-yb', 0.0)];
  const pc = [lerNumeroInput('inp-xc', 297.0), lerNumeroInput('inp-yc', -297.0)];
  const pd = [lerNumeroInput('inp-xd', 0.0), lerNumeroInput('inp-yd', -297.0)];
  const r = await apiPost('config_coordenadas', {
    inverter_jog_x: invJogX,
    ponto_a: pa,
    ponto_b: pb,
    ponto_c: pc,
    ponto_d: pd
  });
  if (!r.ok) { alert(r.erro || 'Calibracao recusada. Confira a distancia entre centros extremos.'); return; }
  if (r.coord) configCoord = r.coord;
  await carregarEstadoInicial();
}

async function resetarQuadrado297() {
  document.getElementById('inp-xa').value = '0.000';
  document.getElementById('inp-ya').value = '0.000';
  document.getElementById('inp-xb').value = '297.000';
  document.getElementById('inp-yb').value = '0.000';
  document.getElementById('inp-xc').value = '297.000';
  document.getElementById('inp-yc').value = '-297.000';
  document.getElementById('inp-xd').value = '0.000';
  document.getElementById('inp-yd').value = '-297.000';
  await aplicarConfigCoordenadas();
}

async function calibrarCantoMatriz(etapa) {
  const r = await apiPost('calibrar_caixa_fisica', { etapa });
  if (!r.ok) { alert(r.erro || 'Calibracao/posicionamento recusado.'); return; }
  if (r.coord) configCoord = r.coord;
  await carregarEstadoInicial();
  await atualizarStatus();
}

async function inverterMotorXGrbl() {
  await apiPost('inverter_motor_x_grbl');
  atualizarStatus();
}

async function inverterMotorYGrbl() {
  if (!confirm('O bit Y do $3 sera invertido no GRBL (mudanca fisica permanente).\n' +
               'Use somente com a CNC parada, Z desabilitado e com area livre.\n' +
               'Depois teste jog Y de 0,5 mm e refaca Home XY e a calibracao.\n' +
               'Nao inverta Y de novo se ja tiver corrigido o sentido no firmware. Continuar?')) return;
  const r = await apiPost('inverter_motor_y_grbl', { confirmado: true });
  if (!r.ok) alert(r.erro || 'Nao foi possivel inverter Y no GRBL.');
  else alert('Direcao Y invertida em $3=' + r.mascara +
             '. Confira o sentido com um jog curto antes de qualquer Home.');
  atualizarStatus();
}

async function conectarPortas() {
  const pGrbl = document.getElementById('sel-porta-grbl').value;
  const pReset = document.getElementById('sel-porta-reset').value;
  const resp = await apiPost('conectar_portas', { porta_grbl: pGrbl, porta_reset: pReset });
  if (!resp.ok) alert('Falha ao conectar: GRBL=' + resp.grbl + ', reset=' + resp.reset);
  atualizarStatus();
}

function obterPassoJog() {
  const sel = document.querySelector('input[name="jog-step"]:checked');
  return sel ? parseFloat(sel.value) : 2.5;
}

async function jog(dxSign, dySign) {
  const passo = obterPassoJog();
  const feed = parseFloat(document.getElementById('inp-feed').value) || 900;
  const resp = await apiPost('jog', { dx: dxSign * passo, dy: dySign * passo, feed });
  if (!resp.ok) alert(resp.erro || 'Jog bloqueado; confira estado GRBL e logs.');
  atualizarStatus();
}

async function acaoUGS(tipo) {
  if (tipo === 'abort') {
    // Nao espera atualizarStatus(), fetch de telemetria ou animacao.
    const painel = document.getElementById('info-progresso');
    if (painel) painel.textContent = 'ABORTAR solicitado: aguarde confirmacao do GRBL; use E-stop fisico se necessario.';
    try {
      const resp = await apiPost('ugs_cmd', { cmd: 'abort' });
      if (!resp.ok) alert(resp.erro || 'Falha na parada serial: USE PARADA FISICA.');
      else if (painel) painel.textContent = resp.mensagem || 'Comando de abortamento enviado ao GRBL.';
    } catch (erro) {
      alert('Sem confirmacao USB. USE A PARADA FISICA de emergencia.');
    }
    return;
  }
  const resp = await apiPost('ugs_cmd', { cmd: tipo });
  if (!resp.ok) alert(resp.erro || 'Comando nao executado.');
  atualizarStatus();
}

async function iniciarHomeXY() {
  const x = document.getElementById('home-dir-x').value;
  const y = document.getElementById('home-dir-y').value;
  const seguro = document.getElementById('home-confirmacao-seguranca').checked;
  if (!x || !y) { alert('Selecione as direcoes reais de busca X e Y no GRBL.'); return; }
  if (!seguro) { alert('Confirme a existencia de parada eletrica de emergencia independente.'); return; }
  const cursoX = Number(document.getElementById('home-curso-x').value);
  const cursoY = Number(document.getElementById('home-curso-y').value);
  const feedHome = Number(document.getElementById('home-feed').value);
  const trechoBusca = Number(document.getElementById('home-passo').value);
  if (!(cursoX >= 1 && cursoX <= 300 && cursoY >= 1 && cursoY <= 300 &&
        feedHome >= 20 && feedHome <= 600 &&
        trechoBusca >= 0.1 && trechoBusca <= 10)) {
    document.getElementById('home-xy-status').textContent =
      'Configure busca 1..300 mm, trecho 0,1..10 mm e feed 20..600 mm/min.';
    return;
  }
  const recuoX = Number(document.getElementById('home-recuo-x').value);
  const recuoY = Number(document.getElementById('home-recuo-y').value);
  if (!(recuoX >= 2 && recuoX <= 10 && recuoY >= 2 && recuoY <= 10)) {
    document.getElementById('home-xy-status').textContent =
      'Recuo X/Y deve estar entre 2 e 10 mm. Verifique o espaco livre.';
    return;
  }
  const valor = id => Number(document.getElementById(id).value);
  const resp = await apiPost('iniciar_home_xy', {
    direcao_x: Number(x), direcao_y: Number(y),
    curso_x: valor('home-curso-x'), curso_y: valor('home-curso-y'),
    passo: valor('home-passo'), feed: valor('home-feed'),
    recuo_x: recuoX, recuo_y: recuoY,
    coord_x: valor('home-coord-x'), coord_y: valor('home-coord-y'),
    seguranca_confirmada: seguro
  });
  document.getElementById('home-confirmacao-seguranca').checked = false;
  document.getElementById('home-xy-status').textContent =
    resp.ok ? (resp.mensagem || 'Home XY iniciado.') :
              (resp.erro || 'Falha ao iniciar Home XY.');
  atualizarStatus();
}

async function mudarWCS(wcs) {
  await apiPost('ugs_cmd', { cmd: 'wcs', wcs });
  atualizarStatus();
}

async function alterarModoZ(habilitar) {
  if (habilitar && !confirm(
    'Habilitar GPIOs e reles Z REAIS na Raspberry Pi?\n' +
    'Confirme Home XY concluido, area livre, parada fisica independente e cabecote protegido.'
  )) return;
  const resposta = await apiPost('definir_modo_z', { habilitar, confirmado: habilitar });
  if (!resposta.ok) alert(resposta.erro || 'Nao foi possivel alterar o modo Z.');
  await atualizarStatus();
}

async function testarNivelZ(nv) {
  const resultado = await apiPost('testar_z', { nivel: nv });
  if (!resultado.ok) alert('Falha no acionamento Z: ' + ((resultado.info && resultado.info.erro) || resultado.erro || 'consulte os logs'));
}

function preencherPulsosFixo() {
  const v = parseInt(document.getElementById('inp-pulsos-fixo').value) || 7;
  for (let i = 1; i <= 8; i++) document.getElementById('p-nv-' + i).value = v;
}

function preencherPulsosEscada() {
  for (let i = 1; i <= 8; i++) document.getElementById('p-nv-' + i).value = i;
}

async function salvarConfigCabecote() {
  const pulsos = [];
  for (let i = 1; i <= 8; i++) {
    pulsos.push(parseInt(document.getElementById('p-nv-' + i).value) || i);
  }
  const on_ms = lerNumeroInput('inp-on-ms', 10);
  const off_ms = lerNumeroInput('inp-off-ms', 10);
  const pausa_ms = lerNumeroInput('inp-pausa-ms', 10);
  const feed = lerNumeroInput('inp-feed', 900);
  const resp = await apiPost('config_cabecote', { on_ms, off_ms, pausa_ms, feed, pulsos_por_nivel: pulsos });
  if (!resp.ok) { alert(resp.erro || 'Parametros Z invalidos'); return; }
  await carregarEstadoInicial();
}

async function iniciarJob() {
  if (revisaoProjeto === null) { alert('Atualize o estado antes de iniciar uma plotagem.'); return; }
  const descricao = fonteJobAtiva === 'gcode' ? (nomeGcodeCarregado || 'G-code importado') : 'matriz 100 × 100';
  if (!confirm('Iniciar plotagem usando: ' + descricao + '?\nA fonte e os parametros serao congelados ate o fim do job.')) return;
  const resposta = await apiPost('iniciar_job', {
    fonte_esperada: fonteJobAtiva, revisao_esperada: revisaoProjeto
  });
  if (!resposta.ok) {
    alert(resposta.erro || 'Job nao iniciado; consulte os logs.');
    await carregarEstadoInicial();
  }
  atualizarStatus();
}

async function selecionarFonteJob(fonte) {
  const resposta = await apiPost('selecionar_fonte_job', { fonte });
  if (!resposta.ok) { alert(resposta.erro || 'Fonte nao selecionada.'); return; }
  await carregarEstadoInicial();
}

async function removerGcodeSelecionado() {
  // Remove somente o arquivo importado em memoria, sem movimentar motores.
  const resposta = await apiPost('remover_gcode_importado', { confirmado: true });
  if (!resposta.ok) { alert(resposta.erro || 'Nao foi possivel remover o G-code.'); return; }
  await carregarEstadoInicial();
}

async function alterarPreferenciaGcode() {
  if (!nomeGcodeCarregado) return; // antes de importar: a caixa escolhe a preferencia do novo arquivo
  const escolha = !!document.getElementById('usar-parametros-gcode').checked;
  const resposta = await apiPost('definir_parametros_gcode', { usar_parametros_arquivo: escolha });
  if (!resposta.ok) alert(resposta.erro || 'Preferencia de parametros nao alterada.');
  await carregarEstadoInicial();
}

async function executarResetCompleto() {
  const resp = await apiPost('reset_mecanico', { subcmd: 'completo' });
  if (!resp.ok) { alert(resp.erro || 'Reset nao confirmado pelo Arduino.'); return; }
  await carregarEstadoInicial();
}

async function acaoReset(subcmd) {
  const resp = await apiPost('reset_mecanico', { subcmd });
  if (!resp.ok) alert(resp.erro || 'Comando de reset nao confirmado.');
}

async function carregarPadrao(padrao) {
  const resposta = await apiPost('gerar_padrao', { padrao });
  if (!resposta.ok) { alert(resposta.erro || 'Padrao nao gerado.'); return; }
  await carregarEstadoInicial();
}

async function reprocessarProfundidade() {
  const modo = document.getElementById('sel-profundidade').value;
  const resp = await apiPost('mudar_profundidade', { modo });
  if (!resp.ok) { alert(resp.erro || 'Nao foi possivel reprocessar.'); return; }
  await carregarEstadoInicial();
}

async function importarGcodeHibrido(input) {
  if (!input.files || !input.files[0]) return;
  const arquivo = input.files[0];
  input.value = '';
  if (arquivo.size > 3000000) {
    alert('Arquivo maior que 3 MB. Divida a trajetoria.');
    return;
  }
  const texto = await arquivo.text();
  const usar_parametros_arquivo = document.getElementById('usar-parametros-gcode').checked;
  const resp = await apiPost('carregar_gcode', { nome: arquivo.name, conteudo: texto, usar_parametros_arquivo });
  if (!resp.ok) {
    alert('Erro ao importar G-code: ' + (resp.erro || 'formato nao suportado'));
    return;
  }
  await carregarEstadoInicial();
  const painel = document.getElementById('info-progresso');
  if (painel) painel.textContent = 'G-code validado: ' + resp.blocos +
    ' blocos. Z nao sera enviado ao GRBL.';
}

async function enviarImagemIA(input) {
  if (!input.files || !input.files[0]) return;
  const reader = new FileReader();
  reader.onload = async function(e) {
    const modo = document.getElementById('sel-profundidade').value;
    const resp = await apiPost('upload_imagem', { imagem_b64: e.target.result, modo_profundidade: modo });
    if (!resp.ok) { alert(resp.erro || 'Falha ao carregar imagem.'); return; }
    await carregarEstadoInicial();
  };
  reader.readAsDataURL(input.files[0]);
}

async function enviarCmdTerminal() {
  const inp = document.getElementById('inp-cmd-term');
  const cmd = inp.value.trim();
  if (!cmd) return;
  inp.value = '';
  const r = await apiPost('terminal_cmd', { cmd });
  if (!r.ok) alert(r.erro || r.resp || 'Erro no terminal integrado.');
  if (r.historico) {
    const box = document.getElementById('term-historico');
    box.textContent = r.historico.slice().reverse().map(h => `[${h.hora}] (${h.dir}) ${h.msg}`).join('\n');
    box.scrollTop = box.scrollHeight;
  }
  await carregarEstadoInicial();
}

async function consultarSettingsGrbl() {
  const r = await apiPost('ler_settings_grbl');
  document.getElementById('box-grbl-settings').textContent = (r.linhas || []).join('\n') || 'Sem resposta.';
}

async function enviarRawGrbl(cmd) {
  await apiPost('terminal_cmd', { cmd });
  await consultarSettingsGrbl();
}

function alternarVista() {
  modoVista3D = !modoVista3D;
  desenharMatriz();
}

async function exportarGCode() {
  // Recupera TODAS as linhas; a caixa na interface exibe somente uma previa.
  const resposta = await fetch('/api/matrix');
  const dados = await resposta.json();
  const texto = (dados.gcode || []).join('\n') + '\n';
  const url = URL.createObjectURL(new Blob([texto], {type:'text/plain;charset=utf-8'}));
  const a = document.createElement('a');
  a.href = url;
  a.download = dados.gcode_importado ? 'smart_matrix_importado.gcode' : 'smart_matrix_v42.gcode';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function desenharMatriz() {
  const canvas = document.getElementById('canvas-matriz');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.fillStyle = '#070b16';
  ctx.fillRect(0, 0, W, H);

  if (!estadoMatriz || !estadoMatriz.length) return;
  const rows = estadoMatriz.length;
  const cols = estadoMatriz[0].length;
  const cores = ['#0f172a', '#1e3a8a', '#1d4ed8', '#0284c7', '#0891b2', '#0d9488', '#10b981', '#f59e0b', '#f43f5e'];

  if (!modoVista3D) {
    const pad = 28;
    const cellW = (W - pad * 2) / cols;
    const cellH = (H - pad * 2) / rows;

    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const nv = estadoMatriz[y][x] || 0;
        if (nv === 0) continue;
        ctx.fillStyle = cores[Math.min(8, nv)];
        ctx.fillRect(pad + x * cellW, pad + y * cellH, Math.max(1.5, cellW - 0.4), Math.max(1.5, cellH - 0.4));
      }
    }

    ctx.strokeStyle = '#334155';
    ctx.strokeRect(pad, pad, W - pad * 2, H - pad * 2);

    ctx.font = 'bold 11px monospace';
    if (configCoord.usar_janela_plotagem) {
      const xMin = configCoord.plot_x_min ?? 246;
      const xMax = configCoord.plot_x_max ?? 270;
      const yTopo = configCoord.plot_y_min ?? -50;
      const yBase = configCoord.plot_y_max ?? -29;
      ctx.fillStyle = '#f43f5e';
      ctx.fillText(`(${xMin}, ${yTopo})`, pad + 2, 18);
      ctx.fillStyle = '#fbbf24';
      ctx.fillText(`(${xMax}, ${yTopo})`, W - pad - 88, 18);
      ctx.fillStyle = '#10b981';
      ctx.fillText(`(${xMin}, ${yBase})`, pad + 2, H - 8);
      ctx.fillStyle = '#38bdf8';
      ctx.fillText(`(${xMax}, ${yBase})`, W - pad - 88, H - 8);
    } else {
      const pa = configCoord.ponto_a || [0, 0];
      const pb = configCoord.ponto_b || [297, 0];
      const pc = configCoord.ponto_c || [297, -297];
      const pd = configCoord.ponto_d || [0, -297];
      ctx.fillStyle = '#f43f5e';
      ctx.fillText(`D(${pd[0]}, ${pd[1]})`, pad + 2, 18);
      ctx.fillStyle = '#fbbf24';
      ctx.fillText(`C(${pc[0]}, ${pc[1]})`, W - pad - 108, 18);
      ctx.fillStyle = '#10b981';
      ctx.fillText(`A(${pa[0]}, ${pa[1]})`, pad + 2, H - 8);
      ctx.fillStyle = '#38bdf8';
      ctx.fillText(`B(${pb[0]}, ${pb[1]})`, W - pad - 92, H - 8);
    }

    const cx = pad + (posCabecote.x_idx + 0.5) * cellW;
    const cy = pad + (posCabecote.y_idx + 0.5) * cellH;
    ctx.strokeStyle = '#f43f5e';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, 7, 0, Math.PI * 2);
    ctx.stroke();
  } else {
    const cx0 = W * 0.5, cy0 = H * 0.16;
    const scale = 2.65;
    for (let y = 0; y < rows; y += 2) {
      for (let x = 0; x < cols; x += 2) {
        const nv = estadoMatriz[y][x] || 0;
        const isoX = cx0 + (x - y) * scale;
        const isoY = cy0 + (x + y) * (scale * 0.52) - nv * 3.2;
        ctx.fillStyle = nv > 0 ? cores[Math.min(8, nv)] : '#162038';
        ctx.fillRect(isoX, isoY, 3.2, 3.2 + nv * 2.2);
      }
    }
  }
}

async function carregarEstadoInicial() {
  const res = await fetch('/api/matrix');
  const d = await res.json();
  estadoMatriz = d.matriz || [];
  if (d.fontes_job) {
    const f = d.fontes_job;
    fonteJobAtiva = f.fonte_ativa;
    revisaoProjeto = f.revisao;
    nomeGcodeCarregado = f.nome_gcode;
    const nome = f.nome_gcode || '(nenhum arquivo importado)';
    document.getElementById('fonte-job-info').textContent = fonteJobAtiva === 'gcode'
      ? 'Selecionado: ' + nome + ' · Z: ' + (f.usar_parametros_arquivo ? 'arquivo' : 'painel')
      : 'Selecionado: matriz física · G-code guardado: ' + nome;
    document.getElementById('bot-fonte-gcode').disabled = !f.gcode_importado;
    document.getElementById('bot-remover-gcode').disabled = !f.gcode_importado;
    document.getElementById('bot-fonte-matriz').style.opacity = fonteJobAtiva === 'matriz' ? '1' : '.6';
    document.getElementById('bot-fonte-gcode').style.opacity = fonteJobAtiva === 'gcode' ? '1' : '.6';
  }
  if (d.coord) {
    configCoord = d.coord;
    document.getElementById('cfg-inv-jog-x').checked = !!d.coord.inverter_jog_x;
    if (d.coord.ponto_a) {
      document.getElementById('inp-xa').value = d.coord.ponto_a[0];
      document.getElementById('inp-ya').value = d.coord.ponto_a[1];
      document.getElementById('home-coord-x').value = d.coord.ponto_a[0];
      document.getElementById('home-coord-y').value = d.coord.ponto_a[1];
      document.getElementById('inp-xb').value = d.coord.ponto_b[0];
      document.getElementById('inp-yb').value = d.coord.ponto_b[1];
      document.getElementById('inp-xc').value = d.coord.ponto_c[0];
      document.getElementById('inp-yc').value = d.coord.ponto_c[1];
      document.getElementById('inp-xd').value = d.coord.ponto_d[0];
      document.getElementById('inp-yd').value = d.coord.ponto_d[1];
    }
    document.getElementById('lbl-meio-fisico').textContent = `Centro (49,5;49,5): X=${d.coord.x_meio_fisico}, Y=${d.coord.y_meio_fisico}`;
    document.getElementById('janela-matriz-info').textContent = d.coord.usar_janela_plotagem
      ? `Recorte: X[${d.coord.plot_x_min},${d.coord.plot_x_max}], Y[${d.coord.plot_y_min},${d.coord.plot_y_max}] · passo físico não muda`
      : 'Malha completa 100 × 100 · passo 3 mm · 297 mm entre centros extremos · faixa nominal 299 mm';
    const av = document.getElementById('lbl-avisos');
    av.style.display = (d.coord.avisos && d.coord.avisos.length) ? 'block' : 'none';
    av.textContent = (d.coord.avisos || []).map(a => '⚠ ' + a).join('\n');
    document.getElementById('badge-origem').textContent = `⌖ Centro(50,50): X=${d.coord.x_meio_fisico}, Y=${d.coord.y_meio_fisico}`;
  }
  document.getElementById('box-gcode').textContent = (d.gcode || []).slice(0, 180).join('\n');
  document.getElementById('badge-qtd-pinos').textContent = d.gcode_importado ? String(d.linhas_importadas || 0) + ' linhas importadas' : String(d.pinos_ativos || 0) + ' pinos';
  if (d.gcode_importado) document.getElementById('usar-parametros-gcode').checked = !!d.usar_parametros_arquivo;
  desenharMatriz();
}

async function atualizarStatus() {
  try {
    const res = await fetch('/api/status');
    const st = await res.json();
    document.getElementById('dro-wx').textContent = `${st.wpos[0].toFixed(2)} mm`;
    document.getElementById('dro-wy').textContent = `${st.wpos[1].toFixed(2)} mm`;
    document.getElementById('dro-wz').textContent = `Nivel ${st.job.nivel_z || 0}`;
    document.getElementById('dro-mx').textContent = `${st.mpos[0].toFixed(2)} mm`;
    document.getElementById('dro-my').textContent = `${st.mpos[1].toFixed(2)} mm`;
    document.getElementById('grbl-state-badge').textContent = `● ${st.estado_grbl} (${st.wcs})`;
    document.getElementById('status-porta-pill').textContent = `● ${st.porta_grbl}`;
    document.getElementById('badge-status-porta').textContent = `Porta: ${st.porta_grbl}`;
    if (st.modo_z) {
      const nomesModo = {
        simulacao: 'Simulacao integral — relés fisicos Z desativados',
        teste_xy: 'Teste fisico XY — Z bloqueado',
        completo: 'Operacao fisica completa — Z habilitado',
        desconectado: 'GRBL desconectado — Z bloqueado',
        inconsistente: 'Falha de modo — Z bloqueado'
      };
      const el = document.getElementById('estado-modo-z');
      el.textContent = nomesModo[st.modo_z.modo] || st.modo_z.mensagem;
      el.style.color = st.modo_z.fisico_habilitado ? '#fbbf24' : '#38bdf8';
    }

    const x_idx_calc = st.idx ? st.idx[0] : 0;   // coluna, calculada no servidor pelos 4 cantos
    const y_idx_calc = st.idx ? st.idx[1] : 0;   // linha

    posCabecote = {
      x_mm: st.wpos[0],
      y_mm: st.wpos[1],
      x_idx: st.job.ativo ? st.job.x_idx : x_idx_calc,
      y_idx: st.job.ativo ? st.job.y_idx : y_idx_calc,
      nivel_z: st.job.nivel_z || 0
    };
    document.getElementById('dro-pn').textContent = `Pn:${st.pinos_ativos || 'Nenhum'} | Col:${posCabecote.x_idx},Lin:${posCabecote.y_idx}`;

    if (st.home_xy) {
      const h = st.home_xy;
      const resumo = h.ativo ? ('Buscando ' + (h.eixo || 'XY') + ': ' + h.fase)
          : h.referenciado ? 'Home XY referenciado; ambos sensores liberados'
          : h.erro ? 'Home nao concluido: ' + h.erro : 'Home XY nao referenciado';
      document.getElementById('home-xy-status').textContent = resumo;
      if (!h.ativo && h.erro) {
        const painel = document.getElementById('info-progresso');
        if (painel) painel.textContent = 'Home XY interrompido: ' + h.erro;
      }
    }
    if (st.home_xy && st.home_xy.ativo) {
      document.getElementById('info-progresso').textContent = 'Home XY: ' + st.home_xy.fase;
    } else if (st.job && st.job.ativo) {
      document.getElementById('info-progresso').textContent = `${st.job.nome_programa || 'Job'}: ${st.job.atual}/${st.job.total} (X=${st.job.x_mm}mm, Y=${st.job.y_mm}mm)`;
    } else {
      document.getElementById('info-progresso').textContent =
        st.job.erro ? 'Falha: ' + st.job.erro : (st.job.concluido ? 'Concluido' : 'Pronto');
    }
    if (st.logs) {
      const lb = document.getElementById('box-logs');
      lb.textContent = st.logs.slice().reverse().map(l => `[${l.hora}] ${l.msg}`).join('\n');
      lb.scrollTop = lb.scrollHeight;
    }
    desenharMatriz();
  } catch (e) {
    console.error('Falha ao atualizar telemetria Smart Matrix:', e);
    document.getElementById('info-progresso').textContent = 'Telemetria indisponivel';
  }
}

window.addEventListener('DOMContentLoaded', async () => {
  await carregarEstadoInicial();
  await atualizarStatus();
  setInterval(atualizarStatus, 650);
});
</script>
</body>
</html>
"""


# ==============================================================================
# SECAO 7: SERVIDOR HTTP/JSON E PONTO DE ENTRADA PRINCIPAL
# ==============================================================================

_grbl_global = None
_reset_global = None
# Adaptadores legados para a API, telemetria e testes: centralizar MUTACOES
# em RepositorioProjetoStudio, sem mudar o formato HTTP nem o HTML atual.
_matriz_atual = None
_gcode_atual = []
_trajetoria_atual = []
estado_importacao_gcode = {"linhas": None, "programa": None, "nome": None, "usar_parametros_arquivo": False}
_imagem_origem = None
_estado_projeto_lock = threading.RLock()
# Fonte e numero de revisao pertencem somente ao RepositorioProjetoStudio.


class RepositorioProjetoStudio:
    """Dono das transacoes de edicao da matriz, importacao e fonte ativa.

    Os nomes globais antigos continuam apenas como ponte temporaria para
    codigo externo: mutacoes de projeto sao concentradas neste componente.
    O lock e compartilhado com a API para leitura consistente do preview.
    """
    def __init__(self, lock):
        self.lock = lock
        self._fonte = "matriz"
        self._revisao = 0
        self.importacao = estado_importacao_gcode

    def resumo(self):
        with self.lock:
            importado = self.importacao["programa"] is not None
            return {
                "fonte_ativa": self._fonte,
                "revisao": self._revisao,
                "gcode_importado": importado,
                "nome_gcode": self.importacao.get("nome") if importado else None,
                "blocos_gcode": len(self.importacao["programa"] or []),
                "usar_parametros_arquivo": bool(self.importacao.get("usar_parametros_arquivo", False)),
                "matriz_disponivel": _matriz_atual is not None,
            }

    def fonte_ativa(self):
        with self.lock:
            return self._fonte

    def selecionar(self, fonte):
        if fonte not in ("matriz", "gcode"):
            raise ValueError("Fonte deve ser matriz ou gcode.")
        with self.lock:
            if fonte == "gcode" and self.importacao["programa"] is None:
                raise ValueError("Nenhum G-code importado selecionavel.")
            if fonte == "matriz" and _matriz_atual is None:
                raise ValueError("Nenhuma matriz disponivel.")
            if fonte != self._fonte:
                self._fonte = fonte
                self._revisao += 1
            return self.resumo()

    def remover_importacao(self):
        with self.lock:
            self.importacao.update({
                "linhas": None, "programa": None, "nome": None,
                "usar_parametros_arquivo": False,
            })
            self._fonte = "matriz"
            self._revisao += 1
            return self.resumo()

    def publicar_matriz(self, nova_matriz, modo_operacao, selecionar_matriz):
        global _matriz_atual, _gcode_atual, _trajetoria_atual
        nova = np.array(nova_matriz, dtype=np.uint8, copy=True)
        if nova.shape != TAMANHO_MATRIZ:
            raise ValueError(f"Matriz deve ter dimensoes {TAMANHO_MATRIZ}.")
        with self.lock:
            # O resultado e publicado somente se toda a compilacao concluir.
            linhas, trajetoria, _ = compilar_matriz_para_estudio(nova, modo_operacao)
            _matriz_atual = nova
            _gcode_atual = list(linhas)
            _trajetoria_atual = list(trajetoria)
            if selecionar_matriz:
                self._fonte = "matriz"
            self._revisao += 1

    def importar(self, conteudo, nome, usar_parametros_arquivo=False):
        with self.lock:
            # Uma importacao invalida jamais substitui o arquivo anterior.
            programa = analisar_gcode_hibrido(conteudo)
            linhas = tuple(conteudo.splitlines())
            self.importacao.update({
                "linhas": linhas,
                "programa": tuple(copy.deepcopy(programa)),
                "nome": str(nome or "arquivo.gcode")[:120],
                "usar_parametros_arquivo": bool(usar_parametros_arquivo),
            })
            self._fonte = "gcode"
            self._revisao += 1
            return self.resumo()

    def definir_preferencia_gcode(self, valor):
        if not isinstance(valor, bool):
            raise ValueError("A preferencia deve ser um booleano.")
        with self.lock:
            if self.importacao["programa"] is None:
                raise ValueError("Importe um G-code antes de definir seus parametros.")
            if self.importacao["usar_parametros_arquivo"] != valor:
                self.importacao["usar_parametros_arquivo"] = valor
                self._revisao += 1
            return self.resumo()

    def preparar_plano(self, fonte_esperada=None, revisao_esperada=None):
        """Copia programacao e parametros sem fazer qualquer movimento."""
        with self.lock:
            fonte = self._fonte
            if fonte_esperada is not None and fonte_esperada != fonte:
                return None, "Fonte alterada na interface. Atualize o painel antes de iniciar."
            if revisao_esperada is not None and int(revisao_esperada) != self._revisao:
                return None, "O projeto foi modificado depois da ultima visualizacao. Confira programa e parametros."
            if fonte == "gcode":
                programa_fonte = self.importacao["programa"]
                if not programa_fonte:
                    return None, "Arquivo G-code selecionado nao esta disponivel."
                programa = copy.deepcopy(list(programa_fonte))
                nome = self.importacao.get("nome") or "arquivo.gcode"
                usar_arquivo = bool(self.importacao["usar_parametros_arquivo"])
            else:
                if _matriz_atual is None or not np.any(_matriz_atual):
                    return None, "Matriz vazia ou indisponivel."
                if not _gcode_atual:
                    return None, "G-code da matriz ainda nao foi compilado."
                programa = analisar_gcode_hibrido("\n".join(_gcode_atual))
                nome = "Matriz 100x100"
                usar_arquivo = False
            snapshot = capturar_snapshot_parametros_job()
            return PlanoJobStudio.criar(fonte, nome, usar_arquivo, programa, snapshot), ""


@dataclass(frozen=True)
class PlanoJobStudio:
    """Plano puramente de dados: nao guarda ponteiros para um projeto editavel."""
    origem: str
    nome_programa: str
    usar_parametros_arquivo: bool
    blocos: tuple
    configuracao: ConfiguracaoExecucao

    @classmethod
    def criar(cls, origem, nome, usar_arquivo, programa, configuracao):
        # Blocos do parser possuem apenas valores escalares imutaveis.
        # Tuplas de pares evitam aliasing com a lista original importada.
        blocos = tuple(tuple(sorted(b.items())) for b in programa)
        return cls(
            origem=origem, nome_programa=nome, usar_parametros_arquivo=bool(usar_arquivo),
            blocos=blocos, configuracao=ConfiguracaoExecucao.de_snapshot(configuracao),
        )

    def programa(self):
        return [dict(b) for b in self.blocos]


_repositorio_projeto = RepositorioProjetoStudio(_estado_projeto_lock)


# Funcoes antigas permanecem como ADAPTADORES. Novos caminhos usam
# RepositorioProjetoStudio + PlanoJobStudio + MotorExecucaoGCode.
def obter_fonte_job_ativa():
    return _repositorio_projeto.fonte_ativa()


def obter_estado_fontes_job():
    return _repositorio_projeto.resumo()


def selecionar_fonte_job(fonte):
    return _repositorio_projeto.selecionar(fonte)


def remover_gcode_importado():
    return _repositorio_projeto.remover_importacao()


def atualizar_matriz_global(nova_matriz, modo_operacao="Domo Gaussiano 3D", descartar_importacao=False):
    """descartar_importacao e um alias historico de selecionar_matriz."""
    return _repositorio_projeto.publicar_matriz(nova_matriz, modo_operacao, descartar_importacao)


def carregar_gcode_importado(conteudo, nome, usar_parametros_arquivo=False):
    return _repositorio_projeto.importar(conteudo, nome, usar_parametros_arquivo)


def definir_preferencia_parametros_gcode(valor):
    return _repositorio_projeto.definir_preferencia_gcode(valor)


class PreparadorJobStudio:
    """Faz a ponte entre plano de dados e unico executor de hardware."""
    def __init__(self, repositorio):
        self.repositorio = repositorio

    def iniciar(self, grbl, reset_ctrl, fonte_esperada=None, revisao_esperada=None):
        # Preserva lock da fonte ate confirmar a reserva do executor,
        # evitando um job de outra revisao por corrida entre requests.
        with self.repositorio.lock:
            plano, erro = self.repositorio.preparar_plano(fonte_esperada, revisao_esperada)
            if plano is None:
                return False, erro
            ok = executar_job_gcode_em_background(
                grbl, plano.programa(),
                reset_ctrl=reset_ctrl if plano.origem == "matriz" else None,
                origem=plano.origem,
                usar_parametros_arquivo=plano.usar_parametros_arquivo,
                parametros_job=plano.configuracao,
                nome_programa=plano.nome_programa,
            )
            if not ok:
                return False, "Job nao iniciado: maquina ocupada, nao referenciada ou validacao falhou."
            return True, f"Job iniciado com snapshot do programa {plano.nome_programa} ({plano.origem})."


_preparador_jobs = PreparadorJobStudio(_repositorio_projeto)


def iniciar_job_selecionado(grbl, reset_ctrl, fonte_esperada=None, revisao_esperada=None):
    return _preparador_jobs.iniciar(grbl, reset_ctrl, fonte_esperada, revisao_esperada)


class StudioRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _enviar_json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            corpo = HTML_CONCEITUAL.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return

        if parsed.path == "/api/matrix":
            with _estado_projeto_lock:
                fonte = obter_estado_fontes_job()
                matriz = _matriz_atual.tolist() if _matriz_atual is not None else []
                gcode = (list(estado_importacao_gcode["linhas"] or []) if _repositorio_projeto.fonte_ativa() == "gcode"
                         else list(_gcode_atual))
                pins = int(np.count_nonzero(_matriz_atual)) if _matriz_atual is not None else 0
                linhas_importadas = len(estado_importacao_gcode["linhas"] or [])
                coord = obter_estado_coordenadas()
            self._enviar_json({
                "matriz": matriz, "gcode": gcode, "gcode_importado": fonte["gcode_importado"],
                "usar_parametros_arquivo": fonte["usar_parametros_arquivo"],
                "linhas_importadas": linhas_importadas,
                "pinos_ativos": pins, "coord": coord, "fontes_job": fonte,
            })
            return

        if parsed.path == "/api/status":
            _grbl_global.atualizar_status()
            self._enviar_json({
                "porta_grbl": _grbl_global.porta_atual,
                "simulado": bool(_grbl_global.modo_simulacao and not _grbl_global.conexao_real),
                "modo_z": obter_estado_modo_z(),
                "estado_grbl": _grbl_global.estado,
                "wpos": _grbl_global.wpos,
                "mpos": _grbl_global.mpos,
                "pinos_ativos": _grbl_global.pinos_pn,
                "wcs": _grbl_global.wcs_ativo,
                "coord": obter_estado_coordenadas(),
                "idx": list(mm_fisico_para_indice(_grbl_global.wpos[0], _grbl_global.wpos[1])),
                "job": _job_progresso,
                "home_xy": dict(_grbl_global.home_xy_estado, referenciado=_grbl_global.home_xy_referenciado),
                "logs": list(ESTADO_ESTUDIO["logs"])[:35],  # mais recentes primeiro
            })
            return

        self._enviar_json({"erro": "Rota nao encontrada"}, 404)

    def do_POST(self):
        global _job_pausado, _job_abortar, MODO_PROFUNDIDADE, AUTO_RESET_APOS_PLOT, FEED_RATE
        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho > 6_000_000:
            self._enviar_json({"ok": False, "erro": "Requisicao maior que 6 MB."}, 413)
            return
        try:
            bruto = self.rfile.read(tamanho).decode("utf-8") if tamanho > 0 else "{}"
            dados = json.loads(bruto) if bruto else {}
            if not isinstance(dados, dict):
                raise ValueError("JSON deve conter um objeto.")
        except (UnicodeError, ValueError) as exc:
            self._enviar_json({"ok": False, "erro": f"Requisicao invalida: {exc}"}, 400)
            return
        acao = dados.get("action", "")
        # Serializa acoes manuais/mutacoes e reserva a maquina ate a sua conclusao.
        # Hold, resume, abort e iniciar_job nao podem depender dessa reserva.
        simultanea = acao in ("iniciar_job", "iniciar_home_xy") or (
            acao == "ugs_cmd" and dados.get("cmd") in ("hold", "resume", "abort")
        )
        if simultanea:
            return self._executar_acao_com_erros(acao, dados)
        if not reservar_operacao_manual():
            self._enviar_json({"ok": False, "erro": "Maquina ocupada: finalize ou aborte o trabalho atual."}, 409)
            return
        try:
            return self._executar_acao_com_erros(acao, dados)
        finally:
            liberar_operacao_manual()

    def _executar_acao_com_erros(self, acao, dados):
        try:
            return self._executar_acao_POST(acao, dados)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            registrar_log(f"Acao {acao} rejeitada: {exc}", "erro")
            self._enviar_json({"ok": False, "erro": str(exc)}, 400)
        except Exception as exc:
            registrar_log(f"Acao {acao} falhou: {exc}", "erro")
            self._enviar_json({"ok": False, "erro": "Falha interna na operacao; consulte os logs."}, 500)

    def _executar_acao_POST(self, acao, dados):
        global _job_pausado, _job_abortar, MODO_PROFUNDIDADE, AUTO_RESET_APOS_PLOT, FEED_RATE, _imagem_origem

        if acao == "carregar_gcode":
            conteudo = dados.get("conteudo", "")
            estado = carregar_gcode_importado(
                conteudo, dados.get("nome", "arquivo.gcode"),
                usar_parametros_arquivo=dados.get("usar_parametros_arquivo", False),
            )
            registrar_log(
                f"G-code importado e selecionado: {estado['nome_gcode']} "
                f"({estado['blocos_gcode']} blocos), independente da matriz.",
                "ok",
            )
            self._enviar_json({"ok": True, "blocos": estado["blocos_gcode"], "fontes_job": estado})
            return

        if acao == "selecionar_fonte_job":
            estado = selecionar_fonte_job(dados.get("fonte"))
            self._enviar_json({"ok": True, "fontes_job": estado})
            return

        if acao == "remover_gcode_importado":
            if dados.get("confirmado") is not True:
                self._enviar_json({"ok": False, "erro": "Confirmacao explicita necessaria para remover o G-code."}, 400)
                return
            estado = remover_gcode_importado()
            self._enviar_json({"ok": True, "fontes_job": estado})
            return

        if acao == "definir_parametros_gcode":
            estado = definir_preferencia_parametros_gcode(dados.get("usar_parametros_arquivo"))
            self._enviar_json({"ok": True, "fontes_job": estado})
            return

        if acao == "config_coordenadas":
            estado = configurar_sistema_coordenadas(
                origem_x_na_direita=dados.get("origem_x_na_direita"),
                sinal_eixo_x=dados.get("sinal_eixo_x"),
                sinal_eixo_y=dados.get("sinal_eixo_y"),
                inverter_jog_x=dados.get("inverter_jog_x"),
                inverter_jog_y=dados.get("inverter_jog_y"),
                ponto_a=dados.get("ponto_a"),
                ponto_b=dados.get("ponto_b"),
                ponto_c=dados.get("ponto_c"),
                ponto_d=dados.get("ponto_d"),
            )
            if _matriz_atual is not None:
                atualizar_matriz_global(_matriz_atual)
            self._enviar_json({"ok": True, "coord": estado})
            return

        if acao == "calibrar_caixa_fisica":
            etapa = dados.get("etapa", "")
            _grbl_global.atualizar_status()
            wx, wy = float(_grbl_global.wpos[0]), float(_grbl_global.wpos[1])
            if etapa.startswith("gravar_"):
                canto = etapa.split("_")[1]
                kw = {f"ponto_{canto}": [wx, wy]}
                estado = configurar_sistema_coordenadas(**kw)
                if _matriz_atual is not None:
                    atualizar_matriz_global(_matriz_atual)
                self._enviar_json({"ok": True, "coord": estado})
                return
            if etapa in ("ir_a", "ir_b", "ir_c", "ir_d"):
                mapa_pt = {"ir_a": PONTO_A, "ir_b": PONTO_B, "ir_c": PONTO_C, "ir_d": PONTO_D}[etapa]
                registrar_log(f"Movendo cabecote para Canto {etapa[-1].upper()} -> X={mapa_pt[0]:.3f}, Y={mapa_pt[1]:.3f}")
                ok, _ = _grbl_global.mover_xy_sincrono(mapa_pt[0], mapa_pt[1], feed=FEED_RATE)
                self._enviar_json({"ok": ok, "coord": obter_estado_coordenadas()})
                return
            if etapa in ("sync_a", "sync_b", "sync_c", "sync_d", "sync_meio"):
                self._enviar_json({
                    "ok": False,
                    "erro": "Zeramento G92 manual removido; a origem G54 e definida apenas pelo Home XY nas chaves."
                }, 409)
                return
            if etapa == "ir_meio":
                xm, ym = indice_para_mm_fisico(49.5, 49.5)
                registrar_log(f"Movendo cabecote para o CENTRO FISICO REAL da matriz (50,50) -> X={xm:.3f}, Y={ym:.3f}")
                ok, _ = _grbl_global.mover_xy_sincrono(xm, ym, feed=FEED_RATE)
                self._enviar_json({"ok": ok, "coord": obter_estado_coordenadas()})
                return
            if etapa == "testar_perimetro":
                def _rodar_perimetro():
                    rota = [("A", PONTO_A), ("B", PONTO_B), ("C", PONTO_C), ("D", PONTO_D), ("A", PONTO_A)]
                    registrar_log("Iniciando teste de perimetro: A -> B -> C -> D -> A...")
                    for nome_p, pt in rota:
                        registrar_log(f"Perimetro -> {nome_p} (X={pt[0]:.3f}, Y={pt[1]:.3f})")
                        ok_p, st_p = _grbl_global.mover_xy_sincrono(pt[0], pt[1], feed=FEED_RATE)
                        if not ok_p:
                            registrar_log(f"Perimetro interrompido em {nome_p}: {st_p}", "erro")
                            return
                    registrar_log("Teste de perimetro A -> B -> C -> D -> A concluido!", "ok")
                # Executa enquanto a reserva da maquina esta ativa.
                _rodar_perimetro()
                self._enviar_json({"ok": True, "coord": obter_estado_coordenadas()})
                return

        if acao == "inverter_motor_x_grbl":
            _, _, cfg_eep = _grbl_global.consultar_configuracoes_grbl()
            mascara_atual = 0
            if "$3" in cfg_eep:
                try:
                    mascara_atual = int(float(cfg_eep["$3"]))
                except Exception:
                    mascara_atual = 0
            nova_mascara = mascara_atual ^ 1  # Inverte bit 0 (Eixo X)
            ok = _grbl_global.gravar_parametro_grbl("$3", nova_mascara)
            self._enviar_json({"ok": ok, "mascara": nova_mascara})
            return

        if acao == "inverter_motor_y_grbl":
            # $3 bit 1 = Y. Aplicar somente apos confirmacao fisica explicita,
            # preservando a inversao existente dos demais eixos.
            if dados.get("confirmado") is not True:
                self._enviar_json({"ok": False, "erro": "Confirme a inversao fisica de Y."}, 400)
                return
            status = _grbl_global.ler_status_grbl()
            if not status.startswith("<Idle"):
                self._enviar_json({
                    "ok": False, "erro": f"GRBL precisa estar Idle para alterar $3: {status}"
                }, 409)
                return
            ok_cfg, resposta_cfg, cfg_eep = _grbl_global.consultar_configuracoes_grbl()
            if not ok_cfg or "$3" not in cfg_eep:
                self._enviar_json({
                    "ok": False,
                    "erro": f"Falha ao ler valor atual de $3. EEPROM nao alterada: {resposta_cfg}"
                }, 409)
                return
            try:
                mascara_atual = int(float(cfg_eep["$3"]))
                if not 0 <= mascara_atual <= 255:
                    raise ValueError("Mascara fora do intervalo.")
            except (TypeError, ValueError):
                self._enviar_json({"ok": False, "erro": "Valor $3 invalido."}, 409)
                return
            nova_mascara = mascara_atual ^ 2
            ok = _grbl_global.gravar_parametro_grbl("$3", nova_mascara)
            if ok:
                _grbl_global.invalidar_home_xy("Sentido do motor Y alterado na EEPROM: refaca Home XY.")
                registrar_log(
                    f"Motor Y invertido pelo operador: $3 {mascara_atual} -> {nova_mascara}. "
                    "Confira com jog curto antes de novo Home.", "aviso"
                )
            self._enviar_json({
                "ok": bool(ok), "mascara": nova_mascara if ok else mascara_atual,
                "erro": None if ok else "GRBL recusou atualizar $3."
            }, 200 if ok else 409)
            return

        if acao == "conectar_portas":
            p_grbl = dados.get("porta_grbl", _grbl_global.porta_atual)
            p_reset = dados.get("porta_reset", _reset_global.porta)
            ok_grbl = _grbl_global.conectar(p_grbl)
            # Conectar() desarma e fecha SEMPRE os GPIOs fisicos antes de trocar a porta.
            ok_reset = _reset_global.conectar(p_reset)
            self._enviar_json({"ok": bool(ok_grbl and ok_reset), "grbl": ok_grbl,
                               "reset": ok_reset, "modo_z": obter_estado_modo_z()})
            return

        if acao == "definir_modo_z":
            habilitar = dados.get("habilitar")
            if not isinstance(habilitar, bool):
                self._enviar_json({"ok": False, "erro": "Informe habilitar=true ou false."}, 400)
                return
            if habilitar:
                if dados.get("confirmado") is not True:
                    self._enviar_json({"ok": False, "erro": "Confirme explicitamente a habilitacao fisica de Z."}, 400)
                    return
                ok_z, mensagem = habilitar_z_fisico()
            else:
                ok_z = bloquear_z_fisico("Operador selecionou Teste fisico XY")
                mensagem = "Teste fisico XY ativo: reles Z bloqueados."
            self._enviar_json({"ok": ok_z, "mensagem": mensagem,
                               "erro": None if ok_z else mensagem, "modo_z": obter_estado_modo_z()},
                              200 if ok_z else 409)
            return

        if acao == "jog":
            dx = float(dados.get("dx", 0.0))
            dy = float(dados.get("dy", 0.0))
            feed = float(dados.get("feed", FEED_RATE))
            ok, _ = _grbl_global.executar_jog_ugs(dx=dx, dy=dy, feed=feed)
            self._enviar_json({"ok": ok})
            return

        if acao == "iniciar_home_xy":
            if dados.get("seguranca_confirmada") is not True:
                self._enviar_json({"ok": False, "erro": "Confirme a parada de emergencia fisica independente."}, 400)
                return
            direcao_x = int(dados.get("direcao_x", 0))
            direcao_y = int(dados.get("direcao_y", 0))
            if direcao_x not in (-1, 1) or direcao_y not in (-1, 1):
                self._enviar_json({"ok": False, "erro": "Selecione o sentido de busca GRBL de X e Y."}, 400)
                return
            curso_x = float(dados.get("curso_x", 30))
            curso_y = float(dados.get("curso_y", 30))
            passo = float(dados.get("passo", 5.0))
            feed = float(dados.get("feed", 500))
            recuo_x = float(dados.get("recuo_x", 5.0))
            recuo_y = float(dados.get("recuo_y", 5.0))
            coord_x = float(dados.get("coord_x", PONTO_A[0]))
            coord_y = float(dados.get("coord_y", PONTO_A[1]))
            if not all(math.isfinite(v) for v in (curso_x, curso_y, passo, feed, recuo_x, recuo_y, coord_x, coord_y)):
                self._enviar_json({"ok": False, "erro": "Parametros numericos invalidos para Home XY."}, 400)
                return
            if not _grbl_global.ser or (not _grbl_global.modo_simulacao and not _grbl_global.conexao_real):
                self._enviar_json({"ok": False, "erro": "Conecte GRBL fisico ou simulador antes de iniciar Home XY."}, 409)
                return
            ok, mensagem = iniciar_home_xy_em_background(
                _grbl_global, direcao_x=direcao_x, direcao_y=direcao_y,
                curso_x=curso_x, curso_y=curso_y, passo=passo, feed=feed,
                coord_x=coord_x, coord_y=coord_y, recuo=recuo_x, recuo_y=recuo_y,
                seguranca_confirmada=True,
            )
            self._enviar_json({"ok": ok, "mensagem": mensagem, "erro": None if ok else mensagem},
                              200 if ok else 409)
            return

        if acao == "ugs_cmd":
            cmd = dados.get("cmd", "")
            if cmd == "hold":
                _job_pausado = True
                _grbl_global.enviar_byte_tempo_real(b"!", "Feed Hold (!)")
            elif cmd == "resume":
                _job_pausado = False
                _grbl_global.enviar_byte_tempo_real(b"~", "Cycle Start (~)")
            elif cmd == "abort":
                # Prioridade absoluta: liberar a espera do job e enviar bytes
                # realtime sem adquirir o lock serial da thread em movimento.
                _job_abortar = True
                _job_pausado = False
                abortar_job_evento.set()
                ok_parada = _grbl_global.parar_grbl()
                mensagem = ("Hold + Ctrl+X enviados ao GRBL; confirme parada na CNC."
                            if ok_parada else
                            "Nao foi possivel enviar parada pela USB. ACIONE A PARADA ELETRICA.")
                self._enviar_json({
                    "ok": bool(ok_parada), "mensagem": mensagem,
                    "erro": None if ok_parada else mensagem,
                }, 200 if ok_parada else 503)
                return
            elif cmd == "jog_cancel":
                _grbl_global.cancelar_jog()
            elif cmd == "unlock":
                _grbl_global.enviar_comando("$X")
            elif cmd == "soft_reset":
                _grbl_global.soft_reset()
            elif cmd == "home_grbl":
                self._enviar_json({"ok": False, "erro": "Home $H desabilitado: use Home XY por chaves sem Z."}, 409)
                return
            elif cmd in ("definir_home_manual", "zerar_x", "zerar_y"):
                self._enviar_json({
                    "ok": False,
                    "erro": "Origem manual desativada: faca Home XY pelas chaves, com recuo de 2 mm."
                }, 409)
                return
            elif cmd == "ir_zero_trabalho":
                _grbl_global.voltar_origem()
            elif cmd == "ir_g28":
                _grbl_global.ir_para_g28()
            elif cmd == "salvar_g28":
                _grbl_global.salvar_g28()
            elif cmd == "ir_g30":
                _grbl_global.ir_para_g30()
            elif cmd == "salvar_g30":
                _grbl_global.salvar_g30()
            elif cmd == "check_mode":
                _grbl_global.alternar_modo_check()
            elif cmd == "wcs":
                _grbl_global.selecionar_wcs(dados.get("wcs", "G54"))
            elif cmd == "teste_t":
                if not testar_reles_um_a_um():
                    self._enviar_json({"ok": False, "erro": "Teste Z interrompido ou bloqueado."}, 409)
                    return
            elif cmd == "seq_cabecote":
                if not acionar_sequencia_cabecote():
                    self._enviar_json({"ok": False, "erro": "Sequencia Z interrompida ou bloqueada."}, 409)
                    return
            self._enviar_json({"ok": True})
            return

        if acao == "testar_z":
            if _job_em_execucao:
                self._enviar_json({"ok": False, "erro": "Z manual bloqueado durante job."})
                return
            nv = int(dados.get("nivel", 1))
            abortar_job_evento.clear()
            ok, info = acionar_eixo_z(nv)
            self._enviar_json({"ok": ok, "info": info})
            return

        if acao == "config_cabecote":
            try:
                feed_novo = float(dados.get("feed", FEED_RATE))
                if not math.isfinite(feed_novo) or not 50 <= feed_novo <= 5000:
                    raise ValueError("Feed deve estar entre 50 e 5000 mm/min.")
                parametros = {
                    "on_ms": dados.get("on_ms"), "off_ms": dados.get("off_ms"),
                    "pausa_ms": dados.get("pausa_ms"),
                    "pulsos_por_nivel": dados.get("pulsos_por_nivel"),
                    "usar_fixo": False,
                }
                configurar_parametros_pulsos_z(**parametros)
                FEED_RATE = feed_novo
            except (ValueError, TypeError) as exc:
                self._enviar_json({"ok": False, "erro": str(exc)}, 400)
                return
            if _matriz_atual is not None:
                atualizar_matriz_global(_matriz_atual)
            self._enviar_json({"ok": True})
            return

        if acao == "iniciar_job":
            if not _grbl_global.modo_simulacao and not _grbl_global.home_xy_referenciado:
                self._enviar_json({"ok": False, "erro": "Home XY por chaves nao confirmado nesta conexao. Execute Home XY antes da plotagem."}, 409)
                return
            ok, mensagem = iniciar_job_selecionado(
                _grbl_global, _reset_global,
                fonte_esperada=dados.get("fonte_esperada"),
                revisao_esperada=dados.get("revisao_esperada"),
            )
            self._enviar_json({"ok": ok, "mensagem": mensagem,
                               "erro": None if ok else mensagem}, 200 if ok else 409)
            return

        if acao == "reset_mecanico":
            if _grbl_global.modo_simulacao and not _reset_global.simulado:
                self._enviar_json({"ok": False, "erro": "GRBL simulado: Arduino de reset REAL bloqueado."}, 409)
                return
            if not _grbl_global.modo_simulacao and _reset_global.simulado:
                self._enviar_json({"ok": False, "erro": "Arduino de reset fisico nao conectado."}, 409)
                return
            sub = dados.get("subcmd", "completo")
            if sub == "completo":
                ok_reset, _ = _reset_global.ciclo_completo_reset(_matriz_atual)
                if ok_reset:
                    _imagem_origem = None
                    atualizar_matriz_global(_matriz_atual)
                else:
                    self._enviar_json({"ok": False, "erro": "Reset mecanico nao concluido."}, 500)
                    return
            elif sub == "descer":
                ok_reset, _ = _reset_global.descer_atuadores()
            elif sub == "subir":
                ok_reset, _ = _reset_global.subir_atuadores()
            elif sub == "varrer":
                ok_reset, _ = _reset_global.varrer_eixo_x_linha(250.0)
            elif sub == "home":
                ok_reset, _ = _reset_global.home_eixo_x_linha()
            else:
                self._enviar_json({"ok": False, "erro": "Subcomando reset desconhecido."}, 400)
                return
            self._enviar_json({"ok": bool(ok_reset)})
            return

        if acao == "gerar_padrao":
            _imagem_origem = None
            padrao = dados.get("padrao", "domo")
            nova = gerar_padrao_teste(padrao, NIVEIS_MAX)
            atualizar_matriz_global(nova, f"Padrao: {padrao.upper()}", descartar_importacao=True)
            self._enviar_json({"ok": True})
            return

        if acao == "mudar_profundidade":
            modo = dados.get("modo", "distancia")
            if modo not in ("distancia", "luminancia", "binario"):
                self._enviar_json({"ok": False, "erro": "Modo de profundidade invalido."}, 400)
                return
            if _imagem_origem is None and modo == "luminancia":
                self._enviar_json({"ok": False, "erro": "Luminancia precisa da imagem original; importe uma imagem primeiro."}, 409)
                return
            MODO_PROFUNDIDADE = modo
            if _imagem_origem is not None:
                nova, _ = processar_imagem_para_matriz(_imagem_origem, modo_profundidade=modo)
            elif _matriz_atual is not None:
                mascara = (_matriz_atual > 0).astype(np.uint8)
                nova = mascara * NIVEIS_MAX if modo == "binario" else calcular_niveis_por_distancia(mascara, NIVEIS_MAX)
            else:
                self._enviar_json({"ok": False, "erro": "Nao ha matriz para reprocessar."}, 409)
                return
            atualizar_matriz_global(nova)
            self._enviar_json({"ok": True})
            return

        if acao == "upload_imagem":
            b64_str = dados.get("imagem_b64", "")
            if "," in b64_str:
                b64_str = b64_str.split(",", 1)[1]
            raw = base64.b64decode(b64_str)
            if Image is not None:
                pil_img = Image.open(io.BytesIO(raw)).convert("RGB")
                _imagem_origem = pil_img.copy()
                modo = dados.get("modo_profundidade", MODO_PROFUNDIDADE)
                if modo not in ("distancia", "luminancia", "binario"):
                    self._enviar_json({"ok": False, "erro": "Modo de profundidade invalido."}, 400)
                    return
                nova, _ = processar_imagem_para_matriz(
                    pil_img,
                    modo_profundidade=modo,
                )
                MODO_PROFUNDIDADE = modo
                atualizar_matriz_global(nova, "Imagem IA (ONNX)", descartar_importacao=True)
            self._enviar_json({"ok": True})
            return

        if acao == "terminal_cmd":
            cmd = dados.get("cmd", "")
            res = executar_comando_terminal_integrado(cmd, _grbl_global, _reset_global)
            if _matriz_atual is not None and res.get("atualizar_matriz"):
                atualizar_matriz_global(_matriz_atual)
            self._enviar_json({
                "ok": res.get("ok", True),
                "resp": res.get("resposta", ""),
                "historico": list(ESTADO_ESTUDIO["terminal"])[:60],  # mais recentes primeiro
            })
            return

        if acao == "ler_settings_grbl":
            _, _, cfg = _grbl_global.consultar_configuracoes_grbl()
            linhas = [f"{k}={v}" for k, v in cfg.items()]
            self._enviar_json({"ok": True, "linhas": linhas})
            return

        self._enviar_json({"erro": f"Acao desconhecida: {acao}"}, 400)


def main():
    global _grbl_global, _reset_global, SIMULADO, ORIGEM_X_NA_DIREITA, SINAL_EIXO_X

    parser = argparse.ArgumentParser(description="Smart Matrix Studio v2.0 (G-code XY + Z GPIO)")
    parser.add_argument("--simulado", action="store_true", help="Forca operacao em modo simulado")
    parser.add_argument("--porta", type=str, default=PORTA_GRBL, help="Porta serial do GRBL")
    parser.add_argument("--origem-esquerda", action="store_true", help="Define (0,0) na extremidade esquerda em vez da direita")
    parser.add_argument("--sinal-x", type=float, default=1.0, help="Sinal do eixo X (+1.0 ou -1.0)")
    parser.add_argument("--http-port", type=int, default=0, help="Porta HTTP fixa (0 = automatica)")
    args, _ = parser.parse_known_args()

    if args.simulado:
        SIMULADO = True
    if args.origem_esquerda:
        ORIGEM_X_NA_DIREITA = False
    if args.sinal_x < 0:
        SINAL_EIXO_X = -1.0

    if carregar_calibracao_matriz():
        registrar_log(f"Calibracao carregada de {ARQUIVO_CALIBRACAO_MATRIZ} (sobrescreve os PONTO_A..D do codigo): "
                      f"A={PONTO_A} B={PONTO_B} C={PONTO_C} D={PONTO_D}", "aviso")
    for aviso in validar_cantos():
        registrar_log(f"ATENCAO CANTOS: {aviso}", "aviso")
    _grbl_global = ControladorGRBL()
    grbl_conectado = _grbl_global.conectar("SIMULADO" if SIMULADO else args.porta, BAUD)
    # Padrao seguro: Z permanece desarmado inclusive com GRBL fisico conectado.
    # O operador habilita Z explicitamente na interface somente apos Home XY.
    bloquear_z_fisico("Inicializacao: Z fisico desativado por padrao")
    if not grbl_conectado:
        registrar_log("GRBL nao esta pronto; Z mantido bloqueado.", "erro")

    _reset_global = ControladorResetArduino()
    _reset_global.conectar(PORTA_ARDUINO_RESET)

    matriz_inicial = gerar_padrao_teste("flor", NIVEIS_MAX)
    atualizar_matriz_global(matriz_inicial, "Flor Central (50,50)")

    t_tel = threading.Thread(target=monitorar_telemetria, daemon=True)
    t_tel.start()

    servidor = ThreadingHTTPServer(("127.0.0.1", args.http_port), StudioRequestHandler)
    porta_http = servidor.server_address[1]
    url = f"http://127.0.0.1:{porta_http}"
    registrar_log(f"Smart Matrix Studio v2.0 (G-code XY + Z GPIO) iniciado em {url}")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        registrar_log("Encerrando Smart Matrix Studio...")
    finally:
        sistema_ativo.clear()
        _grbl_global.parar_grbl()
        _reset_global.fechar()


if __name__ == "__main__":
    main()
