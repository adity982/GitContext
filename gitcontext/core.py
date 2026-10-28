from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models


MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION = "gitcontext"
SKIP_PARTS = {".git", ".venv", "node_modules", "vendor", "__pycache__"}
TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )
    return result.stdout.strip()


def sha_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, value))


def lexical_text(value: str) -> str:
    # Preserve exact code identifiers and also expose their parts to FTS.
    parts = TOKEN_PATTERN.findall(value)
    extra = []
    for part in parts:
        extra.extend(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", part))
        extra.extend(part.split("_"))
    return " ".join(parts + extra)


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    url: str
    commit: str
    author: str = ""
    pr: int | None = None

    @property
    def search_text(self) -> str:
        return f"{self.path} {self.symbol}\n{self.text}"


def make_chunk(repo_name: str, commit: str, kind: str, path: str, symbol: str,
               start_line: int, end_line: int, text: str, *, author: str = "",
               pr: int | None = None, url: str | None = None) -> Chunk:
    key = f"{repo_name}:{commit}:{kind}:{path}:{symbol}:{start_line}:{sha_id(text)}"
    if url is None:
        url = f"https://github.com/{repo_name}/blob/{commit}/{path}#L{start_line}-L{end_line}"
    return Chunk(sha_id(key), kind, path, symbol, start_line, end_line,
                 text, url, commit, author, pr)


def _python_chunks(repo_name: str, commit: str, path: str, source: str) -> list[Chunk]:
    lines = source.splitlines()
    chunks: list[Chunk] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _line_chunks(repo_name, commit, path, source)

    def add(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbol = f"{prefix}.{node.name}" if prefix else node.name
                start = min((d.lineno for d in node.decorator_list), default=node.lineno)
                end = node.end_lineno or node.lineno
                definition_lines = lines[start - 1:end]
                # Index a class header separately; methods get their own chunks.
                if isinstance(node, ast.ClassDef):
                    definition_lines = definition_lines[:min(12, len(definition_lines))]
                # Large functions are split at source-line boundaries, preserving symbol identity.
                for offset in range(0, len(definition_lines), 100):
                    piece = "\n".join(definition_lines[offset:offset + 100])
                    piece_start = start + offset
                    piece_end = min(end, piece_start + len(piece.splitlines()) - 1)
                    chunks.append(make_chunk(repo_name, commit, "code", path, symbol,
                                             piece_start, piece_end, piece))
                if isinstance(node, ast.ClassDef):
                    add(node.body, symbol)
    add(tree.body)
    # Cover module constants, imports, and files that have no parseable definitions.
    header = "\n".join(lines[:min(60, len(lines))])
    if header:
        chunks.append(make_chunk(repo_name, commit, "module", path, Path(path).stem,
                                 1, min(60, len(lines)), header))
    return chunks


def _line_chunks(repo_name: str, commit: str, path: str, source: str) -> list[Chunk]:
    lines = source.splitlines()
    return [make_chunk(repo_name, commit, "text", path, Path(path).name,
                       start + 1, min(start + 80, len(lines)),
                       "\n".join(lines[start:start + 80]))
            for start in range(0, len(lines), 80)]


def _document_chunks(repo_name: str, commit: str, path: str, source: str) -> list[Chunk]:
    lines = source.splitlines()
    boundaries = [0] + [i for i, line in enumerate(lines) if i and
                        re.match(r"^#{1,4}\s+", line)] + [len(lines)]
    chunks = []
    for left, right in zip(boundaries, boundaries[1:]):
        for start in range(left, right, 80):
            end = min(start + 80, right)
            text = "\n".join(lines[start:end]).strip()
            if text:
                chunks.append(make_chunk(repo_name, commit, "docs", path,
                                         lines[left].strip(" #")[:100], start + 1, end, text))
    return chunks


def collect_files(repo: Path, repo_name: str) -> tuple[str, list[Chunk]]:
    commit = git(repo, "rev-parse", "HEAD")
    files = git(repo, "ls-files").splitlines()
    chunks: list[Chunk] = []
    for path in files:
        normalized = path.replace("\\", "/")
        if any(part in SKIP_PARTS for part in Path(path).parts):
            continue
        if not (normalized.endswith((".py", ".md", ".rst")) or normalized == "README"):
            continue
        full = repo / path
        if not full.is_file() or full.stat().st_size > 250_000:
            continue
        source = full.read_text(encoding="utf-8", errors="replace")
        if normalized.endswith(".py"):
            chunks.extend(_python_chunks(repo_name, commit, normalized, source))
        elif normalized.endswith(".md"):
            chunks.extend(_document_chunks(repo_name, commit, normalized, source))
        else:
            chunks.extend(_line_chunks(repo_name, commit, normalized, source))
    return commit, chunks


def collect_history(repo: Path, repo_name: str, limit: int = 100) -> list[Chunk]:
    output = git(repo, "log", f"-{limit}", "--format=%H%x1f%an%x1f%s%x1f%b%x1e")
    chunks = []
    for record in output.split("\x1e"):
        fields = record.strip().split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, author, title, body = fields
        if title.lower().startswith(("bump ", "update lock-issues")):
            continue
        text = f"{title}\n{body}".strip()
        chunks.append(make_chunk(repo_name, sha, "commit", "", title, 0, 0,
                                 text, author=author,
                                 url=f"https://github.com/{repo_name}/commit/{sha}"))
    return chunks


def collect_prs(repo_name: str, path: Path, current_commit: str) -> list[Chunk]:
    if not path.exists():
        return []
    chunks = []
    for pr in json.loads(path.read_text(encoding="utf-8")):
        number = int(pr["number"])
        url = pr["url"]
        title = pr["title"]
        body = (pr.get("body") or "")[:6000]
        author = (pr.get("author") or {}).get("login", "")
        merge = (pr.get("mergeCommit") or {}).get("oid") or current_commit
        text = f"{title}\n{body}".strip()
        if text:
            chunks.append(make_chunk(repo_name, merge, "pr", f"PR#{number}", title,
                                     0, 0, text, author=author, pr=number, url=url))
        for i, comment in enumerate(pr.get("comments") or []):
            body = (comment.get("body") or "")[:3000]
            if body:
                chunks.append(make_chunk(repo_name, merge, "pr_comment", f"PR#{number}",
                                         f"comment-{i}", 0, 0, body,
                                         author=(comment.get("author") or {}).get("login", ""),
                                         pr=number, url=comment.get("url") or url))
    return chunks


class Index:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "metadata.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, kind TEXT, path TEXT, symbol TEXT, start_line INTEGER, end_line INTEGER, text TEXT, url TEXT, commit_sha TEXT, author TEXT, pr INTEGER)")
        self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, content, tokenize='unicode61')")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
        self.vector = QdrantClient(path=str(root / "qdrant"))
        self.embedder = TextEmbedding(model_name=MODEL, cache_dir=str(root / "models"))

    def close(self) -> None:
        self.db.close()
        self.vector.close()

    def state(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def build(self, chunks: Iterable[Chunk], commit: str, repo_name: str) -> int:
        # A deterministic full rebuild is appropriate for this frozen public demo.
        items = list({chunk.id: chunk for chunk in chunks}.values())
        if self.vector.collection_exists(COLLECTION):
            self.vector.delete_collection(COLLECTION)
        self.vector.create_collection(COLLECTION, vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE))
        self.db.execute("DELETE FROM chunks")
        self.db.execute("DELETE FROM chunks_fts")
        for offset in range(0, len(items), 32):
            batch = items[offset:offset + 32]
            vectors = list(self.embedder.embed([item.search_text[:6000] for item in batch]))
            self.vector.upsert(COLLECTION, [models.PointStruct(
                id=item.id, vector=vector.tolist(), payload={"kind": item.kind})
                for item, vector in zip(batch, vectors)])
            self.db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                [(c.id, c.kind, c.path, c.symbol, c.start_line, c.end_line,
                                  c.text, c.url, c.commit, c.author, c.pr) for c in batch])
            self.db.executemany("INSERT INTO chunks_fts(id,content) VALUES (?,?)",
                                [(c.id, lexical_text(c.search_text)) for c in batch])
            self.db.commit()
        for key, value in {"commit": commit, "repo": repo_name, "count": str(len(items))}.items():
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, value))
        self.db.commit()
        return len(items)

    def search(self, query: str, k: int = 5, *, rerank: bool = True) -> dict:
        started = time.perf_counter()
        vector = next(iter(self.embedder.query_embed(query)))
        dense = self.vector.query_points(COLLECTION, query=vector.tolist(), limit=80).points
        terms = list(dict.fromkeys(TOKEN_PATTERN.findall(lexical_text(query).lower())))
        lexical = " OR ".join(f'"{term}"' for term in terms if len(term) > 1)
        try:
            sparse = self.db.execute("SELECT id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 80", (lexical,)).fetchall() if lexical else []
        except sqlite3.OperationalError:
            sparse = []
        scores: dict[str, float] = {}
        for ranking in ([str(p.id) for p in dense], [row[0] for row in sparse]):
            for rank, chunk_id in enumerate(ranking, 1):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)
        candidates = sorted(scores, key=scores.get, reverse=True)[:80]
        rows = [self.db.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone() for chunk_id in candidates]
        rows = [dict(row) for row in rows if row]
        retrieved_ms = (time.perf_counter() - started) * 1000
        # A transparent code-aware reranker; benchmark against the fused baseline.
        if rerank:
            terms = set(TOKEN_PATTERN.findall(query.lower()))
            for row in rows:
                symbol = row["symbol"].lower()
                path = row["path"].lower()
                body = row["text"].lower()
                exact = sum(2.0 for term in terms if len(term) > 2 and term in symbol)
                exact += sum(1.0 for term in terms if len(term) > 2 and term in path)
                exact += sum(0.15 for term in terms if len(term) > 2 and term in body)
                if path.startswith("src/"):
                    exact += 1.5
                if path.startswith("tests/"):
                    exact -= 1.5
                if row["kind"] == "module":
                    exact -= 0.5
                if any(word in query.lower() for word in ("why", "introduced", "changed")):
                    exact += 4.0 if row["kind"] in {"pr", "commit", "pr_comment"} else 0
                row["rank_score"] = scores[row["id"]] * 100 + exact
            rows.sort(key=lambda row: row["rank_score"], reverse=True)
        else:
            for row in rows:
                row["rank_score"] = scores[row["id"]] * 100
        selected = rows[:k]
        answer = self._extractive_answer(selected)
        return {"query": query, "repo": self.state("repo"), "indexed_commit": self.state("commit"),
                "retrieval_ms": round(retrieved_ms, 1), "total_ms": round((time.perf_counter() - started) * 1000, 1),
                "answer": answer, "results": selected}

    @staticmethod
    def _extractive_answer(rows: list[dict]) -> dict:
        if not rows:
            return {"text": "No matching source was found in this index.", "url": None}
        top = rows[0]
        if top["kind"] in {"pr", "pr_comment", "commit"}:
            body = top["text"].split("\n", 1)[-1].strip()
            first_paragraph = body.split("\n\n", 1)[0].strip()
            if first_paragraph:
                return {"text": f"The recorded explanation in {top['path'] or top['symbol']} says: {first_paragraph[:500]}",
                        "url": top["url"]}
        location = f"{top['path']}:{top['start_line']}" if top["start_line"] else top["path"]
        return {"text": f"The strongest indexed source is {top['symbol']} at {location}. Open it to inspect the implementation and nearby context.",
                "url": top["url"]}
