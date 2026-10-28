from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .core import Index, collect_files, collect_history, collect_prs
from .evaluate import evaluate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = ROOT / "data" / "requests"
DEFAULT_INDEX = ROOT / "data" / "index"
DEFAULT_PRS = ROOT / "data" / "prs.json"


def fetch_prs(repo_name: str, output: Path, limit: int = 25) -> int:
    manifest = ROOT / "eval" / "pr_numbers.json"
    if repo_name == "psf/requests" and manifest.exists():
        numbers = json.loads(manifest.read_text(encoding="utf-8"))[:limit]
        selected = []
        for number in numbers:
            result = subprocess.run(
                ["gh", "pr", "view", str(number), "-R", repo_name, "--json",
                 "number,title,body,url,author,mergeCommit,comments,mergedAt"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
            selected.append(json.loads(result.stdout))
    else:
        query = ["gh", "pr", "list", "-R", repo_name, "--state", "merged",
                 "--limit", "200", "--json",
                 "number,title,body,url,author,mergeCommit,comments,mergedAt"]
        result = subprocess.run(query, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=True)
        items = json.loads(result.stdout)
        selected = [item for item in items if not item["title"].lower().startswith(("bump ", "update ", "deps:"))][:limit]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(selected, indent=2), encoding="utf-8")
    return len(selected)


def main() -> None:
    parser = argparse.ArgumentParser(description="GitContext public-repo demo")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch-prs", help="Cache recent merged GitHub PR discussions")
    fetch.add_argument("--repo", default="psf/requests")
    fetch.add_argument("--limit", type=int, default=25)
    build = sub.add_parser("index", help="Index a local public repository")
    build.add_argument("--repo-path", type=Path, default=DEFAULT_REPO)
    build.add_argument("--repo", default="psf/requests")
    build.add_argument("--history", type=int, default=100)
    ask = sub.add_parser("ask", help="Retrieve evidence for a question")
    ask.add_argument("question")
    ask.add_argument("--k", type=int, default=5)
    ask.add_argument("--json", action="store_true")
    bench = sub.add_parser("eval", help="Run the fixed retrieval evaluation")
    bench.add_argument("--questions", type=Path, default=ROOT / "eval" / "questions.json")
    bench.add_argument("--output", type=Path, default=ROOT / "eval" / "report.json")
    args = parser.parse_args()
    if args.command == "fetch-prs":
        print(f"Cached {fetch_prs(args.repo, DEFAULT_PRS, args.limit)} PRs at {DEFAULT_PRS}")
        return
    index = Index(DEFAULT_INDEX)
    try:
        if args.command == "index":
            commit, files = collect_files(args.repo_path, args.repo)
            history = collect_history(args.repo_path, args.repo, args.history)
            prs = collect_prs(args.repo, DEFAULT_PRS, commit)
            count = index.build([*files, *history, *prs], commit, args.repo)
            print(f"Indexed {count} chunks at {commit}: {len(files)} file, {len(history)} commit, {len(prs)} PR")
        elif args.command == "ask":
            result = index.search(args.question, args.k)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Indexed {result['repo']} at {result['indexed_commit']} | retrieval {result['retrieval_ms']} ms")
                print(f"Starting answer: {result['answer']['text']}")
                if result["answer"]["url"]:
                    print(result["answer"]["url"])
                for i, row in enumerate(result["results"], 1):
                    print(f"\n[{i}] {row['kind']} {row['path']} {row['symbol']}\n{row['url']}\n{row['text'][:500]}")
        elif args.command == "eval":
            report = evaluate(index, args.questions)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report["summary"], indent=2))
    finally:
        index.close()


if __name__ == "__main__":
    main()
