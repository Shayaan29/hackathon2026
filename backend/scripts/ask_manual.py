"""Ask the sensor manuals from the command line, without starting the cockpit.

    .venv/bin/python scripts/ask_manual.py "linearity of the ILD5500-50"
    .venv/bin/python scripts/ask_manual.py --search "pin assignment" -k 5
    .venv/bin/python scripts/ask_manual.py --rebuild --list

Uses the same folder, cache and OpenAI settings as the cockpit (backend/.env).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings  # noqa: E402
from app.knowledge import KnowledgeBase, KnowledgeError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the sensor manuals in backend/knowledge.")
    parser.add_argument("question", nargs="?", help="the question or search words")
    parser.add_argument("--search", action="store_true", help="only list the matching passages")
    parser.add_argument("-k", type=int, default=5, help="passages to use (default 5)")
    parser.add_argument("--document", help="search only manuals whose file name or title contains this")
    parser.add_argument("--list", action="store_true", help="list the indexed manuals")
    parser.add_argument("--rebuild", action="store_true", help="read the PDFs again, ignoring the cache")
    args = parser.parse_args()

    settings = load_settings()
    kb = KnowledgeBase(settings.knowledge_dir, settings.openai_api_key,
                       settings.embedding_model, settings.answer_model)
    kb.ensure(force=args.rebuild)
    if args.list or not args.question:
        info = kb.documents()
        print(f"{len(info['documents'])} manual(s) in {settings.knowledge_dir} ({info['search']} search)")
        for doc in info["documents"]:
            print(f"  {doc['name']}: {doc['title']}, {doc['pages']} pages, {doc['passages']} passages")
        for name, error in info["errors"].items():
            print(f"  could not read {name}: {error}")
        if not args.question:
            return 0
    try:
        if args.search:
            result = kb.search(args.question, args.k, args.document)
            if not result["found"]:
                print(result.get("message", "Nothing found."))
            for n, hit in enumerate(result["hits"], start=1):
                print(f"\n[S{n}] {hit['citation']}  (keyword {hit['keyword_score']}, "
                      f"meaning {hit['semantic_score']})\n{hit['text']}")
                for note in hit["footnotes"]:
                    print(f"    {note}")
            return 0
        result = kb.answer(args.question, args.k, args.document)
    except KnowledgeError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(result["answer"])
    print(f"\n({result['mode']} answer, {result['search']} search)")
    for n, hit in enumerate(result["sources"], start=1):
        print(f"  S{n}: {hit['doc_title']} | {hit['citation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
