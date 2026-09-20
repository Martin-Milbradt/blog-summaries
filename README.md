# blog-summaries

RSS feeds of LLM-generated four-paragraph summaries of blogs, one feed per blog.

## Feeds

| Blog | Feed |
| --- | --- |
| [Don't Worry About the Vase](https://thezvi.substack.com) | <https://martinmilbradt.de/blog-summaries/zvi/feed.xml> |
| [Redwood Research blog](https://blog.redwoodresearch.org) | <https://martinmilbradt.de/blog-summaries/redwood/feed.xml> |

## Usage

```bash
uv sync --dev                     # Install dependencies
uv run blog-summaries             # Run pipeline for all blogs (requires OPENROUTER_API_KEY)
uv run blog-summaries redwood     # One blog only
uv run pytest                     # Run tests
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
uv run basedpyright src/          # Type check
```

## How it works

For each blog in `src/blog_summaries/blogs.py`, fetches the RSS feed, summarizes uncached articles via OpenRouter, and writes `docs/<slug>/feed.xml` (served by GitHub Pages). Summaries are cached in `data/<slug>.json`. A GitHub Actions workflow runs this every six hours and commits the result.

## Adding a blog

Add a `BlogConfig` to `blogs.py` with the feed URL, an archive backend for older posts, the prompt subject and the feed metadata, then link the new feed from `docs/index.html`.
