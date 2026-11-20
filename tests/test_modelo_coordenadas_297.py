"""Modelo unico da malha: 100x100 pontos a 3 mm, 297 mm entre extremos."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_coordenadas", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Modelo297(unittest.TestCase):
    def setUp(self):
        self.campos = ["PONTO_A", "PONTO_B", "PONTO_C", "PONTO_D",
                       "ORIGEM_X_NA_DIREITA", "SINAL_EIXO_X", "SINAL_EIXO_Y",
                       "USAR_JANELA_PLOTAGEM", "PLOT_X_MIN", "PLOT_X_MAX",
                       "PLOT_Y_MIN", "PLOT_Y_MAX", "ARQUIVO_CALIBRACAO_MATRIZ",
                       "_grbl_global"]
        self.anterior = {k: getattr(mod,k) for k in self.campos}
        for k in ("PONTO_A","PONTO_B","PONTO_C","PONTO_D"):
            self.anterior[k] = list(self.anterior[k])
        mod.PONTO_A, mod.PONTO_B = [0.0,0.0], [297.0,0.0]
        mod.PONTO_C, mod.PONTO_D = [297.0,-297.0], [0.0,-297.0]
        mod.ORIGEM_X_NA_DIREITA = False
        mod.SINAL_EIXO_X = 1.0
        mod.SINAL_EIXO_Y = -1.0
        mod.USAR_JANELA_PLOTAGEM = False
        mod.PLOT_X_MIN, mod.PLOT_X_MAX = 0.0, 297.0
        mod.PLOT_Y_MIN, mod.PLOT_Y_MAX = -297.0, 0.0
        mod._grbl_global = None

    def tearDown(self):
        for k,v in self.anterior.items():
            setattr(mod,k,v)

    def test_grade_100_pontos_99_passos(self):
        self.assertEqual(mod.TAMANHO_MATRIZ, (100,100))
        self.assertEqual(mod.PASSO_NOMINAL_MALHA_MM, 3.0)
        self.assertEqual(mod.EXTENSAO_UTIL_MALHA_MM, 297.0)
        self.assertEqual(mod.CURSO_NOMINAL_DESLOCAMENTO_MM, 299.0)

    def test_quatro_cantos_exatos(self):
        pontos={(0,99):(0,0),(99,99):(297,0),
                (99,0):(297,-297),(0,0):(0,-297)}
        for (x,y),(wx,wy) in pontos.items():
            with self.subTest(x=x,y=y):
                self.assertEqual(mod.indice_para_mm_fisico(x,y),(wx,wy))
                self.assertEqual(mod.mm_fisico_para_indice(wx,wy),(x,y))

    def test_cada_passo_x_e_y_3mm(self):
        for x,y in ((0,0),(49,50),(98,98)):
            ax,ay=mod.indice_para_mm_fisico(x,y)
            bx,by=mod.indice_para_mm_fisico(x+1,y)
            cx,cy=mod.indice_para_mm_fisico(x,y+1)
            self.assertAlmostEqual(bx-ax,3.0,places=7)
            self.assertAlmostEqual(by-ay,0.0,places=7)
            self.assertAlmostEqual(cx-ax,0.0,places=7)
            self.assertAlmostEqual(cy-ay,3.0,places=7)

    def test_todos_os_indices_tem_inversa(self):
        for y in range(100):
            for x in range(100):
                xx,yy=mod.indice_para_mm_fisico(x,y)
                self.assertEqual(mod.mm_fisico_para_indice(xx,yy),(x,y))

    def test_matriz_espelhada_conserva_passo(self):
        mod.ORIGEM_X_NA_DIREITA=True
        self.assertEqual(mod.indice_para_mm_fisico(0,99),(297.0,0.0))
        self.assertEqual(mod.indice_para_mm_fisico(99,99),(0.0,0.0))
        self.assertEqual(mod.mm_fisico_para_indice(0.0,0.0),(99,99))

    def test_sinal_motor_nao_pode_espelhar_coordenadas(self):
        normal=mod.indice_para_mm_fisico(8,19)
        mod.SINAL_EIXO_X=-1.0
        mod.SINAL_EIXO_Y=1.0
        self.assertEqual(mod.indice_para_mm_fisico(8,19), normal)

    def test_quatro_cantos_reais_obliquos_tem_inversa(self):
        mod.PONTO_A=[12.0,5.0]
        mod.PONTO_B=[309.0,7.0]
        mod.PONTO_C=[311.0,-290.0]
        mod.PONTO_D=[14.0,-292.0]
        self.assertTrue(mod.validar_calibracao_cantos()[0])
        for x,y in ((0,0),(99,99),(36,66),(50,5),(1,98)):
            xx,yy=mod.indice_para_mm_fisico(x,y)
            self.assertEqual(mod.mm_fisico_para_indice(xx,yy),(x,y))

    def test_janela_recorta_sem_reescalar(self):
        sem=mod.indice_para_mm_fisico(50,50)
        mod.USAR_JANELA_PLOTAGEM=True
        mod.PLOT_X_MIN,mod.PLOT_X_MAX=0.0,3.0
        mod.PLOT_Y_MIN,mod.PLOT_Y_MAX=-297.0,-294.0
        self.assertEqual(mod.indice_para_mm_fisico(50,50),sem)
        self.assertTrue(mod.ponto_na_janela_plotagem(*mod.indice_para_mm_fisico(0,0)))
        self.assertFalse(mod.ponto_na_janela_plotagem(*sem))
        matriz=np.zeros((100,100),dtype=np.uint8)
        matriz[0,0]=5
        matriz[99,99]=7
        linhas,traj,_=mod.compilar_matriz_para_estudio(matriz)
        self.assertEqual(len(traj),1)
        self.assertEqual((traj[0]["x"],traj[0]["y"]),(0,0))
        self.assertIn("X0.000 Y-297.000", " ".join(linhas))

    def test_pressionamento_z_fora_do_centro_rejeitado(self):
        grbl=mod.ControladorGRBL()
        grbl.wpos=[0.0,0.0,0.0]
        programa=mod.analisar_gcode_hibrido("G1 X258 Y-40 F900\nZ4")
        with self.assertRaisesRegex(ValueError,"centros"):
            mod.validar_programa_antes_movimento(grbl,programa)
        programa=mod.analisar_gcode_hibrido("G1 X258 Y-39 F900\nZ4")
        self.assertTrue(mod.validar_programa_antes_movimento(grbl,programa))

    def test_indices_invalidos_nao_sao_projetados_silenciosamente(self):
        for par in ((100,0),(-1,5),(1,300)):
            with self.assertRaises(ValueError):
                mod.indice_para_mm_fisico(*par)

    def test_calibracao_com_247mm_e_recusada(self):
        self.assertFalse(mod.validar_calibracao_cantos([
            [0,0],[247.5,0],[247.5,-247.5],[0,-247.5]])[0])

    def test_arquivo_legado_nao_sobrescreve_cantos(self):
        with tempfile.TemporaryDirectory() as directory:
            arquivo=Path(directory)/"calibracao_matriz.json"
            arquivo.write_text(json.dumps({
                "ponto_a":[0,0],"ponto_b":[247.5,0],
                "ponto_c":[247.5,-247.5],"ponto_d":[0,-247.5]
            }),encoding="utf-8")
            mod.ARQUIVO_CALIBRACAO_MATRIZ=str(arquivo)
            self.assertFalse(mod.carregar_calibracao_matriz())
            self.assertEqual(mod.PONTO_B,[297.0,0.0])

    def test_calibracao_invalida_nao_muda_estado_nem_arquivo(self):
        with tempfile.TemporaryDirectory() as directory:
            caminho=Path(directory)/"cal.json"
            mod.ARQUIVO_CALIBRACAO_MATRIZ=str(caminho)
            with self.assertRaises(ValueError):
                mod.configurar_sistema_coordenadas(ponto_b=[240,0])
            self.assertEqual(mod.PONTO_B,[297.0,0.0])
            self.assertFalse(caminho.exists())

    def test_calibracao_valida_invalida_home_anterior(self):
        ctrl=mod.ControladorGRBL()
        ctrl.home_xy_referenciado=True
        mod._grbl_global=ctrl
        with tempfile.TemporaryDirectory() as directory:
            mod.ARQUIVO_CALIBRACAO_MATRIZ=str(Path(directory)/"cal.json")
            mod.configurar_sistema_coordenadas(ponto_b=[297.0,0.0], inverter_jog_x=True)
            self.assertFalse(ctrl.home_xy_referenciado)

    def test_limites_malha_297_sao_conservadores(self):
        self.assertFalse(mod.fora_dos_limites(297,-297))
        self.assertTrue(mod.fora_dos_limites(299,-297))
        self.assertTrue(mod.fora_dos_limites(-1,-50))
        self.assertFalse(mod.fora_dos_limites(150,-150))

    def test_recorte_sem_pinos_falha_sem_mover(self):
        mod.USAR_JANELA_PLOTAGEM=True
        mod.PLOT_X_MIN,mod.PLOT_X_MAX=1.0,2.0
        mod.PLOT_Y_MIN,mod.PLOT_Y_MAX=-296.0,-295.0
        self.assertFalse(mod.validar_janela_calibrada()[0])

    def test_manual_G92_nao_substitui_home(self):
        ctrl=mod.ControladorGRBL()
        with patch.object(ctrl,"enviar_comando") as enviar:
            self.assertFalse(ctrl.zerar_eixo("XY"))
            self.assertFalse(ctrl.calibrar_home(modo="manual_aqui"))
        enviar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
