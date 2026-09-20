# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeStubs=false
import dataclasses
import datetime
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from typing import Protocol, TypedDict, cast

import feedparser
from bs4 import BeautifulSoup, Tag

USER_AGENT = (
    "Mozilla/5.0 (blog-summaries; +https://github.com/Martin-Milbradt/blog-summaries)"
)
# Guard rail against a runaway feed payload, not a budget: the longest posts run
# about 100k characters, so real articles never hit it.
MAX_TEXT_LENGTH = 400_000
# Substack feeds hold 20 entries, so archive page 2 starts where the feed ends.
ARCHIVE_PAGE_SIZE = 20
# Tags that end a paragraph: a blank line before and after.
PARAGRAPH_TAGS = [
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
]
# Tags that end a line within a paragraph-level block.
LINE_TAGS = ["br", "hr", "li", "tr"]


class FeedFetchError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Article:
    guid: str
    title: str
    link: str
    author: str
    pub_date: datetime.datetime
    content_html: str
    audio: bool = False
    """A podcast episode: the body is show notes, not the content."""


class Archive(Protocol):
    """Posts older than the feed carries, one page at a time. Page 1 is the feed itself."""

    def fetch_page(self, page: int, default_author: str) -> list[Article]: ...


@dataclasses.dataclass(frozen=True)
class WordPressArchive:
    feed_url: str

    def fetch_page(self, page: int, default_author: str) -> list[Article]:
        return fetch_articles(f"{self.feed_url}?paged={page}", default_author)


class SubstackByline(TypedDict):
    name: str


class SubstackPost(TypedDict):
    slug: str
    title: str
    type: str
    canonical_url: str
    post_date: str
    publishedBylines: list[SubstackByline]


class SubstackPostBody(TypedDict):
    body_html: str | None


@dataclasses.dataclass(frozen=True)
class SubstackArchive:
    site_url: str

    def fetch_page(self, page: int, default_author: str) -> list[Article]:
        offset = (page - 1) * ARCHIVE_PAGE_SIZE
        listing_url = f"{self.site_url}/api/v1/archive?sort=new&offset={offset}&limit={ARCHIVE_PAGE_SIZE}"
        posts = cast(list[SubstackPost], fetch_json(listing_url))
        articles: list[Article] = []
        for post in posts:
            audio = post["type"] == "podcast"
            content_html = ""
            if not audio:
                # The archive listing carries no bodies; each post is its own request.
                body = cast(
                    SubstackPostBody,
                    fetch_json(f"{self.site_url}/api/v1/posts/{post['slug']}"),
                )
                content_html = body["body_html"] or ""
            bylines = ", ".join(b["name"] for b in post.get("publishedBylines", []))
            articles.append(
                Article(
                    guid=post["canonical_url"],
                    title=post["title"],
                    link=post["canonical_url"],
                    author=bylines or default_author,
                    pub_date=datetime.datetime.fromisoformat(post["post_date"]),
                    content_html=content_html,
                    audio=audio,
                )
            )
        return articles


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = cast(
            http.client.HTTPResponse, urllib.request.urlopen(request, timeout=60)
        )
    except urllib.error.HTTPError as exc:
        raise FeedFetchError(f"Request returned HTTP {exc.code} for {url}.") from exc
    with response:
        return cast(object, json.loads(response.read()))


def fetch_articles(url: str, default_author: str) -> list[Article]:
    feed = feedparser.parse(url, agent=USER_AGENT)

    status = cast(int | None, getattr(feed, "status", None))
    bozo = bool(getattr(feed, "bozo", False))
    bozo_exception = getattr(feed, "bozo_exception", None)
    entries = cast(list[object], feed.entries)
    print(f"Feed status={status} bozo={bozo} entries={len(entries)}")  # noqa: T201

    if status is not None and status >= 400:
        raise FeedFetchError(f"Feed request returned HTTP {status} for {url}.")
    if bozo and not entries:
        raise FeedFetchError(f"Feed parse failed for {url}: {bozo_exception!r}")
    if not entries:
        raise FeedFetchError(
            f"Feed returned zero entries for {url} (likely blocked or empty)."
        )

    articles: list[Article] = []
    for entry in feed.entries:
        content_html = ""
        if entry.get("content"):
            content_html = cast(str, entry["content"][0].get("value", ""))
        if not content_html:
            content_html = cast(str, entry.get("summary", ""))

        pub_date = datetime.datetime.now(datetime.UTC)
        parsed = cast(
            time.struct_time | None,
            entry.get("published_parsed") or entry.get("updated_parsed"),
        )
        if parsed:
            pub_date = datetime.datetime(*parsed[:6], tzinfo=datetime.UTC)

        enclosures = cast(list[dict[str, str]], entry.get("enclosures", []))
        audio = any(e.get("type", "").startswith("audio/") for e in enclosures)

        articles.append(
            Article(
                guid=cast(str, entry.get("id", entry.get("link", ""))),
                title=cast(str, entry.get("title", "")),
                link=cast(str, entry.get("link", "")),
                author=cast(str, entry.get("author", default_author)),
                pub_date=pub_date,
                content_html=content_html,
                audio=audio,
            )
        )
    return articles


def block_text(root: Tag) -> str:
    """Text of a subtree: newlines at block boundaries only, inline markup joined."""
    # get_text(separator) would also split at inline tags like <a> and <strong>,
    # scattering one sentence over several lines, so only block boundaries break.
    for tag in root.find_all(PARAGRAPH_TAGS):
        _ = tag.insert_before("\n\n")
        _ = tag.insert_after("\n\n")
    for tag in root.find_all(LINE_TAGS):
        _ = tag.insert_after("\n")

    text = root.get_text().replace("​", "")
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def strip_html(html: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Article as light markdown: `#` headings, list markers, `>` quotes, `[image]`."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all(["script", "style", "svg"]):
        tag.decompose()

    # Remove Substack subscription widgets
    for div in soup.find_all("div", class_=re.compile(r"subscription-widget")):
        div.decompose()

    # Formatting whitespace between tags would otherwise put blank lines between
    # list items.
    for node in soup.find_all(string=True):
        if "\n" in node and not node.strip():
            _ = node.replace_with(" ")

    # A placeholder keeps sentences like "this chart shows" pointing at something.
    # Images come double-wrapped in <figure>, so only the outer one is replaced.
    for figure in soup.find_all("figure"):
        if figure.find_parent("figure"):
            continue
        caption = figure.find("figcaption")
        label = (
            f"[image: {caption.get_text(' ', strip=True)}]" if caption else "[image]"
        )
        _ = figure.replace_with(f"\n\n{label}\n\n")

    # Emoji arrive as <img> tags with the character in alt.
    for img in soup.find_all("img"):
        _ = img.replace_with(cast(str, img.get("alt", "")) or "[image]")

    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        _ = heading.insert(0, "#" * int(heading.name[1]) + " ")
    for ordered in soup.find_all("ol"):
        for number, item in enumerate(ordered.find_all("li", recursive=False), 1):
            _ = item.insert(0, f"{number}. ")
    for unordered in soup.find_all("ul"):
        for item in unordered.find_all("li", recursive=False):
            _ = item.insert(0, "- ")

    # <blockquote> is how the author marks quoted material. Innermost first, so a
    # quote inside a quote comes out as "> > ".
    for quote in reversed(soup.find_all("blockquote")):
        quoted = "\n".join(
            f"> {line}".rstrip() for line in block_text(quote).split("\n")
        )
        _ = quote.replace_with(f"\n\n{quoted}\n\n")

    text = block_text(soup)

    if len(text) > max_length:
        print(  # noqa: T201
            f"  WARNING: text truncated from {len(text):,} to {max_length:,} chars"
        )
        text = text[:max_length] + "\n\n[Content truncated]"

    return text
