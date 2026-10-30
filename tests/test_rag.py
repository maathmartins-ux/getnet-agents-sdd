"""RAG pipeline: ingestion, BM25 storage/retrieval, extractive generation (FR-03, BR-01)."""
import json
from pathlib import Path

import pytest

from getnet_agents.agents.knowledge import extractive_answer
from getnet_agents.config import DEFAULT_KNOWLEDGE
from getnet_agents.guardrails import grounding_score
from getnet_agents.rag.ingest import chunk, html_to_text, load_corpus
from getnet_agents.rag.store import Doc, KnowledgeStore, load_store, tokenize


def _store() -> KnowledgeStore:
    return KnowledgeStore([
        Doc("a-en", "Payment Link", "https://x/link", "en", "Sell on WhatsApp with a payment link.", ["whatsapp"]),
        Doc("a-pt", "Link de Pagamento", "https://x/link", "pt", "Venda pelo WhatsApp com link de pagamento.", ["whatsapp"]),
        Doc("b-en", "Pix", "https://x/pix", "en", "Receive Pix payments in your account. Pix needs a key.", ["pix"]),
    ])


def test_tokenize_folds_accents_plurals_and_stopwords() -> None:
    assert tokenize("Antecipação das Parcelas") == ["antecipacao", "parcela"]
    assert "the" not in tokenize("the installments")


def test_title_weight_and_language_preference() -> None:
    hits = _store().search("payment link whatsapp", lang="en")
    assert hits[0].doc.id == "a-en"
    assert all(h.doc.lang == "en" for h in hits)
    assert 0 < hits[0].norm < 1


def test_language_fallback_when_no_doc_in_that_language() -> None:
    hits = _store().search("pix key", lang="pt")
    assert hits and hits[0].doc.id == "b-en"


def test_irrelevant_query_returns_nothing() -> None:
    assert _store().search("quantum chromodynamics") == []


def test_html_to_text_drops_scripts_and_keeps_paragraphs() -> None:
    title, text = html_to_text(
        "<html><title>Pix</title><script>var x=1</script>"
        "<p>O Pix cai na hora na sua conta Getnet, inclusive aos sábados.</p></html>"
    )
    assert title == "Pix"
    assert "var x" not in text
    assert "Pix cai na hora" in text


def test_chunk_respects_max_words() -> None:
    parts = chunk("\n".join(["palavra " * 50] * 5), max_words=120)
    assert len(parts) >= 2
    assert all(len(p.split()) <= 120 for p in parts)


def test_load_corpus_rejects_missing_fields(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({"id": "x", "title": "t", "lang": "en", "text": "t"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="url"):
        load_corpus(p)


def test_real_corpus_is_bilingual_with_https_sources() -> None:
    docs = load_corpus(DEFAULT_KNOWLEDGE)
    assert len(docs) >= 20
    assert {d.lang for d in docs} == {"pt", "en"}
    assert all(d.url.startswith("https://") for d in docs)


@pytest.mark.parametrize(
    "query,expected_word",
    [
        ("What's the difference between the Get Clássica and the Get Smart?", "smart"),
        ("Do I need a bank account to receive my sales via Pix?", "pix"),
        ("How does receivables advance (antecipação) work with Getnet?", "advance"),
        ("How many installments can I split a sale into with the crediário?", "crediário"),
        ("Can I sell through WhatsApp using the Payment Link?", "payment link"),
    ],
)
def test_real_corpus_retrieves_case_topics(query: str, expected_word: str) -> None:
    hits = load_store(str(DEFAULT_KNOWLEDGE)).search(query, lang="en", k=3)
    assert hits and hits[0].norm >= 0.35
    assert expected_word in (hits[0].doc.title + " " + hits[0].doc.text).lower()


def test_extractive_answer_is_grounded_and_cited() -> None:
    q = "Can I sell through WhatsApp using the Payment Link?"
    hits = load_store(str(DEFAULT_KNOWLEDGE)).search(q, lang="en", k=3)
    answer = extractive_answer(q, hits, "en")
    assert answer.startswith("According to Getnet's official content:")
    assert "[1]" in answer
    assert grounding_score(answer, [h.doc.text for h in hits]) >= 0.9
