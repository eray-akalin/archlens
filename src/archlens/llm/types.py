"""Request/response types shared by the client, providers, cache, cassettes and FakeLLM."""

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, JsonValue

from archlens.models import LLMCallRecord

ChatMessage = dict[str, JsonValue]  # OpenAI chat-completions message format


@dataclass(frozen=True)
class ToolSpec:
    """A strict function tool; `args` is the Pydantic model of its arguments."""

    name: str
    description: str
    args: type[BaseModel]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text exactly as the model produced it


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens (billed as output)
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    """What a provider returned, normalized; serializable for the exact cache and cassettes."""

    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    refusal: str | None = None
    usage: Usage = field(default_factory=Usage)

    def to_json(self) -> str:
        return json.dumps(
            {
                "content": self.content,
                "tool_calls": [
                    {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls
                ],
                "finish_reason": self.finish_reason,
                "refusal": self.refusal,
                "usage": self.usage.__dict__,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> "ProviderResponse":
        data = json.loads(text)
        return cls(
            content=data["content"],
            tool_calls=tuple(
                ToolCall(c["id"], c["name"], c["arguments"]) for c in data["tool_calls"]
            ),
            finish_reason=data["finish_reason"],
            refusal=data["refusal"],
            usage=Usage(**data["usage"]),
        )


@dataclass(frozen=True)
class ChatPayload:
    """One provider call: what goes over the wire, plus the normalized request `key`."""

    deployment: str
    messages: list[ChatMessage]
    response_format: type[BaseModel]
    tools: tuple[ToolSpec, ...]
    params: dict[str, JsonValue]
    key: str


@dataclass(frozen=True)
class LLMRequest[T: BaseModel]:
    role: str  # evaluator | tiebreak | verifier | skeptic | synth (config/models.yaml)
    stage: str
    prompt_version: str  # "{id}@{version}+{sha8}"
    messages: list[ChatMessage]
    response_format: type[T]
    metric: str | None = None
    tools: tuple[ToolSpec, ...] = ()
    tags: tuple[str, ...] = ()  # e.g. check IDs; FakeLLM scripts can key on them
    use_cache: bool = True


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    parsed: T | None  # None when the model asked for tools instead
    tool_calls: tuple[ToolCall, ...]
    message: ChatMessage  # the assistant message, ready to append to the conversation
    records: tuple[LLMCallRecord, ...]  # one per attempt (cache hits included)


def assistant_message(response: ProviderResponse) -> ChatMessage:
    message: ChatMessage = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in response.tool_calls
        ]
    return message
