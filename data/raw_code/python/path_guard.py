from pathlib import Path


def read_inside(base: str, name: str) -> str:
    root = Path(base).resolve()
    target = (root / name).resolve()
    if root not in target.parents and target != root:
        raise ValueError("path escapes base")
    return target.read_text(encoding="utf-8")
