import argparse
import collections.abc
import datetime
from pathlib import Path
from typing import cast

from blog_summaries.blogs import BLOGS, BlogConfig
from blog_summaries.cache import CachedSummary, load_cache, save_cache
from blog_summaries.fetch import Article, fetch_articles, strip_html
from blog_summaries.generate import build_feed
from blog_summaries.summarize import (
    ArticleRefusedError,
    configured_fallback_model,
    configured_model,
    summarize_with_fallback,
)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DOCS_DIR = Path("docs")


class RefusedArticlesError(Exception):
    pass


def cache_path(data_dir: Path, blog: BlogConfig) -> Path:
    return data_dir / f"{blog.slug}.json"


def output_path(docs_dir: Path, blog: BlogConfig) -> Path:
    return docs_dir / blog.slug / "feed.xml"


def fetch_pages(blog: BlogConfig, pages: int) -> list[Article]:
    """Articles from the feed, plus older archive pages so gaps stay recoverable."""
    articles = fetch_articles(blog.feed_url, blog.default_author)
    seen = {a.guid for a in articles}
    for page in range(2, pages + 1):
        for article in blog.archive.fetch_page(page, blog.default_author):
            if article.guid not in seen:
                seen.add(article.guid)
                articles.append(article)
    return articles


def run_blog(
    blog: BlogConfig,
    data_dir: Path,
    docs_dir: Path,
    pages: int,
    model: str,
    fallback: str | None,
) -> tuple[int, list[str]]:
    """Returns the number of newly summarized articles and the titles refused."""
    cache_file = cache_path(data_dir, blog)
    cache = load_cache(cache_file)
    articles = fetch_pages(blog, pages)

    new_count = 0
    refused: list[str] = []
    for article in articles:
        if article.guid in cache:
            continue
        if article.audio:
            print(f"Skipping podcast: {article.title}")  # noqa: T201
            continue

        text = strip_html(article.content_html)
        print(f"Summarizing: {article.title} ({len(text):,} chars)")  # noqa: T201
        # Refusals must not abort the run: the remaining articles are still
        # summarizable, and the feed only recovers if they get written.
        try:
            summary, used_model = summarize_with_fallback(
                article.title, text, blog.prompt_subject, model, fallback
            )
        except ArticleRefusedError as exc:
            print(f"  REFUSED: {exc}")  # noqa: T201
            refused.append(article.title)
            continue

        cache[article.guid] = CachedSummary(
            title=article.title,
            link=article.link,
            author=article.author,
            pub_date=article.pub_date.isoformat(),
            summary=summary,
            summarized_at=datetime.datetime.now(datetime.UTC).isoformat(),
            model=used_model,
        )
        new_count += 1

        # Save after each article so partial runs preserve progress
        save_cache(cache_file, cache)

    feed_file = output_path(docs_dir, blog)
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    with feed_file.open("w", encoding="utf-8") as f:
        _ = f.write(build_feed(blog, cache))

    print(  # noqa: T201
        f"{blog.slug}: {new_count} new summaries. Total: {len(cache)}."
    )
    return new_count, refused


def run(
    blogs: collections.abc.Sequence[BlogConfig],
    data_dir: Path = DEFAULT_DATA_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    pages: int = 1,
) -> int:
    """Returns the number of newly summarized articles across all blogs."""
    model = configured_model()
    fallback = configured_fallback_model()

    total_new = 0
    refused: list[str] = []
    for blog in blogs:
        new_count, blog_refused = run_blog(
            blog, data_dir, docs_dir, pages, model, fallback
        )
        total_new += new_count
        refused.extend(f"{blog.slug}: {title}" for title in blog_refused)

    # Raised last, so every cache and feed above is already written: the workflow
    # commits them regardless, and the failure still surfaces as a notification.
    if refused:
        raise RefusedArticlesError(
            f"{len(refused)} article(s) refused by the model: {'; '.join(refused)}"
        )
    return total_new


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate summarized RSS feeds")
    _ = parser.add_argument(
        "blogs",
        nargs="*",
        choices=sorted(BLOGS),
        help="Blogs to process; all of them when omitted.",
    )
    _ = parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    _ = parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    _ = parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Archive pages to scan; >1 recovers posts that scrolled out of the feed.",
    )
    args = parser.parse_args()
    slugs = cast(list[str], args.blogs) or sorted(BLOGS)
    _ = run(
        blogs=[BLOGS[slug] for slug in slugs],
        data_dir=cast(Path, args.data_dir),
        docs_dir=cast(Path, args.docs_dir),
        pages=cast(int, args.pages),
    )


if __name__ == "__main__":
    main()
