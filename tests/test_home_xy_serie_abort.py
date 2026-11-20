"""Home XY: tolerancia a falha serial transitoria e abort sem esperar mutex GRBL."""
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_home_abort", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PortaRastreavel(mod.PortaSimulada):
    def __init__(self):
        super().__init__("grbl")
        self.escritas = []
    def write(self, dados):
        self.escritas.append(dados)
        return super().write(dados)


class TestHomeSerialEAbort(unittest.TestCase):
    def setUp(self):
        mod.sistema_ativo.set()
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False
        self.ctrl = mod.ControladorGRBL()
        self.ctrl.ser = PortaRastreavel()
        self.ctrl.conexao_real = False
        self.ctrl.modo_simulacao = True
        self.ctrl.porta_atual = "SIMULADO"

    def tearDown(self):
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False

    def test_retorna_status_sem_pn_quando_chaves_livres(self):
        s = self.ctrl._status_home_xy()
        self.assertTrue(s.startswith("<Idle"))
        self.assertEqual(mod.pinos_ativos(s), "")

    def test_dois_status_vazios_transitorios_nao_bloqueiam_home(self):
        with patch.object(self.ctrl, "ler_status_grbl",
                          side_effect=["", "", "<Idle|MPos:0.000,0.000,0.000>"]) as leitura, \
             patch.object(mod.time, "sleep"):
            result = self.ctrl._status_home_xy()
        self.assertTrue(result.startswith("<Idle"))
        self.assertEqual(leitura.call_count, 3)

    def test_timeout_status_so_apos_tres_tentativas_limitadas(self):
        with patch.object(self.ctrl, "ler_status_grbl", return_value="") as leitura, \
             patch.object(mod.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "3 consultas"):
                self.ctrl._status_home_xy()
        self.assertEqual(leitura.call_count, 3)

    def test_alarme_nao_pode_ser_ignorado_em_uma_retry(self):
        with patch.object(self.ctrl, "ler_status_grbl",
                          side_effect=["<Alarm|MPos:0,0,0>", "<Idle|MPos:0,0,0>"]) as leitura:
            with self.assertRaisesRegex(RuntimeError, "Estado GRBL"):
                self.ctrl._status_home_xy()
        self.assertEqual(leitura.call_count, 1)

    def test_evento_abort_interrompe_retries_imediatamente(self):
        mod.abortar_job_evento.set()
        with patch.object(self.ctrl, "ler_status_grbl") as leitura:
            with self.assertRaisesRegex(RuntimeError, "cancelado"):
                self.ctrl._status_home_xy()
        leitura.assert_not_called()

    def test_abort_prioritario_nao_tenta_obter_mutex_serial(self):
        resposta = []
        handler = mod.StudioRequestHandler.__new__(mod.StudioRequestHandler)
        handler._enviar_json = lambda x, status=200: resposta.append((x, status))
        self.ctrl.home_xy_referenciado = True
        resultados = []
        def solicitar():
            handler._executar_acao_POST("ugs_cmd", {"cmd": "abort"})
            resultados.append(True)
        with patch.object(mod, "_grbl_global", self.ctrl):
            # A thread que le GRBL esta ocupando o mutex serial.
            with self.ctrl.lock:
                t = threading.Thread(target=solicitar, daemon=True)
                t.start()
                t.join(timeout=0.5)
                sem_espera = not t.is_alive()
            t.join(timeout=1.0)
        self.assertTrue(sem_espera, "Abortar ficou preso aguardando status/soft_reset do GRBL")
        self.assertTrue(resultados)
        self.assertTrue(resposta[0][0]["ok"])
        self.assertTrue(mod.abortar_job_evento.is_set())
        self.assertTrue(mod._job_abortar)
        self.assertFalse(self.ctrl.home_xy_referenciado)
        self.assertIn(b"!", self.ctrl.ser.escritas)
        self.assertIn(b"\x18", self.ctrl.ser.escritas)

    def test_abort_usb_com_erro_exige_parada_fisica(self):
        class PortaFalha:
            def write(self, payload):
                raise OSError("USB removida")
        self.ctrl.ser = PortaFalha()
        resposta = []
        handler = mod.StudioRequestHandler.__new__(mod.StudioRequestHandler)
        handler._enviar_json = lambda x, status=200: resposta.append((x, status))
        with patch.object(mod, "_grbl_global", self.ctrl):
            handler._executar_acao_POST("ugs_cmd", {"cmd": "abort"})
        self.assertFalse(resposta[0][0]["ok"])
        self.assertEqual(resposta[0][1], 503)
        self.assertIn("PARADA ELETRICA", resposta[0][0]["erro"])
        self.assertTrue(mod.abortar_job_evento.is_set())

    def test_abort_nao_finge_sucesso_sem_porta(self):
        self.ctrl.ser = None
        self.assertFalse(self.ctrl.parar_grbl())
        self.assertFalse(self.ctrl.home_xy_referenciado)

    def test_parada_por_software_nao_espera_trezentos_ms(self):
        with patch.object(mod.time, "sleep") as adormecer:
            self.assertTrue(self.ctrl.parar_grbl())
        adormecer.assert_not_called()
        self.assertEqual(self.ctrl.ser.escritas[-2:], [b"!", b"\x18"])


if __name__ == "__main__":
    unittest.main()
