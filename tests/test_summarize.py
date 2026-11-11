from typing import cast
from unittest.mock import patch

import pytest

from blog_summaries.summarize import (
    ArticleRefusedError,
    ChatMessage,
    Completion,
    MissingOpenRouterKeyError,
    configured_fallback_model,
    environment_api_key,
    model_display_name,
    summarize_article,
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
    assert "condenses posts by Someone." in system_prompt("posts by Someone")


def test_summarize_article_names_the_author_and_reports_the_resolved_model() -> None:
    with patch(
        "blog_summaries.summarize.post_chat",
        return_value=Completion("Summary.", "vendor/model-2"),
    ) as post_chat:
        completion = summarize_article(
            "Title", "A One, B Two", "Body", "posts", "~vendor/model-latest"
        )
    user_message = cast(list[ChatMessage], post_chat.call_args.args[0])[1]
    assert "Written by: A One, B Two\n" in user_message.content
    assert completion.model == "vendor/model-2"


def test_model_display_name_drops_the_vendor_prefix() -> None:
    names = {"vendor/model-2": "Vendor: Model 2"}
    with patch("blog_summaries.summarize.fetch_model_names", return_value=names):
        assert model_display_name("vendor/model-2") == "Model 2"
        assert model_display_name("vendor/unlisted") == "vendor/unlisted"


def test_summarize_with_fallback_retries_refusal() -> None:
    def fake_summarize(
        title: str, author: str, text: str, subject: str, model: str
    ) -> Completion:
        if model == "primary/model":
            raise ArticleRefusedError("blocked")
        return Completion("Fallback summary.", model)

    with patch(
        "blog_summaries.summarize.summarize_article", side_effect=fake_summarize
    ):
        completion = summarize_with_fallback(
            "Title", "Author", "Body", "posts", "primary/model", "backup/model"
        )
    assert completion == Completion("Fallback summary.", "backup/model")


@pytest.mark.parametrize("fallback", [None, "primary/model"])
def test_summarize_with_fallback_reraises_without_usable_fallback(
    fallback: str | None,
) -> None:
    def always_refuse(
        title: str, author: str, text: str, subject: str, model: str
    ) -> Completion:
        raise ArticleRefusedError("blocked")

    with patch("blog_summaries.summarize.summarize_article", side_effect=always_refuse):
        with pytest.raises(ArticleRefusedError):
            _ = summarize_with_fallback(
                "Title", "Author", "Body", "posts", "primary/model", fallback
            )
