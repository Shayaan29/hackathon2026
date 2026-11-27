"""Manual search (RAG): answers come from the sensor manuals, with section and page.

Put PDF, Markdown or text files in ``backend/knowledge/``. On first use the
folder is read into chunks that never cross a section or a page, so every
passage carries an exact citation: document, section number and title, the
page number printed on the page, and any table or figure caption on it.
Footnotes a passage refers to (``[6]``) are attached to it, because on
datasheets the test conditions live in the footnotes.

Search is BM25 over those chunks and works offline. With ``OPENAI_API_KEY``
set, the chunks are also embedded once (cached on disk) and both rankings are
fused, so "how hot can it get" still finds "Temperature range". The voice
copilot gets the passages from :meth:`KnowledgeBase.tool_result` and phrases
the answer itself; :meth:`KnowledgeBase.answer` writes a grounded text answer
for typed questions, or quotes the best lines when there is no key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

log = logging.getLogger("cockpit.knowledge")

INDEX_VERSION = 6
DOC_SUFFIXES = (".pdf", ".md", ".markdown", ".txt")
MAX_CHUNK_CHARS = 1000
EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
RESPONSES_URL = "https://api.openai.com/v1/responses"
EMBED_BATCH = 96
RRF_K = 60.0
# cosine similarity below which an embedding-only match does not count as found
MIN_SEMANTIC = 0.30

STOPWORDS = frozenset("""
a an and are as at be by can do does for from has have how i if in into is it its me my of on or
please say tell than that the their them then there these this to us was what when where which who
why will with you your about give show find manual datasheet sensor sensors value values
der die das ein eine und ist im in den dem des zu mit von wie was wo welche welcher welches
für auf bei aus oder nicht sind wird werden kann ich mir sie es handbuch datenblatt
""".split())

_TOKEN_RE = re.compile(r"[\wµ°±]+(?:[-.,/][\wµ°±]+)*")
_PAGE_FOOTER_RE = re.compile(r"\bPage\s+(\d{1,4})\s*$", re.IGNORECASE)
_TOC_LINE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+?)\s*\.{4,}\s*(\d+)\s*$")
_HEADING_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\s{2,}([A-Za-zÄÖÜäöü].{1,100})$")
_FOOTNOTE_RE = re.compile(r"^\[(\d{1,3})\]\s+(\S.*)$")
_FOOTNOTE_REF_RE = re.compile(r"(?<!^)\[(\d{1,3})\]")
_CAPTION_RE = re.compile(r"^((?:Tab|Fig)\.\s*\d+(?:\.\d+)*)\s*:?\s*(.*)$")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class KnowledgeError(RuntimeError):
    pass


# -- text helpers ----------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _stem(token: str) -> str:
    """Crude but robust: drop a plural and keep the first six letters, so that
    'operating', 'operation' and 'operational' all become 'operat'."""
    if not token.isalpha():
        return token
    if len(token) > 4 and token.endswith("ies"):
        token = token[:-3] + "y"
    elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token[:6]


def tokenize(text: str) -> list[str]:
    """Lower-case words; 'ILD5500-10' gives 'ild5500-10', 'ild5500' and '10'."""
    out: list[str] = []
    for match in _TOKEN_RE.findall(text.lower()):
        parts = [p for p in re.split(r"[-./,]", match) if p]
        if len(parts) > 1 and not all(p.isdigit() for p in parts):
            out.append(match)
        for part in parts:
            if part not in STOPWORDS and (len(part) > 1 or part.isdigit()):
                out.append(_stem(part))
    return out


def _tidy(line: str) -> str:
    """Layout text uses runs of spaces for table columns; keep them as ' | '."""
    return re.sub(r" {3,}", " | ", line.strip())


# -- chunks ----------------------------------------------------------------------

@dataclass
class Chunk:
    id: str
    doc: str                 # file name
    doc_title: str
    section: str             # "3.3" or ""
    section_title: str
    page: int | None         # page in the PDF file, from 1
    printed_page: int | None  # the number printed in the page footer
    labels: list[str] = field(default_factory=list)   # "Tab. 3.1: LEDs on the receiver"
    footnotes: list[str] = field(default_factory=list)
    text: str = ""

    def citation(self) -> str:
        parts = []
        if self.section:
            parts.append(f"Section {self.section} {self.section_title}".strip())
        elif self.section_title:
            parts.append(self.section_title)
        parts.extend(label.split(":")[0] for label in self.labels[:2])
        page = self.printed_page or self.page
        if page:
            parts.append(f"Page {page}")
        return ", ".join(parts) or self.doc_title

    def search_text(self) -> str:
        head = f"{self.section} {self.section_title}"
        # The document title is left out on purpose: in every chunk it would make words like
        # "operating" (from "Operating Instructions") worthless. See KnowledgeBase._doc_bonus.
        return " ".join([head, head, " ".join(self.labels), self.text, " ".join(self.footnotes)])


@dataclass
class _Page:
    number: int
    printed: int | None
    lines: list[str]


def _read_pdf(path: Path) -> list[_Page]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - listed in requirements.txt
        raise KnowledgeError("pypdf is missing: run pip install -r requirements.txt") from exc
    reader = PdfReader(str(path))
    pages: list[_Page] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout")
        except Exception:  # noqa: BLE001 - some pages only work in plain mode
            text = page.extract_text() or ""
        pages.append(_Page(i, None, [ln.rstrip() for ln in text.splitlines()]))
    return pages


def _strip_furniture(pages: list[_Page]) -> None:
    """Remove running headers and footers; keep the printed page number."""
    edge = Counter()
    for page in pages:
        filled = [ln.strip() for ln in page.lines if ln.strip()]
        edge.update(set(filled[:1] + filled[-1:]))
    repeated = {line for line, n in edge.items() if n >= 3 and len(line) < 120}
    for page in pages:
        lines = page.lines
        for idx in range(len(lines) - 1, -1, -1):
            if lines[idx].strip():
                match = _PAGE_FOOTER_RE.search(lines[idx])
                if match:
                    page.printed = int(match.group(1))
                    lines[idx] = ""
                break
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        for idx in filled[:1] + filled[-1:]:
            if lines[idx].strip() in repeated:
                lines[idx] = ""


def _toc(pages: list[_Page]) -> tuple[dict[str, str], set[int]]:
    """Section numbers and titles from the table of contents, and the TOC pages."""
    entries: dict[str, str] = {}
    toc_pages: set[int] = set()
    for page in pages:
        filled = [ln for ln in page.lines if ln.strip()]
        hits = [m for m in (_TOC_LINE_RE.match(ln) for ln in filled) if m]
        if filled and len(hits) >= max(5, len(filled) * 0.4):
            toc_pages.add(page.number)
            for m in hits:
                entries.setdefault(m.group(1), re.sub(r"\s+", " ", m.group(2)).strip())
    return entries, toc_pages


def _match_heading(line: str, toc: dict[str, str]) -> tuple[str, str] | None:
    m = _HEADING_RE.match(line.strip())
    if not m:
        return None
    number, title = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
    if toc:
        known = toc.get(number)
        if known is None:
            return None
        a, b = _norm(title)[:24], _norm(known)[:24]
        if not (a.startswith(b[:12]) or b.startswith(a[:12])):
            return None
        return number, known
    if "|" in _tidy(title) or len(title) > 90:
        return None
    return number, title


def _page_footnotes(lines: list[str]) -> dict[str, str]:
    notes: dict[str, str] = {}
    current: str | None = None
    for raw in lines:
        line = raw.strip()
        m = _FOOTNOTE_RE.match(line)
        if m:
            current = m.group(1)
            notes[current] = m.group(2).strip()
        elif current and line and not _HEADING_RE.match(line):
            notes[current] += " " + line
        else:
            current = None
    return {k: re.sub(r"\s+", " ", v) for k, v in notes.items()}


def _split_blocks(lines: list[str], limit: int) -> list[str]:
    """Join lines into pieces of at most ``limit`` characters, cutting at blank lines."""
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            blocks[-1].append(_tidy(line))
        elif blocks[-1]:
            blocks.append([])
    pieces: list[str] = []
    current = ""
    for block in (b for b in blocks if b):
        text = "\n".join(block)
        if len(text) > limit:  # a long table: cut it by lines
            for line in block:
                if current and len(current) + len(line) + 1 > limit:
                    pieces.append(current)
                    current = ""
                current = f"{current}\n{line}" if current else line
            continue
        if current and len(current) + len(text) + 2 > limit:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{text}" if current else text
    if current.strip():
        pieces.append(current)
    return pieces


def _doc_title(path: Path, pages: list[_Page]) -> str:
    for page in pages[:1]:
        filled = [ln.strip() for ln in page.lines if ln.strip()]
        if filled:
            return " ".join(_tidy(ln) for ln in filled[:2]).replace(" | ", " ")[:90]
    return path.stem


def chunk_pdf(path: Path) -> list[Chunk]:
    pages = _read_pdf(path)
    title = _doc_title(path, pages)
    _strip_furniture(pages)
    toc, toc_pages = _toc(pages)
    notes_by_page = {p.number: _page_footnotes(p.lines) for p in pages}
    chunks: list[Chunk] = []
    section, section_title = "", ""
    for page in pages:
        if page.number in toc_pages:
            continue
        if page.number == 1 and toc and not any(_match_heading(ln, toc) for ln in page.lines):
            continue  # the cover: model names and an address, no content
        # cut the page wherever a section starts
        runs: list[tuple[str, str, list[str]]] = [(section, section_title, [])]
        for line in page.lines:
            heading = _match_heading(line, toc)
            if heading:
                section, section_title = heading
                runs.append((section, section_title, [line.strip()]))
            else:
                runs[-1][2].append(line)
        for sec, sec_title, lines in runs:
            labels = []
            for line in lines:
                m = _CAPTION_RE.match(line.strip())
                if m:
                    labels.append(f"{m.group(1)}: {m.group(2).strip()}".rstrip(": "))
            for piece in _split_blocks(lines, MAX_CHUNK_CHARS):
                chunks.append(Chunk(
                    id="", doc=path.name, doc_title=title, section=sec, section_title=sec_title,
                    page=page.number, printed_page=page.printed, labels=labels,
                    footnotes=_footnotes_for(piece, page.number, notes_by_page), text=piece))
    return chunks


def _footnotes_for(text: str, page: int, notes_by_page: dict[int, dict[str, str]]) -> list[str]:
    refs: list[str] = []
    for line in text.splitlines():
        for ref in _FOOTNOTE_REF_RE.findall(line.strip()):
            if ref not in refs:
                refs.append(ref)
    out = []
    for ref in refs:
        if re.search(rf"(^|\n)\[{ref}\]\s", text):
            continue  # the footnote itself is already in this passage
        for candidate in (page, page + 1, page - 1):
            note = notes_by_page.get(candidate, {}).get(ref)
            if note:
                out.append(f"[{ref}] {note}")
                break
    return out


def chunk_text(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks: list[Chunk] = []
    title = path.stem
    heading, lines = "", []

    def flush() -> None:
        for piece in _split_blocks(lines, MAX_CHUNK_CHARS):
            chunks.append(Chunk(id="", doc=path.name, doc_title=title, section="", section_title=heading,
                                page=None, printed_page=None, text=piece))

    for line in text.splitlines():
        m = _MD_HEADING_RE.match(line)
        if m:
            flush()
            heading, lines = m.group(2), []
            if len(m.group(1)) == 1 and title == path.stem:
                title = heading
        else:
            lines.append(line)
    flush()
    return chunks


def chunk_file(path: Path) -> list[Chunk]:
    return chunk_pdf(path) if path.suffix.lower() == ".pdf" else chunk_text(path)


# -- BM25 --------------------------------------------------------------------------

class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 0.9, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.lengths = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.lengths.mean()) if self.n else 1.0
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for i, doc in enumerate(docs):
            for term, tf in Counter(doc).items():
                self.postings.setdefault(term, []).append((i, tf))

    def idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5)) if df else 0.0

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n)
        for term in set(query):
            idf = self.idf(term)
            for i, tf in self.postings.get(term, ()):
                denom = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg)
                out[i] += idf * tf * (self.k1 + 1) / denom
        return out


# -- the knowledge base ----------------------------------------------------------------

class KnowledgeBase:
    def __init__(self, folder: Path, api_key: str = "", embedding_model: str = "text-embedding-3-small",
                 answer_model: str = "gpt-5-mini", http: httpx.Client | None = None,
                 cache_dir: Path | None = None) -> None:
        self.folder = Path(folder)
        self.cache_dir = Path(cache_dir) if cache_dir else self.folder / ".cache"
        self.api_key = api_key
        self.embedding_model = embedding_model
        self.answer_model = answer_model
        self._http = http
        self._lock = threading.RLock()
        self._signature: str | None = None
        self.chunks: list[Chunk] = []
        self._bm25: BM25 | None = None
        self._vectors: np.ndarray | None = None
        self._semantic_error: str | None = None
        self.errors: dict[str, str] = {}
        self.built_at: float | None = None

    # building ---------------------------------------------------------------

    def files(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.iterdir()
                      if p.is_file() and p.suffix.lower() in DOC_SUFFIXES and not p.name.startswith("."))

    def _folder_signature(self) -> str:
        parts = [f"v{INDEX_VERSION}"] + [f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}"
                                         for p in self.files()]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

    def ensure(self, force: bool = False) -> None:
        """(Re)build the index when files in the folder were added, changed or removed."""
        with self._lock:
            signature = self._folder_signature()
            if not force and signature == self._signature:
                return
            started = time.time()
            chunks = None if force else self._load_cache(signature)
            if chunks is None:
                chunks, self.errors = [], {}
                for path in self.files():
                    try:
                        chunks.extend(chunk_file(path))
                    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
                        log.warning("could not read %s: %s", path.name, exc)
                        self.errors[path.name] = str(exc)
                for i, chunk in enumerate(chunks):
                    chunk.id = f"K{i + 1:04d}"
                self._save_cache(signature, chunks)
            self.chunks = chunks
            self._bm25 = BM25([tokenize(c.search_text()) for c in chunks])
            self._vectors, self._semantic_error = None, None
            self._signature = signature
            self.built_at = time.time()
            log.info("manuals indexed: %d passages from %d files in %.1f s",
                     len(chunks), len(self.files()), time.time() - started)

    def _cache_file(self) -> Path:
        return self.cache_dir / "chunks.json"

    def _load_cache(self, signature: str) -> list[Chunk] | None:
        try:
            raw = json.loads(self._cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if raw.get("signature") != signature:
            return None
        self.errors = raw.get("errors", {})
        return [Chunk(**c) for c in raw["chunks"]]

    def _save_cache(self, signature: str, chunks: list[Chunk]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_file().write_text(json.dumps(
                {"signature": signature, "errors": self.errors, "chunks": [asdict(c) for c in chunks]}),
                encoding="utf-8")
        except OSError as exc:
            log.warning("could not write the manual cache: %s", exc)

    # embeddings (optional) ------------------------------------------------------

    @property
    def semantic(self) -> bool:
        return bool(self.api_key) and self._semantic_error is None

    def _embed(self, texts: list[str]) -> np.ndarray:
        client = self._http or httpx.Client(timeout=30.0)
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), EMBED_BATCH):
                response = client.post(EMBEDDINGS_URL, headers={"Authorization": f"Bearer {self.api_key}"},
                                       json={"model": self.embedding_model,
                                             "input": [t[:8000] for t in texts[start:start + EMBED_BATCH]]})
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda d: d["index"])
                vectors.extend(d["embedding"] for d in data)
        finally:
            if self._http is None:
                client.close()
        arr = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.maximum(norms, 1e-9)

    def _chunk_vectors(self) -> np.ndarray | None:
        if not self.semantic or not self.chunks:
            return None
        if self._vectors is not None:
            return self._vectors
        path = self.cache_dir / f"vectors-{re.sub(r'[^a-z0-9.-]', '_', self.embedding_model.lower())}.npy"
        meta = path.with_suffix(".sig")
        try:
            if meta.read_text() == self._signature:
                vectors = np.load(path)
                if vectors.shape[0] == len(self.chunks):
                    self._vectors = vectors
                    return vectors
        except (OSError, ValueError):
            pass
        try:
            self._vectors = self._embed([f"{c.citation()}\n{c.text}\n{' '.join(c.footnotes)}" for c in self.chunks])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self._semantic_error = str(exc)
            log.warning("embeddings unavailable, using keyword search only: %s", exc)
            return None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(path, self._vectors)
            meta.write_text(self._signature or "")
        except OSError:
            pass
        return self._vectors

    # searching ---------------------------------------------------------------

    def documents(self) -> dict[str, Any]:
        self.ensure()
        per_doc: dict[str, dict[str, Any]] = {}
        for c in self.chunks:
            d = per_doc.setdefault(c.doc, {"name": c.doc, "title": c.doc_title, "passages": 0, "pages": 0})
            d["passages"] += 1
            d["pages"] = max(d["pages"], c.page or 0)
        return {"documents": list(per_doc.values()), "errors": self.errors,
                "search": "hybrid" if self.api_key else "keyword", "folder": self.folder.name}

    def search(self, query: str, k: int = 5, document: str | None = None) -> dict[str, Any]:
        self.ensure()
        query = " ".join(str(query or "").split())[:500]
        if not query:
            raise KnowledgeError("Ask a question or give search words.")
        if not self.chunks:
            return {"query": query, "found": False, "hits": [], "mode": "keyword",
                    "message": f"No manuals are loaded. Put PDF files in backend/{self.folder.name}/."}
        k = int(min(max(k, 1), 12))
        allowed = np.ones(len(self.chunks), dtype=bool)
        if document:
            want = document.lower()
            allowed = np.array([want in c.doc.lower() or want in c.doc_title.lower() for c in self.chunks])
        terms = tokenize(query)
        bm25 = self._bm25.scores(terms) if self._bm25 else np.zeros(len(self.chunks))
        bm25 = bm25 * self._doc_bonus(terms)
        bm25 = np.where(allowed, bm25, -1.0)
        cosine = None
        vectors = self._chunk_vectors()
        if vectors is not None:
            try:
                cosine = vectors @ self._embed([query])[0]
                cosine = np.where(allowed, cosine, -1.0)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("query embedding failed, keyword search only: %s", exc)
        fused = np.zeros(len(self.chunks))
        for ranking in (bm25, cosine):
            if ranking is None:
                continue
            order = np.argsort(-ranking, kind="stable")
            ranks = np.empty(len(order))
            ranks[order] = np.arange(1, len(order) + 1)
            fused += np.where(ranking > 0, 1.0 / (RRF_K + ranks), 0.0)
        order = [i for i in np.argsort(-fused, kind="stable") if fused[i] > 0][:k]
        hits = []
        for i in order:
            c = self.chunks[i]
            hits.append({
                "id": c.id, "doc": c.doc, "doc_title": c.doc_title, "section": c.section,
                "section_title": c.section_title, "page": c.page, "printed_page": c.printed_page,
                "labels": c.labels, "citation": c.citation(), "text": c.text, "footnotes": c.footnotes,
                "keyword_score": round(float(max(bm25[i], 0.0)), 3),
                "semantic_score": None if cosine is None else round(float(cosine[i]), 3),
                "url": self.file_url(c),
            })
        found = any(h["keyword_score"] > 0 or (h["semantic_score"] or 0) >= MIN_SEMANTIC for h in hits)
        return {"query": query, "found": found, "hits": hits if found else [],
                "mode": "hybrid" if cosine is not None else "keyword",
                **({} if found else {"message": "Nothing in the manuals matches this question."})}

    def _doc_bonus(self, terms: list[str]) -> np.ndarray:
        """A query that names a product ("optoNCDT 5500") prefers that manual's passages."""
        bonus = np.ones(len(self.chunks))
        wanted = {t for t in terms if any(ch.isdigit() for ch in t)}
        if not wanted:
            return bonus
        titles: dict[str, bool] = {}
        for i, c in enumerate(self.chunks):
            if c.doc not in titles:
                titles[c.doc] = bool(wanted & set(tokenize(f"{c.doc_title} {c.doc}")))
            if titles[c.doc]:
                bonus[i] = 1.25
        return bonus

    @staticmethod
    def file_url(chunk: Chunk) -> str:
        url = f"/api/knowledge/file/{chunk.doc}"
        return f"{url}#page={chunk.page}" if chunk.page else url

    def tool_result(self, query: str, document: str | None = None, k: int = 4) -> dict[str, Any]:
        """Compact passages for the voice model, with the rules for using them."""
        result = self.search(query, k=k, document=document)
        passages = [{
            "source": f"S{n}",
            "document": h["doc_title"],
            "cite": h["citation"],
            "text": h["text"][:1400],
            **({"footnotes": h["footnotes"]} if h["footnotes"] else {}),
        } for n, h in enumerate(result["hits"], start=1)]
        out: dict[str, Any] = {"query": result["query"], "found": result["found"], "passages": passages}
        if result["found"]:
            out["how_to_answer"] = ("Answer only from these passages. Keep exact numbers, units, min/typ/max and "
                                    "test conditions from the text and footnotes. Say the model when the value "
                                    "depends on it. Name the section and page you took it from.")
        else:
            out["how_to_answer"] = "Say that the manuals do not mention this. Do not answer from memory."
            out["message"] = result.get("message")
        out["hits"] = result["hits"]
        return out

    # answering -----------------------------------------------------------------

    def answer(self, question: str, k: int = 6, document: str | None = None) -> dict[str, Any]:
        result = self.search(question, k=k, document=document)
        hits = result["hits"]
        base = {"question": result["query"], "found": result["found"], "sources": hits, "search": result["mode"]}
        if not result["found"]:
            return {**base, "mode": "none",
                    "answer": "This specification is not mentioned in the provided manuals."}
        if self.api_key:
            try:
                return {**base, "mode": "generated", "model": self.answer_model,
                        "answer": self._generate(result["query"], hits)}
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                log.warning("answer generation failed, quoting the manual instead: %s", exc)
        return {**base, "mode": "extractive", "answer": self._extract(result["query"], hits)}

    def _generate(self, question: str, hits: list[dict[str, Any]]) -> str:
        context = "\n\n".join(
            f"[S{n}] {h['doc_title']} | {h['citation']}\n{h['text']}"
            + (f"\nFootnotes: {' '.join(h['footnotes'])}" if h["footnotes"] else "")
            for n, h in enumerate(hits, start=1))
        instructions = (
            "You are a precision technical assistant for Micro-Epsilon sensors. Answer ONLY from the manual "
            "passages given. Never use outside knowledge for specifications, pin assignments or tolerances. "
            "If the passages do not state it, reply exactly: \"This specification is not mentioned in the "
            "provided datasheet.\" Keep exact values, min/typ/max, units and test conditions, never round. "
            "Read table rows carefully: layout text separates columns with ' | '. Include footnotes that apply. "
            "If the value differs between model variants and the question names none, list each variant. "
            "Start with the direct answer, then conditions and caveats. Cite every value like "
            "[Section 3.3, Tab. 3.1, Page 12] using the citation given with each passage. Use a Markdown table "
            "when comparing variants or pins. Answer in the language of the question.")
        client = self._http or httpx.Client(timeout=60.0)
        try:
            response = client.post(RESPONSES_URL, headers={"Authorization": f"Bearer {self.api_key}"}, json={
                "model": self.answer_model, "instructions": instructions,
                "input": f"Manual passages:\n\n{context}\n\nQuestion: {question}"})
            response.raise_for_status()
            data = response.json()
        finally:
            if self._http is None:
                client.close()
        texts = [part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                 for part in item.get("content", []) if part.get("type") == "output_text"]
        text = "\n".join(t for t in texts if t).strip()
        if not text:
            raise ValueError("the model returned no text")
        return text

    def _extract(self, question: str, hits: list[dict[str, Any]]) -> str:
        """Without a model: quote the lines that best match the question.

        Words weigh by how rare they are in the manuals. Model numbers weigh
        little, because in a spec table they name the column, not the row.
        Table cells in layout text wrap over several lines, so each picked line
        comes with its neighbours, and with the table's header row.
        """
        weights: dict[str, float] = {}
        for term in set(tokenize(question)):
            idf = self._bm25.idf(term) if self._bm25 else 1.0
            weights[term] = idf * (0.2 if any(ch.isdigit() for ch in term) else 1.0)
        blocks: list[str] = []
        first_best = 0.0
        for h in hits[:2]:
            lines = [ln for ln in h["text"].splitlines() if ln.strip()]
            scores = [sum(weights.get(t, 0.0) for t in set(tokenize(ln))) for ln in lines]
            best = max(scores, default=0.0)
            if best <= 0 or best < first_best * 0.75:
                continue  # the second passage only if it matches about as well as the first
            first_best = first_best or best
            keep: set[int] = set()
            for i, score in enumerate(scores):
                if score >= best * 0.6 and score > 0:
                    keep.update(j for j in (i - 1, i, i + 1) if 0 <= j < len(lines))
            header = next((i for i, ln in enumerate(lines) if "|" in ln and not _HEADING_RE.match(ln)), None)
            if header is not None and any(i > header and "|" in lines[i] for i in keep):
                keep.add(header)
            picked = [lines[i] for i in sorted(keep)][:10]
            refs = set(_FOOTNOTE_REF_RE.findall("\n".join(" " + ln for ln in picked)))
            notes = [n for n in h["footnotes"] if n[1:n.index("]")] in refs]
            blocks.append("\n".join(picked + notes) + f"\n[{h['citation']}]")
        if not blocks:
            top = hits[0]
            return f"{top['text'][:400].strip()}\n[{top['citation']}]"
        return "\n\n".join(blocks)
