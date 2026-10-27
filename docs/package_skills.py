"""Package the two independently usable skills without local state or secrets."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]


def package() -> list[Path]:
    destination = ROOT / "Results" / "skill-packages"
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name in ("search-with-evidence", "find-before-build"):
        source = ROOT / name
        output = destination / f"{name}.zip"
        paths = sorted(path for path in source.rglob("*") if path.is_file() and not path.is_symlink()
                       and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(source).parts)
                       and (path.suffix in {".md", ".py", ".yaml"} or path.name == "LICENSE"))
        required = {source / "SKILL.md", source / "LICENSE", source / "agents" / "openai.yaml"}
        if not required <= set(paths):
            raise ValueError(f"Incomplete skill package: {name}")
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, path.relative_to(ROOT).as_posix())
        outputs.append(output)
    return outputs


if __name__ == "__main__":
    for path in package():
        print(path)
