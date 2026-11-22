from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from src.ai_api import (
    AiAssistant,
    AiMemory,
    AiMediaFile,
    AiConversation,
    AiMessage,
    AiModel,
    AiPrompt,
    DeepbridApiClient,
    DeepbridApiError,
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


class _StreamResponse(_Response):
    def __init__(self, events: list[tuple[str, dict[str, object]]]) -> None:
        self.lines: list[bytes] = []
        for event_name, payload in events:
            if event_name:
                self.lines.append(f"event: {event_name}\n".encode("utf-8"))
            self.lines.append(f"data: {json.dumps(payload)}\n".encode("utf-8"))
            self.lines.append(b"\n")

    def __iter__(self):
        return iter(self.lines)


class _BinaryResponse(_Response):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self.payload
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class DeepbridApiClientTests(unittest.TestCase):
    def test_chat_http_errors_include_safe_request_diagnostics(self) -> None:
        error = HTTPError(
            "https://www.deepbrid.com/api/v1/ai/chat",
            403,
            "Forbidden",
            {
                "Content-Type": "application/json",
                "X-Request-ID": "request-123",
            },
            BytesIO(b""),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(DeepbridApiError) as raised:
                DeepbridApiClient("test-key").chat_stream(
                    "private prompt",
                    on_event=lambda _name, _payload: None,
                )

        message = str(raised.exception)
        self.assertIn("HTTP 403", message)
        self.assertIn("POST /api/v1/ai/chat", message)
        self.assertIn("response_body_bytes=0", message)
        self.assertIn("request_id=request-123", message)
        self.assertNotIn("private prompt", message)
        self.assertNotIn("test-key", message)

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
            _Response(
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "Here is the image",
                            "files": [
                                {
                                    "id": 93,
                                    "kind": "generated",
                                    "name": "scene.png",
                                    "mime": "image/png",
                                    "size": 1200,
                                    "url": "/api/v1/ai/media?id=93",
                                }
                            ],
                        }
                    ]
                }
            ),
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
            self.assertEqual(
                client.get_messages("42"),
                [
                    AiMessage(
                        "assistant",
                        "Here is the image",
                        (
                            AiMediaFile(
                                93,
                                "generated",
                                "scene.png",
                                "image/png",
                                1200,
                                "/api/v1/ai/media?id=93",
                            ),
                        ),
                    )
                ],
            )
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

    def test_streaming_chat_parses_text_media_and_conversation_id(self) -> None:
        media = {
            "id": 93,
            "kind": "generated",
            "name": "lighthouse.png",
            "mime": "image/png",
            "size": 1200,
            "url": "/api/v1/ai/media?id=93",
        }
        response = _StreamResponse(
            [
                ("start", {"conversation_id": 42}),
                ("", {"t": "A lighthouse"}),
                ("", {"t": " at night"}),
                (
                    "done",
                    {
                        "conversation_id": 42,
                        "message": {
                            "content": "A lighthouse at night",
                            "files": [media],
                            "zip_url": None,
                        },
                    },
                ),
            ]
        )
        events: list[tuple[str, object]] = []
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = DeepbridApiClient("test-key").chat_stream(
                "Draw a lighthouse",
                model="gpt-6.1-sol",
                on_event=lambda name, data: events.append((name, data)),
            )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertTrue(body["stream"])
        self.assertEqual(body["model"], "gpt-6.1-sol")
        self.assertEqual(result.content, "A lighthouse at night")
        self.assertEqual(result.conversation_id, "42")
        self.assertEqual(
            result.files,
            (
                AiMediaFile(
                    93,
                    "generated",
                    "lighthouse.png",
                    "image/png",
                    1200,
                    "/api/v1/ai/media?id=93",
                ),
            ),
        )
        self.assertEqual(events[0][0], "start")

    def test_image_generation_uses_documented_endpoint_and_options(self) -> None:
        response = _Response(
            {
                "conversation_id": 42,
                "message": {
                    "content": "",
                    "files": [
                        {
                            "id": 93,
                            "kind": "generated",
                            "name": "lighthouse.png",
                            "size": 1200,
                            "url": "/api/v1/ai/media?id=93",
                        }
                    ],
                },
            }
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = DeepbridApiClient("test-key").generate_image(
                "A lighthouse at night",
                image_format="landscape",
                quality="high",
                conversation_id="41",
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://www.deepbrid.com/api/v1/ai/image",
        )
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "prompt": "A lighthouse at night",
                "format": "landscape",
                "quality": "high",
                "conversation_id": 41,
            },
        )
        self.assertEqual(result.conversation_id, "42")
        self.assertEqual(result.files[0].kind, "generated")
        self.assertEqual(result.files[0].mime, "")

    def test_stop_and_media_download_use_authenticated_ai_endpoints(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=[_Response({"ok": True}), _BinaryResponse(b"image-bytes")],
        ) as urlopen:
            client = DeepbridApiClient("test-key")
            client.stop("42")
            media = client.download_media("/api/v1/ai/media?id=93")

        stop_request = urlopen.call_args_list[0].args[0]
        self.assertEqual(stop_request.full_url, "https://www.deepbrid.com/api/v1/ai/stop")
        self.assertEqual(
            json.loads(stop_request.data.decode("utf-8")),
            {"conversation_id": 42},
        )
        media_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(
            media_request.full_url,
            "https://www.deepbrid.com/api/v1/ai/media?id=93",
        )
        self.assertEqual(media_request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(media, b"image-bytes")

    def test_media_download_streams_to_file_and_rejects_external_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "generated.zip"
            with patch(
                "urllib.request.urlopen",
                return_value=_BinaryResponse(b"archive-bytes"),
            ) as urlopen:
                DeepbridApiClient("test-key").download_media_to(
                    "/api/v1/ai/media?mz=5679",
                    destination,
                )

            self.assertEqual(destination.read_bytes(), b"archive-bytes")
            request = urlopen.call_args.args[0]
            self.assertIn("mz=5679", request.full_url)

        with self.assertRaisesRegex(DeepbridApiError, "invalid media URL"):
            DeepbridApiClient("test-key").download_media(
                "https://example.com/api/v1/ai/media?id=93"
            )

    def test_prompt_and_memory_crud_maps_documented_fields(self) -> None:
        responses = [
            _Response({"prompts": [{"id": 3, "title": "Review", "body": "Review code"}]}),
            _Response({"memories": [{"id": 8, "content": "Use Python"}]}),
            _Response({"prompts": []}),
            _Response({"prompts": []}),
            _Response({"prompts": []}),
            _Response({"memories": []}),
            _Response({"memories": []}),
            _Response({"memories": []}),
            _Response({"memories": []}),
        ]
        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            client = DeepbridApiClient("test-key")
            self.assertEqual(
                client.list_prompts(),
                [AiPrompt("3", "Review", "Review code")],
            )
            self.assertEqual(
                client.list_memories(),
                [AiMemory("8", "Use Python")],
            )
            client.create_prompt("Review", "Review code")
            client.update_prompt("3", "Review", "Review changed code")
            client.delete_prompt("3")
            client.create_memory("Use Python")
            client.update_memory("8", "Use Python 3")
            client.delete_memory("8")
            client.delete_all_memories()

        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual([request.method for request in requests[2:]], [
            "POST",
            "PATCH",
            "DELETE",
            "POST",
            "PATCH",
            "DELETE",
            "DELETE",
        ])
        self.assertIn("/prompts?id=3", requests[3].full_url)
        self.assertIn("/memories?id=8", requests[6].full_url)
        self.assertTrue(requests[8].full_url.endswith("/memories?all=1"))


if __name__ == "__main__":
    unittest.main()
