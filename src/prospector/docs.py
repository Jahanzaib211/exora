"""The markdown document suite, served read-only to the Docs section.

Only files matching DOC_GROUPS are served. Paths are resolved against a whitelist built from these globs,
so a request can never reach anything else on disk.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# (group title, glob relative to docs_root). Groups with no matching files are skipped, so the same list
# serves both layouts: the repository root (playbook/, docs/) and a campaign folder that holds the
# repository as a subfolder next to numbered playbook files.
DOC_GROUPS: list[tuple[str, str]] = [
    ("Playbook", "playbook/*.md"),
    ("Campaign playbook", "[0-9][0-9]_*.md"),
    ("Getting started", "README.md"),
    ("Developer docs", "docs/*.md"),
    ("Developer docs: Exora app", "prospector/docs/*.md"),
    ("Developer docs: Outreach engine", "engine/*.md"),
    ("Developer docs: Outreach engine", "pipeline/outreach-engine/*.md"),
    ("Agent harness evaluation", "harness-eval/*.md"),
    ("Agent harness scorecards", "harness-eval/results/*/SCORECARD.md"),
]


def _title(path: Path) -> str:
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for line in fh:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem.replace("_", " ")


def catalog(root: Path) -> list[dict[str, Any]]:
    root = Path(root).resolve()
    groups = []
    for title, pattern in DOC_GROUPS:
        files = sorted(p for p in root.glob(pattern) if p.is_file())
        if not files:
            continue
        groups.append(
            {
                "group": title,
                "docs": [
                    {
                        "path": p.relative_to(root).as_posix(),
                        "title": _title(p),
                        "modified": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(timespec="seconds"),
                        "bytes": p.stat().st_size,
                    }
                    for p in files
                ],
            }
        )
    return groups


def allowed_paths(root: Path) -> set[str]:
    return {d["path"] for g in catalog(root) for d in g["docs"]}


def read_doc(root: Path, rel: str) -> dict[str, Any] | None:
    rel = rel.replace("\\", "/").lstrip("/")
    if rel not in allowed_paths(root):
        return None
    path = Path(root).resolve() / rel
    text = path.read_text(encoding="utf-8-sig")
    headings = [
        {"level": len(m.group(1)), "text": m.group(2).strip()}
        for m in re.finditer(r"^(#{1,3})\s+(.+)$", text, re.MULTILINE)
    ]
    return {
        "path": rel,
        "title": _title(path),
        "modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
        "markdown": text,
        "headings": headings,
    }
