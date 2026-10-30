# Roteiro do vídeo — Getnet Multi-Agent Support (5–7 min)

Formato: tela compartilhada (terminal + editor + navegador), fala em primeira pessoa e ritmo calmo. Entre colchetes, o que aparece na tela. Números do eval e do `git log` **são lidos da tela na hora da gravação** — não decore nenhum.

Antes de gravar: `docker compose up -d --build`, `python -m evals.run_evals` e `git log --reverse --format='%ad %s' --date=iso` já rodados uma vez, para não esperar em frente à câmera.

---

## 0:00 – 0:45 · Raciocínio: o problema e a regra número um

[Tela: enunciado do case; lista dos 10 cenários]

> Oi, eu sou o Matheus. O desafio pede um sistema multiagente que interprete mensagens de lojistas e responda de forma útil. Olhando os dez cenários, vejo três tipos de pergunta: sobre produto Getnet, sobre a conta do próprio cliente e perguntas gerais, como tempo e câmbio. Cada uma pede uma fonte de verdade diferente. Um prompt único misturaria tudo e inventaria resposta — e um assistente que inventa quando o assunto é o dinheiro do cliente é pior do que nenhum. Então a regra número um é: sem fonte, não responde; encaminha para um humano.

## 0:45 – 2:00 · Arquitetura

[Tela: diagrama mermaid do README; depois `orchestrator.py`]

> São quatro agentes e um orquestrador que chama cada um por função, com mensagens tipadas em Pydantic. O **Router** detecta idioma, classifica a intenção em sete categorias e monta o plano, com uma confiança. O **Knowledge** faz RAG sobre o site oficial — BM25 com peso por campo, sempre citando a URL — e usa ferramentas web para tempo, câmbio e busca. O **Support** consulta dados do cliente por ferramentas de CRM: previsão de liquidação, transações, status do terminal. O quarto, **Escalation**, abre protocolo e passa para um humano quando o cliente pede, quando a confiança é baixa ou quando não há fonte.
>
> Antes do Router, os guardrails: prompt injection e fora de escopo são barrados, e CPF ou cartão são mascarados antes de qualquer agente ou log. O sistema roda em dois modos: `anthropic`, com Claude Haiku no roteamento e Sonnet nos agentes, e `offline`, determinístico, sem chave nem rede. É o offline que roda nos testes e no CI, e é o fallback se a API cair.

## 2:00 – 4:00 · Demo dos cenários

[Tela: terminal; `curl` em `localhost:8000/chat`; destacar `route`, `intent`, `sources`, `tool_calls`, `language`]

> Subiu com um comando, o health responde. Agora os cenários do case, sempre com o cliente1988.
>
> **Get Clássica contra Get Smart:** rota router → knowledge, com as fontes do site. **Dinheiro das vendas de ontem:** router → support, que chama a previsão de liquidação e responde com valor e data em dias úteis. **Maquininha sem internet:** dois agentes na mesma resposta — o Support vê que o terminal está offline e o Knowledge traz o passo a passo. **Transação recusada:** o Support lista as recusas do cliente com o código do emissor, e o Knowledge explica o que fazer. **Tempo em Porto Alegre e câmbio do euro:** o Knowledge chama as ferramentas web.
>
> Pergunta em inglês volta em inglês; em português, em português.

[Tela: três curls rápidos — prompt injection, mensagem com CPF, "quero falar com um atendente"]

> Prompt injection é bloqueado antes de chegar a um modelo. O CPF é mascarado e nunca aparece na resposta. E pedir um atendente vai direto para o agente de escalonamento, que devolve um protocolo.

## 4:00 – 5:00 · SDD e o git log

[Tela: `.specify/memory/constitution.md`; `specs/001-multi-agent-support/spec.md`; saída do `git log --reverse`]

> Eu trabalhei com Spec-Driven Development. O primeiro commit é a spec: constituição com cinco artigos, requisitos com IDs e os dez cenários como critérios de aceite. Só depois vêm implementação, testes e documentação — o git log mostra essa ordem com os horários reais, e a matriz de rastreabilidade no README liga cada requisito ao módulo e ao teste. [Ler da tela: horário do primeiro e do último commit.]

## 5:00 – 6:15 · Avaliação e observabilidade

[Tela: saída de `python -m evals.run_evals`; depois `curl /metrics` e uma linha de log JSON]

> Além dos testes, tem um eval com três conjuntos: os dez cenários oficiais, vinte e duas variações em português e inglês — escritas antes de ajustar o roteador; na primeira rodada elas acharam duas falhas reais, que corrigi na causa, então hoje servem como conjunto de regressão — e seis casos de guardrail, incluindo pedido de dados de outro cliente e vazamento de CPF. O runner mede acurácia de roteamento, plano, ferramentas, handoff, guardrails e latência, e o CI falha se o roteamento geral cair abaixo de noventa por cento. [Ler da tela: a linha "Routing accuracy" de cada coluna e o veredito do gate.]
>
> [Se houver casos falhando, mostrar a lista "Failing cases" e dizer o que são e o que será corrigido — o eval existe justamente para achar isso. Se o gate estiver verde, destacar o que o conjunto de variações cobre.]
>
> Cada requisição tem um `trace_id` e um span por agente no log JSON, e o `/metrics` expõe contadores e latências em formato Prometheus, prontos para dashboard e alerta de taxa de handoff e latência p95.

## 6:15 – 7:00 · Limitações e próximos passos

[Tela: seção Limitações do README]

> Limitações honestas: o CRM é simulado, com dados fictícios; a base de conhecimento é um retrato do site, não um crawler em tempo real; e o modo offline responde por extração de trechos, menos fluido que o Claude. Próximos passos: memória de conversa por usuário, reindexação agendada do site, LLM-as-judge noturno no modo anthropic e integração real do handoff com a fila de atendimento. Obrigado.
