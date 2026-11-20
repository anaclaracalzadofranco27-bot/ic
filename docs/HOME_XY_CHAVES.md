# Home XY automatico por chaves da CNC — SmartV42

## Funcionamento

O novo Home XY da interface usa os pinos X/Y da CNC pela USB, sem enviar $H nem mover Z.

1. Confirma o GRBL em Idle e le as chaves pelo status Pn:X e Pn:Y.
2. Se alguma chave estiver inicialmente acionada, libera essa chave com recuo limitado a 5,0 mm antes de iniciar a busca.
3. Busca X e Y **juntos** usando um comando de jog XY de ate 0,5 mm por eixo em cada passo, supervisionando Pn:.
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

Abra Home XY (chaves), escolha as direcoes reais de busca X e Y, cursos maximos, velocidade 20..150 mm/min e passo 0,1..0,5 mm. Informe G54 X/Y **da posicao final apos recuo**. O padrao e PONTO_A da calibracao; use outra coordenada se a posicao fisica dos sensores nao coincidir com esse ponto. Marque a confirmacao da parada eletrica e inicie.

A rotina roda em background, com progresso e motivos de erro exibidos na interface. O botao Abortar/Reset GRBL permanece disponivel. Apos qualquer erro, Home nao e marcado como concluido.

## Limitacoes

Nao simula o motor Z, nao envia comandos Z ao GRBL, nao injeta valores falsos de MPos. Nao torna seguro trabalhar sem protecao eletrica independente. Homing automatico pelo firmware so e possivel apos reconfiguracao que retire Z do ciclo, nao realizada aqui. Os testes CI usam sensores simulados, sem CNC real.

## Falhas intermitentes de USB e botao Abortar (2026-11)

- O Home consulta o status do GRBL em intervalos de aproximadamente 150 ms durante os pequenos movimentos, em vez de sondar a cada 25 ms.
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
