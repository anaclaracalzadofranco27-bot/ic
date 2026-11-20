"""Regressoes arquiteturais: componentes isolados, sem CNC nem GPIO fisico."""
import copy
from dataclasses import FrozenInstanceError, is_dataclass
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_arquitetura", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CNCFalsa:
    def __init__(self):
        self.wpos = [0.0, 297.0, 0.0]
        self.modo_simulacao = True
        self.home_xy_referenciado = False
        self.chamadas = []

    def preparar_grbl(self):
        return "<Idle|MPos:0.000,297.000,0.000>"

    def mover_xy_e_aguardar(self, x, y, feed):
        self.chamadas.append((x, y, feed))
        self.wpos[:2] = [x, y]
        return True, "ok", ""


class ThreadControlada:
    def __init__(self, target, daemon=True):
        self.target = target
        self.iniciou = False

    def start(self):
        self.iniciou = True


class ComponentesArquitetura(unittest.TestCase):
    def setUp(self):
        self.antigo = {
            "matriz": mod._matriz_atual,
            "gcode": mod._gcode_atual,
            "trajetoria": mod._trajetoria_atual,
            "importacao": copy.deepcopy(mod.estado_importacao_gcode),
            "fonte": mod._repositorio_projeto._fonte,
            "revisao": mod._repositorio_projeto._revisao,
            "job": mod._job_em_execucao,
            "operacao": mod._operacao_manual,
            "pausado": mod._job_pausado,
            "abortado": mod._job_abortar,
            "progresso": copy.deepcopy(mod._job_progresso),
        }
        mod._job_em_execucao = False
        mod._operacao_manual = False
        mod._job_pausado = False
        mod._job_abortar = False
        mod.abortar_job_evento.clear()
        mod.estado_importacao_gcode.update({
            "linhas": None, "programa": None, "nome": None, "usar_parametros_arquivo": False
        })
        mod._repositorio_projeto._fonte = "matriz"
        mod._repositorio_projeto._revisao = 0
        matriz = np.zeros((100, 100), dtype=np.uint8)
        matriz[0, 0] = 4
        mod.atualizar_matriz_global(matriz)

    def tearDown(self):
        mod._matriz_atual = self.antigo["matriz"]
        mod._gcode_atual = self.antigo["gcode"]
        mod._trajetoria_atual = self.antigo["trajetoria"]
        mod.estado_importacao_gcode.clear()
        mod.estado_importacao_gcode.update(self.antigo["importacao"])
        mod._repositorio_projeto._fonte = self.antigo["fonte"]
        mod._repositorio_projeto._revisao = self.antigo["revisao"]
        mod._job_em_execucao = self.antigo["job"]
        mod._operacao_manual = self.antigo["operacao"]
        mod._job_pausado = self.antigo["pausado"]
        mod._job_abortar = self.antigo["abortado"]
        mod._job_progresso.clear()
        mod._job_progresso.update(self.antigo["progresso"])
        mod.abortar_job_evento.clear()

    def test_componentes_existem_e_preservam_adaptadores(self):
        self.assertIsInstance(mod._repositorio_projeto, mod.RepositorioProjetoStudio)
        self.assertIsInstance(mod._preparador_jobs, mod.PreparadorJobStudio)
        self.assertIs(mod._preparador_jobs.repositorio, mod._repositorio_projeto)
        self.assertIsInstance(mod.capturar_snapshot_parametros_job(), mod.ConfiguracaoExecucao)
        self.assertTrue(callable(mod.executar_job_em_background))
        self.assertTrue(callable(mod.executar_job_gcode_em_background))

    def test_configuracao_tem_campos_imutaveis(self):
        config = mod.capturar_snapshot_parametros_job()
        self.assertTrue(is_dataclass(config))
        with self.assertRaises(FrozenInstanceError):
            config.feed_rate = 123.0
        with self.assertRaises(FrozenInstanceError):
            config.pulsos_por_nivel = (99,) * 8
        self.assertEqual(len(config.pulsos_por_nivel), 8)

    def test_dict_legado_se_converte_sem_aliasing(self):
        antigo = mod.capturar_snapshot_parametros_job().to_dict()
        config = mod.ConfiguracaoExecucao.de_snapshot(antigo)
        self.assertIsInstance(config, mod.ConfiguracaoExecucao)
        valor = config["pulsos_por_nivel"][3]
        antigo["pulsos_por_nivel"][3] = 499
        self.assertEqual(config["pulsos_por_nivel"][3], valor)
        self.assertEqual(config.get("feed_rate"), config.feed_rate)

    def test_plano_immutavel_e_independente_do_programa_de_origem(self):
        entrada = mod.analisar_gcode_hibrido("G21\nG1 X3 Y297 F100\nZ4")
        config = mod.capturar_snapshot_parametros_job()
        plano = mod.PlanoJobStudio.criar("gcode", "f1.gcode", True, entrada, config)
        self.assertTrue(is_dataclass(plano))
        with self.assertRaises(FrozenInstanceError):
            plano.origem = "matriz"
        entrada[1]["x"] = 40
        self.assertEqual(plano.programa()[1]["x"], 3.0)
        saida = plano.programa()
        saida[1]["x"] = 99
        self.assertEqual(plano.programa()[1]["x"], 3.0)
        self.assertIs(plano.configuracao, config)

    def test_repositorio_e_dono_de_fonte_e_revisao(self):
        repo = mod._repositorio_projeto
        self.assertEqual(repo.fonte_ativa(), "matriz")
        anterior = repo.resumo()["revisao"]
        mod.carregar_gcode_importado("G1 X3 Y297 F80\nZ3", "arquivo.gcode")
        self.assertEqual(repo.fonte_ativa(), "gcode")
        self.assertGreater(repo.resumo()["revisao"], anterior)
        mod.atualizar_matriz_global(mod._matriz_atual)
        self.assertEqual(repo.fonte_ativa(), "gcode")
        self.assertEqual(repo.resumo()["nome_gcode"], "arquivo.gcode")

    def test_preparacao_nao_aciona_controladores(self):
        mod.carregar_gcode_importado("G1 X3 Y297 F80\nZ3", "referencia.gcode")
        cncf = CNCFalsa()
        with patch.object(cncf, "mover_xy_e_aguardar") as mover:
            plano, motivo = mod._repositorio_projeto.preparar_plano(
                "gcode", mod._repositorio_projeto.resumo()["revisao"]
            )
        mover.assert_not_called()
        self.assertEqual(motivo, "")
        self.assertEqual(plano.nome_programa, "referencia.gcode")
        self.assertEqual(plano.origem, "gcode")

    def test_preparador_encaminha_para_motor_unico(self):
        mod.carregar_gcode_importado("G1 X3 Y297 F80\nZ3", "entrada.gcode")
        cncf = CNCFalsa()
        with patch.object(mod, "executar_job_gcode_em_background", return_value=True) as iniciar:
            ok, mensagem = mod.iniciar_job_selecionado(
                cncf, None, "gcode", mod._repositorio_projeto.resumo()["revisao"]
            )
        self.assertTrue(ok, mensagem)
        iniciar.assert_called_once()
        args, kwargs = iniciar.call_args
        self.assertIs(args[0], cncf)
        self.assertEqual(kwargs["origem"], "gcode")
        self.assertEqual(kwargs["nome_programa"], "entrada.gcode")
        self.assertIsInstance(kwargs["parametros_job"], mod.ConfiguracaoExecucao)

    def test_revisao_incorreta_impede_preparar_e_executar(self):
        mod.carregar_gcode_importado("G1 X3 Y297 F80", "arquivo.gcode")
        atual = mod._repositorio_projeto.resumo()["revisao"]
        mod.selecionar_fonte_job("matriz")
        plano, erro = mod._repositorio_projeto.preparar_plano("gcode", atual)
        self.assertIsNone(plano)
        self.assertIn("Fonte alterada", erro)
        plano, erro = mod._repositorio_projeto.preparar_plano("matriz", atual)
        self.assertIsNone(plano)
        self.assertIn("modificado", erro)

    def test_motor_copia_antes_de_executar_e_respeita_configuracao(self):
        entrada = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        cfg = mod.capturar_snapshot_parametros_job()
        grbl = CNCFalsa()
        motor = mod.MotorExecucaoGCode(grbl, entrada, parametros_job=cfg)
        entrada[0]["x"] = 291
        self.assertEqual(motor.programa[0]["x"], 3.0)
        self.assertIsInstance(motor.config_job, mod.ConfiguracaoExecucao)
        self.assertIs(motor.config_job, cfg)

    def test_motor_concorrencia_rejeita_reserva_manual(self):
        grbl = CNCFalsa()
        prog = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        self.assertTrue(mod.reservar_operacao_manual())
        try:
            self.assertFalse(mod.MotorExecucaoGCode(grbl, prog).iniciar())
        finally:
            mod.liberar_operacao_manual()
        self.assertFalse(mod._job_em_execucao)

    def test_motor_inicia_unica_thread_e_limpa_estado(self):
        grbl = CNCFalsa()
        prog = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        criadas = []
        def criar_thread(target, daemon=True):
            thread = ThreadControlada(target, daemon)
            criadas.append(thread)
            return thread
        with patch.object(mod.threading, "Thread", side_effect=criar_thread):
            motor = mod.MotorExecucaoGCode(grbl, prog)
            self.assertTrue(motor.iniciar())
            self.assertTrue(mod._job_em_execucao)
            self.assertEqual(len(criadas), 1)
            self.assertTrue(criadas[0].iniciou)
            criadas[0].target()
        self.assertFalse(mod._job_em_execucao)
        self.assertTrue(mod._job_progresso["concluido"])
        self.assertEqual(len(grbl.chamadas), 1)
        self.assertAlmostEqual(grbl.chamadas[0][0], 3.0)

    def test_motor_cancela_e_libera_reserva_em_falha_interna(self):
        grbl = CNCFalsa()
        prog = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        threads = []
        def fake_thread(target, daemon=True):
            t = ThreadControlada(target, daemon)
            threads.append(t)
            return t
        with patch.object(mod.threading, "Thread", side_effect=fake_thread):
            self.assertTrue(mod.MotorExecucaoGCode(grbl, prog).iniciar())
            with patch.object(grbl, "preparar_grbl", side_effect=RuntimeError("falha ficticia")):
                threads[0].target()
        self.assertFalse(mod._job_em_execucao)
        self.assertFalse(mod._job_progresso["concluido"])
        self.assertIn("falha ficticia", mod._job_progresso["erro"])

    def test_geometria_alterada_impede_qualquer_movimento(self):
        grbl = CNCFalsa()
        programa = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        motor = mod.MotorExecucaoGCode(grbl, programa)
        with patch.object(mod, "PLOT_X_MIN", mod.PLOT_X_MIN + 12):
            self.assertFalse(motor.iniciar())
        self.assertFalse(grbl.chamadas)
        self.assertFalse(mod._job_em_execucao)

    def test_firmware_real_sem_home_continua_bloqueado(self):
        grbl = CNCFalsa()
        grbl.modo_simulacao = False
        grbl.home_xy_referenciado = False
        programa = mod.analisar_gcode_hibrido("G1 X3 Y297 F90")
        self.assertFalse(mod.MotorExecucaoGCode(grbl, programa).iniciar())
        self.assertFalse(grbl.chamadas)

    def test_interface_legada_recusa_programa_vazio_sem_excecao(self):
        self.assertFalse(mod.executar_job_gcode_em_background(CNCFalsa(), None))
        self.assertFalse(mod.executar_job_gcode_em_background(CNCFalsa(), []))
        self.assertFalse(mod._job_em_execucao)


if __name__ == "__main__":
    unittest.main()
