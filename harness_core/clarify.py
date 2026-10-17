"""Use the selected model to ask only material questions about a build request."""

from __future__ import annotations

import json
import re


def fallback_questions(task: str) -> list[str]:
    lowered = task.lower()
    questions = []
    if len(task.split()) < 7:
        questions.append("What should the tool take as input, and what result should it produce?")
    if re.search(r"\b(?:all|every|any)\b", lowered):
        questions.append("Which sources or cases must the first version cover?")
    return questions[:3]


def questions_for(backend, task: str, guide: str) -> tuple[list[str], object | None]:
    prompt = (
        "You clarify a software build request before a coding agent begins. "
        "Return JSON only: {\"questions\": [up to three concise questions]}. "
        "Ask only for missing details that materially change implementation or acceptance; return [] when the request is specific enough. "
        "Do not ask for credentials or routine preferences.\n" + guide[:3500]
    )
    try:
        reply = backend.chat([{"role": "system", "content": prompt}, {"role": "user", "content": task}])
        text = reply.text.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            parsed = json.loads(match.group(0)) if match else None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("questions"), list):
            raise ValueError("Invalid clarification response")
        questions = [q.strip() for q in parsed["questions"] if isinstance(q, str) and 5 <= len(q.strip()) <= 250]
        return list(dict.fromkeys(questions))[:3], reply
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return fallback_questions(task), None
