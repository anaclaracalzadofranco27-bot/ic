# Isolamento dos estados do SmartV42: matriz, G-code e execução

## O problema corrigido

Anteriormente, uma rotina de atualização da matriz podia apagar o G-code importado e o motor de trabalho consultava variáveis globais para velocidade, pulsos, tempos de acionamento e pausa. Isso permitia situações em que o programa exibido na interface não correspondia ao programa executado ou aos parâmetros selecionados.

## Estado do projeto (editável)

O SmartV42 agora mantém separadamente:

- **Matriz 100 × 100** e seu G-code gerado (centros a 3 mm, extensão útil 297 mm);
- **G-code importado**, incluindo nome do arquivo, linhas e programa analisado;
- **Fonte ativa da próxima plotagem**: `matriz` ou `gcode`;
- **Preferência de parâmetros Z do arquivo**: marcada usa ON/OFF/PULSOS embutidos no arquivo, quando presentes; desmarcada usa a configuração atual do painel.

A importação de outro arquivo só substitui o anterior após análise bem-sucedida. Alterar profundidade, parâmetros de pulso, calibrar coordenadas ou atualizar a matriz **não elimina o G-code importado**.

Ao gerar explicitamente um novo padrão ou carregar imagem, a matriz passa a ser a fonte ativa, mas o arquivo externo continua armazenado; você pode selecioná-lo novamente pelo botão **G-code importado**.

**Remover G-code** é a única ação normal que o apaga e exige confirmação. O arquivo permanece somente **na memória da aplicação**, não em armazenamento persistente: reiniciar o servidor exige importá-lo novamente.

## Snapshot de job (imutável durante a execução)

Ao clicar **Plotar Matriz**, a interface informa fonte e revisão do estado. O servidor recusa iniciar se a seleção/revisão mudou desde a última leitura.

Quando o job é aceito, ele:
1. Copia profundamente a lista de instruções;
2. Registra fonte e nome do arquivo executado;
3. Captura FEED padrão, pulsos de Z1..Z8, ON/OFF/pausa, auto-reset e assinatura dos quatro cantos, janela de plotagem e mapa de GPIO;
4. Valida todos os movimentos e acionamentos Z antes de aceitar o trabalho;
5. Utiliza o snapshot para cada movimento/acionamento, em vez de consultar os parâmetros globais editáveis.

**Matriz gerada internamente:** usa os parâmetros atuais do painel, congelados no início do job. O conteúdo M42 gerado para visualização/exportação não prevalece sobre os ajustes do painel.

**G-code importado:** se marcada **Z do arquivo**, somente os valores ON/OFF/PULSOS explicitamente definidos no arquivo prevalecem. Campos ausentes usam o snapshot do painel. Se desmarcada, todo Z usa o snapshot do painel. Valores de feed explicitamente presentes em comandos G1 continuam sendo usados para esses movimentos.

Alterar a geometria, o recorte ou o mapeamento GPIO durante a execução faz o job falhar sem prosseguir. Quando o G-code inclui um GPIO explícito para Z, ele também é validado novamente contra o mapa atual antes de qualquer movimento.

## Operação e segurança

- A opção **Fonte da próxima plotagem** mostra o programa selecionado e o nome do arquivo guardado.
- A interface indica se Z utilizará os parâmetros do arquivo ou do painel.
- O progresso mostra a identidade do programa em execução.
- Um novo job ainda requer **Home XY automático por chaves**, com recuo de **2 mm** em X e Y, para a CNC real.
- A habilitação dos relés físicos Z continua sujeita às proteções do modo físico completo.

O snapshot é uma garantia de consistência lógica da aplicação, não substitui o desligamento elétrico de emergência nem validação de movimento na bancada. Testes CI utilizam GRBL e GPIO simulados.
