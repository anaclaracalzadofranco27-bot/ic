"""Regressoes de isolamento: simular GRBL nunca energiza GPIOs reais."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_isolamento", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReleFake:
    criados = []
    def __init__(self, pino=5, active_high=False, initial_value=False):
        self.pino = pino
        self.on_calls = 0
        self.off_calls = 0
        self.close_calls = 0
        ReleFake.criados.append(self)

    def on(self):
        self.on_calls += 1

    def off(self):
        self.off_calls += 1

    def close(self):
        self.close_calls += 1


class TestarModosZ(unittest.TestCase):
    def setUp(self):
        ReleFake.criados = []
        mod.reles_z.clear()
        mod.MODO_Z_FISICO_HABILITADO = False
        mod._grbl_global = None
        mod.abortar_job_evento.clear()
        mod._job_abortar = False
        mod._job_pausado = False
        mod._job_em_execucao = False
        mod._operacao_manual = False

    def tearDown(self):
        mod.bloquear_z_fisico("Limpeza do teste")
        mod._grbl_global = None
        mod.MODO_Z_FISICO_HABILITADO = False
        mod.abortar_job_evento.clear()

    def grbl_virtual(self):
        ctrl = mod.ControladorGRBL()
        with patch.object(mod, "SIMULADO", False):
            self.assertTrue(ctrl.conectar("SIMULADO"))
        self.assertTrue(ctrl.modo_simulacao)
        self.assertFalse(ctrl.conexao_real)
        mod._grbl_global = ctrl
        return ctrl

    def grbl_fisico_falso(self, referenciado=True):
        ctrl = mod.ControladorGRBL()
        ctrl.ser = mod.PortaSimulada("grbl")
        ctrl.porta_atual = "/dev/ttyACM0"
        ctrl.modo_simulacao = False
        ctrl.conexao_real = True
        ctrl.home_xy_referenciado = referenciado
        mod._grbl_global = ctrl
        return ctrl

    def test_grbl_virtual_selecionado_com_global_false_simula_Z(self):
        self.grbl_virtual()
        with patch.object(mod, "SIMULADO", False), patch.object(mod.time, "sleep"):
            self.assertEqual(mod.obter_modo_z_efetivo(), "simulacao")
            ok, info = mod.acionar_eixo_z(3)
        self.assertTrue(ok, info["erro"])
        self.assertEqual(info["pulsos"], 3)
        self.assertEqual(ReleFake.criados, [])

    def test_reles_antigos_na_memoria_nao_ligam_em_simulacao(self):
        self.grbl_virtual()
        antigos = [ReleFake(p) for p in mod.PINOS_GPIO_Z]
        mod.reles_z.extend(antigos)
        with patch.object(mod, "SIMULADO", False), patch.object(mod.time, "sleep"):
            self.assertTrue(mod.acionar_eixo_z(4)[0])
            self.assertTrue(mod.testar_reles_um_a_um())
        self.assertTrue(all(x.on_calls == 0 for x in antigos))

    def test_troca_para_porta_virtual_fecha_reles_anteriores(self):
        ctrl = self.grbl_fisico_falso()
        antigos = [ReleFake(p) for p in mod.PINOS_GPIO_Z]
        mod.reles_z.extend(antigos)
        mod.MODO_Z_FISICO_HABILITADO = True
        with patch.object(mod, "SIMULADO", False):
            self.assertTrue(ctrl.conectar("SIMULADO"))
        self.assertFalse(mod.MODO_Z_FISICO_HABILITADO)
        self.assertEqual(mod.reles_z, [])
        self.assertTrue(all(x.close_calls == 1 and x.off_calls >= 1 and x.on_calls == 0 for x in antigos))

    def test_teste_xy_bloqueia_pulsos_e_teste_sequencial(self):
        self.grbl_fisico_falso()
        antigos = [ReleFake(p) for p in mod.PINOS_GPIO_Z]
        mod.reles_z.extend(antigos)
        with patch.object(mod, "SIMULADO", False):
            self.assertEqual(mod.obter_modo_z_efetivo(), "teste_xy")
            ok, info = mod.acionar_eixo_z(2)
            self.assertFalse(ok)
            self.assertIn("Teste fisico XY", info["erro"])
            self.assertFalse(mod.testar_reles_um_a_um())
        self.assertTrue(all(x.on_calls == 0 for x in antigos))

    def test_GPIO_nao_inicializa_em_teste_xy(self):
        self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True):
            self.assertFalse(mod.inicializar_gpio_z())
        self.assertEqual(ReleFake.criados, [])

    def test_habilitacao_recusa_conexao_virtual(self):
        self.grbl_virtual()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True):
            ok, motivo = mod.habilitar_z_fisico()
        self.assertFalse(ok)
        self.assertIn("fisica", motivo)
        self.assertFalse(ReleFake.criados)

    def test_habilitacao_recusa_ausencia_home_xy(self):
        self.grbl_fisico_falso(referenciado=False)
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True):
            ok, motivo = mod.habilitar_z_fisico()
        self.assertFalse(ok)
        self.assertIn("Home XY", motivo)
        self.assertEqual(ReleFake.criados, [])

    def test_habilitacao_fisica_explicitamente_permite_pulso(self):
        self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True), patch.object(mod.time, "sleep"):
            ok, motivo = mod.habilitar_z_fisico()
            self.assertTrue(ok, motivo)
            self.assertTrue(mod.MODO_Z_FISICO_HABILITADO)
            self.assertEqual(len(mod.reles_z), 8)
            ok, info = mod.acionar_eixo_z(4, pulsos_override=2)
            self.assertTrue(ok, info["erro"])
        self.assertEqual(ReleFake.criados[3].on_calls, 2)
        self.assertEqual(sum(x.on_calls for x in ReleFake.criados), 2)

    def test_teste_sequencial_fisico_liga_uma_vez_por_rele(self):
        self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True), patch.object(mod.time, "sleep"):
            self.assertTrue(mod.habilitar_z_fisico()[0])
            self.assertTrue(mod.testar_reles_um_a_um())
        self.assertEqual([x.on_calls for x in ReleFake.criados], [1] * 8)

    def test_modo_global_inconsistente_com_GRBL_real_bloqueia(self):
        self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", True):
            self.assertEqual(mod.obter_modo_z_efetivo(), "inconsistente")
            self.assertFalse(mod.acionar_eixo_z(1)[0])

    def test_reconexao_e_soft_reset_desarmam_Z(self):
        ctrl = self.grbl_fisico_falso()
        antigos = [ReleFake(p) for p in mod.PINOS_GPIO_Z]
        mod.reles_z.extend(antigos)
        mod.MODO_Z_FISICO_HABILITADO = True
        with patch.object(mod, "SIMULADO", False):
            self.assertTrue(ctrl.soft_reset())
        self.assertFalse(mod.MODO_Z_FISICO_HABILITADO)
        self.assertFalse(mod.reles_z)
        self.assertTrue(all(x.on_calls == 0 and x.close_calls > 0 for x in antigos))

    def test_home_xy_desliga_reles_antes_de_buscar(self):
        ctrl = self.grbl_fisico_falso()
        antigos = [ReleFake(p) for p in mod.PINOS_GPIO_Z]
        mod.reles_z.extend(antigos)
        mod.MODO_Z_FISICO_HABILITADO = True
        with patch.object(mod, "SIMULADO", False):
            ok, erro = ctrl.executar_home_xy_chaves(
                direcao_x=-1, direcao_y=1, curso_x=5, curso_y=5,
                seguranca_confirmada=False
            )
        self.assertFalse(ok)
        self.assertFalse(mod.MODO_Z_FISICO_HABILITADO)
        self.assertEqual(mod.reles_z, [])
        self.assertTrue(all(x.close_calls for x in antigos))

    def test_GPIO_indisponivel_nunca_ativa_modo_completo(self):
        self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", False):
            ok, erro = mod.habilitar_z_fisico()
        self.assertFalse(ok)
        self.assertEqual(mod.obter_modo_z_efetivo(), "teste_xy")

    def test_pulso_fisico_falha_se_GRBL_nao_estiver_Idle(self):
        ctrl = self.grbl_fisico_falso()
        with patch.object(mod, "SIMULADO", False), patch.object(mod, "GPIO_DISPONIVEL", True), patch.object(mod, "OutputDevice", ReleFake, create=True), patch.object(mod.time, "sleep"):
            self.assertTrue(mod.habilitar_z_fisico()[0])
            with patch.object(ctrl, "ler_status_grbl", return_value="<Check|MPos:0,0,0>"):
                self.assertFalse(mod.acionar_eixo_z(5)[0])
        self.assertEqual(sum(x.on_calls for x in ReleFake.criados), 0)


if __name__ == "__main__":
    unittest.main()
