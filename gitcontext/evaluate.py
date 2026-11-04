from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

from .answer import LocalGenerator, select_answer_evidence
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


def summarize_answer_evaluation(details: list[dict], index: Index) -> dict:
    """Summarize objective answer-pipeline signals without claiming correctness."""
    times = sorted(item["answer_ms"] for item in details)
    modes = {mode: sum(item["answer"]["mode"] == mode for item in details)
             for mode in sorted({item["answer"]["mode"] for item in details})}
    return {
        "repo": index.state("repo"),
        "indexed_commit": index.state("commit"),
        "questions": len(details),
        "modes": modes,
        "generated_rate": round(modes.get("generated", 0) / len(details), 3),
        "gold_in_generation_context_rate": round(
            sum(item["gold_in_generation_context"] for item in details) / len(details), 3),
        "median_answer_ms": round(statistics.median(times), 1),
        "p95_answer_ms": times[min(len(times) - 1, int(0.95 * len(times)))],
        "scope": (
            "objective pipeline signals only; preserved answers and citations require human review "
            "for correctness and claim-level support"
        ),
    }


async def evaluate_answers(index: Index, questions_path: Path, limit: int | None = None) -> dict:
    """Run the local answer pipeline over the fixed questions with one model process."""
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if limit is not None:
        questions = questions[:limit]
    if not questions:
        raise ValueError("Answer evaluation requires at least one question.")

    details = []
    generator = LocalGenerator()
    try:
        await generator.start()
        for item in questions:
            result = index.search(item["question"], k=5)
            evidence = select_answer_evidence(result["results"])
            started = time.monotonic()
            answer = await generator.answer(
                item["question"], evidence, include_diagnostics=True)
            answer_ms = round((time.monotonic() - started) * 1000, 1)
            gold = item["gold"]
            details.append({
                "id": item["id"],
                "question": item["question"],
                "type": item["type"],
                "gold_in_generation_context": any(
                    any(matches(row, target) for target in gold) for row in evidence
                ),
                "retrieval_ms": result["retrieval_ms"],
                "answer_ms": answer_ms,
                "answer": answer,
                "generation_context": [
                    {"kind": row["kind"], "path": row["path"], "symbol": row["symbol"],
                     "url": row["url"]}
                    for row in evidence
                ],
            })
    finally:
        await generator.close()
    return {"summary": summarize_answer_evaluation(details, index), "details": details}
