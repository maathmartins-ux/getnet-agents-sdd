"""Ingestion step of the RAG pipeline.

Default: load the curated snapshot `data/knowledge/corpus.jsonl` (reproducible, offline, every fact traceable
to its source URL). `--refresh`: fetch the URLs of `data/knowledge/sources.json` live, convert HTML to text,
chunk, and write `corpus.live.jsonl` (point KNOWLEDGE_PATH at it to serve the live crawl).

    python -m getnet_agents.rag.ingest --stats
    python -m getnet_agents.rag.ingest --refresh
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

from ..config import DEFAULT_KNOWLEDGE
from .store import Doc

REQUIRED = ("id", "title", "url", "lang", "text")


def load_corpus(path: Path) -> list[Doc]:
    docs: list[Doc] = []
    if not path.exists():
        return docs
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        missing = [f for f in REQUIRED if not row.get(f)]
        if missing:
            raise ValueError(f"{path.name}:{n} missing {missing}")
        docs.append(
            Doc(
                id=row["id"], title=row["title"], url=row["url"], lang=row["lang"], text=row["text"],
                tags=list(row.get("tags", [])), section=row.get("section", ""),
            )
        )
    return docs


def _front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    _, head, body = raw.split("---", 2)
    meta = {}
    for line in head.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def load_markdown(folder: Path) -> list[Doc]:
    """Curated topic sheets (`*.md` with front matter: title, source_url, keywords, lang_original).

    Each `## ` section becomes one document, so retrieval returns the relevant step, not the whole sheet.
    Files without `source_url` (README, SOURCES) are not knowledge and are skipped.
    """
    docs: list[Doc] = []
    for path in sorted(folder.glob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
        url = meta.get("source_url", "")
        if not url.startswith("https://"):
            continue
        tags = [t.strip() for t in meta.get("keywords", "").strip("[]").split(",") if t.strip()]
        for i, section in enumerate(re.split(r"\n(?=## )", body)):
            lines = [ln for ln in section.strip().splitlines() if ln.strip()]
            if not lines:
                continue
            heading = lines[0].lstrip("#").strip() if lines[0].startswith("#") else ""
            text = " ".join(ln.strip() for ln in (lines[1:] if heading else lines))
            if len(text.split()) < 12:
                continue
            # `lang_original` is the language of the source page, not of the curated text: the sheets are
            # written in Portuguese, and each one carries an "## English summary" section for English queries.
            lang = "en" if heading.lower().startswith("english") else "pt"
            docs.append(Doc(
                id=f"{path.stem}#{i}", title=f"{meta.get('title', path.stem)} — {heading}".strip(" —"),
                url=url, lang=lang, text=text, tags=tags, section=path.stem,
            ))
    return docs


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "br", "div", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 30]
    return parser.title, "\n".join(dict.fromkeys(lines))


def chunk(text: str, max_words: int = 180) -> list[str]:
    chunks, cur = [], []
    for para in text.split("\n"):
        words = para.split()
        if cur and len(cur) + len(words) > max_words:
            chunks.append(" ".join(cur))
            cur = []
        cur.extend(words)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def refresh(sources: Path, out: Path) -> int:
    import httpx

    rows = []
    for src in json.loads(sources.read_text(encoding="utf-8")):
        try:
            resp = httpx.get(src["url"], timeout=10, follow_redirects=True, headers={"User-Agent": "getnet-agents-ingest/1.0"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"skip {src['url']}: {exc}")
            continue
        title, text = html_to_text(resp.text)
        for i, part in enumerate(chunk(text)):
            rows.append({
                "id": hashlib.sha1(f"{src['url']}#{i}".encode()).hexdigest()[:12],
                "title": src.get("title") or title, "url": src["url"], "lang": src.get("lang", "pt"),
                "section": "live", "tags": [], "text": part,
            })
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="crawl sources.json into corpus.live.jsonl")
    ap.add_argument("--stats", action="store_true", help="print corpus statistics")
    args = ap.parse_args()
    if args.refresh:
        n = refresh(DEFAULT_KNOWLEDGE.parent / "sources.json", DEFAULT_KNOWLEDGE.parent / "corpus.live.jsonl")
        print(f"wrote {n} chunks to corpus.live.jsonl")
    docs = load_corpus(DEFAULT_KNOWLEDGE)
    by_lang: dict[str, int] = {}
    for d in docs:
        by_lang[d.lang] = by_lang.get(d.lang, 0) + 1
    print(json.dumps({"documents": len(docs), "by_lang": by_lang, "urls": len({d.url for d in docs})}))


if __name__ == "__main__":
    main()
