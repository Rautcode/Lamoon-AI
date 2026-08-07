"""AI platform seam (ADR-0005, platform §2). Chain:
    PromptRegistry.get → Model → OutputParser(schema) → Cache
Business logic calls analyze(prompt_key, output_schema, ...) and gets a
validated object back — never a prompt, model name, or raw text. The DB-backed
result cache lives in the pipeline (it owns the session); the provider is the
model call + parse only.
"""
import json
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.ai.prompts import Recipe
from app.core.ai.prompts import get as get_prompt
from app.core.config import get_settings

Vector = list[float]

TEMPERATURE = 0.2                 # shared recipe params — bump the schema version if these change
OUTPUT_SCHEMA_VERSION = "v1"


@dataclass
class AIResult:
    output: Any  # parsed to the requested output_schema
    model: str
    prompt_version: str
    recipe_hash: str
    tokens_in: int
    tokens_out: int
    cost: float
    cache_hit: bool


# --- Tool-calling ----------------------------------------------------------
# Vendor-neutral by design: the assistant service speaks these shapes and never
# imports the Gemini SDK, so swapping models is a provider change only.


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict


@dataclass
class ModelTurn:
    """What the model said this step: prose, tool calls, or both."""

    text: str | None
    tool_calls: list[ToolCall]


class AIProvider(Protocol):
    async def analyze(
        self,
        *,
        prompt_key: str,
        prompt_ver: str,
        inputs: dict,
        output_schema: type,
        cache_key: str | None = None,
    ) -> AIResult: ...
    async def embed(self, texts: list[str]) -> list[Vector]: ...
    async def converse(
        self, *, system: str, messages: list[dict], tools: list[ToolSpec]
    ) -> ModelTurn: ...


def _flash_cost(tokens_in: int, tokens_out: int) -> float:
    # ponytail: rough Gemini Flash rate; good enough for cost accounting, refine
    # against the live price sheet if AI spend ever becomes a real line item.
    return round((tokens_in * 0.075 + tokens_out * 0.30) / 1_000_000, 6)


class GeminiProvider:
    """V1: Gemini 2.5 Flash default; Pro only on explicit deep-review demand."""

    async def analyze(self, *, prompt_key, prompt_ver, inputs, output_schema, cache_key=None):
        # Lazy: no import cost / key requirement until a real call. Import the
        # submodule directly (not `from google import genai` + attribute access)
        # — mypy resolves `google` as a namespace package shared with
        # google-auth/google-oauth2 and, via attribute access, fails to see
        # `genai` as a sibling; a direct submodule import resolves cleanly.
        from google.genai import Client

        s = get_settings()
        if not s.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        prompt = get_prompt(prompt_key, prompt_ver)
        recipe = Recipe(
            prompt_key, prompt_ver, OUTPUT_SCHEMA_VERSION, s.ai_default_model, TEMPERATURE
        )
        content = f"{prompt}\n\nRESUME + JOB CONTEXT (JSON):\n{json.dumps(inputs)}"

        client = Client(api_key=s.gemini_api_key)
        resp = client.models.generate_content(
            model=s.ai_default_model,
            contents=content,
            config={
                "response_mime_type": "application/json",
                "response_schema": output_schema,
                "temperature": TEMPERATURE,
            },
        )
        parsed = output_schema.model_validate_json(resp.text)
        usage = getattr(resp, "usage_metadata", None)
        ti = getattr(usage, "prompt_token_count", 0) or 0
        to = getattr(usage, "candidates_token_count", 0) or 0
        return AIResult(
            output=parsed,
            model=s.ai_default_model,
            prompt_version=prompt_ver,
            recipe_hash=recipe.hash(prompt),
            tokens_in=ti,
            tokens_out=to,
            cost=_flash_cost(ti, to),
            cache_hit=False,
        )

    async def converse(self, *, system, messages, tools):
        """One step of a tool-calling exchange. The caller owns the loop and the
        tool execution; this only translates our vendor-neutral message shapes
        to/from the Gemini SDK.

        ponytail: UNVERIFIED against a live Gemini key — none exists in this
        environment. Written to the documented google-genai API; the assistant
        service around it is fully tested with a fake provider. Treat the first
        real call as the actual integration test.
        """
        from google.genai import Client, types

        s = get_settings()
        if not s.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set")

        contents = []
        for m in messages:
            role = m["role"]
            if role == "user":
                contents.append(
                    types.Content(role="user", parts=[types.Part(text=m["text"])])
                )
            elif role == "model":
                parts = []
                if m.get("text"):
                    parts.append(types.Part(text=m["text"]))
                for call in m.get("tool_calls", []):
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(name=call["name"], args=call["args"])
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=m["name"], response=m["result"]
                                )
                            )
                        ],
                    )
                )

        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters=t.parameters
            )
            for t in tools
        ]

        client = Client(api_key=s.gemini_api_key)
        resp = client.models.generate_content(
            model=s.ai_default_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=declarations)],
                temperature=TEMPERATURE,
            ),
        )

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    calls.append(ToolCall(name=fc.name, args=dict(fc.args or {})))

        return ModelTurn(text="".join(text_parts).strip() or None, tool_calls=calls)

    async def embed(self, texts):
        from google.genai import Client

        s = get_settings()
        client = Client(api_key=s.gemini_api_key)
        r = client.models.embed_content(model="text-embedding-004", contents=texts)
        if not r.embeddings:
            raise RuntimeError("Gemini embed_content returned no embeddings")
        return [e.values for e in r.embeddings]


def get_ai_provider() -> AIProvider:
    return GeminiProvider()  # FastAPI dependency — tests override with a fake
