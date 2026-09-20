import json
from pathlib import Path

from blog_summaries.cache import CachedSummary, load_cache, save_cache


def make_summary(title: str = "Test Article") -> CachedSummary:
    return CachedSummary(
        title=title,
        link="https://thezvi.substack.com/p/test",
        author="Zvi Mowshowitz",
        pub_date="2026-03-15T12:00:00+00:00",
        summary="First paragraph.\n\nSecond paragraph.",
        summarized_at="2026-03-15T14:00:00+00:00",
    )


def test_load_cache_missing_file(tmp_path: Path) -> None:
    result = load_cache(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_cache_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("")
    result = load_cache(path)
    assert result == {}


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = {
        "guid-1": make_summary("Article One"),
        "guid-2": make_summary("Article Two"),
    }

    save_cache(path, cache)
    loaded = load_cache(path)

    assert loaded == cache


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "cache.json"
    cache = {"guid-1": make_summary()}

    save_cache(path, cache)

    assert path.exists()
    assert json.loads(path.read_text()) == cache


def test_save_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    save_cache(path, {"guid-1": make_summary()})
    assert path.read_text(encoding="utf-8").endswith("\n")
