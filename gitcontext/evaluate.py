from __future__ import annotations

import json
import statistics
from pathlib import Path

from .core import Index


def matches(row: dict, gold: dict) -> bool:
    return all(row.get(key) == value for key, value in gold.items())


def evaluate(index: Index, questions_path: Path) -> dict:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    details = []
    for item in questions:
        result = index.search(item["question"], k=5)
        gold = item["gold"]
        hits = [rank for rank, row in enumerate(result["results"], 1)
                if any(matches(row, target) for target in gold)]
        details.append({"id": item["id"], "question": item["question"],
                        "type": item["type"], "hit_at_5": bool(hits),
                        "first_hit_rank": min(hits) if hits else None,
                        "retrieval_ms": result["retrieval_ms"],
                        "returned": [{"kind": r["kind"], "path": r["path"],
                                      "symbol": r["symbol"], "url": r["url"]}
                                     for r in result["results"]]})
    times = sorted(d["retrieval_ms"] for d in details)
    by_type = {}
    for kind in sorted({d["type"] for d in details}):
        subset = [d for d in details if d["type"] == kind]
        by_type[kind] = {"n": len(subset), "recall_at_5": round(sum(d["hit_at_5"] for d in subset) / len(subset), 3)}
    summary = {"repo": index.state("repo"), "indexed_commit": index.state("commit"),
               "questions": len(details), "recall_at_5": round(sum(d["hit_at_5"] for d in details) / len(details), 3),
               "median_retrieval_ms": round(statistics.median(times), 1),
               "p95_retrieval_ms": times[min(len(times) - 1, int(0.95 * len(times)))],
               "by_type": by_type,
               "scope": "source-location retrieval only; answer correctness and citation support require human review"}
    return {"summary": summary, "details": details}
