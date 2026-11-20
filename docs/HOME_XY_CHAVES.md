# Home XY automatico por chaves da CNC — SmartV42

## Funcionamento

O novo Home XY da interface usa os pinos X/Y da CNC pela USB, sem enviar $H nem mover Z.

1. Confirma o GRBL em Idle e le as chaves pelo status Pn:X e Pn:Y.
2. Se alguma chave estiver inicialmente acionada, libera essa chave com recuo limitado a 5,0 mm antes de iniciar a busca.
3. Busca X e Y **juntos** usando um comando de jog XY de ate 10 mm por eixo em cada passo, supervisionando Pn:.
4. Ao detectar uma das chaves, cancela o jog de **ambos os eixos** (byte 0x85), confirma o contato e **recua 5,0 mm no eixo acionado**, sem avancar no outro. Prossegue somente com os eixos restantes ate a deteccao de ambas as chaves.
5. Confirma a liberacao de X e Y. So depois grava os comandos G21, G90, G54, G92.1 e G10 L20 P1 X... Y..., verificando WPos.
6. Invalida o Home apos reconexao USB, soft reset, Alarm, Sleep ou alteracao externa de offsets. Jobs ficam bloqueados sem nova referencia confirmada.

O recuo **comandado** eh de 5,0 mm apos a deteccao, nao uma medida metrologica do ponto mecanico exato: latencia USB, curso do switch e desaceleracao produzem alguma incerteza. Para maior repetibilidade, selecionar passos menores (0,1 mm).

## Condicoes de seguranca obrigatorias

- Mantenha chaves X/Y conectadas a CNC; nao as conecte em paralelo a Raspberry Pi.
- Confirme Pn:X e Pn:Y separadamente com motores desenergizados e leitura do comando ?.
- A rotina exige **$22=0** (homing interno desativado) e **$21=0** (hard limits desligados), ambos conferidos por $$; se $21=1, tocar as mesmas chaves normalmente provoca ALARM.
- **$21=0 remove a protecao por fins de curso rigidos** da CNC. O codigo nunca altera $21 ou $22 automaticamente. Apenas opere com **parada de emergencia eletrica independente** e protecoes mecanicas adequadas, verificadas na bancada.
- A busca por USB/Linux nao fornece seguranca em tempo real. Mesmo com curso maximo de software, uma falha na comunicacao pode impedir o cancelamento do movimento.
- **Nao presuma o sinal dos motores** a partir das setas da interface: defina X negativo/positivo e Y negativo/positivo somente apos teste fisico seguro. Sentido errado pode causar colisao.
- Selecione curso maximo que nao ultrapasse a distancia livre ate o fim mecanico, inclusive se uma chave falhar. Inicie com poucos milimetros. O movimento nao executa o eixo Z.

## Uso na interface

Abra Home XY (chaves), escolha os sentidos reais no GRBL e os cursos maximos de **busca** (1..300 mm por eixo). O padrao de curso e 30 mm por seguranca; aumente somente apos medir o espaco livre. A velocidade de busca tem padrao **500 mm/min** e permite 20..600 mm/min; o passo por segmento e 0,1..10 mm. O Y tem **Y negativo preselecionado** por relato de inversao fisica, mas deve ser conferido na bancada. O recuo apos contato continua **5 mm**, nunca 300 mm. Informe G54 X/Y **da posicao final apos recuo**. O padrao e PONTO_A da calibracao; use outra coordenada se a posicao fisica dos sensores nao coincidir com esse ponto. Marque a confirmacao da parada eletrica e inicie.

A rotina roda em background, com progresso e motivos de erro exibidos na interface. O botao Abortar/Reset GRBL permanece disponivel. Apos qualquer erro, Home nao e marcado como concluido.

## Limitacoes

Nao simula o motor Z, nao envia comandos Z ao GRBL, nao injeta valores falsos de MPos. Nao torna seguro trabalhar sem protecao eletrica independente. Homing automatico pelo firmware so e possivel apos reconfiguracao que retire Z do ciclo, nao realizada aqui. Os testes CI usam sensores simulados, sem CNC real.

## Falhas intermitentes de USB e botao Abortar (2026-11)

- O Home consulta o status do GRBL em intervalos de aproximadamente 50 ms durante os segmentos de busca, em vez de sondar a cada 25 ms.
- Se uma consulta `?` voltar sem resposta, tenta mais duas vezes, com pequena pausa. `<Idle|MPos:...>` sem `Pn:` e uma resposta VALIDA com as chaves livres.
- Se as tres consultas falharem, o programa NAO continua aproximando o cabeçote de uma chave que nao pode confirmar: interrompe e registra o ultimo status recebido para diagnostico.
- O botao **Abortar** sinaliza a interrupcao imediatamente e envia `!` seguido de `Ctrl+X (0x18)` diretamente pela USB, sem aguardar a trava de leitura de status. O GRBL perde a referencia e precisa de novo Home XY.
- Os pulsos fisicos Z e o teste manual dos oito relés usam espera interrompivel; ao receber Abortar, desligam os relés no respectivo `finally`.
- **A acao de software nao garante parada instantanea mecanica** em falha USB/firmware e nao substitui parada eletrica de emergencia. Se a USB nao confirmar envio dos bytes, a interface indica que a parada fisica deve ser acionada.

Se a maquina continuar parando no primeiro passo, com motores DESENERGIZADOS verifique se `?` responde repetidamente com USB conectada, teste `Pn:X` e `Pn:Y` separadamente e procure erros de alimentacao/interferencia. Nao remova intertravamentos de curso nem trate `Pn` ausente como chave acionada.

## Quando uma chave nao libera apos recuo de 5 mm

A busca e sincronizada enquanto ambos os eixos estao pendentes. Se X falhar, Y pode ja ter se movimentado, mas o Home conjunto **nao e concluido**. Uma falha de X nao significa necessariamente defeito no motor Y.

A mensagem do Home passa a incluir o recuo solicitado, a diferenca de posicao **informada** pelo GRBL (MPos) e o Pn final. MPos e contabilizacao de passos do controlador, **nao** sensor de deslocamento real do carro; um motor travado ou desconectado pode gerar MPos aparentemente normal.

1. Desligue a alimentacao dos motores (mantenha a USB/GRBL funcionando). Envie `?` no Terminal UGS com as duas chaves soltas. Nao deve haver `Pn:X` nem `Pn:Y`.
2. Pressione somente a chave X: `Pn:X` deve aparecer. Solte: precisa desaparecer. Repita com a Y e `Pn:Y`. Se X continuar aparecendo solta, confira cabo, contato NO/NC, GND e polaridade `$5`.
3. Com a CNC parada e area livre, confira se o sentido da busca X selecionado na tela realmente aponta para a chave. O recuo usa o sentido contrario.
4. Se a posicao MPos reportar recuo ~5 mm mas X permanecer ativo, verifique fisicamente se o carro realmente afastou, o acionador da chave e a folga mecanica. Nao deduza deslocamento mecanico apenas de MPos.
5. Teste Y separadamente pelo Jog Manual em passo de **0,5 mm**, baixa velocidade e apenas com area desobstruida, chaves livres, Z bloqueado e parada eletrica independente pronta. O Jog nao e um Home valido.
6. Se o recuo informado estiver errado, confira alimentacao e habilitacao do driver, cabeamento STEP/DIR, acoplamento e configuracao dos passos por mm (`$100` para X e `$101` para Y).

Nao pule a confirmacao do sensor nem continue movimentos depois de uma falha. Antes de usar o recuo de 5 mm, confirme espaco livre, sentido de retorno, acoplamento e limite fisico em ambos os eixos.

### Teste antes da primeira execucao fisica com recuo de 5 mm

O operador confirmou que 2 mm eram insuficientes para a chave X deixar de ser pressionada, mas **5 mm ainda nao foram validados nesta maquina**. Com a CNC desenergizada, verifique a folga mecanica no sentido de afastamento de cada chave e a trajetoria completa ate 5 mm. Teste os sensores individualmente com a USB, sem energia nos motores. So execute movimentos reais apos confirmar area livre, sentidos X/Y e parada de emergencia eletrica independente. O Home por USB com $21=0 nao substitui hard limits.

## Inversao do motor Y no GRBL

O seletor Y+ / Y- do Home altera o sinal da **busca da chave**, sem alterar o sentido fisico do motor para outros comandos. Se todo comando Y (inclusive Jog) desloca para o lado fisico contrario ao esperado, a interface disponibiliza **Inverter Direcao do Motor Y no GRBL ($3)**, apenas apos confirmacao explicita e com GRBL Idle. A acao alterna o **bit 1 (valor decimal 2)** na mascara $3, preservando os bits dos outros motores, invalida Home e exige teste manual curto (0,5 mm) antes de novo Home e revisao dos quatro cantos.

**Nao aplique inversao dupla:** se ja corrigiu $3, reveja qual sinal de Y realmente aponta para a chave; nao confie cegamente na selecao inicial Y-. O indicador MPos registra a posicao contabilizada pelo GRBL e nao confirma movimento mecanico real.

## Feed 500 e curso de busca de ate 300 mm

A velocidade de **Home** foi alterada para 500 mm/min por padrao (faixa permitida 20..600 mm/min); nao altera automaticamente a velocidade de plotagem. O limite de busca configuravel foi ajustado para ate 300 mm por eixo. Como a matriz nominal tem 299 mm de deslocamento e 297 mm entre os centros extremos, **300 mm so pode ser escolhido se houver distancia mecanica real suficiente, margem de parada e protecao independente**. O formulario continua com padrao inicial de 30 mm; isso nao significa que o eixo so possa ir ate 30 mm. Com $21=0, se a chave falhar, o software nao garante evitar colisao.

Quando as duas chaves sao detectadas no mesmo passo de busca, o sistema tenta recuar ambas juntas; quando apenas uma aciona, recua apenas essa e depois continua o eixo pendente. Logs `HOME XY COMANDO CONJUNTO`, `HOME XY MPos do primeiro jog conjunto` e `Home XY CONFIG` permitem confirmar os comandos enviados e o sentido configurado.

## Recuo ajustavel sem eliminar referencia

O modal agora permite **recuo X** e **recuo Y** separados entre **2 e 10 mm**, ambos iniciando em 5 mm. Isso atende ao caso em que X solta normalmente apos 5 mm mas Y precisa de afastamento maior. O valor maior NAO deve ser aplicado sem verificar mecanicamente a folga na direcao correta. Se ambas as chaves acionam no mesmo passo, o jog de recuo usa um unico comando XY com as distancias individuais; se so uma aciona, o recuo e apenas desse eixo. O GRBL precisa relatar as duas chaves liberadas antes que G54 seja gravado.

O Home continua exigindo a confirmacao explicita da parada fisica, mas o segundo popup de confirmacao foi removido. A retirada de G-code importado em memoria tambem nao exibe confirmacao duplicada, e o aviso de importacao bem-sucedida aparece no painel. Alertas de falha e intertravamentos mecanicos permanecem.

A referencia anexada `smartv42_2.py` foi analisada como exemplo de ergonomia, nao copiada integralmente: nao sao aplicados destravamento automatico `$X`, Z habilitado sem Home nem declaracao de referencia com chave presa. Uma execucao sem erro nao demonstra que os finais de curso e a parada funcionem em todos os casos.

## Se Y encontra a chave e X para antes de encontra-la

Os campos **Busca max X/Y** iniciam em **30 mm**, mesmo que o limite superior configuravel seja de 300 mm. Esse limite nao e o recuo de 5 mm. Quando Y conclui seu recuo, X continua sozinho; ao atingir seu limite de busca antes de ver `Pn:X`, o Home interrompe com o motivo `Busca do eixo X encerrada por limite: ...` e informa que Y ja havia sido encontrado. Amplie a busca de X somente depois de medir o deslocamento seguro na direcao correta e confirmar a parada eletrica independente; o limite maximo de 300 mm nao e garantia de curso mecanico protegido.

Apos recuar uma chave, o controle agora pode consultar seu estado durante ate cerca de 2 segundos (duas leituras Idle consecutivas sem `Pn`), sem enviar deslocamento adicional. Se a chave continuar ativa, ou o GRBL entrar em alarme, a rotina nao prossegue para a origem G54. O painel exibe tambem o motivo exato da interrupcao.

## Home mais fluido: distancia de segmento ampliada

O campo **Trecho de busca (mm)** mudou do maximo antigo de **0,5 mm** para **0,1 a 10 mm**, com **5 mm por padrao**. Os motores X e Y, quando ambos estao pendentes, recebem um unico comando coordenado por segmento. O software consulta o GRBL a cada ~50 ms enquanto o comando esta em andamento e solicita cancelamento de jog (0x85) se detectar `Pn:X` ou `Pn:Y`. Apos o contato, somente o eixo encontrado recua e o outro continua em novos segmentos de busca. Se os dois sensores forem encontrados juntos, o recuo e coordenado.

**Diferencie os campos:** trecho de busca (0,1..10 mm) = distancia de cada comando; busca maxima X/Y (ate 300 mm) = distancia acumulada permitida; recuo X/Y (2..10 mm) = afastamento da chave apos o acionamento. Trechos maiores reduzem solavancos mas **nao garantem interrupcao imediata no batente**: o cancelamento depende da USB, do periodo de leitura e da desaceleracao do GRBL. O simulador testa comandos e estados, nao a distancia mecanica real ate parar. Confira o posicionamento das chaves, direcoes e o espaco livre com emergencia eletrica independente antes da operacao real.

## Busca verdadeiramente continua de X e Y (fila Jog do GRBL 1.1)

A versao anterior enviava um `$J` de 5 mm e aguardava `Idle` antes do proximo, fazendo os motores parar e arrancar sucessivamente. A rotina agora usa **fila curta de comandos de Jog** aceita pelo planejador do GRBL 1.1 (sem esperar `Idle` entre blocos), com **X e Y no mesmo `$J`** quando ambos estao pendentes. A fila representa aproximadamente ate dois trechos de busca (5 mm cada por padrao) e e supervisionada continuamente por `Pn:`; o primeiro sensor detectado dispara o comando de cancelamento `0x85`, limpa a fila, aguarda `Idle` e somente entao realiza o recuo desse eixo. O outro eixo volta a buscar em fluxo continuo. Quando ambas as chaves se encontram, os recuos tambem podem ser coordenados.

**Diagnostico de Y:** com busca no sinal Y-, o recuo correto e Y+; com busca Y+, o recuo e Y-. O log `HOME XY RECUO Y: busca no sentido ...; afastamento comandado ...` mostra o comando preciso enviado ao GRBL. Se o carrinho nao se afastar da chave, confira sentido fisico, configuracao $3, quantidade de deslocamento e `Pn:Y` com motores desenergizados antes de repetir. A programacao nao tem como verificar por encoder se o deslocamento mecanico corresponde ao `MPos` do GRBL. **Nao se envia $X automaticamente apos alarme, nao se ignora Pn ativo e nao se grava G54 se as duas chaves nao liberarem.**

Com os hard limits desligados ($21=0), a supervisao por USB pode atrasar ou falhar. Nenhuma estrategia de Jog por software equivale a um ciclo de homing nativo do firmware com protecao eletrica adequada. Uma implementacao de homing XY no firmware, sem Z cartesiano, seria a opcao mais robusta para uso autonomo apos validacao da bancada.

## Segunda chave Y para medir a borda oposta C e D

Duas chaves na mesma entrada Y do GRBL resultam no mesmo `Pn:Y`; a leitura isolada nao informa se a chave acionada e a de A/B ou a de C/D. O Home XY continua utilizando a chave na extremidade A/B. A calibracao guiada da extremidade oposta exige que o usuario posicione o cabeçote perto de C (ate 35 mm do Y previsto e 8 mm do X de B), com `Pn:Y` ainda livre, e confirme as protecoes externas. A busca curta usa `$J` no eixo Y (maximo 15 mm por padrao, limite de 20 mm, F120 mm/min, segmentos de 1 mm), cancela ao detectar `Pn:Y`, aguarda Idle, recua 4 mm para a direcao de B e confirma liberacao da chave. **Somente apos essa sequencia o Y seguro e aplicado simultaneamente a C e D.**

O centro fisico extremo nao coincide necessariamente com o ponto em que a chave comuta. Por isso a coordenada gravada e a posicao afastada do sensor, e nao o contato bruto. Antes de aceitar, a geometria deve continuar compativel com 100 pontos e passo nominal de 3 mm; a validacao nao e desativada. C e D compartilham o mesmo Y medido; se a guia for significativamente obliqua, meça os cantos individualmente com instrumentacao apropriada. A calibracao nova e persistida no arquivo de calibracao; qualquer edicao manual dos cantos invalida seu status. O teste A-B-C-D-A e bloqueado ate que essa medicao seja concluida.

A rotina **nao desbloqueia ALARM**, **nao ignora Pn:Y** e **nao altera automaticamente $21**. A segunda chave fisica nao pode ser tratada como uma permissao de ultrapassar o fim de curso. Se a maquina parou em B->C devido a ALARM, investigue e recupere manualmente o GRBL, refaca Home XY e so entao use a nova calibracao. Use uma parada eletrica de emergencia independente. A supervisao USB pode atrasar e nao substitui protecao no firmware.
