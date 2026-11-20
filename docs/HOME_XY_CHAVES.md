# Home XY automatico por chaves da CNC — SmartV42

## Funcionamento

O novo Home XY da interface usa os pinos X/Y da CNC pela USB, sem enviar $H nem mover Z.

1. Confirma o GRBL em Idle e le as chaves pelo status Pn:X e Pn:Y.
2. Se a chave ja estiver acionada, libera primeiro com movimento limitado de 2,0 mm.
3. Busca X e depois Y em passos de ate 0,5 mm via jog incremental $J=G91 G21, supervisionando Pn: a cada passo.
4. Ao detectar uma chave, cancela o jog em andamento (byte 0x85), confirma contato estavel e **recua 2,0 mm** no sentido oposto.
5. Confirma a liberacao de X e Y. So depois grava os comandos G21, G90, G54, G92.1 e G10 L20 P1 X... Y..., verificando WPos.
6. Invalida o Home apos reconexao USB, soft reset, Alarm, Sleep ou alteracao externa de offsets. Jobs ficam bloqueados sem nova referencia confirmada.

O recuo **comandado** eh de 2,0 mm apos a deteccao, nao uma medida metrologica do ponto mecanico exato: latencia USB, curso do switch e desaceleracao produzem alguma incerteza. Para maior repetibilidade, selecionar passos menores (0,1 mm).

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

## Quando X nao libera apos recuo de 2 mm e Y nao se movimenta

A busca e sequencial: primeiro X, depois Y. Se X nao liberar, Y **nao inicia**. Nao e prova de defeito no motor Y.

A mensagem do Home passa a incluir o recuo solicitado, a diferenca de posicao **informada** pelo GRBL (MPos) e o Pn final. MPos e contabilizacao de passos do controlador, **nao** sensor de deslocamento real do carro; um motor travado ou desconectado pode gerar MPos aparentemente normal.

1. Desligue a alimentacao dos motores (mantenha a USB/GRBL funcionando). Envie `?` no Terminal UGS com as duas chaves soltas. Nao deve haver `Pn:X` nem `Pn:Y`.
2. Pressione somente a chave X: `Pn:X` deve aparecer. Solte: precisa desaparecer. Repita com a Y e `Pn:Y`. Se X continuar aparecendo solta, confira cabo, contato NO/NC, GND e polaridade `$5`.
3. Com a CNC parada e area livre, confira se o sentido da busca X selecionado na tela realmente aponta para a chave. O recuo usa o sentido contrario.
4. Se a posicao MPos reportar recuo ~2 mm mas X permanecer ativo, verifique fisicamente se o carro realmente afastou, o acionador da chave e a folga mecanica. Nao deduza deslocamento mecanico apenas de MPos.
5. Teste Y separadamente pelo Jog Manual em passo de **0,5 mm**, baixa velocidade e apenas com area desobstruida, chaves livres, Z bloqueado e parada eletrica independente pronta. O Jog nao e um Home valido.
6. Se o recuo informado estiver errado, confira alimentacao e habilitacao do driver, cabeamento STEP/DIR, acoplamento e configuracao dos passos por mm (`$100` para X e `$101` para Y).

Nao pule a confirmacao do sensor para liberar automaticamente o Y e nao aumente o recuo sem confirmar o espaco mecanico disponivel.
