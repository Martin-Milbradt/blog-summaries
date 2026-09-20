from unittest.mock import patch

import pytest

from blog_summaries.summarize import (
    ArticleRefusedError,
    MissingOpenRouterKeyError,
    configured_fallback_model,
    environment_api_key,
    summarize_with_fallback,
    system_prompt,
)


def test_environment_api_key_missing() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(MissingOpenRouterKeyError):
            environment_api_key()


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"FALLBACK_MODEL": "ci/model", "LLM_STRONG": "tier/model"}, "ci/model"),
        ({"LLM_STRONG": "tier/model"}, "tier/model"),
        ({}, None),
    ],
)
def test_configured_fallback_model(
    environ: dict[str, str], expected: str | None
) -> None:
    with patch.dict("os.environ", environ, clear=True):
        assert configured_fallback_model() == expected


def test_system_prompt_names_the_blog() -> None:
    assert system_prompt("posts by Someone").startswith(
        "You summarize posts by Someone.\n"
    )


def test_summarize_with_fallback_retries_refusal() -> None:
    def fake_summarize(title: str, text: str, subject: str, model: str) -> str:
        if model == "primary/model":
            raise ArticleRefusedError("blocked")
        return "Fallback summary."

    with patch(
        "blog_summaries.summarize.summarize_article", side_effect=fake_summarize
    ):
        summary, used = summarize_with_fallback(
            "Title", "Body", "posts", "primary/model", "backup/model"
        )
    assert (summary, used) == ("Fallback summary.", "backup/model")


@pytest.mark.parametrize("fallback", [None, "primary/model"])
def test_summarize_with_fallback_reraises_without_usable_fallback(
    fallback: str | None,
) -> None:
    def always_refuse(title: str, text: str, subject: str, model: str) -> str:
        raise ArticleRefusedError("blocked")

    with patch("blog_summaries.summarize.summarize_article", side_effect=always_refuse):
        with pytest.raises(ArticleRefusedError):
            _ = summarize_with_fallback(
                "Title", "Body", "posts", "primary/model", fallback
            )
