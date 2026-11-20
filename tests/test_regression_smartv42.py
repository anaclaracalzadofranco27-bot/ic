"""Regressoes A-H: tudo executado em memoria, sem relés, Arduino ou motores."""
import importlib.util
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_reg", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeGRBL:
    def __init__(self):
        self.wpos = [0., 0., 0.]
        self.movimentos = []
        self.enviados = []
        self.conexao_real = True
        self.ser = object()
    def enviar_comando(self, comando, logar=False):
        self.enviados.append(comando)
        return True, ""
    def mover_xy_e_aguardar(self, x, y, feed):
        self.movimentos.append((x, y, feed))
        self.wpos[:2] = [x, y]
        return True, "ok", ""
    def aguardar_grbl_idle(self):
        return True, ""
    def ler_status_grbl(self, timeout=0.5):
        return "<Idle|WPos:258.000,-39.000,0.000>"


class TestesABSeguranca(unittest.TestCase):
    def setUp(self):
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False

    def test_perda_usb_nao_ativa_porta_simulada(self):
        controlador = mod.ControladorGRBL()
        serial_fake = types.SimpleNamespace(Serial=lambda *a, **k: (_ for _ in ()).throw(OSError("USB removido")))
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "serial", serial_fake):
            ok = controlador.conectar("/dev/smartv42-porta-inexistente")
        self.assertFalse(ok)
        self.assertIsNone(controlador.ser)
        self.assertFalse(controlador.conexao_real)
        self.assertEqual(controlador.porta_atual, "DESCONECTADO")

    def test_check_nao_pode_liberar_z(self):
        ctrl = mod.ControladorGRBL()
        ctrl.ser = object()
        with patch.object(ctrl, "ler_status_grbl", return_value="<Check|WPos:0,0,0>"):
            ok, motivo = ctrl.aguardar_grbl_idle(timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("Check", motivo)

    def test_preparar_grbl_nao_remove_alarme_automaticamente(self):
        ctrl = mod.ControladorGRBL()
        ctrl.ser = object()
        ctrl.conexao_real = True
        with patch.object(mod, "SIMULADO", False), patch.object(ctrl, "ler_status_grbl", return_value="<Alarm|MPos:0,0,0>"), patch.object(ctrl, "enviar_comando") as enviar:
            self.assertFalse(ctrl.preparar_grbl())
            enviar.assert_not_called()

    def test_gpio_real_bloqueado_sem_xy_fisico(self):
        ctrl = FakeGRBL()
        ctrl.conexao_real = False
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "_grbl_global", ctrl):
            ok, info = mod.acionar_eixo_z(4)
        self.assertFalse(ok)
        self.assertIn("GRBL fisico", info["erro"])

    def test_gpio_fisico_bloqueia_check(self):
        ctrl = FakeGRBL()
        ctrl.ler_status_grbl = lambda timeout=0.5: "<Check|MPos:0,0,0>"
        ctrl.home_xy_referenciado = True
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "_grbl_global", ctrl), patch.object(mod, "MODO_Z_FISICO_HABILITADO", True):
            ok, info = mod.acionar_eixo_z(4)
        self.assertFalse(ok)
        self.assertIn("Idle", info["erro"])

    def test_reserva_manual_rejeitada_durante_job(self):
        mod._job_em_execucao = True
        try:
            self.assertFalse(mod.reservar_operacao_manual())
        finally:
            mod._job_em_execucao = False

    def test_reserva_manual_inibe_inicio_de_job(self):
        self.assertTrue(mod.reservar_operacao_manual())
        try:
            p = mod.analisar_gcode_hibrido("G1 X258 Y-39 F900\nZ2")
            self.assertFalse(mod.executar_job_gcode_em_background(FakeGRBL(), p))
        finally:
            mod.liberar_operacao_manual()

    def test_pausa_bloqueia_inicio_do_proximo_pulso_z(self):
        with patch.object(mod, "SIMULADO", True), patch.object(mod, "aguardar_liberacao_z", return_value=False):
            ok, info = mod.acionar_eixo_z(4)
        self.assertFalse(ok)
        self.assertEqual(info["pulsos"], 0)

    def test_grbl_nao_aceita_jog_z_cartesiano(self):
        ctrl = mod.ControladorGRBL()
        ok, detalhe = ctrl.executar_jog_ugs(dz=3)
        self.assertFalse(ok)
        self.assertIn("Z", detalhe)


class TestesCDCoordenadasEDados(unittest.TestCase):
    def test_origem_direita_inverte_mapa_e_inverse(self):
        with patch.object(mod, "ORIGEM_X_NA_DIREITA", True), patch.object(mod, "SINAL_EIXO_X", 1):
            x0, _ = mod.indice_para_mm_fisico(0, 50)
            x99, _ = mod.indice_para_mm_fisico(99, 50)
            self.assertAlmostEqual(x0, 297)
            self.assertAlmostEqual(x99, 0)
            self.assertEqual(mod.mm_fisico_para_indice(x99, -40)[0], 99)

    def test_sinal_x_persistido(self):
        with tempfile.TemporaryDirectory() as p:
            arq = str(Path(p) / "cal.json")
            with patch.object(mod, "ARQUIVO_CALIBRACAO_MATRIZ", arq), patch.object(mod, "SINAL_EIXO_X", -1):
                mod.salvar_calibracao_matriz()
                mod.SINAL_EIXO_X = 1
                self.assertTrue(mod.carregar_calibracao_matriz())
                self.assertEqual(mod.SINAL_EIXO_X, -1)

    def test_atualizar_matriz_nao_apaga_gcode_importado(self):
        importado = {"linhas": ["G1 X258 Y-40", "Z3"], "programa": [{"tipo": "xy"}], "nome": "teste", "usar_parametros_arquivo": True}
        with patch.dict(mod.estado_importacao_gcode, importado, clear=True), patch.object(mod, "compilar_matriz_para_estudio", return_value=(["G21"], [], 0)):
            mod.atualizar_matriz_global(np.zeros((100, 100), dtype=np.uint8))
            self.assertEqual(mod.estado_importacao_gcode["nome"], "teste")
            self.assertTrue(mod.estado_importacao_gcode["usar_parametros_arquivo"])
            mod.atualizar_matriz_global(np.zeros((100, 100), dtype=np.uint8), descartar_importacao=True)
            # descartar_importacao eh parametro legado: seleciona matriz, NAO apaga arquivo.
            self.assertEqual(mod.estado_importacao_gcode["nome"], "teste")
            self.assertIsNotNone(mod.estado_importacao_gcode["programa"])
            self.assertEqual(mod.obter_fonte_job_ativa(), "matriz")

    def test_luminancia_eh_processada_pelo_pipeline_de_imagem(self):
        codigo = (ROOT / "smartv42_src/10_server/application.pyfrag").read_text(encoding="utf-8")
        self.assertIn("processar_imagem_para_matriz(_imagem_origem, modo_profundidade=modo)", codigo)
        self.assertIn("Luminancia precisa da imagem original", codigo)

    def test_pulsos_zero_off_pausa_preservados(self):
        with patch.object(mod, "TEMPO_OFF_Z_S", 0.01), patch.object(mod, "PAUSA_ANTES_Z_S", 0.01):
            with patch.object(mod, "registrar_log"), patch.object(mod, "registrar_terminal"):
                d = mod.configurar_parametros_pulsos_z(off_ms=0, pausa_ms=0)
            self.assertEqual(d["z_off_ms"], 0)
            self.assertEqual(d["pausa_z_ms"], 0)


class TestesEHMotorUnico(unittest.TestCase):
    def setUp(self):
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False

    def test_matriz_passada_ao_executor_unico(self):
        mat = np.zeros((100,100),dtype=np.uint8)
        mat[50,50] = 4
        with patch.object(mod, "executar_job_gcode_em_background", return_value=True) as run:
            self.assertTrue(mod.executar_job_em_background(FakeGRBL(), None, mat))
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(kwargs["origem"], "matriz")
        self.assertTrue(any(x["tipo"] == "z" for x in args[1]))

    def test_xy_invalidos_rejeitados_antes_do_primeiro_movimento(self):
        fake = FakeGRBL()
        p = mod.analisar_gcode_hibrido("G1 X258 Y-40\nZ3\nG1 X9999 Y0")
        with self.assertRaises(ValueError):
            mod.validar_programa_antes_movimento(fake, p)
        self.assertEqual(fake.movimentos, [])

    def test_z_fora_da_janela_rejeitado_no_preflight(self):
        fake = FakeGRBL()
        p = mod.analisar_gcode_hibrido("G1 X5 Y-5\nZ2")
        with self.assertRaises(ValueError):
            mod.validar_programa_antes_movimento(fake, p)

    def test_gcode_matriz_elimina_marcadores_redundantes(self):
        mat = np.zeros((100,100),dtype=np.uint8)
        mat[10,10] = 5
        linhas, _, _ = mod.compilar_matriz_para_estudio(mat)
        self.assertTrue(any(x.startswith("M42 P1") for x in linhas))
        self.assertFalse(any(x.startswith(("G4 P0","M3 ","M5 ")) for x in linhas))

    def test_arquivo_importado_respeita_precedencia_de_parametros(self):
        fake = FakeGRBL()
        bloco = {"tipo": "z", "nivel": 4, "pulsos": 12, "on_ms": 15, "off_ms": 0}
        with patch.object(mod, "acionar_eixo_z", return_value=(True, {"pino_gpio": 27, "pulsos": 4})) as z, patch.object(mod.time, "sleep"):
            mod.executar_bloco_hibrido(fake, bloco, {"posicao_xy": (258, -39), "usar_parametros_arquivo": False})
            self.assertEqual(z.call_args.kwargs["pulsos_override"], mod.obter_pulsos_para_nivel(4))
            mod.executar_bloco_hibrido(fake, bloco, {"posicao_xy": (258, -39), "usar_parametros_arquivo": True})
            self.assertEqual(z.call_args.kwargs["pulsos_override"], 12)
            self.assertEqual(z.call_args.kwargs["off_ms_override"], 0)

    def test_reset_completo_nao_aciona_etapas_duplicadas(self):
        c = mod.ControladorResetArduino()
        c.simulado = False
        chamadas = []
        with patch.object(c, "_enviar_cmd", side_effect=lambda cmd,tempo_simulado=0.5: (chamadas.append(cmd) or True, "DONE")):
            self.assertTrue(c.ciclo_completo_reset()[0])
        self.assertEqual(chamadas, ["D"])

    def test_reset_nao_confirma_sem_done(self):
        c = mod.ControladorResetArduino()
        c.simulado = False
        with patch.object(c, "_enviar_cmd", return_value=(True, "OK")):
            ok, detalhe = c.ciclo_completo_reset()
        self.assertFalse(ok)
        self.assertIn("conclusao", detalhe)

    def test_programa_gerado_igual_ao_montado_pelos_modulos(self):
        buildspec=importlib.util.spec_from_file_location("smartv42_build", ROOT / "tools/build_smartv42.py")
        build=importlib.util.module_from_spec(buildspec)
        buildspec.loader.exec_module(build)
        self.assertEqual((ROOT / "smartv42.py").read_text(encoding="utf-8"), build.gerar())


if __name__ == "__main__":
    unittest.main()
