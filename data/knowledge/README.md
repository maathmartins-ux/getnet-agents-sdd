# Base de conhecimento — procedência

**Coleta:** 2026-10-30. **Arquivos:** `sources.json` (páginas lidas) e `corpus.jsonl` (documentos curados, 1 JSON por linha).

## Método

1. **Busca:** WebFetch nas páginas oficiais (`getnet.net/en`, `getnet.net`, `site.getnet.com.br` e subpáginas da Central de ajuda). Os manuais em PDF foram baixados e o texto foi extraído com `pypdf`.
2. **Texto:** extração do conteúdo relevante de cada página.
3. **Curadoria manual:** 15 tópicos, cada um com um documento `pt` e um `en` sob a mesma `url`. Frases são declarativas e curtas, e todo número, prazo ou preço foi lido na fonte.

`getnet.net/en` é o site institucional global e não descreve os produtos brasileiros (maquininhas, Pix, crediário, Link de Pagamento). Por isso as versões `en` desses tópicos são **traduções fiéis** do texto `pt`, com a mesma `url` da fonte em português.

## Esquema de `corpus.jsonl`

`{"id", "title", "url", "lang": "pt"|"en", "section", "tags": [...], "text"}`. O `id` segue o formato `<tópico>-<lang>`.

## Tópico → fonte

| Tópico | URL principal | Páginas que também alimentaram o texto |
|---|---|---|
| get-classica-vs-get-smart | /todas-as-maquininhas/ | manuais Get Clássica e Get Smart, /duvidas/get-smart/ |
| get-smart | /duvidas/get-smart/ | — |
| pix-conta-bancaria | /duvidas/pix/ | — |
| pix-na-maquininha | /pix/ | manuais Get Clássica e Get Smart |
| maquininha-sem-internet | manual Get Clássica (PDF) | — |
| get-smart-conexao | manual Get Smart (PDF) | — |
| erro-transacao-recusada | manual Get Clássica (PDF), tabela "Solução de erros" | — |
| antecipacao-recebiveis | /duvidas/antecipacao-de-vendas/ | — |
| antecipacao-na-maquininha | manual Get Smart (PDF) | — |
| crediario | /crediario/ | — |
| link-de-pagamento-whatsapp | /link-de-pagamento/ | — |
| prazos-recebimento | /duvidas/ (raiz da Central de ajuda) | /duvidas/antecipacao-de-vendas/, /pix/, home (oferta Get Conta) |
| atendimento-humano | /get-ajuda/ | /duvidas/, manuais |
| sobre-getnet | getnet.net/en | — |
| get-tap-e-maquininhas | home site.getnet.com.br | — |

## Limitações e o que não foi encontrado

- **Transação recusada:** nenhuma página oficial explica a recusa pelo emissor do cartão (saldo, limite, senha). O corpus cobre só os **códigos de erro da maquininha** (tabela ADQ/IDL do manual da Get Clássica) e orienta procurar a Central se o erro continuar.
- **Senhas de técnico e de operador** aparecem no manual público, mas foram **omitidas** de propósito: o assistente não deve repassá-las.
- **Antecipação:** a página oficial não traz taxa nem percentual. O texto informa apenas que a taxa faz parte do contrato.
- **Get Clássica vs Get Smart:** os preços variam entre páginas. A home anuncia a Get Clássica por "12x R$ 7,90 ou 94,80 à vista", e /todas-as-maquininhas/ lista a mensalidade de uso. O corpus usa só a mensalidade de /todas-as-maquininhas/ e recomenda confirmar as condições vigentes.
- **Pix e conta bancária:** o FAQ cita "domicílio bancário Santander e uma chave Pix ativa" como critério de elegibilidade do botão Pix nos portais. Não diz se outros bancos atendem em todos os casos, só que a disponibilidade depende dos PSPs integrados.
- **Get Mini:** o manual foi baixado mas não ganhou tópico próprio; aparece só como produto citado.
- **Atualização:** o conteúdo reflete o site em 2026-10-30. Para atualizar, refaça a coleta das URLs de `sources.json` e revise os textos.
