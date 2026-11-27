"""Recommendations for one incident: the AI proposes, the rules verify.

The chain for an incident:

  1. Facts. Everything the analysis measured about the incident, as short numbered
     statements (F1, F2, ...). No model is involved.
  2. Passages. The manual passages that match the kind of incident (knowledge.py).
  3. Draft. A language model writes at most a few recommendations. Each one has to
     name the facts it rests on and, when it relies on a manual, quote the passage.
  4. Verification, in plain code. A recommendation is dropped unless it cites a real
     fact, every number in it appears in the measured facts of the incident or in its
     quote, every quote is word for word in the passage it names, and it does not state
     a cause as certain. Sensor names and incident ids are labels, not numbers.
  5. Fallback. If there is no key, the call fails or nothing survives, the checks
     from config/playbook.yaml are shown instead and the screen says so.

What reaches the screen is therefore either checked or labelled as rule based. The
number of dropped drafts is part of the result and is shown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from .config import Settings
from .knowledge import KnowledgeBase, Passage
from .openai_client import OpenAIClient, OpenAIError

log = logging.getLogger("cockpit.recommend")

DEFAULT_TEXT_MODEL = "gpt-4.1-mini"
PASSAGES = 6
MAX_RECOMMENDATIONS = 4
PREFETCH_LIMIT = 8
FALLBACK_TTL_S = 60.0

KIND_TERMS = {   # English first, then German, so that a manual in either language is found by plain keywords too
    "vibration": "vibration oscillation frequency mounting fixture clamp resonance stable installation cable "
                 "Schwingung Vibration Frequenz Montage Befestigung Halterung Resonanz Kabel",
    "noise": "noise resolution averaging filter signal quality measuring rate smoothing "
             "Rauschen Auflösung Mittelwert Mittelung Filter Messrate Signalqualität Glättung",
    "drift": "temperature drift thermal warm-up warm up stability compensation expansion "
             "Temperatur Drift Temperaturdrift Aufwärmzeit Stabilität Kompensation Ausdehnung",
    "out_of_range": "measuring range distance mounting midrange target out of range "
                    "Messbereich Abstand Montage Messobjekt Mittenabstand",
    "invalid": "invalid measurement no target error value surface reflectivity tilt angle contamination "
               "ungültig Messwert Fehler Messobjekt Oberfläche Reflexion Neigung Verschmutzung",
}
KIND_FOCUS = {   # one short, natural question per kind, in the words manuals use for it (English and German)
    "vibration": "mechanical vibrations of the sensor or the target: stable, vibration-damped mounting. Schwingungen, stabile Montage",
    "noise": "noise and measuring accuracy: averaging, measuring rate, ambient light, surface. Rauschen, Mittelwert, Messrate",
    "drift": "thermal influences: warm-up time and temperature changes of the sensor and its holder. Temperaturdrift, Aufwärmzeit",
    "out_of_range": "target outside the measuring range: start and end of the measuring range, extended measuring range. Messbereich",
    "invalid": "no valid measurement: reflectivity of the target surface, tilt angle, ambient light, dirty windows. ungültiger Messwert",
}
KIND_WORDS = {"vibration": "vibration", "noise": "increased noise", "drift": "slow drift",
              "out_of_range": "value outside the tolerance", "invalid": "invalid readings"}

_NUMBER = re.compile(r"(?<![\w.])[-+−]?\d+(?:[.,]\d+)?")
_THOUSANDS = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_CERTAIN = re.compile(r"\b(is|was) caused by\b|\bthe (root )?cause (is|was)\b|\bproves?\b|\bwithout (a )?doubt\b"
                      r"|\bdefinitely\b|\bcertainly\b", re.I)
_CITES_MANUAL = re.compile(r"\b(manual|datasheet|data sheet|handbuch|operating instructions|page \d+)\b", re.I)
_GPT = re.compile(r"^gpt-(\d+(?:\.\d+)?)(-mini)?$")


# -- facts ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    id: str
    text: str


def _num(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def build_facts(incident: dict[str, Any]) -> list[Fact]:
    """What the analysis measured, as short statements the draft has to cite."""
    lines = [f"Incident {incident['id']}: {incident['title']}. Severity: {incident['severity']}."]
    if incident.get("summary"):
        lines.append(f"Summary: {incident['summary']}")
    for check in incident.get("checks", []):
        line = f"{check['label']}: {check['value']}"
        if check.get("detail"):
            line += f" ({check['detail']})"
        lines.append(line)
    numbers = incident.get("numbers") or {}
    shown = [f"{key.replace('_', ' ')} {_num(value)}" for key, value in numbers.items()
             if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if shown:
        unit = numbers.get("unit")
        lines.append("Key numbers: " + ", ".join(shown) + (f" (unit {unit})" if unit else ""))
    if incident.get("forecast_min") is not None:
        lines.append(f"Forecast: the tolerance limit is reached in about {_num(incident['forecast_min'])} minutes.")
    rpm = (incident.get("source") or {}).get("part_rpm")
    if rpm:
        lines.append(f"Part speed entered for this recording: {_num(rpm)} rpm.")
    if incident.get("unknown"):
        lines.append("Not known from the file: " + "; ".join(str(u) for u in incident["unknown"]) + ".")
    return [Fact(f"F{n}", text) for n, text in enumerate(lines, 1)]


def build_queries(incident: dict[str, Any]) -> list[str]:
    """The questions put to the manuals: one focused sentence per kind of alert, then the broad keyword list."""
    kinds = [k for k in (incident.get("kinds") or [incident.get("kind")]) if k]
    return [KIND_FOCUS[k] for k in kinds if k in KIND_FOCUS] + [build_query(incident)]


def build_query(incident: dict[str, Any]) -> str:
    kinds = incident.get("kinds") or [incident.get("kind")]
    words = " ".join(KIND_WORDS.get(k, str(k)) for k in kinds if k)
    terms = " ".join(KIND_TERMS.get(k, "") for k in kinds if k)
    return f"{words} on a displacement sensor, what to check: {terms}".strip()


# -- the draft ----------------------------------------------------------------------------

SYSTEM = """You help a technician decide what to check after an anomaly was found in sensor measurement data.
You get FACTS measured in the data (ids F1, F2, ...) and PASSAGES from the sensor manuals (ids P1, P2, ...).

Write up to {limit} recommendations. Each one is something a person can check or do next, in the order to do it.

Rules:
1. Every recommendation names at least one fact id in "facts". Use only ids that exist.
2. A number may appear in "action" or "why" only if it appears in a cited fact or in your quote. Never invent a
   number, limit, speed, distance, time or percentage. Copy units as written.
3. When a passage supports the recommendation, give its id and a quote copied word for word from that passage,
   6 to 40 words, in "sources", and set "basis" to "manual". If no passage supports it, give no source, set
   "basis" to "data" and do not mention a manual or a page.
4. These are checks to carry out, not diagnoses. Never state a cause as certain. Use verbs such as check,
   compare, confirm, repeat.
5. If a fact says something is not known, you may recommend finding it out. Do not guess it.
6. "action" is one sentence. "why" is one sentence that links the facts to the quote when there is one.
7. Write "action" and "why" in {language}. Quotes stay in the language of the passage.
8. Answer with JSON only, in this shape:
{{"recommendations":[{{"action":"...","why":"...","facts":["F2"],"basis":"manual","sources":[{{"passage":"P1","quote":"..."}}]}}]}}"""


def build_messages(facts: list[Fact], passages: list[Passage], language: str) -> list[dict[str, str]]:
    fact_text = "\n".join(f"{f.id}: {f.text}" for f in facts)
    if passages:
        passage_text = "\n\n".join(
            f"[P{n}] {p.doc}" + (f", page {p.page}" if p.page else "") + (f", {p.section}" if p.section else "")
            + f"\n{p.text}" for n, p in enumerate(passages, 1))
    else:
        passage_text = "(no manual is loaded: every recommendation has basis \"data\" and no sources)"
    user = f"FACTS\n{fact_text}\n\nPASSAGES\n{passage_text}\n\nWrite the recommendations as JSON."
    return [{"role": "system", "content": SYSTEM.format(limit=MAX_RECOMMENDATIONS, language=language)},
            {"role": "user", "content": user}]


def parse_json(content: str) -> Any:
    text = (content or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


# -- the verifier -----------------------------------------------------------------------


def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"[‘’´`]", "'", text)
    text = re.sub(r"[“”„]", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in(quote: str, passage_text: str) -> bool:
    """The quote is in the passage word for word. `...` may stand for left-out words."""
    squashed = _squash(quote)
    if not 12 <= len(squashed) <= 400:
        return False
    haystack = _squash(passage_text)
    fragments = [f.strip(" .,;:") for f in re.split(r"\.\.\.|…|\[\.\.\.\]", squashed)]
    fragments = [f for f in fragments if f]
    return bool(fragments) and all(len(f) >= 8 and f in haystack for f in fragments)


def _decimal(token: str) -> tuple[Decimal, int]:
    clean = token.replace("+", "").replace("-", "").replace("−", "")
    if _THOUSANDS.match(clean):
        clean = clean.replace(",", "")
    clean = clean.replace(",", ".")
    decimals = len(clean.split(".")[1]) if "." in clean else 0
    return Decimal(clean), decimals


def numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(text)


def _grounded(token: str, allowed: list[Decimal]) -> bool:
    """The number equals a number from the evidence, rounded to the digits the text shows."""
    value, decimals = _decimal(token)
    step = Decimal(1).scaleb(-decimals)
    return any(a.quantize(step, rounding=ROUND_HALF_UP) == value for a in allowed)


def _allowed(texts: list[str]) -> list[Decimal]:
    out: list[Decimal] = []
    for text in texts:
        for token in numbers_in(text):
            try:
                out.append(_decimal(token)[0])
            except Exception:  # noqa: BLE001 - an odd token is simply not usable evidence
                continue
    return out


def _clean(value: Any) -> str:
    return re.sub(r"^\s*\d+[.)]\s+", "", " ".join(str(value or "").split()))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _ignore_pattern(names: Iterable[str]) -> re.Pattern[str]:
    """Names such as "Sensor 2" and ids such as "INC-003" contain digits that are labels, not measurements."""
    parts = [re.escape(n) for n in sorted({n for n in names if n}, key=len, reverse=True)] + [r"INC-\d+"]
    return re.compile("|".join(parts), re.I)


def _check_one(raw: Any, facts: dict[str, Fact], passages: dict[str, Passage],
               ignore: re.Pattern[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    action, why = _clean(raw.get("action")), _clean(raw.get("why"))
    if not action or len(action) > 320 or len(why) > 420:
        return None
    cited = [str(i).strip().upper() for i in _as_list(raw.get("facts"))]
    cited = [i for i in dict.fromkeys(cited) if i in facts]
    if not cited:
        return None
    claimed = _as_list(raw.get("sources"))
    sources: list[dict[str, Any]] = []
    for item in claimed:
        if not isinstance(item, dict):
            continue
        passage = passages.get(str(item.get("passage", "")).strip().upper())
        quote = _clean(item.get("quote"))
        if passage is not None and quote_in(quote, passage.text):
            sources.append({"doc": passage.doc, "page": passage.page, "section": passage.section, "quote": quote})
    if claimed and not sources:
        return None
    text = f"{action} {why}"
    if not sources and _CITES_MANUAL.search(text):
        return None
    if _CERTAIN.search(text):
        return None
    # A number may come from anything that was measured for this incident, or from a verified quote.
    allowed = _allowed([f.text for f in facts.values()] + [s["quote"] for s in sources])
    for token in numbers_in(ignore.sub(" ", text)):
        try:
            if not _grounded(token, allowed):
                return None
        except Exception:  # noqa: BLE001
            return None
    return {"action": action, "why": why, "basis": "manual" if sources else "data", "facts": cited,
            "sources": sources}


def verify(draft: Any, facts: list[Fact], passages: list[Passage], limit: int = MAX_RECOMMENDATIONS,
           names: Iterable[str] = ()) -> tuple[list[dict[str, Any]], int]:
    """Keep what checks out. Returns the recommendations and how many drafts were dropped."""
    items = draft.get("recommendations") if isinstance(draft, dict) else None
    if not isinstance(items, list):
        return [], 0
    fact_by_id = {f.id: f for f in facts}
    passage_by_id = {f"P{n}": p for n, p in enumerate(passages, 1)}
    ignore = _ignore_pattern(names)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for raw in items:
        checked = _check_one(raw, fact_by_id, passage_by_id, ignore)
        if checked is None:
            dropped += 1
            continue
        key = _squash(checked["action"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(checked)
    kept = kept[:limit]
    for n, rec in enumerate(kept, 1):
        rec["id"] = f"R{n}"
    return kept, dropped


# -- model choice and the fallback ---------------------------------------------------------


def choose_model(ids: list[str]) -> str | None:
    """Prefer the newest "-mini" text model: fast and cheap enough, since the checker verifies the output."""
    best: tuple[tuple[bool, float], str] | None = None
    for model_id in ids:
        match = _GPT.match(model_id)
        if match:
            rank = (bool(match.group(2)), float(match.group(1)))
            if best is None or rank > best[0]:
                best = (rank, model_id)
    return best[1] if best else None


def rules_result(incident: dict[str, Any], note: str, facts: list[Fact], dropped: int = 0) -> dict[str, Any]:
    recs = [{"id": f"R{n}", "action": s["text"], "why": s["reason"], "basis": "rules", "facts": [], "sources": []}
            for n, s in enumerate(incident.get("suggestions", [])[:5], 1)]
    return {"status": "rules", "incident": incident["id"], "model": "", "note": note, "dropped": dropped,
            "recommendations": recs, "facts": [{"id": f.id, "text": f.text} for f in facts],
            "manuals": 0, "passages": 0, "seconds": 0.0, "generated": time.strftime("%H:%M:%S")}


# -- the service ----------------------------------------------------------------------------


class Recommender:
    def __init__(self, settings: Settings, kb: KnowledgeBase, client: OpenAIClient, engine: Any) -> None:
        self.settings = settings
        self.kb = kb
        self.client = client
        self.engine = engine
        self._model = ""
        self._cache: dict[str, dict[str, Any]] = {}
        self._fallbacks: dict[str, tuple[float, dict[str, Any]]] = {}
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._prefetch_task: asyncio.Task[None] | None = None

    @property
    def language(self) -> str:
        return "German" if self.settings.cockpit.language.lower().startswith("de") else "English"

    def key(self, incident: dict[str, Any]) -> str:
        source = incident.get("source") or {}
        return "|".join([incident["id"], str(source.get("digest", "")), self.kb.digest, self.language])

    def cached(self, incident: dict[str, Any]) -> dict[str, Any] | None:
        return self._cache.get(self.key(incident))

    def status(self) -> dict[str, Any]:
        return {"ai": self.client.enabled, "model": self.settings.text_model or self._model or "",
                "prefetching": bool(self._prefetch_task and not self._prefetch_task.done()),
                "cached": len(self._cache)}

    async def model(self) -> str:
        if self.settings.text_model:
            return self.settings.text_model
        if self._model:
            return self._model
        try:
            data = await self.client.request("GET", "/models", timeout=20.0)
            ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
        except OpenAIError as exc:
            log.warning("model list not available (%s); using %s", exc, DEFAULT_TEXT_MODEL)
            return DEFAULT_TEXT_MODEL
        self._model = choose_model(ids) or DEFAULT_TEXT_MODEL
        log.info("recommendations use the model %s", self._model)
        return self._model

    async def recommend(self, incident: dict[str, Any], force: bool = False) -> dict[str, Any]:
        key = self.key(incident)
        if not force:
            if key in self._cache:
                return self._cache[key]
            held = self._fallbacks.get(key)
            if held and held[0] > time.time():
                return held[1]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._compute(incident, key))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def recommend_within(self, incident: dict[str, Any], seconds: float) -> dict[str, Any] | None:
        """For the voice copilot: the result if it is ready in time, otherwise None while the work goes on."""
        try:
            return await asyncio.wait_for(self.recommend(incident), timeout=seconds)
        except asyncio.TimeoutError:
            return None

    async def _compute(self, incident: dict[str, Any], key: str) -> dict[str, Any]:
        started = time.time()
        facts = build_facts(incident)
        try:
            result = await self._draft_and_verify(incident, facts)
        except Exception as exc:  # noqa: BLE001 - the incident view must always get an answer
            log.exception("recommendations failed")
            result = rules_result(incident, f"Unexpected error, rule based checks shown: {exc}", facts)
        result["seconds"] = round(time.time() - started, 1)
        if result["status"] == "ai":
            self._cache[key] = result
            self._fallbacks.pop(key, None)
        else:
            self._fallbacks[key] = (time.time() + FALLBACK_TTL_S, result)
        return result

    async def _draft_and_verify(self, incident: dict[str, Any], facts: list[Fact]) -> dict[str, Any]:
        if not self.client.enabled:
            return rules_result(incident, "The AI is off because no OpenAI key is set. Rule based checks shown.",
                                facts)
        hits = await self.kb.search_many(build_queries(incident), k=PASSAGES)
        passages = [h.passage for h in hits]
        messages = build_messages(facts, passages, self.language)
        try:
            model = await self.model()
            content = await self._ask(model, messages)
            draft = parse_json(content)
        except OpenAIError as exc:
            return rules_result(incident, f"The AI call failed, rule based checks shown. {exc}", facts)
        except ValueError:
            return rules_result(incident, "The AI answer could not be read, rule based checks shown.", facts)
        recs, dropped = verify(draft, facts, passages, names=[sensor.name for sensor in self.settings.sensors])
        if not recs:
            note = ("Every AI draft was dropped by the checker, rule based checks shown."
                    if dropped else "The AI returned no recommendations, rule based checks shown.")
            return rules_result(incident, note, facts, dropped)
        return {"status": "ai", "incident": incident["id"], "model": model, "note": "", "dropped": dropped,
                "recommendations": recs, "facts": [{"id": f.id, "text": f.text} for f in facts],
                "manuals": len(self.kb.documents), "passages": len(passages), "seconds": 0.0,
                "generated": time.strftime("%H:%M:%S")}

    async def _ask(self, model: str, messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages,
                                   "response_format": {"type": "json_object"}}
        try:
            data = await self.client.request("POST", "/chat/completions", payload, timeout=120.0)
        except OpenAIError as exc:
            if exc.status == 400 and "response_format" in str(exc):
                payload.pop("response_format")
                data = await self.client.request("POST", "/chat/completions", payload, timeout=120.0)
            else:
                raise
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenAIError("OpenAI answered without a message.") from exc
        if message.get("refusal"):
            raise OpenAIError("The model declined to answer.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OpenAIError("The model returned an empty answer.")
        return content

    # -- background preparation ---------------------------------------------------------

    def prefetch(self) -> None:
        """After a recording is analysed, prepare the recommendations so the first click is instant."""
        if not (self.client.enabled and self.settings.recommend_prefetch and self.kb.passages):
            return
        if self._prefetch_task and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = asyncio.ensure_future(self._prefetch())

    async def _prefetch(self) -> None:
        order = {"critical": 0, "warning": 1}
        items = sorted(self.engine.incidents.items, key=lambda i: order.get(i["severity"], 2))[:PREFETCH_LIMIT]
        for incident in items:
            try:
                await self.recommend(incident)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("prefetch of %s failed", incident.get("id"))
