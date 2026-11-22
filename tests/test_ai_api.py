from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ai_api import (
    AiAssistant,
    AiConversation,
    AiMessage,
    AiModel,
    DeepbridApiClient,
)


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
        self.assertEqual(conversation_id, "42")

    def test_api_catalog_and_conversation_endpoints_are_mapped(self) -> None:
        responses = [
            _Response(
                {
                    "models": [{"id": "test-model", "label": "Test Model"}],
                    "default_model": "test-model",
                }
            ),
            _Response(
                {
                    "assistants": [
                        {
                            "id": 7,
                            "name": "Code Helper",
                            "description": "Assists with code",
                        }
                    ],
                    "templates": [],
                }
            ),
            _Response(
                {
                    "conversations": [
                        {
                            "id": 42,
                            "title": "Project",
                            "updated_at": "Today",
                            "model": "test-model",
                            "pinned": True,
                        }
                    ]
                }
            ),
            _Response({"messages": [{"role": "user", "content": "Review this"}]}),
            _Response({"id": 43, "conversation": {"id": 43}}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            client = DeepbridApiClient("test-key")
            self.assertEqual(client.list_models(), [AiModel("test-model", "Test Model")])
            self.assertEqual(
                client.list_assistants(),
                [AiAssistant("7", "Code Helper", "Assists with code")],
            )
            self.assertEqual(
                client.list_conversations("project"),
                [AiConversation("42", "Project", "Today", "test-model", True)],
            )
            self.assertEqual(client.get_messages("42"), [AiMessage("user", "Review this")])
            self.assertEqual(
                client.create_conversation(model="test-model", assistant="7"),
                "43",
            )

        self.assertIn("/models", urlopen.call_args_list[0].args[0].full_url)
        self.assertIn("/assistants", urlopen.call_args_list[1].args[0].full_url)
        self.assertIn("/search?q=project", urlopen.call_args_list[2].args[0].full_url)
        self.assertIn("conversation_id=42", urlopen.call_args_list[3].args[0].full_url)
        self.assertIn("/conversations", urlopen.call_args_list[4].args[0].full_url)

    def test_existing_conversation_updates_preferences_before_sending(self) -> None:
        responses = [
            _Response({"conversation": {"id": 42}}),
            _Response(
                {
                    "conversation_id": 42,
                    "message": {"content": "Updated"},
                }
            ),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            answer, conversation_id = DeepbridApiClient("test-key").chat(
                "Continue",
                model="gpt-6-luna",
                assistant="assistant-7",
                use_web=True,
                conversation_id="42",
            )

        update_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(update_request.method, "PATCH")
        self.assertIn("id=42", update_request.full_url)
        self.assertEqual(
            json.loads(update_request.data.decode("utf-8")),
            {
                "model": "gpt-6-luna",
                "assistant": "assistant-7",
                "use_web": True,
            },
        )
        chat_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(json.loads(chat_request.data.decode("utf-8"))["conversation_id"], 42)
        self.assertEqual(answer, "Updated")
        self.assertEqual(conversation_id, "42")

    def test_upload_uses_multipart_api_and_returns_file_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "notes.txt"
            path.write_text("selected content", encoding="utf-8")
            with patch(
                "urllib.request.urlopen",
                return_value=_Response({"file": {"id": 91}}),
            ) as urlopen:
                file_id = DeepbridApiClient("test-key").upload(path)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://www.deepbrid.com/api/v1/ai/upload")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data;"))
        self.assertIn(b'filename="notes.txt"', request.data)
        self.assertIn(b"selected content", request.data)
        self.assertEqual(file_id, 91)


if __name__ == "__main__":
    unittest.main()
