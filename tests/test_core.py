from pathlib import Path

from gitcontext.core import _python_chunks, lexical_text
from gitcontext.evaluate import matches


def test_python_chunks_keep_symbol_and_exact_lines():
    source = "class Example:\n    def run(self):\n        return 7\n"
    chunks = _python_chunks("org/repo", "a" * 40, "example.py", source)
    method = next(c for c in chunks if c.symbol == "Example.run")
    assert (method.start_line, method.end_line) == (2, 3)
    assert method.url.endswith("example.py#L2-L3")


def test_lexical_text_keeps_identifiers_and_parts():
    text = lexical_text("should_strip_auth")
    assert "should_strip_auth" in text
    assert "strip" in text


def test_gold_requires_all_fields():
    assert matches({"kind": "pr", "path": "PR#42"}, {"kind": "pr", "path": "PR#42"})
    assert not matches({"kind": "code", "path": "PR#42"}, {"kind": "pr", "path": "PR#42"})
