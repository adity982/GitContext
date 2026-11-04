import asyncio

import pytest

from gitcontext.answer import (
    evidence_prompt, retrieval_answer, retrieval_fallback, select_answer_evidence, validate_answer,
)
from gitcontext.evaluate import summarize_answer_evaluation
from gitcontext.web import _answer_html


SOURCES = [{"kind": "code", "path": "src/requests/sessions.py", "symbol": "SessionRedirectMixin.resolve_redirects",
            "start_line": 186, "end_line": 285, "text": "def resolve_redirects():\n    yield response",
            "url": "https://github.com/psf/requests/blob/abc/src/requests/sessions.py#L186-L285"}]


def test_prompt_contains_question_and_numbered_evidence():
    prompt = evidence_prompt("How are redirects handled?", SOURCES)
    assert "How are redirects handled?" in prompt[1]["content"]
    assert "[1] code src/requests/sessions.py:186-285" in prompt[1]["content"]
    assert "Do not infer behavior from a class name" in prompt[0]["content"]


def test_generated_answer_requires_existing_citations():
    with pytest.raises(ValueError):
        validate_answer("The request redirects automatically.", SOURCES)
    with pytest.raises(ValueError):
        validate_answer("The request redirects automatically. [2]", SOURCES)
    answer = validate_answer("The redirect loop yields responses. [1]", SOURCES)
    assert answer["citations"][0]["url"] == SOURCES[0]["url"]


def test_generated_answer_rejects_broken_shape():
    with pytest.raises(ValueError):
        validate_answer(("word " * 91) + "[1]", SOURCES)
    with pytest.raises(ValueError):
        validate_answer("First paragraph. [1]\n\nSecond paragraph. [1]", SOURCES)


def test_retrieval_fallback_names_and_links_ranked_evidence():
    sources = [{**SOURCES[0], "symbol": f"source_{number}"} for number in range(2)]
    answer = retrieval_fallback(sources)
    assert answer["mode"] == "retrieval_fallback"
    assert "source_0" in answer["text"]
    assert "source_1" in answer["text"]
    assert [item["number"] for item in answer["citations"]] == [1, 2]


def test_retrieval_only_answer_does_not_claim_generation_failed():
    sources = [{**SOURCES[0], "symbol": f"source_{number}"} for number in range(2)]
    answer = retrieval_answer(sources)
    assert answer["mode"] == "retrieval_only"
    assert "No model explanation was requested" in answer["text"]
    assert "withheld" not in answer["text"]


def test_answer_evidence_omits_thin_code_declarations():
    thin = {**SOURCES[0], "symbol": "RedirectMixin", "text": "class RedirectMixin:\n    def send(self): ...\n" * 10}
    substantive = {**SOURCES[0], "text": SOURCES[0]["text"] * 20}
    selected = select_answer_evidence([thin, substantive])
    assert selected == [substantive]


def test_answer_evidence_caps_generation_context_at_two_ranked_sources():
    sources = [
        {**SOURCES[0], "symbol": f"source_{index}", "text": SOURCES[0]["text"] * 20}
        for index in range(3)
    ]
    selected = select_answer_evidence(sources)
    prompt = evidence_prompt("How are redirects handled?", selected)
    assert selected == sources[:2]
    assert "source_0" in prompt[1]["content"]
    assert "source_1" in prompt[1]["content"]
    assert "source_2" not in prompt[1]["content"]


def test_answer_html_links_citations_and_escapes_model_text():
    rendered = _answer_html("<script>bad</script> [1]")
    assert "<script>" not in rendered
    assert "href='#source-1'" in rendered


def test_answer_evaluation_summary_separates_pipeline_signals_from_correctness():
    class FakeIndex:
        def state(self, key):
            return {"repo": "psf/requests", "commit": "abc"}[key]

    details = [
        {"answer_ms": 1000.0, "gold_in_generation_context": True,
         "answer": {"mode": "generated"}},
        {"answer_ms": 3000.0, "gold_in_generation_context": False,
         "answer": {"mode": "retrieval_fallback"}},
    ]
    summary = summarize_answer_evaluation(details, FakeIndex())
    assert summary["modes"] == {"generated": 1, "retrieval_fallback": 1}
    assert summary["generated_rate"] == 0.5
    assert summary["gold_in_generation_context_rate"] == 0.5
    assert "human review" in summary["scope"]


def test_answer_diagnostics_are_opt_in(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": "unsupported"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("gitcontext.answer.httpx.AsyncClient", lambda **kwargs: Client())
    from gitcontext.answer import LocalGenerator

    public = asyncio.run(LocalGenerator().answer("question", SOURCES))
    diagnostic = asyncio.run(LocalGenerator().answer("question", SOURCES, include_diagnostics=True))
    assert "model_output" not in public
    assert diagnostic["model_output"] == "unsupported"
    assert diagnostic["fallback_reason"]
