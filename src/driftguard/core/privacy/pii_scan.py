"""Varredura de PII em artefatos publicáveis (logs e relatórios).

Garante tecnicamente (não só por política) que identificadores originais e o salt
não vazem para arquivos versionados ou compartilhados.
"""

from __future__ import annotations

import re
from pathlib import Path


def scan_files(paths: list[Path], forbidden_tokens: set[str], id_pattern: str | None = None) -> list[dict]:
    findings: list[dict] = []
    regex = re.compile(id_pattern) if id_pattern else None
    for path in paths:
        if not path.is_file() or path.suffix in {".parquet", ".db", ".png", ".ubj", ".json.gz"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in forbidden_tokens:
            if token and token in text:
                findings.append({"file": path.as_posix(), "type": "forbidden_token"})
                break
        if regex and regex.search(text):
            findings.append({"file": path.as_posix(), "type": "id_pattern"})
    return findings
