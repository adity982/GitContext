from __future__ import annotations

import html
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

from .cli import DEFAULT_INDEX
from .core import Index


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.index = Index(DEFAULT_INDEX)
    yield
    app.state.index.close()


app = FastAPI(title="GitContext public demo", lifespan=lifespan)


def page(question: str = "", results: dict | None = None) -> str:
    cards = ""
    if results:
        for i, row in enumerate(results["results"], 1):
            excerpt = html.escape(row["text"][:1000])
            cards += f"<article><div class='eyebrow'>EVIDENCE {i} · {html.escape(row['kind'])}</div><h2>{html.escape(row['symbol'])}</h2><p class='path'>{html.escape(row['path'])}</p><pre>{excerpt}</pre><a href='{html.escape(row['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>Open exact source ↗</a></article>"
    status = ""
    answer = ""
    if results:
        status = f"<p class='status'>Indexed <code>{html.escape(results['repo'] or '')}</code> at <code>{html.escape(results['indexed_commit'] or '')}</code> · retrieval {results['retrieval_ms']} ms. Evidence is retrieved from this pinned revision; inspect each source before relying on an interpretation.</p>"
        summary = results["answer"]
        link = f"<a href='{html.escape(summary['url'], quote=True)}' target='_blank' rel='noopener noreferrer'>Open cited source ↗</a>" if summary["url"] else ""
        answer = f"<aside><div class='eyebrow'>SOURCE-LINKED STARTING ANSWER</div><p>{html.escape(summary['text'])}</p>{link}</aside>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>GitContext</title><style>
    :root{{--ink:#172522;--muted:#59716a;--paper:#f5f4ee;--line:#cbd5ca;--accent:#196c53}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}}main{{max-width:1050px;margin:auto;padding:48px 24px}}header{{border-bottom:1px solid var(--line);padding-bottom:28px}}.eyebrow{{font-size:12px;letter-spacing:.16em;font-weight:700;color:var(--accent)}}h1{{font:700 clamp(42px,8vw,86px)/.98 Georgia,serif;letter-spacing:-.05em;margin:20px 0}}header p{{max-width:650px;color:var(--muted);font-size:19px}}form{{display:flex;gap:10px;margin:32px 0}}input{{flex:1;min-width:0;padding:16px;border:1px solid var(--line);background:white;font:inherit}}button{{background:var(--ink);color:white;border:0;padding:0 25px;font:inherit;cursor:pointer}}.status{{color:var(--muted);font-size:14px}}aside{{background:#e5eee4;border-left:4px solid var(--accent);padding:20px;margin-top:24px}}aside p{{font-size:19px;margin:10px 0}}section{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-top:24px}}article{{background:white;border:1px solid var(--line);padding:22px;min-width:0}}h2{{font:700 26px/1.1 Georgia,serif;overflow-wrap:anywhere;margin:12px 0 3px}}.path{{color:var(--muted);font-size:13px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f1;padding:14px;max-height:270px;overflow:auto;font-size:12px}}a{{color:var(--accent);font-weight:700}}@media(max-width:600px){{form{{flex-direction:column}}button{{padding:14px}}}}</style></head><body><main><header><div class='eyebrow'>GITCONTEXT / PUBLIC REPOSITORY DEMO</div><h1>Find the reason<br>in the record.</h1><p>Search code, documentation, commits, and merged pull requests in psf/requests. Each result links to its source.</p></header><form method='post'><input name='question' aria-label='Repository question' placeholder='How are redirects handled?' value='{html.escape(question, quote=True)}' required><button>Find evidence</button></form>{status}{answer}<section>{cards}</section></main></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def home():
    return page()


@app.post("/", response_class=HTMLResponse)
async def search(question: str = Form(...)):
    result = app.state.index.search(question, k=5)
    return page(question, result)


@app.get("/api/search")
async def api_search(q: str, k: int = 5):
    return app.state.index.search(q, k=min(max(k, 1), 20))
