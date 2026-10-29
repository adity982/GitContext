import pytest

from gitcontext.answer import evidence_prompt, select_answer_evidence, validate_answer
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


def test_answer_evidence_omits_thin_code_declarations():
    thin = {**SOURCES[0], "symbol": "RedirectMixin", "text": "class RedirectMixin:\n    def send(self): ...\n" * 10}
    substantive = {**SOURCES[0], "text": SOURCES[0]["text"] * 20}
    selected = select_answer_evidence([thin, substantive])
    assert selected == [substantive]


def test_answer_html_links_citations_and_escapes_model_text():
    rendered = _answer_html("<script>bad</script> [1]")
    assert "<script>" not in rendered
    assert "href='#source-1'" in rendered
