"""Download selected permissively licensed GitHub sources without executing them."""

from __future__ import annotations

import re
from pathlib import Path
import subprocess

NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PERMISSIVE = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "0BSD"}


def selected_names(decision: dict) -> list[str]:
    names = decision.get("repositories")
    if names is None:
        names = [decision["repository"]] if decision.get("repository") else []
    if not isinstance(names, list):
        return []
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and NAME.fullmatch(name)))[:3]


def prepare_references(decision: dict, ranked: list[dict], cache_root: Path) -> tuple[dict, list[str]]:
    """Clone only named shortlist entries with verified permissive license and bounded size."""
    known = {item["full_name"]: item for item in ranked}
    references: dict[str, dict] = {}
    warnings: list[str] = []
    for name in selected_names(decision):
        item = known.get(name)
        if not item:
            warnings.append(f"{name}: not in the searched shortlist")
            continue
        license_id = item["signals"]["license"]["reason"]
        if license_id not in PERMISSIVE:
            warnings.append(f"{name}: source import disabled because license is {license_id}")
            continue
        size_kb = item.get("size_kb")
        if not isinstance(size_kb, int) or size_kb > 50_000:
            warnings.append(f"{name}: repository size is unknown or exceeds the 50 MB metadata limit")
            continue
        target = cache_root / name.replace("/", "__")
        if not (target / ".git").is_dir():
            cache_root.mkdir(parents=True, exist_ok=True)
            try:
                cloned = subprocess.run(
                    ["git", "clone", "--depth", "1", "--single-branch", "--filter=blob:none", f"https://github.com/{name}.git", str(target)],
                    capture_output=True, text=True, timeout=90, shell=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                warnings.append(f"{name}: clone failed: {str(exc)[:120]}")
                continue
            if cloned.returncode:
                warnings.append(f"{name}: clone failed: {cloned.stderr[-200:]}")
                continue
        try:
            head = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            warnings.append(f"{name}: could not verify clone commit")
            continue
        references[name] = {"root": target.resolve(), "license": license_id, "commit": head, "url": item["url"]}
    return references, warnings
