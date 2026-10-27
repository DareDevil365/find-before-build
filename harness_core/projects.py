"""Readable, collision-safe output folders and bounded project file discovery."""

from __future__ import annotations

import os
from pathlib import Path
import re
import unicodedata


GENERATED_DIRS = {"node_modules", "__pycache__", "venv", "env", "dist", "build", "coverage"}


def project_name(task: str, *, explicit: bool = False) -> str:
    """Derive a filesystem-safe name locally, without another model request."""
    title = task.strip().splitlines()[0] if task.strip() else "program"
    title = re.sub(r"https?://\S+", "", title)
    if not explicit:
        title = re.sub(r"^\s*(?:(?:please|can you|could you|i want to)\s+)*(?:build|create|make|develop|write|generate)\s+(?:(?:me|a|an|the|new)\s+)*", "", title, flags=re.I)
        title = re.split(r"[,;:]|\b(?:that|which|with|using|include|including)\b", title, maxsplit=1, flags=re.I)[0]
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", title.lower())[:7]
    name = "-".join(words)[:64].rstrip("-") or "program"
    if re.fullmatch(r"con|prn|aux|nul|com[0-9]|lpt[0-9]", name):
        name += "-app"
    return name


def create_project(root: Path, task: str, name: str | None = None) -> Path:
    """Reserve Results/<name>, then <name>-2, <name>-3, without overwriting."""
    results = root.resolve() / "Results"
    results.mkdir(exist_ok=True)
    base = project_name(name, explicit=True) if name and name.strip() else project_name(task)
    for number in range(1, 100_001):
        folder = results / (base if number == 1 else f"{base}-{number}")
        # A leftover record must not become associated with a different project.
        if (root / "runs" / "builds" / folder.name).exists():
            continue
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("Too many projects with the same name")


def project_files(root: Path):
    """Skip dependency/cache trees and links before descending into them."""
    root = root.resolve()
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith(".") and name not in GENERATED_DIRS and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if not name.startswith(".") and not path.is_symlink() and path.resolve().is_relative_to(root):
                yield path
