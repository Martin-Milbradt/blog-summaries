# blog-summaries

RSS feeds of LLM-generated four-paragraph summaries, one feed per source blog. Blogs are declared in `blogs.py` as `BlogConfig` entries; everything else is shared.

## Commands

```bash
uv sync --dev                                    # Install dependencies
uv run blog-summaries                            # Run the pipeline for every blog (needs OPENROUTER_API_KEY)
uv run blog-summaries redwood --pages 3          # One blog, scanning two archive pages beyond the feed
uv run pytest                                    # Run tests
uv run ruff check src/ tests/                    # Lint
uv run ruff format src/ tests/                   # Format
uv run basedpyright src/                         # Type check
```

## Architecture

Pipeline per blog: fetch the RSS feed -> filter uncached articles -> summarize via OpenRouter -> write cache + feed XML. `run()` loops over the requested blogs and raises only after all of them are written.

- `src/blog_summaries/blogs.py` -- `BlogConfig` registry: feed URL, archive backend, prompt subject, feed metadata
- `src/blog_summaries/fetch.py` -- RSS parsing, archive backends, HTML to light markdown
- `src/blog_summaries/summarize.py` -- OpenRouter client, summarization prompt
- `src/blog_summaries/cache.py` -- JSON cache of article summaries
- `src/blog_summaries/generate.py` -- build RSS 2.0 XML from cache
- `src/blog_summaries/main.py` -- CLI entry point

A model may refuse an article under its content policy. The run retries once on `FALLBACK_MODEL`, keeps going past articles both models refuse, and raises `RefusedArticlesError` only after every cache and feed is written, so a failure still commits the summaries that succeeded and still sends a workflow-failure notification.

Podcast episodes (an `audio/*` enclosure in RSS, `type == "podcast"` in the Substack API) are skipped: their body is show notes, not the content.

Articles go to the model whole. `MAX_TEXT_LENGTH` in `fetch.py` only guards against a runaway payload; the longest posts reach about a quarter of it. The run log prints each article's character count. The text is light markdown: `#` headings, `1.` and `-` list markers, `>` on block quotes (the author's marker for quoted material, so the model can tell the author's words from quoted text), and an `[image]` placeholder where a figure was, so a sentence like "this chart shows" still points at something.

## Sources

- `zvi`: Zvi Mowshowitz's blog via the WordPress mirror feed. The Substack feed sits behind a Cloudflare bot challenge that returns 403 to GitHub Actions runners. Older pages come from `?paged=N` on the same feed.
- `redwood`: the Redwood Research blog via its Substack feed, which GitHub Actions can reach. Older pages come from the Substack archive API (`/api/v1/archive`, 20 posts per page) plus one `/api/v1/posts/<slug>` request per post for the body.

Bot protection is a per-publication Substack setting, so check a new Substack source from a runner before assuming either behavior.

## Output

- `data/<slug>.json` -- persisted summaries (committed)
- `docs/<slug>/feed.xml` -- generated RSS feed (served by GitHub Pages at `PAGES_BASE_URL` in `blogs.py`)
- `docs/index.html` -- hand-written list of the feeds; add a line when adding a blog

## Environment

- `OPENROUTER_API_KEY` -- required for LLM summarization
- `SUMMARY_MODEL` -- model id; the CI repo variable wins, then the global `LLM_MAX` tier. With neither set, `configured_model()` raises `MissingModelError` rather than picking one.
- `FALLBACK_MODEL` -- model retried when `SUMMARY_MODEL` refuses an article; the CI repo variable wins, then the global `LLM_STRONG` tier. Unset means no retry. Refusals are per-model, not per-provider, so a sibling model from the same provider is a valid choice.
- `KNOWLEDGE_CUTOFF` -- optional cutoff stated in the prompt (CI repo variable), defaults to `DEFAULT_KNOWLEDGE_CUTOFF`. It describes the configured model, so revisit it when `SUMMARY_MODEL` changes.

`--pages N` scans older archive pages as well as the feed. The WordPress feed holds 10 entries and Substack feeds 20, so an outage longer than that drops articles out of reach; `--pages 2` recovers them. Backfilling a whole archive is the same command with a large N.
