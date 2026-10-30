Escalation agent — handoff policy (deterministic; this file documents the behaviour implemented in `agents/escalation.py`).

When it runs
- The merchant explicitly asks for a person (intent human_request).
- The Router's confidence is below ROUTER_MIN_CONFIDENCE (default 0.45).
- The Knowledge agent finds no relevant official source (no source, no claim) or the LLM answers NOT_IN_SOURCES.
- The Support agent cannot find the merchant or the CRM is unavailable.

What it does
1. Opens a ticket with `open_support_ticket(user_id, category="<intent>:<reason>", summary=<message with PII masked>)`.
2. Tells the merchant, in their language, why a human is needed, the ticket number, and that the human receives the conversation history.
3. Returns `handoff = {required: true, reason, ticket_id}` so the channel (chat widget, WhatsApp, Teams) can route the conversation to the human queue.

What it never does
- Promise a response time, phone number or outcome that is not in an official source.
- Retry the question with a guess.

Why it is not LLM-driven: a handoff must keep working when the LLM, the network or the knowledge base is down (Constitution, Article II).
