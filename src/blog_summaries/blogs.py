import dataclasses

from blog_summaries.fetch import Archive, SubstackArchive, WordPressArchive

PAGES_BASE_URL = "https://martinmilbradt.de/blog-summaries"


@dataclasses.dataclass(frozen=True)
class BlogConfig:
    slug: str
    """CLI name and the path segment of the cache file and the published feed."""
    name: str
    """Title of the source blog, shown in the summary feed's title."""
    site_url: str
    feed_url: str
    archive: Archive
    """Source of posts older than the feed holds, for `--pages` and backfills."""
    default_author: str
    """Creator credited when the feed entry names none."""
    prompt_subject: str
    """Completes "You summarize ..." in the system prompt."""
    feed_description: str

    @property
    def feed_title(self) -> str:
        return f"{self.name} -- Summaries"

    @property
    def self_url(self) -> str:
        return f"{PAGES_BASE_URL}/{self.slug}/feed.xml"


# Zvi's Substack sits behind a Cloudflare bot challenge that rejects GitHub
# Actions runners, so posts come from the WordPress mirror, which serves full
# bodies without comments.
ZVI = BlogConfig(
    slug="zvi",
    name="Don't Worry About the Vase",
    site_url="https://thezvi.substack.com",
    feed_url="https://thezvi.wordpress.com/feed/",
    archive=WordPressArchive(feed_url="https://thezvi.wordpress.com/feed/"),
    default_author="Zvi Mowshowitz",
    prompt_subject="blog posts by Zvi Mowshowitz",
    feed_description=(
        "LLM-generated four-paragraph summaries of Zvi Mowshowitz's blog posts."
    ),
)

REDWOOD = BlogConfig(
    slug="redwood",
    name="Redwood Research blog",
    site_url="https://blog.redwoodresearch.org",
    feed_url="https://blog.redwoodresearch.org/feed",
    archive=SubstackArchive(site_url="https://blog.redwoodresearch.org"),
    default_author="Redwood Research",
    prompt_subject=(
        "posts from the Redwood Research blog, written by researchers at an AI "
        "safety nonprofit about AI control, misalignment risk and AI forecasting"
    ),
    feed_description=(
        "LLM-generated four-paragraph summaries of posts on the Redwood Research blog."
    ),
)

BLOGS: dict[str, BlogConfig] = {blog.slug: blog for blog in (ZVI, REDWOOD)}
