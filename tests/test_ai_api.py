from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.ai_api import DeepbridApiClient


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DeepbridApiClientTests(unittest.TestCase):
    def test_chat_uses_bearer_auth_and_returns_conversation(self) -> None:
        response = _Response(
            {
                "conversation_id": 42,
                "message": {"content": "Hello"},
                "error": None,
            }
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            content, conversation_id = DeepbridApiClient("test-key").chat(
                "Hello",
                use_web=True,
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://www.deepbrid.com/api/v1/ai/chat")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["message"], "Hello")
        self.assertFalse(body["stream"])
        self.assertTrue(body["use_web"])
        self.assertEqual(content, "Hello")
        self.assertEqual(conversation_id, 42)


if __name__ == "__main__":
    unittest.main()
