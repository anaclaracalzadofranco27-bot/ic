# Registro de correções A–H — Smart Matrix Studio v42

Revisão aplicada aos dez fragmentos do programa, mantendo o executável gerado pelo GitHub Actions.

## A. Segurança
- USB GRBL desconectado: conexão permanece em erro, sem simulação implícita.
- GPIO físico só é preparado após inicialização GRBL válida.
- A confirmação de movimento requer Idle; Check, Hold, Sleep e Alarm não equivalem a movimento.
- Envio de Z cartesiano ao GRBL é rejeitado na camada central, inclusive a partir do terminal.
- Relés só podem atuar com estado seguro validado. Pausas e abortamentos são reavaliados durante a sequência de pulsos.
- Limites físicos XY deixam de ter uma margem permissiva extra de 30 mm.

## B. Operações concorrentes
- _job_lock, _job_em_execucao e _operacao_manual coordenam jobs e ações manuais.
- Testes sequenciais e perímetro de calibração não ficam rodando em threads sem reserva.
- Hold e Abort continuam disponíveis durante jobs e testes; o timeout de XY considera a pausa voluntária.

## C. Coordenadas
- Quatro cantos físicos definem os limites absolutos.
- Janela de plotagem define a pequena área XY onde o Z pode atuar.
- Conversão índice↔milímetro compartilha a regra de origem/orientação X.
- Calibração persiste os sinais de X e Y.
- Z não participa de homing, jog nem zeramento GRBL cartesiano.

## D. Importação e imagem
- Configurações de calibração/pulsos preservam o trabalho importado.
- Uma nova imagem, um novo padrão ou um reset físico comprovadamente concluído altera a origem do trabalho.
- A profundidade de luminância é recalculada a partir da imagem original.
- Checkbox “Z do arquivo”: desmarcado = parâmetros atuais do cabeçote; marcado = overrides validados presentes no G-code.

## E. Execução unificada
- O executor da matriz compila comandos tipados e chama o mesmo executor híbrido dos arquivos importados.
- Pré-validação de todas as coordenadas ocorre antes de enviar movimentos.
- Z usa GPIO na Raspberry Pi; M42 nunca é enviado ao GRBL.

## F. Observabilidade
- Tratamento de falhas HTTP e logs explícitos.
- Interface mostra erros de conexão/jog/reset, aceita OFF=0 e pausa=0.
- Erro final e conclusão do job são publicados no estado compartilhado.

## G. Código residual
- Constantes órfãs e funções sem referências internas foram retiradas.
- G4 P0, M3 e M5 deixam de ser emitidos pelo compilador novo, mas continuam aceitos pelo importador para compatibilidade com arquivos legados.
- O comando D de reset físico já representa um ciclo completo e deixou de ser seguido por etapas duplicadas.

## H. Testes
- Testes originais do parser híbrido e regressões adicionais de falha de USB, estado Check, exclusão mútua, coordenadas, dados, parâmetros e reset.
- Reconstrução, compilação, testes e verificação do gerado são executados no GitHub Actions.

## Limitações de validação
O fluxo automático usa simuladores/mocks: não verifica a eletrônica, a cinemática nem o firmware da bancada. O firmware de reset físico deve retornar DONE ou CONCLUIDO para ser considerado concluído. Homing, GPIO, fim de curso, parada física de emergência, offsets e pulsos devem ser testados separadamente na Raspberry Pi com segurança elétrica.
