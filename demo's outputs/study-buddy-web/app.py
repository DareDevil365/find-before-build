"""Browser interface for the generated math study buddy."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, jsonify, render_template, request

from math_quiz_generator import generate_quiz_question
from study_buddy_chatbot import StudyBuddyChatbot


app = Flask(__name__, template_folder=str(Path(__file__).with_name("templates")))
sessions: dict[str, dict] = {}
lock = threading.Lock()


def api_key() -> str | None:
    names = ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2", "GOOGLE_API_KEY")
    for name in names:
        if os.getenv(name):
            return os.environ[name]
    for path in (parent / ".env" for parent in Path(__file__).resolve().parents[:5]):
        if path.is_file():
            values = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    name, value = line.split("=", 1)
                    values[name.strip()] = value.strip().strip('"').strip("'")
            for name in names:
                if values.get(name):
                    return values[name]
    return None


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.post("/api/question")
def question():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "")
    if len(session_id) > 100:
        return jsonify(error="Invalid session"), 400
    with lock:
        if session_id not in sessions:
            session_id = uuid4().hex
            sessions[session_id] = {"bot": StudyBuddyChatbot(), "answer": None}
        problem, answer = generate_quiz_question()
        sessions[session_id]["answer"] = answer
    return jsonify(session_id=session_id, question=f"What is {problem}?")


@app.post("/api/answer")
def answer():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "")
    try:
        proposed = float(data.get("answer"))
    except (TypeError, ValueError):
        return jsonify(error="Enter a number"), 400
    if not math.isfinite(proposed):
        return jsonify(error="Enter a finite number"), 400
    with lock:
        entry = sessions.get(session_id)
        if not entry or entry["answer"] is None:
            return jsonify(error="Get a question first"), 400
        correct_answer = entry["answer"]
        entry["answer"] = None
        bot = entry["bot"]
        correct = math.isclose(proposed, correct_answer, abs_tol=1e-8)
        bot.total_questions += 1
        bot.correct_answers += int(correct)
        bot.learning_style_data.append("correct" if correct else "incorrect")
        accuracy = round(bot.correct_answers / bot.total_questions * 100)
        suggestion = "Keep going!"
        if bot.total_questions >= 5:
            suggestion = "Let's practice the basics again." if accuracy < 60 else "Ready for a harder challenge!" if accuracy > 80 else "You're making steady progress."
    return jsonify(correct=correct, correct_answer=correct_answer, total=bot.total_questions, score=bot.correct_answers, accuracy=accuracy, suggestion=suggestion)


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages") or []
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        return jsonify(error="Send up to 12 messages"), 400
    contents = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "model"}:
            return jsonify(error="Invalid message"), 400
        text = str(item.get("text") or "")[:1500]
        contents.append({"role": item["role"], "parts": [{"text": text}]})
    key = api_key()
    if not key:
        return jsonify(error="Gemini key unavailable; the local math quiz still works"), 503
    body = {"systemInstruction": {"parts": [{"text": "You are a friendly math study buddy. Explain concepts clearly, guide practice, and keep replies concise."}]}, "contents": contents, "generationConfig": {"temperature": 0.4, "maxOutputTokens": 600}}
    req = Request("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
        parts = ((result.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        reply = "\n".join(str(part["text"]) for part in parts if "text" in part).strip()
        return jsonify(reply=reply or "I couldn't form a reply. Try another question.")
    except (HTTPError, URLError, TimeoutError):
        return jsonify(error="Gemini is unavailable right now; try again shortly"), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8766")))
