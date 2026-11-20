"""Testes sem CNC fisica."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ARQUIVO = Path(__file__).resolve().parents[1] / 'smartv42.py'
spec = importlib.util.spec_from_file_location('smartv42', ARQUIVO)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class GRBLFalso:
    def __init__(self):
        self.wpos = [0.0, 0.0, 0.0]
        self.comandos = []
        self.movimentos = []

    def enviar_comando(self, cmd, logar=False):
        self.comandos.append(cmd)
        return True, ''

    def mover_xy_e_aguardar(self, x, y, feed):
        self.movimentos.append((x, y, feed))
        self.wpos[:2] = [x, y]
        return True, 'ok', ''

    def aguardar_grbl_idle(self):
        return True, ''


class TesteHibrido(unittest.TestCase):
    def setUp(self):
        mod.abortar_job_evento.clear()
        mod._job_abortar = False

    def test_exportacao_m42_esta_preservada(self):
        gcode = '''G21
G90
G54
M3 S1000 ; matriz
G1 X258.000 Y39.000 F900
G4 P0
M42 P1 ; Z_LEVEL=4 GPIO=27 PULSOS=4 ON_MS=10 OFF_MS=10 IDX=(50,50)
G1 X261.000 Y36.000 F900
M42 P1 ; Z_LEVEL=2 GPIO=6 PULSOS=2
M5
G0 X0 Y0
'''
        instr = mod.analisar_gcode_hibrido(gcode)
        z = [i for i in instr if i['tipo'] == 'z']
        self.assertEqual([i['nivel'] for i in z], [4, 2])
        self.assertEqual(z[0]['pulsos'], 4)

    def test_nao_aceita_z_cartesiano(self):
        for texto in ('G1 X258 Y39 Z4', 'G91\nG1 X5', 'M42 P1', 'G1 X1 Y2\nG28'):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                mod.analisar_gcode_hibrido(texto)

    def test_nao_envia_m42_ao_grbl(self):
        grbl = GRBLFalso()
        programa = mod.analisar_gcode_hibrido('G1 X258 Y39 F900\nM42 P1 ; Z_LEVEL=4 GPIO=27 PULSOS=3')
        with patch.object(mod, 'acionar_eixo_z', return_value=(True, {'pino_gpio': 27, 'pulsos': 3})) as z:
            with patch.object(mod.time, 'sleep'):
                contexto = {}
                for bloco in programa:
                    mod.executar_bloco_hibrido(grbl, bloco, contexto)
        z.assert_called_once()
        self.assertEqual(len(grbl.movimentos), 1)
        self.assertEqual(grbl.comandos, [])

    def test_bloqueia_z_fora_da_janela(self):
        grbl = GRBLFalso()
        programa = mod.analisar_gcode_hibrido('G1 X20 Y30\nZ4')
        with patch.object(mod, 'acionar_eixo_z') as z:
            ctx = {}
            mod.executar_bloco_hibrido(grbl, programa[0], ctx)
            with self.assertRaises(RuntimeError):
                mod.executar_bloco_hibrido(grbl, programa[1], ctx)
            z.assert_not_called()

    def test_calibracao_antiga_nao_e_aceita(self):
        with patch.object(mod, 'PONTO_C', [247.5, -247.5]), patch.object(mod, 'PONTO_D', [0.0, -247.5]):
            self.assertFalse(mod.validar_janela_calibrada()[0])

    def test_calibracao_atual_aceita(self):
        self.assertTrue(mod.validar_janela_calibrada()[0])


if __name__ == '__main__':
    unittest.main()
