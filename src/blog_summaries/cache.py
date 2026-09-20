import json
from pathlib import Path
from typing import NotRequired, TypedDict


class CachedSummary(TypedDict):
    title: str
    link: str
    author: str
    pub_date: str
    summary: str
    summarized_at: str
    model: NotRequired[str]


def load_cache(path: Path) -> dict[str, CachedSummary]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)  # pyright: ignore[reportAny]


def save_cache(path: Path, cache: dict[str, CachedSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
        _ = f.write("\n")
