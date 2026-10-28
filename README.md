# GitContext — public repository evidence demo

GitContext is a first-milestone prototype for exploring why code behaves as it does. It indexes a **pinned public revision** of `psf/requests`, recent commits, and a cached set of merged PR descriptions and conversation comments. It retrieves source evidence with dense vectors and SQLite FTS5 BM25, fuses both rankings, and applies a transparent code-aware reranker. The web and CLI views link to the original file, commit, or PR.

This milestone is an **evidence retrieval demo**. It shows an extractive, source-linked starting answer and ranked evidence; it does not use an LLM to synthesize a full explanation or claim that a top result proves intent. It does not support private repositories, permission filtering, automatic updates, incident connectors, or production serving. Those belong to later milestones.

## Setup (Windows PowerShell)

Requires Python 3.12, Git, and GitHub CLI authentication for the PR cache. The embedding model downloads on first indexing run.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
git clone https://github.com/psf/requests.git data/requests
git -C data/requests checkout 3c7baaa05ab4aa49cc819ce3a819d260de19c157
.\.venv\Scripts\python.exe -m gitcontext.cli fetch-prs --limit 25
.\.venv\Scripts\python.exe -m gitcontext.cli index
.\.venv\Scripts\python.exe -m gitcontext.cli ask "Where does Requests strip redirect authorization?"
.\.venv\Scripts\python.exe -m gitcontext.cli eval
.\.venv\Scripts\python.exe -m uvicorn gitcontext.web:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. The API is available at `/api/search?q=...`.

## Evidence contract

Each result includes source type, path or PR number, symbol, immutable commit SHA, line range where applicable, author if supplied, and a GitHub link. The index reports the pinned source revision. PR records retain their own merge SHA; a PR match is not automatically treated as proof that the current code still behaves as it did when the PR merged.

The demo uses Python's standard-library AST parser for Python functions, methods, and classes. It chunks Markdown by heading and other text by lines. Large symbols are split into 100-line pieces. Unsupported or unparsable Python files fall back to line chunks. Vector storage uses embedded Qdrant; SQLite stores provenance and the BM25 full-text index.

## Evaluation

`eval/questions.json` contains 25 fixed, manually written questions: 20 code-location and 5 PR-history questions. Each has a source locator as a gold label. `python -m gitcontext.cli eval` writes `eval/report.json` with Recall@5 by category and retrieval latency. The labels test whether a relevant source appears in the top five, **not** whether a natural-language answer is correct or each claim is supported. Question writers inspected the repository and PR bodies to define source locators; independent human answer grading has not been done.

The recorded local run at commit `3c7baaa05ab4aa49cc819ce3a819d260de19c157` indexed 908 chunks and found the labeled source in the top five for **21/25 questions (84%)**: 16/20 code questions and 5/5 PR-history questions. Median retrieval time was **174.3 ms**, and p95 was **655.1 ms**, measured in one warm, sequential run on this Windows machine. These timings exclude model initialization and indexing. The four missed labels were Q10, Q15, Q17, and Q19; their returned candidates are preserved in the report.

The current ranking is intentionally simple and measurable. To compare components, run with `Index.search(question, rerank=False)` for fused retrieval and compare against the default. The fixed 25-question set is small and partly identifier-rich, so its score is a demo result and must not be generalized to production workloads.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Model files, the cloned repository, PR cache, and indices live under ignored `data/`. No API key is required to run the demo.
