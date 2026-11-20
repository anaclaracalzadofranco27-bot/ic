"""Testes de Home XY com chaves GRBL simuladas, sem hardware fisico."""
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_home_xy", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class HomeXYChavesTeste(unittest.TestCase):
    def setUp(self):
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False

    def novo_controlador(self):
        ctrl = mod.ControladorGRBL()
        with patch.object(mod, "SIMULADO", True):
            self.assertTrue(ctrl.conectar("SIMULADO"))
        return ctrl

    def executar_simulado(self, ctrl, **kwargs):
        dados = dict(
            direcao_x=-1, direcao_y=1, curso_x=6.0, curso_y=6.0,
            passo=0.5, feed=120.0, recuo=2.0, coord_x=0.0, coord_y=0.0,
            seguranca_confirmada=True,
        )
        dados.update(kwargs)
        with patch.object(mod, "SIMULADO", True):
            return ctrl.executar_home_xy_chaves(**dados)

    def test_home_com_chaves_x_y_e_recuo_de_dois_mm(self):
        ctrl = self.novo_controlador()
        self.assertTrue(ctrl.enviar_comando("$22=0")[0])
        ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        self.assertTrue(ctrl.home_xy_referenciado)
        self.assertEqual(ctrl.home_xy_estado["fase"], "Home XY concluido")
        self.assertAlmostEqual(ctrl.mpos[0], -1.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], 1.0, places=2)
        self.assertAlmostEqual(ctrl.wpos[0], 0.0, places=2)
        self.assertAlmostEqual(ctrl.wpos[1], 0.0, places=2)
        self.assertNotIn("X", ctrl.pinos_pn)
        self.assertNotIn("Y", ctrl.pinos_pn)

    def test_exige_homing_firmware_desabilitado_22(self):
        ctrl = self.novo_controlador()
        ok, motivo = self.executar_simulado(ctrl)
        self.assertFalse(ok)
        self.assertIn("$22=0", motivo)
        self.assertFalse(ctrl.home_xy_referenciado)
        self.assertEqual(ctrl.mpos[:2], [0.0, 0.0])

    def test_nao_desativa_hard_limits_automaticamente(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.enviar_comando("$21=1")
        ok, motivo = self.executar_simulado(ctrl)
        self.assertFalse(ok)
        self.assertIn("$21=1", motivo)
        self.assertEqual(ctrl.ser.settings[21][0], "1")
        self.assertEqual(ctrl.mpos[:2], [0.0, 0.0])

    def test_sem_confirmacao_de_parada_fisica_nao_move(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ok, motivo = self.executar_simulado(ctrl, seguranca_confirmada=False)
        self.assertFalse(ok)
        self.assertIn("emergencia", motivo)
        self.assertEqual(ctrl.mpos[:2], [0.0, 0.0])

    def test_direcao_errada_e_curso_limitado_nao_marcam_home(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ok, motivo = self.executar_simulado(ctrl, direcao_x=1, curso_x=1.0)
        self.assertFalse(ok)
        self.assertIn("Chave X nao encontrada", motivo)
        self.assertFalse(ctrl.home_xy_referenciado)
        self.assertLessEqual(ctrl.mpos[0], 1.01)

    def test_chave_inicialmente_acionada_primeiro_libera(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.ser.mpos[0] = -3.0
        ctrl.ser.mpos[1] = 3.0
        ok, motivo = self.executar_simulado(ctrl)
        self.assertTrue(ok, motivo)
        self.assertAlmostEqual(ctrl.mpos[0], -1.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], 1.0, places=2)

    def test_usb_reconectada_invalida_referencia(self):
        ctrl = self.novo_controlador()
        ctrl.home_xy_referenciado = True
        with patch.object(mod, "SIMULADO", True):
            self.assertTrue(ctrl.conectar("SIMULADO"))
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_soft_reset_invalida_referencia(self):
        ctrl = self.novo_controlador()
        ctrl.home_xy_referenciado = True
        self.assertTrue(ctrl.soft_reset())
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_comando_g10_externo_invalida_referencia(self):
        ctrl = self.novo_controlador()
        ctrl.home_xy_referenciado = True
        self.assertTrue(ctrl.enviar_comando("G10 L20 P1 X0 Y0")[0])
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_hardware_homing_H_bloqueado(self):
        ctrl = self.novo_controlador()
        ok, erro = ctrl.enviar_comando("$H")
        self.assertFalse(ok)
        self.assertIn("Home XY", erro)

    def test_job_real_sem_referencia_nao_inicia(self):
        ctrl = self.novo_controlador()
        ctrl.modo_simulacao = False
        ctrl.conexao_real = True
        with patch.object(mod, "SIMULADO", False):
            programa = mod.analisar_gcode_hibrido("G1 X258 Y-40 F900\nZ4")
            self.assertFalse(mod.executar_job_gcode_em_background(ctrl, programa))
            self.assertFalse(mod._job_em_execucao)

    def test_simulador_reporta_pinos_das_chaves(self):
        ctrl = self.novo_controlador()
        ctrl.ser.mpos[:2] = [-3.5, 3.5]
        status = ctrl.ler_status_grbl()
        self.assertIn("Pn:XY", status)
        self.assertEqual(mod.pinos_ativos(status), "XY")

    def test_sem_chave_dentro_do_curso_nao_confirma_referencia(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.ser.home_simulado_x = -999.0
        ok, erro = self.executar_simulado(ctrl, curso_x=1.0)
        self.assertFalse(ok)
        self.assertIn("Chave X nao encontrada", erro)
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_abortar_antes_da_busca_bloqueia_motor(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        mod.abortar_job_evento.set()
        ok, erro = self.executar_simulado(ctrl)
        self.assertFalse(ok)
        self.assertIn("cancelado", erro)
        self.assertEqual(ctrl.mpos[:2], [0.0, 0.0])

    def test_home_em_background_reserva_maquina_e_limpa_abort_antigo(self):
        ctrl = self.novo_controlador()
        inicio = threading.Event()
        liberar = threading.Event()

        def simular_home(**kwargs):
            inicio.set()
            self.assertFalse(mod._job_abortar)
            liberar.wait(2)
            return True, "simulado"

        mod._job_abortar = True
        mod.abortar_job_evento.set()
        with patch.object(ctrl, "executar_home_xy_chaves", side_effect=simular_home):
            ok, msg = mod.iniciar_home_xy_em_background(ctrl)
            self.assertTrue(ok, msg)
            self.assertTrue(inicio.wait(2))
            self.assertTrue(mod._operacao_manual)
            self.assertFalse(mod.reservar_operacao_manual())
            liberar.set()
            import time
            for _ in range(100):
                if not mod._operacao_manual:
                    break
                time.sleep(0.01)
            self.assertFalse(mod._operacao_manual)
            self.assertFalse(mod.abortar_job_evento.is_set())


if __name__ == "__main__":
    unittest.main()
