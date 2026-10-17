# Study Buddy project guide

This folder began as a Gemini-generated command-line math quiz. Its generated files are `main.py`, `math_quiz_generator.py`, and `study_buddy_chatbot.py`. The original program asks questions through terminal input and prints feedback; it did not provide a web page or live AI chat.

The local browser adapter adds `app.py` and `templates/index.html`. Flask was retained as a small Python server so it can reuse the generated `generate_quiz_question` function and `StudyBuddyChatbot` progress fields without rewriting them in JavaScript. The browser requests questions and submits answers over `/api/question` and `/api/answer`; progress lives in server memory and resets when the server stops. The `/api/chat` route sends short math-help conversations to Gemini 2.5 Flash using a server-side API key. The key is read from an environment variable or the Hack project's ignored `.env`; it is never sent to the browser. The browser quiz works even when the AI service is unavailable.

Run `python app.py` from this directory, then open the printed `http://127.0.0.1` address. Flask must be installed. When launched through Find Before Build's UI, its runtime launcher chooses an available port and shows the link under Build artifacts. The original CLI remains available with `python main.py`.

This web adapter was added after the generated run completed because the model had produced only CLI files. It reuses the generated quiz logic but is not itself a model-generated artifact. The adaptive feature is still basic: the interface shows an accuracy-based suggestion after five questions. It does not yet adjust quiz difficulty. The chat requires a Gemini API key and network access, and AI replies are not independently fact-checked.
