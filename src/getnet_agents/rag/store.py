"""Storage + retrieval of the RAG pipeline (ADR-03): in-memory BM25 with per-field weights, pure Python."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..guardrails import fold

FIELD_WEIGHTS: dict[str, float] = {"title": 3.0, "tags": 2.0, "text": 1.0}
K1, B = 1.5, 0.75
NORM_K = 4.0  # norm = raw / (raw + NORM_K): monotone map of BM25 to 0..1

STOPWORDS = set(
    """a an and are as at be by can could do does for from has have how i if in is it its me my of on or our
    should so than that the their them then there these they this to was we what when where which who why will
    with would you your yours about into over get got
    o os as um uma uns umas de da do das dos e em no na nos nas para por com que se ao aos meu minha meus
    minhas seu sua seus suas eu voce voces ele ela isso esse essa este esta como qual quais quando onde porque
    mais muito ser ter tem posso pode sao foi esta estao vou""".split()
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in re.findall(r"[a-z0-9]+", fold(text)):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        if len(tok) > 4 and tok.endswith("s"):  # light plural folding: installments→installment, parcelas→parcela
            tok = tok[:-1]
        tokens.append(tok)
    return tokens


@dataclass
class Doc:
    id: str
    title: str
    url: str
    lang: str
    text: str
    tags: list[str] = field(default_factory=list)
    section: str = ""


@dataclass
class Hit:
    doc: Doc
    score: float

    @property
    def norm(self) -> float:
        return round(self.score / (self.score + NORM_K), 4)


class KnowledgeStore:
    def __init__(self, docs: list[Doc]) -> None:
        self.docs = docs
        self._tf: list[dict[str, Counter[str]]] = []
        self._len: list[dict[str, int]] = []
        df: dict[str, Counter[str]] = {f: Counter() for f in FIELD_WEIGHTS}
        for d in docs:
            fields = {"title": tokenize(d.title), "tags": tokenize(" ".join(d.tags)), "text": tokenize(d.text)}
            self._tf.append({f: Counter(toks) for f, toks in fields.items()})
            self._len.append({f: len(toks) for f, toks in fields.items()})
            for f, toks in fields.items():
                df[f].update(set(toks))
        n = max(len(docs), 1)
        self._idf = {f: {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df[f].items()} for f in FIELD_WEIGHTS}
        self._avg = {f: (sum(lengths[f] for lengths in self._len) / n) or 1.0 for f in FIELD_WEIGHTS}

    def __len__(self) -> int:
        return len(self.docs)

    def _score(self, i: int, q: list[str]) -> float:
        total = 0.0
        for f, w in FIELD_WEIGHTS.items():
            tf, dl, avg, idf = self._tf[i][f], self._len[i][f], self._avg[f], self._idf[f]
            for t in q:
                c = tf.get(t)
                if c:
                    total += w * idf.get(t, 0.0) * (c * (K1 + 1)) / (c + K1 * (1 - B + B * dl / avg))
        return total

    def search(self, query: str, lang: str | None = None, k: int = 4) -> list[Hit]:
        """Top-k documents. With `lang`, documents in that language are preferred (fallback: any language)."""
        q = list(dict.fromkeys(tokenize(query)))
        if not q:
            return []
        hits = [Hit(d, s) for i, d in enumerate(self.docs) if (s := self._score(i, q)) > 0]
        if lang and any(h.doc.lang == lang for h in hits):
            hits = [h for h in hits if h.doc.lang == lang]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


@lru_cache(maxsize=4)
def load_store(path: str) -> KnowledgeStore:
    """Index = curated bilingual corpus (`corpus.jsonl`) + curated topic sheets (`*.md`) in the same folder."""
    from .ingest import load_corpus, load_markdown

    p = Path(path)
    return KnowledgeStore(load_corpus(p) + load_markdown(p.parent))
