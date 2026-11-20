"""Estado da UI separado do G-code importado e snapshot de cada execucao."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("smartv42_estado_projeto", ROOT / "smartv42.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class GRBLParaJob:
    def __init__(self):
        self.modo_simulacao = True
        self.home_xy_referenciado = False
        self.wpos = [0.0, -297.0, 0.0]
    def preparar_grbl(self):
        return "<Idle|MPos:0,-297,0>"


class ThreadAdiada:
    def __init__(self, target, daemon=True):
        self.target = target
    def start(self):
        pass


class EstadosIsolados(unittest.TestCase):
    def setUp(self):
        self.anterior = {
            "matriz": mod._matriz_atual,
            "gcode": mod._gcode_atual,
            "trajetoria": mod._trajetoria_atual,
            "importado": copy.deepcopy(mod.estado_importacao_gcode),
            "fonte": mod._fonte_job_ativa,
            "revisao": mod._revisao_estado_projeto,
            "job_em_execucao": mod._job_em_execucao,
            "operacao_manual": mod._operacao_manual,
            "job_abortar": mod._job_abortar,
            "job_pausado": mod._job_pausado,
            "progresso": copy.deepcopy(mod._job_progresso),
        }
        mod._job_em_execucao = False
        mod._operacao_manual = False
        mod._job_abortar = False
        mod._job_pausado = False
        mod.abortar_job_evento.clear()
        mod.estado_importacao_gcode.update({
            "linhas": None, "programa": None, "nome": None, "usar_parametros_arquivo": False
        })
        mod._fonte_job_ativa = "matriz"
        mod._revisao_estado_projeto = 0
        m = np.zeros((100, 100), dtype=np.uint8)
        m[0, 1] = 4
        mod.atualizar_matriz_global(m)

    def tearDown(self):
        mod._matriz_atual = self.anterior["matriz"]
        mod._gcode_atual = self.anterior["gcode"]
        mod._trajetoria_atual = self.anterior["trajetoria"]
        mod.estado_importacao_gcode.clear()
        mod.estado_importacao_gcode.update(self.anterior["importado"])
        mod._fonte_job_ativa = self.anterior["fonte"]
        mod._revisao_estado_projeto = self.anterior["revisao"]
        mod._job_em_execucao = self.anterior["job_em_execucao"]
        mod._operacao_manual = self.anterior["operacao_manual"]
        mod._job_abortar = self.anterior["job_abortar"]
        mod._job_pausado = self.anterior["job_pausado"]
        mod._job_progresso.clear()
        mod._job_progresso.update(self.anterior["progresso"])
        mod.abortar_job_evento.clear()

    def importar(self, nome="programa_01.gcode", arquivo=False):
        return mod.carregar_gcode_importado(
            "G21\nG90\nG54\nG1 X3 Y-297 F700\nZ4",
            nome, usar_parametros_arquivo=arquivo,
        )

    def test_importacao_seleciona_gcode_sem_apagar_matriz(self):
        original = mod._matriz_atual.copy()
        d = self.importar()
        self.assertEqual(d["fonte_ativa"], "gcode")
        self.assertTrue(d["gcode_importado"])
        self.assertEqual(d["nome_gcode"], "programa_01.gcode")
        self.assertTrue(np.array_equal(mod._matriz_atual, original))

    def test_reprocessar_matriz_nao_apaga_gcode_ou_preferencia(self):
        self.importar(arquivo=True)
        m = np.zeros((100, 100), dtype=np.uint8)
        m[50, 75] = 8
        mod.atualizar_matriz_global(m)
        d = mod.obter_estado_fontes_job()
        self.assertEqual(d["fonte_ativa"], "gcode")
        self.assertEqual(d["nome_gcode"], "programa_01.gcode")
        self.assertTrue(d["usar_parametros_arquivo"])

    def test_gerar_novo_padrao_muda_fonte_mas_preserva_arquivo(self):
        self.importar()
        mod.atualizar_matriz_global(
            np.ones((100, 100), dtype=np.uint8), descartar_importacao=True
        )
        d = mod.obter_estado_fontes_job()
        self.assertEqual(d["fonte_ativa"], "matriz")
        self.assertTrue(d["gcode_importado"])
        mod.selecionar_fonte_job("gcode")
        self.assertEqual(mod.obter_fonte_job_ativa(), "gcode")

    def test_importacao_invalida_preserva_arquivo_anterior(self):
        self.importar(nome="bom.gcode")
        with self.assertRaises(ValueError):
            mod.carregar_gcode_importado("COMANDO DESCONHECIDO", "ruim.gcode")
        self.assertEqual(mod.obter_estado_fontes_job()["nome_gcode"], "bom.gcode")

    def test_remocao_sera_somente_explicita(self):
        self.importar()
        mod.remover_gcode_importado()
        d = mod.obter_estado_fontes_job()
        self.assertFalse(d["gcode_importado"])
        self.assertEqual(d["fonte_ativa"], "matriz")
        with self.assertRaises(ValueError):
            mod.selecionar_fonte_job("gcode")

    def test_nao_permite_modificar_preferencia_sem_arquivo(self):
        with self.assertRaises(ValueError):
            mod.definir_preferencia_parametros_gcode(True)

    def test_preferencia_arquivo_eh_editavel_sem_reimportar(self):
        self.importar(arquivo=False)
        anterior = mod.obter_estado_fontes_job()["revisao"]
        mod.definir_preferencia_parametros_gcode(True)
        self.assertTrue(mod.estado_importacao_gcode["usar_parametros_arquivo"])
        self.assertGreater(mod.obter_estado_fontes_job()["revisao"], anterior)
        mod.selecionar_fonte_job("matriz")
        mod.selecionar_fonte_job("gcode")
        self.assertTrue(mod.estado_importacao_gcode["usar_parametros_arquivo"])

    def test_matriz_publica_copia_sem_aliasing(self):
        matriz = np.zeros((100, 100), dtype=np.uint8)
        matriz[10, 10] = 4
        mod.atualizar_matriz_global(matriz)
        matriz[10, 10] = 0
        self.assertEqual(int(mod._matriz_atual[10, 10]), 4)

    def test_falha_compilacao_nao_publica_estado_parcial(self):
        antes = mod._matriz_atual.copy()
        gcode = list(mod._gcode_atual)
        with patch.object(mod, "compilar_matriz_para_estudio", side_effect=RuntimeError("falhou")):
            with self.assertRaises(RuntimeError):
                mod.atualizar_matriz_global(np.zeros((100, 100), dtype=np.uint8))
        self.assertTrue(np.array_equal(mod._matriz_atual, antes))
        self.assertEqual(mod._gcode_atual, gcode)

    def test_fonte_esperada_e_revisao_impedem_plotagem_errada(self):
        self.importar()
        anterior = mod.obter_estado_fontes_job()["revisao"]
        mod.selecionar_fonte_job("matriz")
        with patch.object(mod, "executar_job_gcode_em_background", return_value=True) as iniciar:
            ok, erro = mod.iniciar_job_selecionado(GRBLParaJob(), None,
                                                    fonte_esperada="gcode", revisao_esperada=anterior)
            self.assertFalse(ok)
            self.assertIn("Fonte alterada", erro)
            iniciar.assert_not_called()
            ok, erro = mod.iniciar_job_selecionado(GRBLParaJob(), None,
                                                    fonte_esperada="matriz", revisao_esperada=anterior)
            self.assertFalse(ok)
            self.assertIn("modificado", erro)
            iniciar.assert_not_called()

    def test_job_selecionado_passado_com_copia_arquivo_e_snapshot(self):
        self.importar(nome="arquivo_a.gcode", arquivo=True)
        with patch.object(mod, "executar_job_gcode_em_background", return_value=True) as iniciar:
            ok, msg = mod.iniciar_job_selecionado(GRBLParaJob(), None,
                         fonte_esperada="gcode",
                         revisao_esperada=mod.obter_estado_fontes_job()["revisao"])
            self.assertTrue(ok, msg)
        programa = iniciar.call_args.args[1]
        cfg = iniciar.call_args.kwargs["parametros_job"]
        self.assertEqual(iniciar.call_args.kwargs["nome_programa"], "arquivo_a.gcode")
        self.assertTrue(iniciar.call_args.kwargs["usar_parametros_arquivo"])
        self.assertIsNot(programa[3], mod.estado_importacao_gcode["programa"][3])
        self.assertEqual(cfg["feed_rate"], mod.FEED_RATE)
        self.assertEqual(cfg["pulsos_por_nivel"][3], mod.obter_pulsos_para_nivel(4))

    def test_job_selecionado_somente_matriz_ignora_arquivo_guardado(self):
        self.importar(nome="outro.gcode")
        mod.selecionar_fonte_job("matriz")
        with patch.object(mod, "executar_job_gcode_em_background", return_value=True) as iniciar:
            ok, _ = mod.iniciar_job_selecionado(GRBLParaJob(), None)
            self.assertTrue(ok)
        self.assertEqual(iniciar.call_args.kwargs["origem"], "matriz")
        self.assertEqual(iniciar.call_args.kwargs["nome_programa"], "Matriz 100x100")

    def test_get_matrix_mostra_preview_da_fonte_selecionada(self):
        self.importar(nome="arquivo_preview.gcode")
        saidas = []
        handler = mod.StudioRequestHandler.__new__(mod.StudioRequestHandler)
        handler.path = "/api/matrix"
        handler._enviar_json = lambda resposta, status=200: saidas.append(resposta)
        handler.do_GET()
        self.assertTrue(saidas[0]["gcode_importado"])
        self.assertEqual(saidas[0]["fontes_job"]["nome_gcode"], "arquivo_preview.gcode")
        self.assertIn("G1 X3 Y-297 F700", saidas[0]["gcode"])
        mod.selecionar_fonte_job("matriz")
        saidas.clear()
        handler.do_GET()
        self.assertNotIn("G1 X3 Y-297 F700", saidas[0]["gcode"])
        self.assertTrue(saidas[0]["gcode_importado"])

    def test_job_copia_profunda_programa_antes_da_thread(self):
        programa = mod.analisar_gcode_hibrido("G1 X3 Y-297 F700")
        threads = []
        obs = []
        with patch.object(mod.threading, "Thread", side_effect=lambda target,daemon: (threads.append(ThreadAdiada(target,daemon)) or threads[-1])), \
             patch.object(mod, "validar_programa_antes_movimento", return_value=True), \
             patch.object(mod, "validar_janela_calibrada", return_value=(True, "")), \
             patch.object(mod, "executar_bloco_hibrido", side_effect=lambda g,b,c: obs.append((copy.deepcopy(b), copy.deepcopy(c["config_job"])))):

            self.assertTrue(mod.executar_job_gcode_em_background(GRBLParaJob(), programa))
            programa[0]["x"] = 250
            with patch.object(mod, "FEED_RATE", 10):
                threads[0].target()

        self.assertEqual(obs[0][0]["x"], 3.0)
        self.assertEqual(obs[0][1]["feed_rate"], self.anterior.get("nunca", mod.FEED_RATE))
        self.assertFalse(mod._job_em_execucao)

    def test_parametros_z_congelados_ate_fim_do_job(self):
        cfg = mod.capturar_snapshot_parametros_job()
        nivel = 4
        bloco = {"tipo": "z", "nivel": nivel, "pulsos": 12, "on_ms": 20, "off_ms": 0}
        contexto = {"posicao_xy": (3.0, -297.0), "usar_parametros_arquivo": False,
                    "config_job": cfg}
        fake = GRBLParaJob()
        fake.aguardar_grbl_idle = lambda timeout=None: (True, "")
        with patch.object(mod, "PULSOS_POR_NIVEL", [99] * 8), \
             patch.object(mod, "TEMPO_ON_Z_S", 0.5), \
             patch.object(mod, "TEMPO_OFF_Z_S", 0.6), \
             patch.object(mod, "PAUSA_ANTES_Z_S", 0), \
             patch.object(mod, "acionar_eixo_z", return_value=(True, {"pino_gpio": 27, "pulsos": 3})) as pulso, \
             patch.object(mod.time, "sleep"):
            mod.executar_bloco_hibrido(fake, bloco, contexto)
            kw = pulso.call_args.kwargs
        self.assertEqual(kw["pulsos_override"], cfg["pulsos_por_nivel"][3])
        self.assertEqual(kw["on_ms_override"], cfg["on_ms"])
        self.assertEqual(kw["off_ms_override"], cfg["off_ms"])

    def test_arquivo_prevalece_sobre_snapshot_se_opcao_marcada(self):
        cfg = mod.capturar_snapshot_parametros_job()
        bloco = {"tipo": "z", "nivel": 4, "pulsos": 12, "on_ms": 20, "off_ms": 0}
        fake = GRBLParaJob()
        fake.aguardar_grbl_idle = lambda timeout=None: (True, "")
        contexto = {"posicao_xy": (3.0, -297.0), "usar_parametros_arquivo": True, "config_job": cfg}
        with patch.object(mod, "acionar_eixo_z", return_value=(True, {"pino_gpio": 27, "pulsos": 12})) as z, \
             patch.object(mod.time,"sleep"):
            mod.executar_bloco_hibrido(fake, bloco, contexto)
        self.assertEqual(z.call_args.kwargs["pulsos_override"], 12)
        self.assertEqual(z.call_args.kwargs["on_ms_override"], 20)
        self.assertEqual(z.call_args.kwargs["off_ms_override"], 0)

    def test_alterar_geometria_aborta_job_snapshot(self):
        cfg = mod.capturar_snapshot_parametros_job()
        with patch.object(mod, "PLOT_X_MIN", -1.0):
            with self.assertRaises(RuntimeError):
                mod.validar_snapshot_geometria_job(cfg)

    def test_gpio_embutido_no_gcode_revalidado_no_inicio(self):
        arquivo = "G1 X3 Y-297 F900\nM42 P1 ; Z_LEVEL=4 GPIO=27 PULSOS=7"
        programa = mod.analisar_gcode_hibrido(arquivo)
        ctrl = GRBLParaJob()
        # O arquivo foi analisado quando Z4 era GPIO27. Uma reconfiguracao
        # posterior nao pode redirecionar seus acionamentos para outro pino.
        com_pinos = list(mod.PINOS_GPIO_Z)
        com_pinos[3] = 22
        with patch.object(mod, "PINOS_GPIO_Z", com_pinos):
            with self.assertRaisesRegex(ValueError, "GPIO de Z4"):
                mod.validar_programa_antes_movimento(ctrl, programa)

    def test_fonte_gcode_preservada_apos_recompilar_preview(self):
        self.importar(nome="preservado.gcode")
        atual = mod.obter_estado_fontes_job()["revisao"]
        with patch.object(mod, "FEED_RATE", 345.0):
            mod.atualizar_matriz_global(mod._matriz_atual)
        estado = mod.obter_estado_fontes_job()
        self.assertEqual(estado["fonte_ativa"], "gcode")
        self.assertEqual(estado["nome_gcode"], "preservado.gcode")
        self.assertGreater(estado["revisao"], atual)



if __name__ == "__main__":
    unittest.main()
