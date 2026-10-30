---
title: Erro na maquininha e transação negada (códigos ADQ e o que fazer)
source_url: https://sitehml.getnet.com.br/wp-content/uploads/2024/01/Manul_GetClassica.pdf
lang_original: pt
keywords: [transação negada, transação recusada, declined, decline error, erro na maquininha, erro ADQ, ADQ 4-83, ADQ 4-91, ADQ 1-21, IDL 1-04, S-2074, venda teste, estorno, chargeback, card machine error, transaction declined]
fetched_at: 2026-10-30
---

# Erro de transação ou transação negada

## O que o material oficial cobre
O manual da Get Clássica traz uma tabela de "Solução de erros": se a maquininha mostrar "Erro ADQ" (ou IDL / S-), você mesmo pode tentar resolver. Os materiais oficiais consultados (manuais e Central de ajuda) não trazem uma lista pública dos motivos pelos quais o banco emissor recusa uma compra (por exemplo limite ou saldo): essa decisão é do emissor do cartão, e a orientação é falar com a Central Getnet.

## Principais códigos do manual
- Erros de integração com as bandeiras ou de aprovação (ADQ 4-83, ADQ 4-91, ADQ 1-50, ADQ 4-30, ADQ 4-23, ADQ 4-27, IDL 1-04, IDL 1-36, S-2074, ADQ 1-02): Função > 38 > Verde > Inicialização > Verde > fazer venda teste.
- ADQ 1-21 (erro no processamento da transação): o manual indica apagar a memória com a Função 60 (exige senha técnica, então é melhor acionar a Central) e depois inicializar e fazer venda teste.
- ADQ 3-30 (erro de criptografia, após período sem uso) e ADQ 1-33 (erro de leitura do cartão).
- ADQ 1-07 (maquininha não inicializada) e ADQ 1-06 (não configurada): inicialização guiada pelo manual.
- ADQ 1-19, 1-18, 1-84, 1-85, 1-87: problemas de conexão ou chip (veja `troubleshooting-conexao.md`).
- Erro na leitora NFC (ao aproximar o cartão): teste de teclado e de cartão e depois inicialização.

A tecla de Função pode variar conforme o modelo. A "venda teste" é uma transação real de R$ 0,01; se usar o cartão de um cliente, o valor pode ser estornado depois.

## Estorno e contestação
Estorno pela maquininha: Get Clássica, só vendas do mesmo dia; Get Smart, até 24h; depois, pelo Aplicativo Getnet Brasil ou Portal Minha Conta. Chargeback é o cancelamento de uma venda, com débito ao estabelecimento, após contestação do cliente junto ao banco emissor (devolução ou não recebimento de mercadoria, transação não reconhecida, duplicidade, estorno não lançado ou pagamento por meio alternativo).

## Se o erro persistir
Anote o código exibido na tela e ligue para a Central: 4002-4000 ou 4003-4000 (capitais), 0800-648-8000 (interior), WhatsApp 11 97694-6815. Veja `atendimento-canais.md`.

## English summary
Getnet's Get Clássica manual lists terminal error codes (ADQ, IDL, S-) with fixes. Most integration and approval errors (ADQ 4-83, 4-91, 4-30, 4-23, 4-27, 1-50, IDL 1-04/1-36) are cleared with the Function key > 38 > Initialization > test sale of R$ 0.01. Connection errors (ADQ 1-18, 1-19, 1-84, 1-85, 1-87) point to signal or chip problems. The official sources do not publish a list of issuer decline reasons; the issuer bank decides. Chargebacks are reversals after a cardholder dispute. If the error persists, note the code and call Getnet at 4002-4000.
