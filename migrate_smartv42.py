#!/usr/bin/env python3
"""Cria SmartV42 modular, sem alterar smartv41.py e smartv37.py.

Uso: python migrate_smartv42.py /caminho/para/clone/smaartvmatriz
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys

DIR = Path(__file__).resolve().parent
SHA_V41 = '317ee3207e25d5d58d1218b3c8ad9eb8a4a5247c'
PARTES = [
    'smartv42_src/01_config/config.pyfrag',
    'smartv42_src/02_telemetry/logging.pyfrag',
    'smartv42_src/03_actuators/gpio_z.pyfrag',
    'smartv42_src/04_grbl/controller.pyfrag',
    'smartv42_src/05_mechanics/reset.pyfrag',
    'smartv42_src/06_vision/pipeline.pyfrag',
    'smartv42_src/07_trajectory/matrix_job.pyfrag',
    'smartv42_src/09_web/interface.pyfrag',
    'smartv42_src/10_server/application.pyfrag',
]
FATIAS = [0, 380, 473, 761, 2030, 2161, 2429, 2664, 3613]


def trocar(texto, antes, depois, descricao):
    ocorrencias = texto.count(antes)
    if ocorrencias != 1:
        raise RuntimeError(f'{descricao}: esperado trecho exato uma vez; encontrei {ocorrencias}.')
    return texto.replace(antes, depois, 1)


def montar_partes(origem):
    fonte_bytes = origem.read_bytes().replace(b'\r\n', b'\n')  # Git no Windows pode converter CRLF
    git_sha = hashlib.sha1(f'blob {len(fonte_bytes)}\0'.encode() + fonte_bytes).hexdigest()
    if git_sha != SHA_V41:
        raise RuntimeError(
            f'smartv41.py difere da versao analisada: {git_sha}. '
            'Este migrador exige a revisao do GitHub analisada em 19/11/2026. '
            'Nao aplique substituicoes automaticas em outro codigo sem revalidar os trechos.'
        )
    linhas = fonte_bytes.decode('utf-8').splitlines(keepends=True)
    if not (3950 <= len(linhas) <= 4000):
        raise RuntimeError(f'Tamanho inesperado: {len(linhas)} linhas')
    cortes = FATIAS + [len(linhas)]
    partes = {nome: ''.join(linhas[cortes[i]:cortes[i + 1]]) for i, nome in enumerate(PARTES)}

    def patch(i, a, b, rotulo):
        nome = PARTES[i]
        partes[nome] = trocar(partes[nome], a, b, rotulo)

    patch(0, '# -*- coding: utf-8 -*-\n',
          '# -*- coding: utf-8 -*-\n'
          '# GERADO AUTOMATICAMENTE: edite apenas smartv42_src/ e rode tools/build_smartv42.py.\n'
          '# NAO EDITE smartv42.py DIRETAMENTE: GitHub Actions reconstruira o arquivo.\n', 'cabecalho')

    # Falhar fechadamente quando nao ha GPIO fisico. Nao pode contar como sucesso.
    patch(2, '    if not SIMULADO and reles_z and len(reles_z) >= sensor:\n',
          '''    if not SIMULADO and (not GPIO_DISPONIVEL or len(reles_z) != len(PINOS_GPIO_Z)):
        info["erro"] = "GPIO Z indisponivel: confira reles ou use --simulado"
        return False, info
    if not (1 <= num_batidas <= 500):
        info["erro"] = "Pulsos Z fora do intervalo 1..500"
        return False, info
    if not (0.001 <= t_on <= 2.0 and 0 <= t_off <= 2.0):
        info["erro"] = "Duracao Z invalida (ON 1..2000ms; OFF 0..2000ms)"
        return False, info
    pulsos_realizados = 0
    if not SIMULADO and reles_z and len(reles_z) >= sensor:
''', 'validacao GPIO')
    patch(2, '                pino_rele.off()\n                if i < num_batidas - 1:\n',
          '                pino_rele.off()\n                pulsos_realizados += 1\n                if i < num_batidas - 1:\n', 'contagem pulso GPIO')
    patch(2, '            time.sleep(t_on)\n            if i < num_batidas - 1:\n',
          '            time.sleep(t_on)\n            pulsos_realizados += 1\n            if i < num_batidas - 1:\n', 'contagem pulso simulado')
    patch(2, '    info["pulsos"] = num_batidas\n    info["tempo_ms"] = int((time.perf_counter() - t_inicio) * 1000)\n    return True, info\n',
          '''    info["pulsos"] = pulsos_realizados
    info["tempo_ms"] = int((time.perf_counter() - t_inicio) * 1000)
    if pulsos_realizados != num_batidas:
        info["erro"] = "Z interrompido antes do fim da sequencia de pulsos"
        return False, info
    return True, info
''', 'resultado real do Z')

    patch(6, '        _job_em_execucao = True\n        _job_pausado = False\n        _job_abortar = False\n',
          '        _job_em_execucao = True\n        _job_pausado = False\n        _job_abortar = False\n        abortar_job_evento.clear()\n', 'reinicio de job matricial')
    patch(6, '        try:\n            if not grbl.preparar_grbl():\n',
          '''        try:
            ok_cal, erro_cal = validar_janela_calibrada()
            if not ok_cal:
                registrar_log(erro_cal, "erro")
                return
            if not grbl.preparar_grbl():
''', 'pre-validacao de calibracao matriz')

    # Integra o importador sem substituir nenhum dos botoes existentes.
    patch(7, '''          <input type="file" id="inp-file-img" accept="image/*" style="display:none;" onchange="enviarImagemIA(this)">
        </label>''',
          '''          <input type="file" id="inp-file-img" accept="image/*" style="display:none;" onchange="enviarImagemIA(this)">
        </label>
        <label class="btn btn-sky" style="cursor:pointer;" title="XY vai ao GRBL; Z aos GPIOs">
          ⬆ Importar G-code X/Y + Z
          <input type="file" id="inp-file-gcode" accept=".gcode,.nc,.tap,.txt" style="display:none;" onchange="importarGcodeHibrido(this)">
        </label>''', 'botao de Gcode')
    patch(7, 'async function enviarImagemIA(input) {', '''async function importarGcodeHibrido(input) {
  if (!input.files || !input.files[0]) return;
  const arquivo = input.files[0];
  input.value = '';
  if (arquivo.size > 3000000) {
    alert('Arquivo maior que 3 MB. Divida a trajetoria.');
    return;
  }
  const texto = await arquivo.text();
  const resp = await apiPost('carregar_gcode', { nome: arquivo.name, conteudo: texto });
  if (!resp.ok) {
    alert('Erro ao importar G-code: ' + (resp.erro || 'formato nao suportado'));
    return;
  }
  await carregarEstadoInicial();
  alert('G-code validado: ' + resp.blocos + ' blocos. O Z sera enviado aos GPIOs, nao ao GRBL.');
}

async function enviarImagemIA(input) {''', 'JS importa Gcode')
    patch(7, '''async function testarNivelZ(nv) {
  await apiPost('testar_z', { nivel: nv });
}''',
          '''async function testarNivelZ(nv) {
  const resultado = await apiPost('testar_z', { nivel: nv });
  if (!resultado.ok) alert('Falha no acionamento Z: ' + ((resultado.info && resultado.info.erro) || resultado.erro || 'consulte os logs'));
}''', 'erro visivel no teste Z manual')
    patch(7, '''async function iniciarJob() {
  await apiPost('iniciar_job');
  atualizarStatus();
}''',
          '''async function iniciarJob() {
  const resposta = await apiPost('iniciar_job');
  if (!resposta.ok) alert(resposta.erro || 'Job nao iniciado; consulte os logs.');
  atualizarStatus();
}''', 'JS inicia job')
    patch(7, '''function exportarGCode() {
  const txt = document.getElementById('box-gcode').textContent;
  const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'smart_matrix_origem_direita.gcode';
  a.click();
}''',
          r'''async function exportarGCode() {
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
}''', 'exportacao completa')
    patch(7, "  document.getElementById('badge-qtd-pinos').textContent = `${d.pinos_ativos || 0} pinos`;",
          "  document.getElementById('badge-qtd-pinos').textContent = d.gcode_importado ? String(d.linhas_importadas || 0) + ' linhas importadas' : String(d.pinos_ativos || 0) + ' pinos';", 'status importado')

    # Evita acionamentos manuais de Z em paralelo com um trabalho em andamento.
    patch(3, '''        nv = int(up[1])
        registrar_terminal("TX", f">>> {up} (GPIO {PINOS_GPIO_Z[nv-1]})", "cmd")
        ok, info = acionar_eixo_z(nv)
''',
          '''        nv = int(up[1])
        registrar_terminal("TX", f">>> {up} (GPIO {PINOS_GPIO_Z[nv-1]})", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Z manual bloqueado durante job."}
        abortar_job_evento.clear()
        ok, info = acionar_eixo_z(nv)
''', 'terminal Z seguro')
    patch(3, '''    if up in ("TESTE_Z", "T"):
        registrar_terminal("TX", f">>> {up}", "cmd")
''',
          '''    if up in ("TESTE_Z", "T"):
        registrar_terminal("TX", f">>> {up}", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Teste Z bloqueado durante job."}
        abortar_job_evento.clear()
''', 'terminal teste Z seguro')
    patch(3, '''    if up == "SEQ_Z":
        registrar_terminal("TX", ">>> SEQ_Z", "cmd")
''',
          '''    if up == "SEQ_Z":
        registrar_terminal("TX", ">>> SEQ_Z", "cmd")
        if _job_em_execucao:
            return {"ok": False, "resposta": "Sequencia Z bloqueada durante job."}
        abortar_job_evento.clear()
''', 'terminal sequencia Z segura')

    # Backend: mantem as duas modalidades (matriz sintetizada e G-code externo).
    patch(8, '_trajetoria_atual = []\n',
          '_trajetoria_atual = []\n'
          'estado_importacao_gcode = {"linhas": None, "programa": None, "nome": None}\n', 'estado de importacao')
    patch(8, '    _matriz_atual = np.asarray(nova_matriz, dtype=np.uint8)\n',
          '    estado_importacao_gcode.update({"linhas": None, "programa": None, "nome": None})\n'
          '    _matriz_atual = np.asarray(nova_matriz, dtype=np.uint8)\n', 'limpa importacao ao trocar matriz')
    patch(8, '                "gcode": _gcode_atual,\n',
          '                "gcode": estado_importacao_gcode["linhas"] if estado_importacao_gcode["linhas"] is not None else _gcode_atual,\n'
          '                "gcode_importado": estado_importacao_gcode["linhas"] is not None,\n'
          '                "linhas_importadas": len(estado_importacao_gcode["linhas"] or []),\n', 'resposta de Gcode')
    patch(8, '''        tamanho = int(self.headers.get("Content-Length", 0))
        bruto = self.rfile.read(tamanho).decode("utf-8") if tamanho > 0 else "{}"
''',
          '''        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho > 6_000_000:
            self._enviar_json({"ok": False, "erro": "Requisicao maior que 6 MB."}, 413)
            return
        bruto = self.rfile.read(tamanho).decode("utf-8") if tamanho > 0 else "{}"
''', 'limite de requisicao')
    patch(8, '''        acao = dados.get("action", "")

        if acao == "config_coordenadas":''',
          '''        acao = dados.get("action", "")

        if acao == "carregar_gcode":
            if _job_em_execucao:
                self._enviar_json({"ok": False, "erro": "Conclua o job anterior antes de importar."})
                return
            try:
                conteudo = dados.get("conteudo", "")
                programa = analisar_gcode_hibrido(conteudo)
            except (ValueError, TypeError) as exc:
                self._enviar_json({"ok": False, "erro": str(exc)})
                return
            estado_importacao_gcode.update({
                "linhas": conteudo.splitlines(), "programa": programa,
                "nome": str(dados.get("nome", "arquivo.gcode"))[:120],
            })
            registrar_log(f"G-code importado: {len(programa)} blocos; Z separado do GRBL.", "ok")
            self._enviar_json({"ok": True, "blocos": len(programa)})
            return

        if acao == "config_coordenadas":''', 'endpoint importacao')
    patch(8, '''                _job_abortar = True
                _job_pausado = False
                _grbl_global.soft_reset()
''',
          '''                _job_abortar = True
                _job_pausado = False
                if _job_em_execucao:
                    abortar_job_evento.set()
                _grbl_global.soft_reset()
''', 'abort sincronizado')
    patch(8, '''        if acao == "testar_z":
            nv = int(dados.get("nivel", 1))
            acionar_eixo_z(nv)
            self._enviar_json({"ok": True})
            return
''',
          '''        if acao == "testar_z":
            if _job_em_execucao:
                self._enviar_json({"ok": False, "erro": "Z manual bloqueado durante job."})
                return
            nv = int(dados.get("nivel", 1))
            abortar_job_evento.clear()
            ok, info = acionar_eixo_z(nv)
            self._enviar_json({"ok": ok, "info": info})
            return
''', 'status real de Z')
    patch(8, '''        if acao == "iniciar_job":
            ok = executar_job_em_background(_grbl_global, _reset_global, _matriz_atual)
            self._enviar_json({"ok": ok})
            return
''',
          '''        if acao == "iniciar_job":
            if estado_importacao_gcode["programa"] is not None:
                ok = executar_job_gcode_em_background(_grbl_global, estado_importacao_gcode["programa"])
            else:
                ok = executar_job_em_background(_grbl_global, _reset_global, _matriz_atual)
            self._enviar_json({"ok": ok, "erro": None if ok else "Ja existe job ativo ou falha ao iniciar."})
            return
''', 'seletor de executor')
    patch(8, 'parser = argparse.ArgumentParser(description="Smart Matrix Studio v1.9 (UGS + Origem Direita 0,0)")',
          'parser = argparse.ArgumentParser(description="Smart Matrix Studio v2.0 (G-code XY + Z GPIO)")', 'versao de interface CLI')
    patch(8, 'registrar_log(f"Smart Matrix Studio v1.9 (UGS + Origem Direita 0,0) iniciado em {url}")',
          'registrar_log(f"Smart Matrix Studio v2.0 (G-code XY + Z GPIO) iniciado em {url}")', 'versao de log')
    partes['smartv42_src/08_gcode/hybrid_runner.pyfrag'] = (
        DIR / 'smartv42_src/08_gcode/hybrid_runner.pyfrag'
    ).read_text(encoding='utf-8')
    return partes


def migrar(destino, force=False):
    fonte = destino / 'smartv41.py'
    if not fonte.is_file():
        raise RuntimeError(f'Nao encontrei smartv41.py em {destino}')
    partes = montar_partes(fonte)
    alvo = destino / 'smartv42.py'
    if alvo.exists() and not force:
        raise RuntimeError('smartv42.py ja existe. Para refazer a migracao, use --force (revisando suas edicoes).')
    # Calcular tudo antes de escrever; se um patch falhar, nada e modificado.
    for relativo, texto in partes.items():
        path = destino / relativo
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texto, encoding='utf-8', newline='\n')
    # Os arquivos auxiliares ja estao versionados nos destinos corretos.
    from subprocess import run
    result = run([sys.executable, str(destino / 'tools/build_smartv42.py')], check=False)
    if result.returncode:
        raise RuntimeError('Falha ao compilar smartv42.py. Confira os modulos.')
    print('MIGRACAO CONCLUIDA: smartv42.py + smartv42_src/ + bot + testes.')
    print('Os arquivos smartv41.py e smartv37.py permaneceram intactos.')
    print('Teste primeiro: python smartv42.py --simulado')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('repositorio', type=Path, help='Pasta local do clone do repositorio smaartvmatriz')
    cli.add_argument('--force', action='store_true')
    args = cli.parse_args()
    migrar(args.repositorio.resolve(), args.force)


if __name__ == '__main__':
    main()
