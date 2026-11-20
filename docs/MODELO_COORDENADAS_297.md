# Coordenadas unificadas da Smart Matrix v42

## Dimensões verificadas pelo modelo

- Matriz tátil: **100 linhas × 100 colunas = 10.000 pontos**.
- Índices de pontos: **0..99**, tanto em linha quanto em coluna.
- Distância nominal entre os centros: **3,0 mm**.
- Distância nominal entre os centros extremos de cada eixo: **99 × 3 = 297 mm**.
- Escala nominal de movimento relatada: **0..299 mm**. **299 não é índice de pino nem cria 300 posições**; é um valor nominal do mecanismo e não comprova curso mecanicamente seguro além dos quatro cantos medidos.
- A função de movimentação absoluta mantém como área conservadora os quatro cantos medidos da malha. Não autoriza automaticamente alcançar 299 mm fora dos centros extremos. Limites mecânicos e de software devem ser validados em bancada separadamente.

## Mapeamento único da malha para G54

No padrão inicial, A=(0,0) corresponde à linha 99, coluna 0; B=(297,0) à linha 99, coluna 99; C=(297,297) à linha 0, coluna 99; D=(0,297) à linha 0, coluna 0. Isso segue a bancada com `$3=1`: X+ vai de A para B e Y+ vai de A para D. Vista de frente, A fica embaixo à direita; por isso `ORIGEM_X_NA_DIREITA=True` espelha as colunas da imagem (coluna 0 da imagem fica do lado de B/C). As chaves X/Y do Home ficam no canto A e a chave 2 de Y na borda C/D.

Para uma malha quadrada perfeitamente alinhada, as coordenadas são:

- X = 3 × coluna
- Y = 297 − 3 × linha

Calibrações salvas no modelo antigo (C/D em Y negativo) são ignoradas na inicialização.

Para uma matriz calibrada com quatro cantos levemente não ortogonais, a mesma rotina de interpolação bilinear converte índices da imagem para coordenadas G54 e faz a transformação inversa para telemetria, mantendo correspondência nos quatro cantos.

A opção de origem visual na direita espelha somente a **coluna lógica da imagem** (col 99 e col 0 trocam de lado); não altera sinal de motor nem sobrescreve a referência G54. A inversão visual de JOG tem efeito somente nos botões de operação manual.

## Homing

O Home XY automático **continua lendo as chaves X/Y ligadas à CNC via `Pn:`** e recua **2,0 mm** de cada chave, sem enviar `$H` e sem usar Z. A posição final depois dos recuos recebe as coordenadas G54 fornecidas pelo operador. **Não presuma que ela é A=(0,0)**: isso depende da posição física das chaves em relação ao primeiro pino.

Calibrar A/B/C/D e mudar orientação invalidam a referência Home XY anteriormente confirmada. O programa não aceita mais usar `G92` para reposicionar a malha pelas rotas manuais antigas. Comandos externos de redefinição de origem também invalidam Home. Faça novamente Home XY antes de um trabalho real. A entrada Z fica desarmada até a habilitação explícita do modo físico completo.

## Janela de plotagem

O padrão é **`USAR_JANELA_PLOTAGEM=False`**, ou seja, utiliza toda a matriz de 297 × 297 mm. Se a janela opcional for ativada, ela seleciona os centros físicos dentro do retângulo; **não estica nem comprime a imagem**.

O retângulo antigo X=246..270 / Y=-50..-29 tinha tamanho 24 × 21 mm. Ele foi removido como mapeamento padrão, porque concentrava pontos que deveriam estar distantes 3 mm.

## G-code externo e precisão

Antes de acionar um relé Z, um G-code importado deve posicionar X/Y em um centro de ponto físico calibrado com erro de no máximo **0,35 mm**. Um movimento XY sem acionamento Z pode atravessar posições intermediárias, desde que estejam dentro da área de quatro cantos.

A exatidão final depende da calibração mecânica: a interpolação assume apenas que os quatro cantos descrevem suficientemente bem a geometria. Flexões, folgas, falta de passos e deformações internas não podem ser detectadas pelos cantos isoladamente.

## Migração da calibração salva

No início do aplicativo, `calibracao_matriz.json` só é utilizado se os quatro cantos medidos forem compatíveis com aproximadamente 297 mm em cada aresta (tolerância de validação de 12 mm, **não tolerância aceita de precisão de impressão**). Perfis antigos com extensão de 247,5 mm ou 24 × 21 mm são recusados e o programa mantém o padrão 297 mm. Verifique as mensagens de log e refaça a calibração dos quatro cantos.

## Ensaio seguro de bancada

1. Com a alimentação dos solenoides desligada, confirme os sensores de X e Y por `Pn:X` e `Pn:Y`.
2. Confirme parada elétrica independente, curso livre e configuração GRBL; mantenha Z desarmado em **Teste físico XY**.
3. Execute Home XY pela interface, confirmando sentidos, cursos e **recuos de 2 mm**.
4. Verifique no G54 a posição real de A, D, B, C usando movimentos limitados antes de plotar.
5. Compare as posições previstas: coluna 0 e 1 devem distar aproximadamente 3 mm, assim como linhas 0 e 1. Não faça testes de perímetro com a cabeça próxima às bordas sem confirmar folgas.
6. Somente após isso habilite Z físico e execute primeiro um padrão pequeno sob observação.

**Os testes automatizados não validam o hardware; o Home pelo USB não é intertravamento de segurança e `$21=0` deixa os limites rígidos da CNC desativados.**
