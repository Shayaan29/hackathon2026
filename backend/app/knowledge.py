"""The manuals the recommendations are grounded in.

Documents (PDF, text, Markdown) in backend/data/knowledge/ are cut into passages
that keep their page number. Search works in two ways and merges them:

  * keywords (BM25), always available, offline, no cost;
  * meaning (OpenAI embeddings), used only when a key is set. If the call fails the
    search quietly continues with keywords and says why in status().

Nothing in here raises on a bad file or a failed API call. A file that cannot be
read is reported in `warnings`, and the rest keeps working.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .openai_client import OpenAIClient, OpenAIError

log = logging.getLogger("cockpit.knowledge")

KINDS = {".pdf": "pdf", ".txt": "text", ".md": "markdown"}
MAX_BYTES = 60 * 1024 * 1024
CHUNK_CHARS = 900
OVERLAP_CHARS = 160
MIN_CHARS = 30
EMBED_BATCH = 96
RRF_K = 60

_STOP = frozenset("""
a an and are as at be but by can do does for from has have how i if in into is it its of on or so that the their
then there these this to was were what when where which while who why will with you your not no than also may must
der die das den dem des ein eine einer eines einem einen und oder aber ist sind war wird werden wurde nicht mit von
zu zur zum im in am auf für bei nach aus als auch wie was wenn dass daß sich es sie er wir ihr
""".split())


class KnowledgeError(ValueError):
    """A manual could not be added. The message says what to do."""


# -- reading files ------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    uid: str
    doc: str
    page: int | None
    text: str
    section: str = ""

    def public(self, score: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"uid": self.uid, "doc": self.doc, "page": self.page,
                               "section": self.section, "text": self.text}
        if score is not None:
            out["score"] = round(score, 3)
        return out


@dataclass(frozen=True)
class ParsedDocument:
    name: str
    kind: str
    pages: int
    passages: tuple[Passage, ...]
    size: int

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "pages": self.pages,
                "passages": len(self.passages), "bytes": self.size}


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float
    via: str


def clean_name(name: str) -> str:
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    base = re.sub(r"[^\w.\- ()]", "_", base)[:120].strip(" .")
    if Path(base).suffix.lower() not in KINDS:
        raise KnowledgeError("Only PDF, text (.txt) and Markdown (.md) files can be added as manuals.")
    return base


_LEADER = re.compile(r"\.{5,}\s*\d+\s*$")


def is_contents_page(text: str) -> bool:
    """A table of contents lists every heading of the manual, so it would answer every question and say nothing."""
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) >= 6 and sum(bool(_LEADER.search(line)) for line in lines) / len(lines) >= 0.4


def _normalise_page(text: str) -> str:
    text = text.replace("\r", "").replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\.{5,}", " ", text)          # dot leaders of a table of contents
    text = re.sub(r"[ \t\f\v]+", " ", text)
    paragraphs = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def _units(paragraph: str, limit: int) -> list[str]:
    units: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            units.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            units.append(sentence)
    return units


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Pack sentences into passages of about `size` characters; each one starts with the tail of the last."""
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        units.extend(_units(paragraph, size))
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in units:
        if current and length + 1 + len(unit) > size:
            chunks.append(" ".join(current))
            tail: list[str] = []
            tail_len = 0
            for earlier in reversed(current):
                if tail_len + len(earlier) + 1 > overlap:
                    break
                tail.insert(0, earlier)
                tail_len += len(earlier) + 1
            current, length = tail, tail_len
        current.append(unit)
        length += len(unit) + 1
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c) >= MIN_CHARS]


def _passage(doc: str, page: int | None, text: str, section: str) -> Passage:
    uid = hashlib.sha1(f"{doc}|{page}|{section}|{text}".encode("utf-8")).hexdigest()[:12]
    return Passage(uid=uid, doc=doc, page=page, text=text, section=section)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _read_pdf(raw: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise KnowledgeError("PDF support needs the pypdf package. Run: pip install pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # noqa: BLE001 - any failure to open means it stays locked
                unlocked = 0
            if not unlocked:
                raise KnowledgeError("This PDF is password protected. Save an unprotected copy and add that.")
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page must not lose the whole manual
                pages.append("")
        return pages
    except KnowledgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise KnowledgeError(f"This PDF could not be read: {exc}") from exc


def parse_document(name: str, raw: bytes) -> ParsedDocument:
    """Turn one file into passages. Pure: it touches no state, so it can run in a worker thread."""
    clean = clean_name(name)
    if not raw:
        raise KnowledgeError("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise KnowledgeError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    kind = KINDS[Path(clean).suffix.lower()]
    passages: list[Passage] = []
    if kind == "pdf":
        page_texts = _read_pdf(raw)
        for number, text in enumerate(page_texts, 1):
            if is_contents_page(text):
                continue
            for chunk in split_text(_normalise_page(text)):
                passages.append(_passage(clean, number, chunk, ""))
        pages = len(page_texts)
    elif kind == "markdown":
        section, body = "", []

        def flush() -> None:
            for chunk in split_text(_normalise_page("\n".join(body))):
                passages.append(_passage(clean, None, chunk, section))

        for line in _decode(raw).splitlines():
            heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading:
                flush()
                section, body = heading.group(1).strip(), []
            else:
                body.append(line)
        flush()
        pages = 0
    else:
        parts = _decode(raw).split("\f")
        for number, text in enumerate(parts, 1):
            for chunk in split_text(_normalise_page(text)):
                passages.append(_passage(clean, number if len(parts) > 1 else None, chunk, ""))
        pages = len(parts) if len(parts) > 1 else 0
    if not passages:
        hint = " It may be a scan without text. Use a version that has selectable text." if kind == "pdf" else ""
        raise KnowledgeError(f"No readable text was found in {clean}.{hint}")
    return ParsedDocument(name=clean, kind=kind, pages=pages, passages=tuple(passages), size=len(raw))


# -- keyword search -----------------------------------------------------------------


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"\w+", text.lower()) if len(w) > 1 and w not in _STOP]


class _Bm25:
    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.counts = [Counter(tokens) for tokens in documents]
        self.lengths = [len(tokens) for tokens in documents]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        frequency: Counter[str] = Counter()
        for counts in self.counts:
            frequency.update(counts.keys())
        n = len(documents)
        self.idf = {t: math.log(1.0 + (n - df + 0.5) / (df + 0.5)) for t, df in frequency.items()}

    def rank(self, query: list[str], top: int = 30) -> list[tuple[int, float]]:
        scored: list[tuple[int, float]] = []
        terms = [t for t in set(query) if t in self.idf]
        if not terms:
            return scored
        for index, counts in enumerate(self.counts):
            score = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / (self.avg or 1.0))
            for term in terms:
                tf = counts.get(term, 0)
                if tf:
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                scored.append((index, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top]


# -- the knowledge base -------------------------------------------------------------


class KnowledgeBase:
    def __init__(self, folder: Path, client: OpenAIClient | None = None,
                 embed_model: str = "text-embedding-3-small") -> None:
        self.folder = Path(folder)
        self.client = client
        self.embed_model = embed_model
        self.documents: dict[str, ParsedDocument] = {}
        self.warnings: list[str] = []
        self.passages: list[Passage] = []
        self._bm25: _Bm25 | None = None
        self._vectors: dict[str, np.ndarray] = {}
        self._matrix: tuple[tuple[str, ...], np.ndarray] | None = None
        self._digest = ""
        self.embed_error: str | None = None
        self._embed_retry_at = 0.0
        self._lock = asyncio.Lock()
        self._load_vectors()
        self.load_folder()

    # -- documents -----------------------------------------------------------------

    @property
    def cache_dir(self) -> Path:
        return self.folder / ".cache"

    @property
    def digest(self) -> str:
        return self._digest

    def load_folder(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.documents.clear()
        self.warnings = []
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.name.startswith((".", "README", "readme")):
                continue
            if path.suffix.lower() not in KINDS:
                continue
            try:
                raw = path.read_bytes()
                self._put(self._parse_cached(path.name, raw))
            except (KnowledgeError, OSError) as exc:
                self.warnings.append(f"{path.name}: {exc}")
                log.warning("manual %s not loaded: %s", path.name, exc)
        self._reindex()

    def _parse_cached(self, name: str, raw: bytes) -> ParsedDocument:
        """Reading a long PDF takes seconds, so the passages are kept next to the file."""
        key = hashlib.sha1(raw).hexdigest()[:12]
        cache = self.cache_dir / f"{clean_name(name)}.{key}.json"
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            passages = tuple(_passage(data["name"], p[0], p[2], p[1]) for p in data["passages"])
            if passages and data.get("version") == 1:
                return ParsedDocument(data["name"], data["kind"], int(data["pages"]), passages, len(raw))
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass
        log.info("reading manual %s", name)
        parsed = parse_document(name, raw)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for old in self.cache_dir.glob(f"{parsed.name}.*.json"):
                old.unlink(missing_ok=True)
            cache.write_text(json.dumps({"version": 1, "name": parsed.name, "kind": parsed.kind,
                                         "pages": parsed.pages,
                                         "passages": [[p.page, p.section, p.text] for p in parsed.passages]}),
                             encoding="utf-8")
        except OSError as exc:
            log.warning("could not cache %s: %s", name, exc)
        return parsed

    def _put(self, parsed: ParsedDocument) -> None:
        self.documents[parsed.name] = parsed

    def _reindex(self) -> None:
        self.passages = [p for doc in self.documents.values() for p in doc.passages]
        self._bm25 = _Bm25([tokenize(f"{p.section} {p.text}") for p in self.passages]) if self.passages else None
        self._matrix = None
        joined = "|".join(sorted(p.uid for p in self.passages))
        self._digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12] if joined else ""

    def add_parsed(self, parsed: ParsedDocument, raw: bytes) -> dict[str, Any]:
        """Store a file that parse_document already read. Call this on the event loop thread."""
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / parsed.name).write_bytes(raw)
        self._put(parsed)
        self._reindex()
        self.warnings = [w for w in self.warnings if not w.startswith(parsed.name + ":")]
        return parsed.public()

    def add(self, name: str, raw: bytes) -> dict[str, Any]:
        return self.add_parsed(parse_document(name, raw), raw)

    def remove(self, name: str) -> bool:
        clean = clean_name(name)
        found = clean in self.documents
        (self.folder / clean).unlink(missing_ok=True)
        for old in self.cache_dir.glob(f"{clean}.*.json"):
            old.unlink(missing_ok=True)
        self.documents.pop(clean, None)
        self._reindex()
        return found

    # -- search ----------------------------------------------------------------------

    async def search(self, query: str, k: int = 5) -> list[Hit]:
        query = " ".join(str(query or "").split())[:500]
        if not query or not self._bm25:
            return []
        k = max(1, min(int(k), 12))
        lexical = self._bm25.rank(tokenize(query))
        dense: list[tuple[int, float]] = []
        if await self._embeddings_ready():
            dense = await self._dense(query)
        if not dense:
            top = lexical[0][1] if lexical else 1.0
            return [Hit(self.passages[i], s / top, "keywords") for i, s in lexical[:k]]
        fused: dict[int, float] = {}
        for ranking in (lexical, dense):
            for rank, (index, _) in enumerate(ranking):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:k]
        best = order[0][1] if order else 1.0
        return [Hit(self.passages[i], s / best, "meaning and keywords") for i, s in order]

    async def search_many(self, queries: list[str], k: int = 5) -> list[Hit]:
        """Several focused questions at once: a page that ranks well for any of them comes up.

        Each question is searched on its own (keywords, plus meaning when it is available), then the rankings
        are merged. This finds the short, precise paragraph of a manual that one long keyword list would bury."""
        runs = [await self.search(query, k=12) for query in queries if query and query.strip()]
        fused: dict[str, float] = {}
        best: dict[str, Hit] = {}
        for hits in runs:
            for rank, hit in enumerate(hits):
                uid = hit.passage.uid
                fused[uid] = fused.get(uid, 0.0) + 1.0 / (RRF_K + rank + 1)
                best.setdefault(uid, hit)
        order = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)[:max(1, min(int(k), 12))]
        top = order[0][1] if order else 1.0
        return [Hit(best[uid].passage, score / top, best[uid].via) for uid, score in order]

    async def _dense(self, query: str) -> list[tuple[int, float]]:
        assert self.client is not None
        try:
            data = await self.client.request("POST", "/embeddings",
                                             {"model": self.embed_model, "input": [query]}, timeout=30.0)
            vector = np.asarray(data["data"][0]["embedding"], dtype=np.float32)
        except (OpenAIError, KeyError, IndexError, TypeError, ValueError) as exc:
            self.embed_error = str(exc)
            self._embed_retry_at = time.time() + 60
            return []
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return []
        uids = tuple(p.uid for p in self.passages)
        if self._matrix is None or self._matrix[0] != uids:
            if any(u not in self._vectors for u in uids):
                return []
            self._matrix = (uids, np.vstack([self._vectors[u] for u in uids]))
        scores = self._matrix[1] @ (vector / norm)
        order = np.argsort(-scores)[:30]
        return [(int(i), float(scores[i])) for i in order if scores[i] > 0]

    # -- embeddings --------------------------------------------------------------------

    async def _embeddings_ready(self) -> bool:
        if not (self.client and self.client.enabled) or time.time() < self._embed_retry_at:
            return False
        if all(p.uid in self._vectors for p in self.passages):
            return True
        async with self._lock:
            missing = [p for p in self.passages if p.uid not in self._vectors]
            try:
                for start in range(0, len(missing), EMBED_BATCH):
                    batch = missing[start:start + EMBED_BATCH]
                    data = await self.client.request(
                        "POST", "/embeddings",
                        {"model": self.embed_model, "input": [p.text[:6000] for p in batch]}, timeout=90.0)
                    items = sorted(data["data"], key=lambda item: item["index"])
                    if len(items) != len(batch):
                        raise OpenAIError("OpenAI returned a different number of embeddings than passages.")
                    for passage, item in zip(batch, items):
                        vector = np.asarray(item["embedding"], dtype=np.float32)
                        norm = float(np.linalg.norm(vector))
                        self._vectors[passage.uid] = vector / norm if norm else vector
                self.embed_error = None
            except (OpenAIError, KeyError, TypeError, ValueError) as exc:
                self.embed_error = str(exc)
                self._embed_retry_at = time.time() + 60
                log.warning("embeddings not available: %s", exc)
                self._save_vectors()
                return False
            self._save_vectors()
        return True

    async def warm(self) -> None:
        """Embed every passage in the background so the first question is not slow."""
        try:
            await self._embeddings_ready()
        except Exception:  # noqa: BLE001 - a background helper must never take the app down
            log.exception("embedding warm-up failed")

    def _vector_file(self) -> Path:
        return self.cache_dir / "embeddings.npz"

    def _save_vectors(self) -> None:
        if not self._vectors:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            uids = sorted(self._vectors)
            np.savez(self._vector_file(), model=np.array(self.embed_model), uids=np.array(uids),
                     matrix=np.vstack([self._vectors[u] for u in uids]))
        except OSError as exc:
            log.warning("could not save embeddings: %s", exc)

    def _load_vectors(self) -> None:
        try:
            with np.load(self._vector_file(), allow_pickle=False) as data:
                if str(data["model"]) != self.embed_model:
                    return
                self._vectors = {str(u): row for u, row in zip(data["uids"], data["matrix"])}
        except (OSError, ValueError, KeyError):
            self._vectors = {}

    # -- status --------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        ready = sum(1 for p in self.passages if p.uid in self._vectors)
        return {
            "documents": [doc.public() for doc in self.documents.values()],
            "passages": len(self.passages),
            "warnings": list(self.warnings),
            "digest": self._digest,
            "embeddings": {"enabled": bool(self.client and self.client.enabled), "model": self.embed_model,
                           "ready": ready, "error": self.embed_error},
        }
