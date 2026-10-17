"""Normalize KoboldCpp and Gemini API chat/function responses."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def post_json(url: str, body: dict, headers: dict, timeout: float = 120) -> dict:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read(500).decode("utf-8", "replace")
            if exc.code in {429, 500, 502, 503} and attempt < 2:
                match = re.search(r"retry in\s+([\d.]+)s", detail, re.I)
                delay = min(40, max(2 ** attempt, float(match.group(1)) + 1 if match else 2 ** attempt))
                print(f"Model API HTTP {exc.code}; retrying in {delay:.1f}s", file=sys.stderr, flush=True)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Model API HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Model API unavailable: {exc}") from exc
    raise RuntimeError("Model API retry limit reached")


class KoboldCpp:
    def __init__(self, base_url: str = "http://127.0.0.1:5001", model: str = "koboldcpp"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        payload = {"model": self.model, "messages": messages, "temperature": 0.2, "max_tokens": 768}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        raw = post_json(f"{self.base_url}/v1/chat/completions", payload, {})
        choice = raw.get("choices", [{}])[0].get("message", {})
        calls = []
        for i, call in enumerate(choice.get("tool_calls") or []):
            function = call.get("function") or {}
            args = function.get("arguments") or "{}"
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                parsed = {"_invalid_json": args}
            calls.append(ToolCall(str(call.get("id") or f"call_{i}"), str(function.get("name") or ""), parsed if isinstance(parsed, dict) else {"_invalid_json": args}))
        return Reply(str(choice.get("content") or ""), calls, raw.get("usage") or {})


class GemmaAPI:
    def __init__(self, model: str = "gemma-4-26b-a4b-it", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) for the Gemma API backend")

    @staticmethod
    def convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        system = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        contents = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "assistant":
                parts = []
                if message.get("content"):
                    parts.append({"text": str(message["content"])})
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    args = fn.get("arguments") or "{}"
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                contents.append({"role": "user", "parts": [{"functionResponse": {"name": message.get("name") or "tool", "response": {"result": message.get("content") or ""}}}]})
            else:
                contents.append({"role": "user", "parts": [{"text": str(message.get("content") or "")} ]})
        return system, contents

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        system, contents = self.convert_messages(messages)
        body: dict = {"contents": contents, "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            declarations = []
            for tool in tools:
                fn = tool["function"]
                declarations.append({"name": fn["name"], "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object"})})
            body["tools"] = [{"functionDeclarations": declarations}]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        raw = post_json(url, body, {"x-goog-api-key": self.api_key})
        candidate = (raw.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        calls = []
        for i, part in enumerate(parts):
            fn = part.get("functionCall")
            if fn:
                calls.append(ToolCall(f"gemma_call_{i}", str(fn.get("name") or ""), fn.get("args") or {}))
        usage = raw.get("usageMetadata") or {}
        return Reply("\n".join(str(p["text"]) for p in parts if "text" in p), calls, {"prompt_tokens": usage.get("promptTokenCount"), "completion_tokens": usage.get("candidatesTokenCount"), "total_tokens": usage.get("totalTokenCount")})
