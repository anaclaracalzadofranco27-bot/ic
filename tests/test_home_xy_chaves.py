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
            passo=0.5, feed=120.0, recuo=5.0, coord_x=0.0, coord_y=0.0,
            seguranca_confirmada=True,
        )
        dados.update(kwargs)
        with patch.object(mod, "SIMULADO", True):
            return ctrl.executar_home_xy_chaves(**dados)

    def test_home_com_chaves_x_y_e_recuo_de_cinco_mm(self):
        ctrl = self.novo_controlador()
        self.assertTrue(ctrl.enviar_comando("$22=0")[0])
        ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        self.assertTrue(ctrl.home_xy_referenciado)
        self.assertEqual(ctrl.home_xy_estado["fase"], "Home XY concluido")
        self.assertAlmostEqual(ctrl.mpos[0], 2.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -2.0, places=2)
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
        self.assertIn("Busca do eixo X encerrada por limite", motivo)
        self.assertFalse(ctrl.home_xy_referenciado)
        self.assertLessEqual(ctrl.mpos[0], 1.01)

    def test_y_liberado_x_ainda_pendente_parada_por_curso_x_de_30_mm(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_x = -50.0
        ctrl.ser.home_simulado_y = 3.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, erro = self.executar_simulado(ctrl, curso_x=30.0, curso_y=30.0)
        self.assertFalse(ok)
        self.assertIn("Busca do eixo X encerrada por limite", erro)
        self.assertIn("30.0/30.0 mm", erro)
        self.assertIn("Eixos ja encontrados: Y", erro)
        comandos = [c.args[0] for c in enviar.call_args_list]
        indice_recuo_y = next(i for i, c in enumerate(comandos)
                               if "$J=G91 G21 Y-5.000 " in c)
        self.assertTrue(any("$J=G91 G21 X-0.500 " in c
                            for c in comandos[indice_recuo_y + 1:]))
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_y_encontrado_antes_x_continua_ate_chave_x_mais_distante(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_x = -8.0
        ctrl.ser.home_simulado_y = 2.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(
                ctrl, curso_x=12.0, curso_y=12.0
            )
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        indice_recuo_y = next(i for i, c in enumerate(comandos)
                               if "$J=G91 G21 Y-5.000 " in c)
        self.assertTrue(any("$J=G91 G21 X-0.500 " in c
                            for c in comandos[indice_recuo_y + 1:]))
        self.assertTrue(ctrl.home_xy_referenciado)

    def test_leitura_de_chave_que_demora_a_liberar_nao_aborta(self):
        ctrl = self.novo_controlador()
        ativo = "<Idle|MPos:0,0,0|Pn:Y>"
        livre = "<Idle|MPos:0,0,0>"
        with patch.object(ctrl, "_status_home_xy",
                          side_effect=[ativo] * 6 + [livre, livre]):
            ok, estado, leituras = ctrl._confirmar_chave_liberada_home("Y")
        self.assertTrue(ok)
        self.assertEqual(len(leituras), 8)
        self.assertEqual(estado, livre)

    def test_chave_inicialmente_acionada_primeiro_libera(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.ser.mpos[0] = -3.0
        ctrl.ser.mpos[1] = 3.0
        ok, motivo = self.executar_simulado(ctrl)
        self.assertTrue(ok, motivo)
        self.assertAlmostEqual(ctrl.mpos[0], 2.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -2.0, places=2)

    def test_chave_x_presa_apos_recuo_interrompe_home_sem_referencia(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ler_status_original = ctrl._status_home_xy
        contato_x_ocorreu = [False]

        def status_com_chave_x_presa():
            estado = ler_status_original()
            if "X" in mod.pinos_ativos(estado):
                contato_x_ocorreu[0] = True
            if contato_x_ocorreu[0] and "X" not in mod.pinos_ativos(estado):
                if "|Pn:" in estado:
                    estado = estado.replace("|Pn:", "|Pn:X", 1)
                else:
                    estado = estado.replace(">", "|Pn:X>", 1)
            return estado

        with patch.object(ctrl, "_status_home_xy", side_effect=status_com_chave_x_presa), \
             patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, erro = self.executar_simulado(ctrl)
        self.assertFalse(ok)
        self.assertIn("Chave X nao liberou", erro)
        self.assertIn("MPos informado", erro)
        self.assertIn("Pn final:X", erro)
        self.assertIn("Home XY interrompido", erro)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-0.500 Y0.500 " in c
                            for c in comandos))  # X e Y participaram da busca
        self.assertFalse(ctrl.home_xy_referenciado)

    def test_trecho_cinco_mm_default_produz_um_jog_xy_conjunto(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, msg = self.executar_simulado(
                ctrl, passo=5.0, feed=500.0, curso_x=30.0, curso_y=30.0
            )
        self.assertTrue(ok, msg)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-5.000 Y5.000 F500.0" in c
                            for c in comandos))
        self.assertTrue(ctrl.home_xy_referenciado)

    def test_trecho_dez_mm_permitido_com_supervisao_das_duas_chaves(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        # Uma simulacao que confirma a deteccao apenas no fim do segmento,
        # nao a distancia de parada fisica real em uma CNC.
        ctrl.ser.home_simulado_x = -10.0
        ctrl.ser.home_simulado_y = 10.0
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, msg = self.executar_simulado(
                ctrl, passo=10.0, feed=500.0, curso_x=30.0, curso_y=30.0
            )
        self.assertTrue(ok, msg)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-10.000 Y10.000 F500.0" in c
                            for c in comandos))
        self.assertTrue(ctrl.home_xy_referenciado)

    def test_trecho_superior_a_dez_mm_rejeitado_sem_movimento(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ok, msg = self.executar_simulado(ctrl, passo=11.0)
        self.assertFalse(ok)
        self.assertIn("0.1..10 mm", msg)
        self.assertEqual(ctrl.mpos[:2], [0.0, 0.0])

    def test_busca_continua_enfileira_jog_sem_esperar_idle(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.ser.home_simulado_x = -8.0
        ctrl.ser.home_simulado_y = 8.0
        estados_na_emissao = []
        enviar_original = ctrl.enviar_comando

        def observar_envio(comando, *args, **kwargs):
            if comando.startswith("$J=G91 G21 X-0.500 Y0.500"):
                estados_na_emissao.append(ctrl.estado)
            return enviar_original(comando, *args, **kwargs)

        with patch.object(ctrl, "enviar_comando", side_effect=observar_envio):
            ok, msg = self.executar_simulado(
                ctrl, passo=0.5, curso_x=12.0, curso_y=12.0
            )
        self.assertTrue(ok, msg)
        self.assertGreaterEqual(len(estados_na_emissao), 2)
        self.assertTrue(any(st in ("JOG", "RUN") for st in estados_na_emissao[1:]))
        self.assertTrue(ctrl.home_xy_referenciado)

    def test_y_primeiro_recuo_oposto_a_busca_e_x_segue_em_fluxo(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_x = -8.0
        ctrl.ser.home_simulado_y_sentido = -1
        ctrl.ser.home_simulado_y = -2.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, msg = self.executar_simulado(
                ctrl, direcao_y=-1, passo=1.0, feed=500.0,
                recuo=5.0, recuo_y=7.0, curso_x=12.0, curso_y=12.0
            )
        self.assertTrue(ok, msg)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-1.000 Y-1.000 " in c
                            for c in comandos))
        indice_recuo_y = next(
            i for i, c in enumerate(comandos) if "$J=G91 G21 Y7.000 " in c
        )
        self.assertTrue(any("$J=G91 G21 X-1.000 " in c
                            for c in comandos[indice_recuo_y+1:]))
        self.assertFalse(any(
            "$J=G91 G21 X-1.000 Y" in c
            for c in comandos[indice_recuo_y+1:]
        ))
        self.assertNotIn("Y", ctrl.pinos_pn)

    def test_y_inicialmente_pressionado_recua_antes_de_buscar_xy(self):
        ctrl = self.novo_controlador()
        ctrl.ser.mpos[1] = 3.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, msg = self.executar_simulado(ctrl)
        self.assertTrue(ok, msg)
        comandos = [c.args[0] for c in enviar.call_args_list]
        indice_recuo = next(i for i, c in enumerate(comandos)
                            if "$J=G91 G21 Y-5.000 " in c)
        indice_busca = next(i for i, c in enumerate(comandos)
                            if "$J=G91 G21 X-0.500 Y0.500 " in c)
        self.assertLess(indice_recuo, indice_busca)
        self.assertNotIn("Y", ctrl.pinos_pn)

    def test_busca_sincronizada_envia_x_e_y_no_mesmo_jog(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-0.500 Y0.500 " in c for c in comandos))
        self.assertTrue(any("$J=G91 G21 X5.000 Y-5.000 " in c for c in comandos))

    def test_y_chega_primeiro_x_continua_sem_mover_y(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_y = 1.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        indice_recuo_y = next(i for i, c in enumerate(comandos)
                               if "$J=G91 G21 Y-5.000 " in c)
        self.assertTrue(any("$J=G91 G21 X-0.500 " in c
                            for c in comandos[indice_recuo_y + 1:]))
        self.assertAlmostEqual(ctrl.mpos[0], 2.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -4.0, places=2)

    def test_x_chega_primeiro_y_continua_sem_mover_x(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_x = -1.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        indice_recuo_x = next(i for i, c in enumerate(comandos)
                               if "$J=G91 G21 X5.000 " in c)
        self.assertTrue(any("$J=G91 G21 Y0.500 " in c
                            for c in comandos[indice_recuo_x + 1:]))
        self.assertAlmostEqual(ctrl.mpos[0], 4.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -2.0, places=2)

    def test_ambas_chaves_inicialmente_acionadas_recuam_juntas(self):
        ctrl = self.novo_controlador()
        ctrl.ser.mpos[:2] = [-3.0, 3.0]
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any(
            "$J=G91 G21 X5.000 Y-5.000 F" in c for c in comandos
        ))
        self.assertTrue(any(
            "$J=G91 G21 X-0.500 Y0.500 F" in c for c in comandos
        ))

    def test_duas_chaves_detectadas_no_mesmo_passo_recuam_juntas(self):
        ctrl = self.novo_controlador()
        ctrl.ser.home_simulado_x = -1.0
        ctrl.ser.home_simulado_y = 1.0
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any(
            "$J=G91 G21 X5.000 Y-5.000 F" in c for c in comandos
        ))
        self.assertAlmostEqual(ctrl.mpos[0], 4.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -4.0, places=2)

    def test_y_presa_apos_recuo_nao_confirma_home(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        leitura_original = ctrl._status_home_xy
        detectou_y = [False]

        def status_com_y_presa():
            st = leitura_original()
            pn = mod.pinos_ativos(st)
            if "Y" in pn:
                detectou_y[0] = True
            if detectou_y[0] and "Y" not in pn:
                if "|Pn:" in st:
                    st = st.replace("|Pn:", "|Pn:Y", 1)
                else:
                    st = st.replace(">", "|Pn:Y>", 1)
            return st

        with patch.object(ctrl, "_status_home_xy", side_effect=status_com_y_presa):
            ok, mensagem = self.executar_simulado(ctrl)
        self.assertFalse(ok)
        self.assertIn("Chave Y nao liberou", mensagem)
        self.assertIn("MPos informado", mensagem)
        self.assertFalse(ctrl.home_xy_referenciado)
        self.assertGreater(ctrl.mpos[0], 0.0)

    def test_recuo_fora_da_faixa_2_a_10_mm_nao_e_aceito(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        for kwargs in ({"recuo": 1.0}, {"recuo_y": 11.0}):
            ok, mensagem = self.executar_simulado(ctrl, **kwargs)
            self.assertFalse(ok)
            self.assertIn("entre 2 e 10 mm", mensagem)

    def test_recuo_y_independente_do_x(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, mensagem = self.executar_simulado(ctrl, recuo=5.0, recuo_y=8.0)
        self.assertTrue(ok, mensagem)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X5.000 Y-8.000 F" in c
                            for c in comandos))
        self.assertAlmostEqual(ctrl.mpos[0], 2.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], -5.0, places=2)

    def test_confirmacao_chave_requer_duas_leituras_livres_consecutivas(self):
        ctrl = self.novo_controlador()
        ativo = "<Idle|MPos:0,0,0|Pn:X>"
        livre = "<Idle|MPos:0,0,0>"
        with patch.object(ctrl, "_status_home_xy",
                          side_effect=[ativo, livre, livre]):
            ok, _, amostras = ctrl._confirmar_chave_liberada_home("X")
        self.assertTrue(ok)
        self.assertEqual(amostras, ["X", "livre", "livre"])
        with patch.object(ctrl, "_status_home_xy",
                          side_effect=[livre, ativo] * 6):
            ok, _, amostras = ctrl._confirmar_chave_liberada_home("X")
        self.assertFalse(ok)
        self.assertEqual(amostras, ["livre", "X"] * 6)

    def test_y_negativo_com_feed_500_e_busca_maxima_300_mm(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ctrl.ser.home_simulado_y_sentido = -1
        ctrl.ser.home_simulado_y = -3.0
        with patch.object(ctrl, "enviar_comando", wraps=ctrl.enviar_comando) as enviar:
            ok, msg = self.executar_simulado(
                ctrl, direcao_y=-1, feed=500.0, curso_x=300.0, curso_y=300.0
            )
        self.assertTrue(ok, msg)
        comandos = [c.args[0] for c in enviar.call_args_list]
        self.assertTrue(any("$J=G91 G21 X-0.500 Y-0.500 F500.0" in c
                            for c in comandos))
        self.assertTrue(any("$J=G91 G21 X5.000 Y5.000 F500.0" in c
                            for c in comandos))
        self.assertAlmostEqual(ctrl.mpos[0], 2.0, places=2)
        self.assertAlmostEqual(ctrl.mpos[1], 2.0, places=2)

    def test_home_rejeita_curso_acima_de_300_e_feed_acima_de_600(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$22=0")
        ok, msg = self.executar_simulado(ctrl, curso_y=301.0)
        self.assertFalse(ok)
        self.assertIn("1..300 mm", msg)
        ok, msg = self.executar_simulado(ctrl, feed=601.0)
        self.assertFalse(ok)
        self.assertIn("20..600 mm/min", msg)

    def test_inversao_y_no_grbl_exige_confirmacao_e_preserva_x(self):
        ctrl = self.novo_controlador()
        ctrl.enviar_comando("$3=1")
        respostas = []
        handler = mod.StudioRequestHandler.__new__(mod.StudioRequestHandler)
        handler._enviar_json = lambda corpo, status=200: respostas.append((corpo, status))
        with patch.object(mod, "_grbl_global", ctrl):
            handler._executar_acao_POST("inverter_motor_y_grbl", {"confirmado": False})
            self.assertFalse(respostas[-1][0]["ok"])
            self.assertEqual(ctrl.ser.settings[3][0], "1")
            handler._executar_acao_POST("inverter_motor_y_grbl", {"confirmado": True})
        self.assertTrue(respostas[-1][0]["ok"], respostas[-1])
        self.assertEqual(respostas[-1][0]["mascara"], 3)
        self.assertEqual(ctrl.ser.settings[3][0], "3")
        self.assertFalse(ctrl.home_xy_referenciado)

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
        self.assertIn("Busca do eixo X encerrada por limite", erro)
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
