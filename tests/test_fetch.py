from unittest.mock import patch

from blog_summaries.fetch import SubstackArchive, fetch_articles, strip_html

RSS_WITH_AUDIO = """\
<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Feed</title>
<item>
  <title>Episode</title><link>https://example.com/p/episode</link>
  <guid isPermaLink="false">https://example.com/p/episode</guid>
  <dc:creator>Host</dc:creator>
  <enclosure url="https://example.com/episode.mp3" length="1" type="audio/mpeg"/>
  <description><![CDATA[<p>Show notes.</p>]]></description>
</item>
<item>
  <title>Essay</title><link>https://example.com/p/essay</link>
  <guid isPermaLink="false">https://example.com/p/essay</guid>
  <enclosure url="https://example.com/cover.png" length="1" type="image/png"/>
  <description><![CDATA[<p>Body.</p>]]></description>
</item>
</channel></rss>
"""


def test_fetch_articles_marks_audio_enclosures_and_fills_missing_author() -> None:
    episode, essay = fetch_articles(RSS_WITH_AUDIO, "Fallback Author")
    assert (episode.title, episode.audio, episode.author) == ("Episode", True, "Host")
    assert (essay.audio, essay.author) == (False, "Fallback Author")


def test_substack_archive_fetches_bodies_except_for_podcasts() -> None:
    listing = [
        {
            "slug": "essay",
            "title": "Essay",
            "type": "newsletter",
            "canonical_url": "https://example.com/p/essay",
            "post_date": "2026-09-11T17:12:25.000Z",
            "publishedBylines": [{"name": "A"}, {"name": "B"}],
        },
        {
            "slug": "episode",
            "title": "Episode",
            "type": "podcast",
            "canonical_url": "https://example.com/p/episode",
            "post_date": "2026-09-10T00:00:00.000Z",
            "publishedBylines": [],
        },
    ]

    def fake_fetch_json(url: str) -> object:
        if url.endswith("/api/v1/posts/essay"):
            return {"body_html": "<p>Body.</p>"}
        assert "/api/v1/archive?sort=new&offset=20&limit=20" in url
        return listing

    with patch("blog_summaries.fetch.fetch_json", side_effect=fake_fetch_json) as fetch:
        essay, episode = SubstackArchive("https://example.com").fetch_page(2, "Blog")

    assert fetch.call_count == 2
    assert (essay.guid, essay.author, essay.content_html) == (
        "https://example.com/p/essay",
        "A, B",
        "<p>Body.</p>",
    )
    assert essay.pub_date.isoformat() == "2026-09-11T17:12:25+00:00"
    assert (episode.audio, episode.author, episode.content_html) == (True, "Blog", "")


def test_strip_html_basic() -> None:
    html = "<p>Hello <strong>world</strong>.</p><p>Second paragraph.</p>"
    result = strip_html(html)
    assert "Hello" in result
    assert "world" in result
    assert "Second paragraph." in result


def test_strip_html_removes_images() -> None:
    html = '<p>Before</p><img src="photo.jpg"><p>After</p>'
    result = strip_html(html)
    assert "Before" in result
    assert "After" in result
    assert "photo.jpg" not in result


def test_strip_html_removes_subscription_widget() -> None:
    html = (
        "<p>Content here.</p>"
        '<div class="subscription-widget-wrap"><p>Subscribe now</p></div>'
        "<p>More content.</p>"
    )
    result = strip_html(html)
    assert "Content here." in result
    assert "More content." in result
    assert "Subscribe" not in result


def test_strip_html_removes_script_and_style() -> None:
    html = "<style>body{color:red}</style><script>alert(1)</script><p>Text</p>"
    result = strip_html(html)
    assert "Text" in result
    assert "color:red" not in result
    assert "alert" not in result


def test_strip_html_truncation() -> None:
    html = "<p>" + "a" * 20_000 + "</p>"
    result = strip_html(html, max_length=100)
    assert len(result) < 200
    assert "[Content truncated]" in result


def test_strip_html_no_truncation_within_limit() -> None:
    html = "<p>Short text</p>"
    result = strip_html(html, max_length=1000)
    assert "[Content truncated]" not in result


def test_strip_html_collapses_blank_lines() -> None:
    html = "<p>One</p><br><br><br><br><p>Two</p>"
    result = strip_html(html)
    # Should not have more than one blank line between paragraphs
    assert "\n\n\n" not in result


def test_strip_html_keeps_inline_markup_in_one_line() -> None:
    html = '<p>Hello <strong>world</strong>, see <a href="x">this post</a>.</p>'
    assert strip_html(html) == "Hello world, see this post."


def test_strip_html_separates_paragraphs_and_list_items() -> None:
    html = "<p>Intro</p><ol><li>One</li><li>Two</li></ol><p>Outro</p>"
    assert strip_html(html) == "Intro\n\n1. One\n2. Two\n\nOutro"


def test_strip_html_keeps_emoji_alt_text() -> None:
    html = '<p>Done <img class="wp-smiley" alt="✅" src="e.png"> here.</p>'
    assert strip_html(html) == "Done ✅ here."


def test_strip_html_default_limit_fits_long_posts() -> None:
    html = "<p>" + "word " * 30_000 + "</p>"
    assert "[Content truncated]" not in strip_html(html)


def test_strip_html_prefixes_block_quotes() -> None:
    html = (
        "<p>He wrote:</p>"
        "<blockquote><p>First.</p><p>Second <em>line</em>.</p></blockquote>"
        "<p>Reply.</p>"
    )
    assert strip_html(html) == "He wrote:\n\n> First.\n>\n> Second line.\n\nReply."


def test_strip_html_nests_quote_prefixes() -> None:
    html = "<blockquote><p>Outer</p><blockquote><p>Inner</p></blockquote></blockquote>"
    assert strip_html(html) == "> Outer\n>\n> > Inner"


def test_strip_html_marks_headings_and_list_items() -> None:
    html = "<h4>Title</h4>\n<ol>\n<li>One</li>\n<li>Two</li>\n</ol>\n<ul><li>Dash</li></ul>"
    assert strip_html(html) == "#### Title\n\n1. One\n2. Two\n\n- Dash"


def test_strip_html_replaces_figures_with_placeholder() -> None:
    html = (
        "<p>See:</p>"
        '<figure><figure><img src="a.png" alt=""></figure>'
        "<figcaption>A chart</figcaption></figure>"
        '<p>Plain <img src="b.png"> here.</p>'
    )
    assert strip_html(html) == "See:\n\n[image: A chart]\n\nPlain [image] here."


def test_strip_html_drops_zero_width_spaces() -> None:
    assert strip_html("<p>A​B</p>") == "AB"
