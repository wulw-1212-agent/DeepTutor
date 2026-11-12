"""Diagnostics for artifacts an exec call produced outside the servable area.

The exec tool collects the files a model wrote and keeps only those the
server can actually serve (``PathService.is_public_output_path``). A model
that writes to a path built from ``DEEPTUTOR_WORKSPACE_ROOT`` - which points
at ``data/user`` and therefore misses the ``workspace/`` level - produces a
file that exists and is readable, yet is dropped from the artifact list and
404s on download. The turn still ends up ``completed`` and the only signal the
model gets is "No new or changed artifacts were produced by this call."

This module turns that silence into an actionable message. It never changes
a turn's status: a path problem is a reporting problem, and failing the turn
over it would be a behavioural regression. The goal is for the model (and the
user) to learn the real cause during the same turn.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from deeptutor.services.path_service import PathService, get_path_service

# Extensions that make a path look like a deliverable the user would download.
_DELIVERABLE_SUFFIXES = frozenset(
    {
        ".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt",
        ".csv", ".tsv", ".md", ".txt", ".html", ".htm",
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg",
        ".zip", ".mp3", ".mp4", ".wav", ".json",
    }
)

# "saved /path/to/file.docx", "wrote .../x.pdf", "created .../y.xlsx"
_SAVE_CLAIM_RE = re.compile(
    r"(?:saved|wrote|created|exported|generated)\s+"
    r"['\"]?([^\s'\"<>|]{1,400}?\.[A-Za-z0-9]{1,8})['\"]?",
    re.IGNORECASE,
)

_MAX_REPORTED = 5


@dataclass(frozen=True, slots=True)
class ArtifactDiagnostic:
    """One problem worth telling the model about."""

    kind: str  # "unservable" | "missing"
    path: str
    detail: str


def _is_deliverable(path_text: str) -> bool:
    return Path(path_text).suffix.lower() in _DELIVERABLE_SUFFIXES


def _snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    """Map every visible file under *root* to a cheap change signature.

    Hidden trees are skipped so the sandbox's own runtime state never shows up
    as a model-visible artifact.
    """

    if not root.exists() or not root.is_dir():
        return {}

    signature: dict[str, tuple[int, int]] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        stat = entry.stat()
                        signature[entry.path] = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    continue
    return signature


def find_unservable_writes(
    *,
    workdir: str | Path,
    public_root: str | Path,
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    path_service: PathService | None = None,
) -> list[ArtifactDiagnostic]:
    """Return files written outside *workdir* that the server cannot serve.

    A file qualifies when it is new or changed between the two snapshots, it
    lives outside the exec working directory, and the server refuses to serve
    it. Files that ARE servable are left to the normal artifact collection.
    """

    service = path_service or get_path_service()
    work = Path(workdir).expanduser().resolve()
    public = Path(public_root).expanduser().resolve()

    found: list[ArtifactDiagnostic] = []
    for path_text, signature in after.items():
        if before.get(path_text) == signature:
            continue
        try:
            path = Path(path_text).resolve()
        except OSError:
            continue
        if path == work or work in path.parents:
            # Inside the working directory: the normal collector handles it.
            continue
        if public != path and public not in path.parents:
            # Written outside the workspace entirely - not this tool's business.
            continue
        if service.is_public_output_path(path):
            # Servable, just not in the working directory.
            continue
        found.append(
            ArtifactDiagnostic(
                kind="unservable",
                path=str(path),
                detail=(
                    "written outside the exec working directory, to a path the server "
                    "cannot serve, so it will 404 on download"
                ),
            )
        )
    return found[:_MAX_REPORTED]


def find_missing_claimed_files(
    *,
    output: str,
    workdir: str | Path,
    path_service: PathService | None = None,
) -> list[ArtifactDiagnostic]:
    """Return save-like claims in *output* whose file does not exist.

    The model often reports "saved out.docx" from a ``save()`` that raised and
    was swallowed. Only paths that look like deliverables and are genuinely
    absent are reported, so ordinary output is never flagged.
    """

    if not output:
        return []

    work = Path(workdir).expanduser().resolve()
    service = path_service or get_path_service()

    missing: list[ArtifactDiagnostic] = []
    seen: set[str] = set()
    for match in _SAVE_CLAIM_RE.finditer(output):
        claimed = match.group(1).strip().strip("'\"")
        if not _is_deliverable(claimed):
            continue
        candidate = Path(claimed)
        if not candidate.is_absolute():
            candidate = work / claimed
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            continue
        missing.append(
            ArtifactDiagnostic(
                kind="missing",
                path=claimed,
                detail="the output claims this file was saved, but no such file exists on disk",
            )
        )
        if len(missing) >= _MAX_REPORTED:
            break
    return missing


def render_artifact_diagnostics(
    diagnostics: list[ArtifactDiagnostic] | tuple[ArtifactDiagnostic, ...],
) -> str:
    """Render diagnostics as a short, model-facing instruction block."""

    if not diagnostics:
        return ""

    lines = ["", "WARNING - artifact delivery problem; the file(s) below will NOT be downloadable:"]
    for diagnostic in diagnostics:
        lines.append(f"- {diagnostic.path} - {diagnostic.detail}")
    lines.extend(
        [
            "",
            "How to fix it: the exec working directory is already a location the server "
            "serves, so save with a bare filename there "
            '(`doc.save("out.docx")`) instead of building a path from '
            "DEEPTUTOR_WORKSPACE_ROOT (that points at data/user, which is not servable). "
            "If you need a subfolder, create it first "
            '(`Path("subdir").mkdir(parents=True, exist_ok=True)`). Then call '
            "workspace_present with the exact workspace-relative path to present it.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "ArtifactDiagnostic",
    "find_missing_claimed_files",
    "find_unservable_writes",
    "render_artifact_diagnostics",
]