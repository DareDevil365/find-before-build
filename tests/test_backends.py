"""Check both model APIs' wire-format normalization without network calls."""

import json
from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from harness_core.backends import GemmaAPI, KoboldCpp, post_json


class KoboldTests(unittest.TestCase):
    def test_openai_compatible_tool_call_and_usage(self):
        wire = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "one", "function": {"name": "write_file", "arguments": '{"path":"x.txt","content":"hi"}'}}]}}], "usage": {"total_tokens": 17}}
        with patch("harness_core.backends.post_json", return_value=wire) as send:
            reply = KoboldCpp().chat([{"role": "user", "content": "write"}], tools=[{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object"}}}])
        self.assertEqual(reply.tool_calls[0].arguments["path"], "x.txt")
        self.assertEqual(reply.usage["total_tokens"], 17)
        self.assertIn("/v1/chat/completions", send.call_args.args[0])
        self.assertEqual(send.call_args.args[1]["tool_choice"], "auto")


class GemmaTests(unittest.TestCase):
    def test_429_rotates_without_waiting_or_retrying_same_key(self):
        error = HTTPError("https://example.test", 429, "Too Many Requests", {}, BytesIO(b'{"error":"rate limit"}'))
        with patch("harness_core.backends.urlopen", side_effect=error) as send, patch("harness_core.backends.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                post_json("https://example.test", {}, {})
        error.close()
        self.assertEqual(send.call_count, 1)
        sleep.assert_not_called()

    def test_convert_full_tool_loop(self):
        messages = [
            {"role": "system", "content": "Follow task"},
            {"role": "user", "content": "Create file"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": json.dumps({"path": "a.txt", "content": "x"})}}]},
            {"role": "tool", "name": "write_file", "content": "Wrote a.txt"},
        ]
        system, contents = GemmaAPI.convert_messages(messages)
        self.assertEqual(system, "Follow task")
        self.assertEqual(contents[1]["parts"][0]["functionCall"]["args"]["path"], "a.txt")
        self.assertEqual(contents[2]["parts"][0]["functionResponse"]["name"], "write_file")

    def test_gemini_response_and_key_header(self):
        wire = {"candidates": [{"content": {"parts": [{"text": "Working"}, {"functionCall": {"name": "list_files", "args": {}}}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 12}}
        with patch("harness_core.backends.post_json", return_value=wire) as send:
            reply = GemmaAPI(api_key="test-secret").chat([{"role": "user", "content": "list"}])
        self.assertEqual(reply.text, "Working")
        self.assertEqual(reply.tool_calls[0].name, "list_files")
        self.assertEqual(reply.usage["total_tokens"], 12)
        self.assertNotIn("test-secret", send.call_args.args[0])
        self.assertEqual(send.call_args.args[2]["x-goog-api-key"], "test-secret")

    def test_gemini_function_call_keeps_thought_signature_in_history(self):
        signed = {"functionCall": {"name": "list_files", "args": {}}, "thoughtSignature": "opaque-signature"}
        wire = {"candidates": [{"content": {"parts": [signed]}}]}
        with patch("harness_core.backends.post_json", return_value=wire):
            reply = GemmaAPI(api_key="test-secret").chat([{"role": "user", "content": "list"}])
        messages = [
            {"role": "user", "content": "list"},
            {"role": "assistant", "content": reply.text, "gemini_parts": reply.raw_parts},
            {"role": "tool", "name": "list_files", "content": "[]"},
        ]
        _, contents = GemmaAPI.convert_messages(messages)
        self.assertEqual(contents[1]["parts"][0]["thoughtSignature"], "opaque-signature")
        self.assertEqual(contents[2]["parts"][0]["functionResponse"]["name"], "list_files")


if __name__ == "__main__":
    unittest.main()
