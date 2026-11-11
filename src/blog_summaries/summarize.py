import collections.abc
import dataclasses
import datetime
import functools
import http.client
import json
import os
import urllib.request
from typing import TypedDict, cast

import openai

DEFAULT_KNOWLEDGE_CUTOFF = "June 2026"
MAX_OUTPUT_TOKENS = 5000
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class MissingModelError(Exception):
    pass


def configured_model() -> str:
    """Model id to use: SUMMARY_MODEL (CI repo variable) wins, then the global LLM_MAX tier."""
    model = (
        os.environ.get("SUMMARY_MODEL", "").strip()
        or os.environ.get("LLM_MAX", "").strip()
    )
    if model:
        return model
    raise MissingModelError(
        "No summarization model configured. Set SUMMARY_MODEL or the global LLM_MAX tier."
    )


def configured_fallback_model() -> str | None:
    """Model retried after a refusal: FALLBACK_MODEL (CI repo variable) wins, then LLM_STRONG.

    Refusals are per-model rather than per-provider, so any model that does not
    share SUMMARY_MODEL's content restrictions works here.
    """
    return (
        os.environ.get("FALLBACK_MODEL", "").strip()
        or os.environ.get("LLM_STRONG", "").strip()
        or None
    )


def configured_knowledge_cutoff() -> str:
    """Cutoff stated in the prompt: KNOWLEDGE_CUTOFF (CI) wins, then the default."""
    return os.environ.get("KNOWLEDGE_CUTOFF", "").strip() or DEFAULT_KNOWLEDGE_CUTOFF


SYSTEM_PROMPT_TEMPLATE = """\
You write the entries of an RSS feed that condenses {subject}. Subscribers read your entry in place of the post, or to decide whether the full post is worth their time. So it should carry what the post actually says: its main claims, the reasoning and evidence behind them, and its conclusions.

Voice: write a short version of the post in its authors' own voice, not a report about the post. Keep the grammatical person the post uses: where it says "I think" or "we found", so do you. State the content directly. Framing such as "the author argues", "the post concludes" or "this piece lists" only reminds readers that they are reading a summary, which they know, and costs words that could carry content. For example, write "Labs should publish their escalation rules", not "The author argues that labs should publish their escalation rules". Because you are speaking for the authors, stay with what they say and the stance they take, and add no assessment of your own.

Names: the user message says who wrote the post. Sometimes an author has to be mentioned in the third person, for instance when co-authors wrote separate sections or disagree with each other. Use their first name then, never "the author". If the post does not make clear which co-author a passage belongs to, write "the authors", or simply keep the post's "we".

Format: exactly four paragraphs of plain prose, separated by blank lines. The feed shows your text as written, so markdown, headings and bullet points would appear as literal characters. The reader already sees the post's title, so begin with the content itself.

The post arrives as light markdown. Lines starting with ">" are material the authors quote from someone else, so attribute those views by name to the person quoted and keep them distinct from the authors' own. "[image]" marks where a figure stood. You cannot see it, so rely on what the surrounding text says about it.

Your knowledge cutoff is {knowledge_cutoff} and today is {today}. The post may cover events, models and people from after your cutoff, so trust its account over your priors."""

USER_PROMPT_TEMPLATE = """\
Title: {title}
Written by: {author}

<post>
{text}
</post>

Write the feed entry for this post."""


def system_prompt(subject: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject,
        knowledge_cutoff=configured_knowledge_cutoff(),
        today=datetime.date.today().isoformat(),
    )


@dataclasses.dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclasses.dataclass(frozen=True)
class Completion:
    content: str
    model: str
    """Id of the model that answered, which resolves an alias like `~vendor/x-latest`."""


class OpenRouterModel(TypedDict):
    id: str
    name: str
    canonical_slug: str


class OpenRouterModels(TypedDict):
    data: list[OpenRouterModel]


class MissingOpenRouterKeyError(Exception):
    pass


class OpenRouterRequestError(Exception):
    pass


class ArticleRefusedError(Exception):
    """The model declined to summarize the article, e.g. under a usage policy."""


def environment_api_key() -> str:
    token = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if token:
        return token
    raise MissingOpenRouterKeyError("Set the OPENROUTER_API_KEY environment variable.")


def serialize_messages(
    messages: collections.abc.Sequence[ChatMessage],
) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def post_chat(
    messages: collections.abc.Sequence[ChatMessage], model: str
) -> Completion:
    api_key = environment_api_key()
    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        timeout=120.0,
    )
    # OpenRouter reserves credit for the full max_tokens up front, so leaving this
    # unset holds the model's entire output ceiling against the key's limit.
    response = client.chat.completions.create(
        model=model,
        messages=serialize_messages(messages),  # pyright: ignore[reportArgumentType]
        max_tokens=MAX_OUTPUT_TOKENS,
    )
    choice = response.choices[0]
    refusal = choice.message.refusal
    if refusal or choice.finish_reason == "content_filter":
        raise ArticleRefusedError(
            refusal or "Blocked by the provider's content filter."
        )
    if choice.finish_reason == "length":
        raise OpenRouterRequestError(
            f"Summary hit the {MAX_OUTPUT_TOKENS}-token output cap and was truncated."
        )

    content = choice.message.content
    if not content:
        raise OpenRouterRequestError("OpenRouter response contained empty content.")
    return Completion(content=content, model=response.model)


@functools.cache
def fetch_model_names() -> dict[str, str]:
    """OpenRouter's display name per model id, e.g. "Anthropic: Claude Opus 4.1"."""
    response = cast(
        http.client.HTTPResponse,
        urllib.request.urlopen(f"{OPENROUTER_BASE_URL}/models", timeout=60),
    )
    with response:
        models = cast(OpenRouterModels, json.loads(response.read()))["data"]
    names = {model["id"]: model["name"] for model in models}
    # A completion may report the dated slug instead of the listed id.
    for model in models:
        _ = names.setdefault(model["canonical_slug"], model["name"])
    return names


def model_display_name(model: str) -> str:
    """Readable name for a model id, without the vendor prefix. Unlisted ids pass through."""
    return fetch_model_names().get(model, model).split(": ", 1)[-1]


def summarize_with_fallback(
    title: str,
    author: str,
    text: str,
    subject: str,
    model: str,
    fallback: str | None,
) -> Completion:
    """Summarize, retrying a refusal once on the fallback model.

    Raises ArticleRefusedError if there is no usable fallback or if the fallback
    refuses too.
    """
    try:
        return summarize_article(title, author, text, subject, model)
    except ArticleRefusedError:
        if not fallback or fallback == model:
            raise
        print(f"  refused by {model}, retrying with {fallback}")  # noqa: T201

    return summarize_article(title, author, text, subject, fallback)


def summarize_article(
    title: str, author: str, text: str, subject: str, model: str
) -> Completion:
    messages = [
        ChatMessage(role="system", content=system_prompt(subject)),
        ChatMessage(
            role="user",
            content=USER_PROMPT_TEMPLATE.format(title=title, author=author, text=text),
        ),
    ]
    return post_chat(messages, model)
