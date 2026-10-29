from __future__ import annotations

import html
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse

from .answer import LocalGenerator, select_answer_evidence
from .cli import DEFAULT_INDEX
from .core import Index


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.index = Index(DEFAULT_INDEX)
    app.state.generator = LocalGenerator()
    try:
        await app.state.generator.start()
        yield
    finally:
        await app.state.generator.close()
        app.state.index.close()


app = FastAPI(title="GitContext public demo", lifespan=lifespan)


def _answer_html(value: str) -> str:
    parts = re.split(r"(\[\d+\])", value)
    output = []
    for part in parts:
        if re.fullmatch(r"\[\d+\]", part):
            number = int(part[1:-1])
            output.append(f"<a href='#source-{number}'>{part}</a>")
        else:
            output.append(html.escape(part).replace("\n", "<br>"))
    return "".join(output)


def page(question: str = "", results: dict | None = None, error: str | None = None) -> str:
    cards = ""
    if results:
        for i, row in enumerate(results["results"], 1):
            excerpt = html.escape(row["text"][:1000])
            cards += f"<article id='source-{i}'><div class='eyebrow'>EVIDENCE {i} · {html.escape(row['kind'])}</div><h2>{html.escape(row['symbol'])}</h2><p class='path'>{html.escape(row['path'])}</p><pre>{excerpt}</pre><a href='{html.escape(row['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>Open exact source ↗</a></article>"
    status = ""
    answer = ""
    if results and "answer" in results:
        status = f"<p class='status'>Indexed <code>{html.escape(results['repo'] or '')}</code> at <code>{html.escape(results['indexed_commit'] or '')}</code> · retrieval {results['retrieval_ms']} ms. Evidence is retrieved from this pinned revision; inspect each source before relying on an interpretation.</p>"
        summary = results["answer"]
        citations = " ".join(
            f"<a href='{html.escape(c['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>[{c['number']}] {html.escape(c['label'])} ↗</a>"
            for c in summary["citations"]
        )
        answer = f"<aside><div class='eyebrow'>ANSWER FROM REPOSITORY EVIDENCE</div><p>{_answer_html(summary['text'])}</p><div class='citation-links'>{citations}</div></aside>"
    if error:
        answer = f"<aside role='alert'><div class='eyebrow'>ANSWER UNAVAILABLE</div><p>{html.escape(error)}</p></aside>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>GitContext</title><style>
    :root{{--ink:#172522;--muted:#59716a;--paper:#f5f4ee;--line:#cbd5ca;--accent:#196c53}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}}main{{max-width:1050px;margin:auto;padding:48px 24px}}header{{border-bottom:1px solid var(--line);padding-bottom:28px}}.eyebrow{{font-size:12px;letter-spacing:.16em;font-weight:700;color:var(--accent)}}h1{{font:700 clamp(42px,8vw,86px)/.98 Georgia,serif;letter-spacing:-.05em;margin:20px 0}}header p{{max-width:650px;color:var(--muted);font-size:19px}}form{{display:flex;gap:10px;margin:32px 0}}input{{flex:1;min-width:0;padding:16px;border:1px solid var(--line);background:white;font:inherit}}button{{background:var(--ink);color:white;border:0;padding:0 25px;font:inherit;cursor:pointer}}.status{{color:var(--muted);font-size:14px}}aside{{background:#e5eee4;border-left:4px solid var(--accent);padding:20px;margin-top:24px}}aside p{{font-size:19px;margin:10px 0}}.citation-links{{display:flex;gap:14px;flex-wrap:wrap;font-size:13px}}section{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:24px}}article{{background:white;border:1px solid var(--line);padding:22px;min-width:0;scroll-margin-top:20px}}h2{{font:700 26px/1.1 Georgia,serif;overflow-wrap:anywhere;margin:12px 0 3px}}.path{{color:var(--muted);font-size:13px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f1;padding:14px;max-height:270px;overflow:auto;font-size:12px}}a{{color:var(--accent);font-weight:700}}@media(max-width:600px){{form{{flex-direction:column}}button{{padding:14px}}}}</style></head><body><main><header><div class='eyebrow'>GITCONTEXT / PUBLIC REPOSITORY DEMO</div><h1>Find the reason<br>in the record.</h1><p>Ask about code, documentation, commits, and merged pull requests in psf/requests. Every answer links to its evidence.</p></header><form method='post'><input name='question' aria-label='Repository question' placeholder='How are redirects handled?' value='{html.escape(question, quote=True)}' maxlength='500' required><button id='ask-button'>Ask GitContext</button></form>{status}{answer}<section>{cards}</section></main><script>document.querySelector('form').addEventListener('submit',()=>{{const b=document.querySelector('#ask-button');b.textContent='Reading the repository…';b.disabled=true;}})</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return page()


@app.post("/", response_class=HTMLResponse)
async def search(question: str = Form(...)):
    question = question.strip()
    if not question or len(question) > 500:
        return page(question, error="Enter a question of 1 to 500 characters.")
    result = app.state.index.search(question, k=8)
    result["results"] = select_answer_evidence(result["results"])
    try:
        result["answer"] = await app.state.generator.answer(question, result["results"])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return page(question, result, f"The local model could not produce a grounded answer: {exc}")
    return page(question, result)


@app.get("/api/search")
async def api_search(q: str, k: int = 5):
    return app.state.index.search(q, k=min(max(k, 1), 20))


@app.get("/api/ask")
async def api_ask(q: str):
    q = q.strip()
    if not q or len(q) > 500:
        raise HTTPException(status_code=422, detail="Question must contain 1 to 500 characters.")
    result = app.state.index.search(q, k=8)
    result["results"] = select_answer_evidence(result["results"])
    try:
        result["answer"] = await app.state.generator.answer(q, result["results"])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Grounded answer unavailable: {exc}") from exc
    return result
