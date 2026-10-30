# Roteiro do vídeo — Getnet Multi-Agent Support (≈ 8 min)

Formato: tela compartilhada (terminal + editor + navegador). Fala em primeira pessoa, ritmo calmo. Entre colchetes, o que aparece na tela.

---

## 0:00 – 0:40 · Abertura

[Tela: README no GitHub, diagrama mermaid da arquitetura]

> Oi, eu sou o Matheus. Neste vídeo eu mostro a solução para o desafio AI Hardcore Engineer: um sistema multiagente de atendimento para a Getnet, exposto como API, empacotado em Docker, com RAG, ferramentas, guardrails, handoff humano, observabilidade e avaliação automática. Vou mostrar o raciocínio, as decisões de arquitetura e o sistema rodando nos dez cenários do case.

## 0:40 – 1:20 · O problema

[Tela: enunciado do case, lista dos 10 cenários]

> Um lojista pergunta coisas muito diferentes no mesmo canal: diferença entre maquininhas, quando cai o dinheiro, maquininha sem internet, cotação do euro. Cada pergunta pede uma competência diferente: conhecimento de produto, dados da conta do cliente, ou informação geral da web. E existe um risco central: um assistente que inventa resposta sobre dinheiro do cliente é pior do que nenhum assistente. Então a regra número um do sistema é: sem fonte, não responde — encaminha para um humano.

## 1:20 – 2:30 · Spec-Driven Development e a linha do tempo

[Tela: `.specify/memory/constitution.md`, depois `specs/001-multi-agent-support/spec.md`, depois `git log --reverse`]

> Eu trabalhei com Spec-Driven Development. Antes de qualquer linha de código, escrevi a constituição do projeto, com cinco artigos: spec primeiro; confiabilidade acima de esperteza; observabilidade; segurança e guardrails; simplicidade.
>
> Depois, a spec: histórias de usuário, requisitos funcionais e não funcionais com IDs, e os dez cenários do case como critérios de aceite. O plan traz as decisões de arquitetura em ADRs curtos e o pipeline de RAG. O tasks liga cada tarefa a um requisito e a um teste.
>
> O git log é a prova do processo: o primeiro commit é a spec; depois vêm implementação, testes e documentação, nessa ordem. Tudo em menos de uma hora. A matriz de rastreabilidade no README mostra requisito, módulo e teste lado a lado.

## 2:30 – 3:50 · Arquitetura

[Tela: diagrama mermaid; depois `orchestrator.py` e `schemas.py`]

> São quatro agentes, coordenados por um orquestrador por chamadas diretas de função, com contratos tipados em Pydantic.
>
> O **Router** é a porta de entrada: detecta idioma, classifica a intenção em uma de sete categorias e monta o plano — a sequência de agentes — com um grau de confiança.
>
> O **Knowledge** responde sobre produtos Getnet com RAG sobre o conteúdo do site oficial, sempre com citação de URL, e usa ferramentas web para perguntas gerais: previsão do tempo, câmbio, busca.
>
> O **Support** atende o cliente com dados dele, num loop de tool use sobre as ferramentas de CRM: perfil, previsão de liquidação, transações recentes, status do terminal e abertura de chamado.
>
> O quarto, **Escalation**, abre protocolo e faz o handoff para um humano quando o cliente pede, quando a confiança é baixa ou quando não há fonte.
>
> O sistema roda em dois modos: `anthropic`, com Claude Haiku no roteamento e Claude Sonnet nos agentes, e `offline`, determinístico, sem chave e sem rede. O modo offline é o que roda nos testes e no CI — e também é o fallback se a API cair.

## 3:50 – 4:30 · Pipeline RAG

[Tela: `rag/ingest.py`, `rag/store.py`, `data/knowledge/`]

> Ingestão: as páginas do site da Getnet são baixadas, limpas e quebradas em trechos com título, URL e idioma. Armazenamento: um índice BM25 com pesos por campo — título pesa mais que corpo —, sem dependência pesada. Recuperação: top-k trechos, filtrados pelo idioma da pergunta. Geração: o agente responde só com o que está nos trechos e cita a fonte; uma verificação de grounding na saída confere se a resposta está ancorada nos trechos.

## 4:30 – 6:00 · Demo dos 10 cenários

[Tela: terminal com `docker compose up -d --build`, depois os curls]

> Sobe com um comando: `docker compose up -d --build`. O health check responde. Agora os dez cenários do case, todos com o usuário cliente1988.

[Rodar cada curl e destacar `route`, `intent`, `sources`, `tool_calls`, `language`]

> Get Clássica versus Get Smart: rota router → knowledge, com fontes do site.
> Previsão do tempo em Porto Alegre: knowledge chamando a ferramenta de clima.
> Dinheiro das vendas de ontem: router → support, ferramenta de previsão de liquidação, com a data e o valor.
> Pix sem conta bancária: knowledge com fonte.
> Maquininha sem internet: support consulta o status do terminal e knowledge traz o passo a passo — dois agentes na mesma resposta.
> Antecipação de recebíveis: knowledge com fonte.
> Cotação do euro: ferramenta de câmbio.
> Erro de transação recusada: support olha as transações recusadas do cliente e knowledge explica o que fazer.
> Crediário e Link de Pagamento pelo WhatsApp: knowledge.
>
> Repare que a pergunta em inglês volta em inglês, e em português volta em português.

## 6:00 – 6:50 · Guardrails e handoff

[Tela: curls de prompt injection, fora de escopo, CPF, pedido de atendente]

> Prompt injection — "ignore as instruções anteriores" — é bloqueado antes de chegar a qualquer modelo. Pedido fora de escopo, como um poema sobre gatos, é recusado com educação. Um CPF na mensagem é mascarado antes de ser processado e nunca aparece na resposta nem no log. E "quero falar com um atendente" vai direto para o agente de escalonamento, que devolve um número de protocolo.

## 6:50 – 7:30 · Observabilidade e avaliação

[Tela: log JSON com trace_id e spans; `curl /metrics`; saída do `evals/run_evals.py`]

> Cada requisição tem um trace_id e um span por agente no log estruturado em JSON, e o endpoint `/metrics` expõe contadores e latências no formato Prometheus — pronto para um dashboard no Grafana com alerta de taxa de handoff, erro de ferramenta e latência p95.
>
> A avaliação tem um golden set com os dez cenários, as versões em português, paráfrases e casos adversariais. O runner mede acurácia de intenção, rota, idioma, cobertura de fontes, recall de ferramentas, handoff e precisão dos guardrails. Se o roteamento ficar abaixo de 90%, o CI falha.

## 7:30 – 8:00 · Limitações e próximos passos

[Tela: seção Limitações do README]

> Limitações honestas: o CRM é simulado com dados fictícios, a base de conhecimento é um snapshot do site e o modo offline gera respostas extrativas, menos fluidas que as do Claude. Próximos passos: memória de conversa por usuário, reindexação agendada do site, LLM-as-judge rodando toda noite no modo anthropic e integração real do handoff com a fila de atendimento. Obrigado.
