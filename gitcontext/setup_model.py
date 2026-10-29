"""Download the pinned local inference runtime and model for Windows."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download

from .answer import MODEL_PATH, ROOT, SERVER_PATH


RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11972/llama-b11972-bin-win-cpu-x64.zip"
RUNTIME_SHA256 = "3ae2300ba3abbd7dc3ed1555a2688338451767737b1a66cd211becb4531244d2"
MODEL_SHA256 = "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a"


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def main() -> None:
    if not SERVER_PATH.exists():
        archive = ROOT / "data" / "runtime" / "llama-b11972-bin-win-cpu-x64.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            with httpx.stream("GET", RUNTIME_URL, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for block in response.iter_bytes(chunk_size=1024 * 1024):
                        output.write(block)
        if digest(archive) != RUNTIME_SHA256:
            raise RuntimeError("llama.cpp release archive failed SHA-256 verification")
        SERVER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                destination = (SERVER_PATH.parent / member.filename).resolve()
                if not destination.is_relative_to(SERVER_PATH.parent.resolve()):
                    raise RuntimeError("Unsafe release archive path")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(member) as source_file, destination.open("wb") as output:
                        shutil.copyfileobj(source_file, output)
    print(f"Runtime ready: {SERVER_PATH}")
    model = Path(hf_hub_download(repo_id="Qwen/Qwen3-1.7B-GGUF",
                                 filename=MODEL_PATH.name,
                                 local_dir=str(MODEL_PATH.parent)))
    if digest(model) != MODEL_SHA256:
        raise RuntimeError("Qwen model failed SHA-256 verification")
    print(f"Model ready: {model}")


if __name__ == "__main__":
    main()
