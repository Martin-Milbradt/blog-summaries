import collections.abc
import dataclasses
import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from blog_summaries.blogs import REDWOOD, ZVI, BlogConfig
from blog_summaries.cache import load_cache
from blog_summaries.fetch import Article
from blog_summaries.main import RefusedArticlesError, fetch_pages, run
from blog_summaries.summarize import ArticleRefusedError, Completion


def make_article(guid: str, title: str, audio: bool = False) -> Article:
    return Article(
        guid=guid,
        title=title,
        link=f"https://example.com/{guid}",
        author="Author",
        pub_date=datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC),
        content_html="<p>Body text.</p>",
        audio=audio,
    )


def fake_summarize(
    title: str, author: str, text: str, subject: str, model: str, fallback: str | None
) -> Completion:
    if title == "Refused":
        raise ArticleRefusedError("blocked under usage policy")
    return Completion(f"Summary of {title} by {author} for {subject}.", model)


@pytest.fixture(autouse=True)
def model_names() -> collections.abc.Iterator[None]:
    with patch(
        "blog_summaries.summarize.fetch_model_names",
        return_value={"test/model": "Test: Model 1"},
    ):
        yield


def test_run_writes_survivors_then_raises_on_refusal(tmp_path: Path) -> None:
    articles = [make_article("a", "Refused"), make_article("b", "Fine")]

    with (
        patch("blog_summaries.main.fetch_pages", return_value=articles),
        patch("blog_summaries.main.configured_model", return_value="test/model"),
        patch("blog_summaries.main.configured_fallback_model", return_value=None),
        patch(
            "blog_summaries.main.summarize_with_fallback", side_effect=fake_summarize
        ),
        pytest.raises(RefusedArticlesError, match="zvi: Refused"),
    ):
        _ = run([ZVI], data_dir=tmp_path, docs_dir=tmp_path)

    # The refusal must not cost us the article that did summarize.
    cache = load_cache(tmp_path / "zvi.json")
    assert set(cache) == {"b"}
    assert "Summary of Fine" in (tmp_path / "zvi" / "feed.xml").read_text("utf-8")


def test_run_keeps_blogs_in_separate_files_and_prompts(tmp_path: Path) -> None:
    def fake_fetch(blog: BlogConfig, pages: int) -> list[Article]:
        return [make_article(blog.slug, f"Post {blog.slug}")]

    with (
        patch("blog_summaries.main.fetch_pages", side_effect=fake_fetch),
        patch("blog_summaries.main.configured_model", return_value="test/model"),
        patch("blog_summaries.main.configured_fallback_model", return_value=None),
        patch(
            "blog_summaries.main.summarize_with_fallback", side_effect=fake_summarize
        ),
    ):
        assert run([ZVI, REDWOOD], data_dir=tmp_path, docs_dir=tmp_path) == 2

    zvi = load_cache(tmp_path / "zvi.json")
    redwood = load_cache(tmp_path / "redwood.json")
    assert set(zvi) == {"zvi"} and set(redwood) == {"redwood"}
    assert ZVI.prompt_subject in zvi["zvi"]["summary"]
    assert REDWOOD.prompt_subject in redwood["redwood"]["summary"]
    assert (tmp_path / "redwood" / "feed.xml").exists()


def test_run_skips_podcast_episodes(tmp_path: Path) -> None:
    articles = [make_article("ep", "Episode", audio=True), make_article("b", "Fine")]

    with (
        patch("blog_summaries.main.fetch_pages", return_value=articles),
        patch("blog_summaries.main.configured_model", return_value="test/model"),
        patch("blog_summaries.main.configured_fallback_model", return_value=None),
        patch(
            "blog_summaries.main.summarize_with_fallback", side_effect=fake_summarize
        ),
    ):
        assert run([REDWOOD], data_dir=tmp_path, docs_dir=tmp_path) == 1

    assert set(load_cache(tmp_path / "redwood.json")) == {"b"}


def test_run_credits_full_bylines_and_names_the_model(tmp_path: Path) -> None:
    archive = RecordingArchive([], bylines={"a": "A One, B Two"})
    blogs = [
        dataclasses.replace(REDWOOD, archive=archive),
        dataclasses.replace(ZVI, author_names={"Author": "Full Name"}),
    ]
    articles = [make_article("a", "Post")]

    with (
        patch("blog_summaries.main.fetch_pages", return_value=articles),
        patch("blog_summaries.main.configured_model", return_value="test/model"),
        patch("blog_summaries.main.configured_fallback_model", return_value=None),
        patch(
            "blog_summaries.main.summarize_with_fallback", side_effect=fake_summarize
        ),
    ):
        _ = run(blogs, data_dir=tmp_path, docs_dir=tmp_path)

    redwood = load_cache(tmp_path / "redwood.json")["a"]
    assert redwood["author"] == "A One, B Two"
    assert "by A One, B Two for" in redwood["summary"]
    assert redwood.get("model_name") == "Model 1"
    assert load_cache(tmp_path / "zvi.json")["a"]["author"] == "Full Name"
    feed = (tmp_path / "redwood" / "feed.xml").read_text("utf-8")
    assert "<dc:creator>A One, B Two (summary by Model 1)</dc:creator>" in feed


class RecordingArchive:
    def __init__(
        self, page_two: list[Article], bylines: dict[str, str] | None = None
    ) -> None:
        self.page_two = page_two
        self.bylines = bylines or {}
        self.calls: list[tuple[int, str]] = []

    def fetch_older_pages(
        self, count: int, default_author: str
    ) -> collections.abc.Iterator[list[Article]]:
        self.calls.append((count, default_author))
        if count:
            yield self.page_two

    def fetch_feed_bylines(self) -> dict[str, str]:
        return self.bylines


def test_fetch_pages_deduplicates_across_pages() -> None:
    page_one = [make_article("a", "One"), make_article("b", "Two")]
    archive = RecordingArchive([make_article("b", "Two"), make_article("c", "Three")])
    blog = dataclasses.replace(ZVI, archive=archive)

    with patch("blog_summaries.main.fetch_articles", return_value=page_one):
        assert [a.guid for a in fetch_pages(blog, 2)] == ["a", "b", "c"]
    assert archive.calls == [(1, ZVI.default_author)]


def test_fetch_pages_single_page_skips_archive() -> None:
    archive = RecordingArchive([])
    blog = dataclasses.replace(ZVI, archive=archive)

    with patch(
        "blog_summaries.main.fetch_articles", return_value=[make_article("a", "One")]
    ) as fetch:
        assert len(fetch_pages(blog, 1)) == 1
    fetch.assert_called_once_with(ZVI.feed_url, ZVI.default_author)
    assert archive.calls == [(0, ZVI.default_author)]
