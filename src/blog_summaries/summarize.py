import collections.abc
import dataclasses
import datetime
import os

import openai

DEFAULT_KNOWLEDGE_CUTOFF = "January 2026"
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
You summarize {subject}.
Write exactly four paragraphs of plain prose: no bullet points, headers, or editorializing beyond what the author wrote.
Your knowledge cutoff is {knowledge_cutoff} and today is {today}; the article may cover events after your cutoff, so trust its account over your priors."""


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


def post_chat(messages: collections.abc.Sequence[ChatMessage], model: str) -> str:
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
    return content


def summarize_with_fallback(
    title: str,
    text: str,
    subject: str,
    model: str,
    fallback: str | None,
) -> tuple[str, str]:
    """Summarize, retrying a refusal once on the fallback model.

    Returns the summary and the model that produced it. Raises ArticleRefusedError
    if there is no usable fallback or if the fallback refuses too.
    """
    try:
        return summarize_article(title, text, subject, model), model
    except ArticleRefusedError:
        if not fallback or fallback == model:
            raise
        print(f"  refused by {model}, retrying with {fallback}")  # noqa: T201

    return summarize_article(title, text, subject, fallback), fallback


def summarize_article(title: str, text: str, subject: str, model: str) -> str:
    messages = [
        ChatMessage(role="system", content=system_prompt(subject)),
        ChatMessage(
            role="user",
            content=f"Summarize this article.\n\nTitle: {title}\n\nContent:\n{text}",
        ),
    ]
    return post_chat(messages, model)
