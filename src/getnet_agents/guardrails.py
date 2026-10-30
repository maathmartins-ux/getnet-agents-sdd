"""Input and output guardrails (FR-08, FR-09, BR-07). Rule-based, cheap and auditable; runs before any LLM."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- PII


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cnpj", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("cpf", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{11}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone", re.compile(r"(?:\+55\s?)?\(\d{2}\)\s?9?\d{4}-?\d{4}\b|\+55\s?\d{2}\s?9?\d{4}-?\d{4}\b")),
]
_MASK = {"cnpj": "[CNPJ]", "cpf": "[CPF]", "card": "[CARD]", "email": "[EMAIL]", "phone": "[PHONE]"}


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Replace PII with typed placeholders. Returns (masked_text, kinds_found)."""
    found: list[str] = []
    for kind, pattern in _PII_PATTERNS:
        def repl(m: re.Match[str], kind: str = kind) -> str:
            if kind == "card":
                digits = re.sub(r"\D", "", m.group(0))
                if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                    return m.group(0)
            found.append(kind)
            return _MASK[kind]

        text = pattern.sub(repl, text)
    return text, sorted(set(found))


# --------------------------------------------------------------------------- injection / scope


def fold(text: str) -> str:
    """Lowercase and strip accents, so 'Antecipação' matches 'antecipacao'."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules)"
    r"|disregard (all |any |the )?(previous|prior|above) "
    r"|ignore (as |todas as )?(instrucoes|regras) (anteriores|acima)"
    r"|esqueca (as |todas as )?(instrucoes|regras)"
    r"|(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)"
    r"|(mostre|revele|repita) (o seu |seu |o )?(prompt|instrucoes)"
    r"|system prompt|prompt do sistema|developer mode|modo desenvolvedor|jailbreak|\bdan mode\b"
    r"|you are now (an?|the) |a partir de agora voce e |act as an? (unrestricted|unfiltered)"
)
_HARMFUL = re.compile(
    r"clon(e|ar|ing) (a |o )?(card|cartao)|carding|stolen card|cartao roubado|cartoes roubados"
    r"|lavagem de dinheiro|money laundering|launder money|bypass (the )?(fraud|antifraud)|burlar (o )?antifraude"
    r"|chargeback fraud|golpe do"
)
_OFF_TOPIC = re.compile(
    r"\b(poem|poema|poesia|joke|piada|recipe|receita de|homework|licao de casa|dever de casa|essay|redacao"
    r"|song|cancao|letra de musica|lyrics|horoscope|horoscopo|bet|aposta|apostas|dating|namoro"
    r"|write (me )?(a |some )?code|escreva (um )?codigo|movie|filme|novel|romance|story|historinha)\b"
)
_DOMAIN = re.compile(
    r"getnet|maquin|card machine|terminal|pos\b|pix|venda|sale|sell|vender|pagament|payment|cartao|card"
    # "conta" alone is also the verb "contar" ("me conta uma piada"), so only unambiguous account phrases count.
    r"|receb|receiv|antecip|crediario|parcel|install|link|whatsapp|taxa|fee|deposit|settle|liquida"
    r"|minha conta|conta (bancaria|digital|getnet|de recebimento)|account"
    r"|weather|tempo|clima|cambio|exchange|cotacao|euro|dolar|dollar"
)


@dataclass
class InputCheck:
    text: str
    blocked: bool = False
    category: str | None = None
    reason: str = ""
    pii: list[str] = field(default_factory=list)


def check_input(message: str) -> InputCheck:
    masked, pii = mask_pii(message)
    folded = fold(masked)
    if _INJECTION.search(folded):
        return InputCheck(masked, True, "prompt_injection", "Attempt to override system instructions", pii)
    if _HARMFUL.search(folded):
        return InputCheck(masked, True, "harmful", "Request for fraud or illegal activity", pii)
    if _OFF_TOPIC.search(folded) and not _DOMAIN.search(folded):
        return InputCheck(masked, True, "out_of_scope", "Request unrelated to Getnet merchant support", pii)
    if pii:
        return InputCheck(masked, False, "pii_redacted", f"Masked: {', '.join(pii)}", pii)
    return InputCheck(masked)


# --------------------------------------------------------------------------- output grounding

_STOP = set(
    "the a an and or of to in on for with is are be can you your it this that from by as at was were will "
    "o a os as de da do das dos e ou em no na nos nas para com por que se um uma uns umas ao aos voce sua seu "
    "sources fontes getnet".split()
)


def _content_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", fold(text)) if len(t) > 3 and t not in _STOP and not t.isdigit()]


def grounding_score(answer: str, passages: list[str]) -> float:
    """Share of the answer's content words that appear in the retrieved passages (lexical support)."""
    tokens = _content_tokens(answer)
    if not tokens:
        return 1.0
    support = set(_content_tokens(" ".join(passages)))
    return sum(1 for t in tokens if t in support) / len(tokens)


def is_grounded(answer: str, passages: list[str], threshold: float) -> bool:
    return bool(passages) and grounding_score(answer, passages) >= threshold
