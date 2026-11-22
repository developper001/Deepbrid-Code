from __future__ import annotations

import json
import mimetypes
import shutil
import tempfile
import urllib.parse
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, NoReturn


API_BASE = "https://www.deepbrid.com/api/v1/ai"
LEGACY_MODEL_IDS = {
    "GPT-6.1 Sol · Best": "gpt-6.1-sol",
    "GPT-6 Luna · Fast": "gpt-6-luna",
    "Grok 4.7": "grok-4.7",
    "Gemini 3.8 Flash · Fast": "gemini-3.8-flash",
    "Gemini 4 Argon": "gemini-4-argon",
}


@dataclass(frozen=True)
class AiModel:
    model_id: str
    label: str


@dataclass(frozen=True)
class AiAssistant:
    assistant_id: str
    label: str
    description: str


@dataclass(frozen=True)
class AiConversation:
    conversation_id: str
    title: str
    updated: str
    model: str
    pinned: bool = False


@dataclass(frozen=True)
class AiMessage:
    role: str
    content: str
    files: tuple[AiMediaFile, ...] = ()
    zip_url: str | None = None


@dataclass(frozen=True)
class AiMediaFile:
    file_id: int
    kind: str
    name: str
    mime: str
    size: int
    url: str


@dataclass(frozen=True)
class AiChatResponse:
    content: str
    conversation_id: str | None
    files: tuple[AiMediaFile, ...] = ()
    zip_url: str | None = None
    stopped: bool = False


@dataclass(frozen=True)
class AiPrompt:
    prompt_id: str
    title: str
    body: str


@dataclass(frozen=True)
class AiMemory:
    memory_id: str
    content: str


class DeepbridApiError(RuntimeError):
    pass


class DeepbridApiClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def validate(self) -> dict[str, object]:
        result = self._request("GET", "/models")
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid AI models response.")
        return result

    def list_models(self) -> list[AiModel]:
        result = self.validate()
        raw_models = result.get("models")
        if not isinstance(raw_models, list):
            raise DeepbridApiError("Deepbrid returned an invalid AI models list.")
        models: list[AiModel] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            label = item.get("label") or model_id
            if isinstance(model_id, str) and isinstance(label, str):
                models.append(AiModel(model_id, label))
        if not models:
            raise DeepbridApiError("Deepbrid returned no available AI models.")
        return models

    def list_assistants(self) -> list[AiAssistant]:
        result = self._request("GET", "/assistants")
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid assistants response.")
        assistants: list[AiAssistant] = []
        for key in ("assistants", "templates"):
            values = result.get(key, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                assistant_id = item.get("id")
                label = item.get("name") or item.get("nom") or item.get("label")
                description = item.get("description", "")
                if (
                    isinstance(assistant_id, (int, str))
                    and isinstance(label, str)
                    and label
                ):
                    assistants.append(
                        AiAssistant(str(assistant_id), label, str(description))
                    )
        return assistants

    def list_conversations(self, query: str = "") -> list[AiConversation]:
        if query.strip():
            path = f"/search?{urllib.parse.urlencode({'q': query.strip()})}"
        else:
            path = "/conversations"
        result = self._request("GET", path)
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid conversations response.")
        raw_conversations = result.get("conversations")
        if not isinstance(raw_conversations, list):
            raise DeepbridApiError("Deepbrid returned an invalid conversations list.")
        conversations: list[AiConversation] = []
        for item in raw_conversations:
            if not isinstance(item, dict):
                continue
            conversation_id = item.get("id")
            title = item.get("title") or item.get("titre") or "New Chat"
            updated = item.get("updated_at") or item.get("updated") or ""
            model = item.get("model") or ""
            if isinstance(conversation_id, (int, str)) and isinstance(title, str):
                conversations.append(
                    AiConversation(
                        str(conversation_id),
                        title,
                        str(updated),
                        str(model),
                        item.get("pinned") is True,
                    )
                )
        return conversations

    def get_messages(self, conversation_id: str) -> list[AiMessage]:
        query = urllib.parse.urlencode({"conversation_id": conversation_id})
        result = self._request("GET", f"/messages?{query}")
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid messages response.")
        raw_messages = result.get("messages")
        if not isinstance(raw_messages, list):
            raise DeepbridApiError("Deepbrid returned an invalid messages list.")
        messages: list[AiMessage] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append(
                    AiMessage(
                        role,
                        content,
                        self._parse_media_files(item.get("files")),
                        self._optional_string(item.get("zip_url")),
                    )
                )
        return messages

    def list_prompts(self) -> list[AiPrompt]:
        result = self._request("GET", "/prompts")
        raw_items = self._record_list(result, "prompts")
        prompts: list[AiPrompt] = []
        for item in raw_items:
            prompt_id = item.get("id")
            title = item.get("title")
            body = item.get("body")
            if (
                isinstance(prompt_id, (int, str))
                and isinstance(title, str)
                and isinstance(body, str)
            ):
                prompts.append(AiPrompt(str(prompt_id), title, body))
        return prompts

    def list_memories(self) -> list[AiMemory]:
        result = self._request("GET", "/memories")
        raw_items = self._record_list(result, "memories")
        memories: list[AiMemory] = []
        for item in raw_items:
            memory_id = item.get("id")
            content = item.get("content")
            if isinstance(memory_id, (int, str)) and isinstance(content, str):
                memories.append(AiMemory(str(memory_id), content))
        return memories

    def create_prompt(self, title: str, body: str) -> None:
        self._request("POST", "/prompts", json_body={"title": title, "body": body})

    def update_prompt(self, prompt_id: str, title: str, body: str) -> None:
        query = urllib.parse.urlencode({"id": prompt_id})
        self._request(
            "PATCH",
            f"/prompts?{query}",
            json_body={"title": title, "body": body},
        )

    def delete_prompt(self, prompt_id: str) -> None:
        query = urllib.parse.urlencode({"id": prompt_id})
        self._request("DELETE", f"/prompts?{query}")

    def create_memory(self, content: str) -> None:
        self._request("POST", "/memories", json_body={"content": content})

    def update_memory(self, memory_id: str, content: str) -> None:
        query = urllib.parse.urlencode({"id": memory_id})
        self._request(
            "PATCH",
            f"/memories?{query}",
            json_body={"content": content},
        )

    def delete_memory(self, memory_id: str) -> None:
        query = urllib.parse.urlencode({"id": memory_id})
        self._request("DELETE", f"/memories?{query}")

    def delete_all_memories(self) -> None:
        self._request("DELETE", "/memories?all=1")

    def create_conversation(
        self,
        *,
        model: str | None = None,
        assistant: str | None = None,
        use_web: bool = False,
    ) -> str:
        body: dict[str, object] = {"use_web": use_web}
        if model:
            body["model"] = model
        if assistant:
            body["assistant"] = assistant
        result = self._request("POST", "/conversations", json_body=body)
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid new-conversation response.")
        conversation = result.get("conversation")
        conversation_id = result.get("id")
        if conversation_id is None and isinstance(conversation, dict):
            conversation_id = conversation.get("id")
        if not isinstance(conversation_id, (int, str)):
            raise DeepbridApiError("Deepbrid did not return the new conversation ID.")
        return str(conversation_id)

    def update_conversation(
        self,
        conversation_id: str,
        *,
        model: str | None,
        assistant: str | None,
        use_web: bool,
    ) -> None:
        query = urllib.parse.urlencode({"id": conversation_id})
        self._request(
            "PATCH",
            f"/conversations?{query}",
            json_body={
                "model": model,
                "assistant": assistant,
                "use_web": use_web,
            },
        )

    def chat(
        self,
        message: str,
        *,
        model: str | None = None,
        use_web: bool = False,
        conversation_id: str | None = None,
        assistant: str | None = None,
        attachment_paths: list[Path] | None = None,
    ) -> tuple[str, str | None]:
        model_id = LEGACY_MODEL_IDS.get(model, model) if model else None
        file_ids: list[int] = []
        for path in attachment_paths or []:
            file_ids.append(self.upload(path))

        if conversation_id is not None:
            self.update_conversation(
                conversation_id,
                model=model_id,
                assistant=assistant,
                use_web=use_web,
            )

        body: dict[str, object] = {
            "message": message,
            "stream": False,
        }
        if conversation_id is None:
            body["use_web"] = use_web
            if model_id:
                body["model"] = model_id
            if assistant:
                body["assistant"] = assistant
        else:
            body["conversation_id"] = int(conversation_id)
        if file_ids:
            body["fichiers"] = file_ids

        result = self._request("POST", "/chat", json_body=body)
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid AI chat response.")
        if result.get("error"):
            code = result.get("code") or result.get("error")
            message_text = str(result.get("message") or "Deepbrid AI Chat failed.")
            raise DeepbridApiError(f"{message_text} ({code})")

        returned_message = result.get("message")
        content = ""
        if isinstance(returned_message, dict):
            content_value = returned_message.get("content")
            if isinstance(content_value, str):
                content = content_value
        if not content:
            content_value = result.get("content")
            if isinstance(content_value, str):
                content = content_value

        returned_conversation_id = result.get("conversation_id")
        if isinstance(returned_conversation_id, (int, str)):
            next_conversation_id = str(returned_conversation_id)
        else:
            next_conversation_id = conversation_id
        return content, next_conversation_id

    def chat_stream(
        self,
        message: str,
        *,
        model: str | None = None,
        use_web: bool = False,
        conversation_id: str | None = None,
        assistant: str | None = None,
        attachment_paths: list[Path] | None = None,
        on_event: Callable[[str, object], None],
    ) -> AiChatResponse:
        model_id = LEGACY_MODEL_IDS.get(model, model) if model else None
        file_ids = [self.upload(path) for path in attachment_paths or []]

        if conversation_id is not None:
            self.update_conversation(
                conversation_id,
                model=model_id,
                assistant=assistant,
                use_web=use_web,
            )

        body: dict[str, object] = {"message": message, "stream": True}
        if conversation_id is None:
            body["use_web"] = use_web
            if model_id:
                body["model"] = model_id
            if assistant:
                body["assistant"] = assistant
        else:
            body["conversation_id"] = int(conversation_id)
        if file_ids:
            body["fichiers"] = file_ids

        request = urllib.request.Request(
            f"{API_BASE}/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers("application/json"),
            method="POST",
        )
        content_parts: list[str] = []
        next_conversation_id = conversation_id
        response_message: dict[str, object] = {}
        zip_url: str | None = None
        stopped = False
        saw_done = False

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for event_name, event_data in self._iter_sse_events(response):
                    try:
                        payload = json.loads(event_data)
                    except json.JSONDecodeError as error:
                        raise DeepbridApiError(
                            "Deepbrid returned invalid streaming event data."
                        ) from error
                    if not isinstance(payload, dict):
                        raise DeepbridApiError(
                            "Deepbrid returned an invalid streaming event."
                        )
                    on_event(event_name, payload)

                    if event_name == "start":
                        returned_id = payload.get("conversation_id")
                        if isinstance(returned_id, (int, str)):
                            next_conversation_id = str(returned_id)
                    elif event_name == "error":
                        code = payload.get("code") or "chat_error"
                        detail = payload.get("message") or "Deepbrid AI Chat failed."
                        detail = str(detail).replace(self.api_key, "<redacted>")
                        code = str(code).replace(self.api_key, "<redacted>")
                        raise DeepbridApiError(f"{detail} ({code})")
                    elif event_name == "done":
                        saw_done = True
                        stopped = payload.get("stopped") is True
                        returned_id = payload.get("conversation_id")
                        if isinstance(returned_id, (int, str)):
                            next_conversation_id = str(returned_id)
                        message_value = payload.get("message")
                        if isinstance(message_value, dict):
                            response_message = message_value
                        zip_url = self._optional_string(
                            response_message.get("zip_url")
                        )
                    elif event_name in {"", "message"}:
                        text = payload.get("t")
                        if isinstance(text, str):
                            content_parts.append(text)

        except urllib.error.HTTPError as error:
            self._raise_http_error(error)
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as error:
            raise DeepbridApiError(
                f"Could not reach the Deepbrid AI API: {error}"
            ) from error

        if not saw_done:
            raise DeepbridApiError("Deepbrid's streaming response ended unexpectedly.")
        final_content = response_message.get("content")
        if isinstance(final_content, str) and not content_parts:
            content_parts.append(final_content)
        return AiChatResponse(
            content="".join(content_parts),
            conversation_id=next_conversation_id,
            files=self._parse_media_files(response_message.get("files")),
            zip_url=zip_url,
            stopped=stopped,
        )

    def stop(self, conversation_id: str) -> None:
        self._request(
            "POST",
            "/stop",
            json_body={"conversation_id": int(conversation_id)},
        )

    def generate_image(
        self,
        prompt: str,
        *,
        image_format: str = "square",
        quality: str = "auto",
        conversation_id: str | None = None,
    ) -> AiChatResponse:
        if not prompt.strip():
            raise DeepbridApiError("Enter a description for the image.")
        if image_format not in {"square", "landscape", "portrait"}:
            raise DeepbridApiError("Choose a supported image format.")
        if quality not in {"auto", "low", "medium", "high"}:
            raise DeepbridApiError("Choose a supported image quality.")

        body: dict[str, object] = {
            "prompt": prompt.strip(),
            "format": image_format,
            "quality": quality,
        }
        if conversation_id is not None:
            body["conversation_id"] = int(conversation_id)
        result = self._request("POST", "/image", json_body=body)
        if not isinstance(result, dict):
            raise DeepbridApiError("Deepbrid returned an invalid image response.")
        returned_id = result.get("conversation_id")
        next_conversation_id = (
            str(returned_id)
            if isinstance(returned_id, (int, str))
            else conversation_id
        )
        message = result.get("message")
        if not isinstance(message, dict):
            raise DeepbridApiError("Deepbrid returned an invalid generated image.")
        files = self._parse_media_files(message.get("files"))
        if not files:
            raise DeepbridApiError("Deepbrid did not return a generated image file.")
        return AiChatResponse(
            content=message.get("content")
            if isinstance(message.get("content"), str)
            else "",
            conversation_id=next_conversation_id,
            files=files,
            zip_url=self._optional_string(message.get("zip_url")),
        )

    def download_media(
        self,
        media_url: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        endpoint = self._media_endpoint(media_url)
        request = urllib.request.Request(
            f"{API_BASE}{endpoint}",
            headers=self._headers("application/octet-stream", accept="*/*"),
            method="GET",
        )
        chunks: list[bytes] = []
        total_size = 0
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                while chunk := response.read(64 * 1024):
                    total_size += len(chunk)
                    if max_bytes is not None and total_size > max_bytes:
                        raise DeepbridApiError(
                            "The downloaded media exceeds the preview size limit."
                        )
                    chunks.append(chunk)
        except urllib.error.HTTPError as error:
            self._raise_http_error(error)
        except (urllib.error.URLError, TimeoutError) as error:
            raise DeepbridApiError(
                f"Could not download the Deepbrid media: {error}"
            ) from error
        return b"".join(chunks)

    def download_media_to(self, media_url: str, destination: Path) -> None:
        endpoint = self._media_endpoint(media_url)
        request = urllib.request.Request(
            f"{API_BASE}{endpoint}",
            headers=self._headers("application/octet-stream", accept="*/*"),
            method="GET",
        )
        temporary_path: Path | None = None
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent,
                    prefix=".deepbrid-",
                    suffix=".part",
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    shutil.copyfileobj(response, temporary_file)
            temporary_path.replace(destination)
            temporary_path = None
        except urllib.error.HTTPError as error:
            self._raise_http_error(error)
        except (urllib.error.URLError, TimeoutError) as error:
            raise DeepbridApiError(
                f"Could not download the Deepbrid media: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _media_endpoint(media_url: str) -> str:
        parsed = urllib.parse.urlsplit(media_url)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.path != "/api/v1/ai/media"
            or not parsed.query
        ):
            raise DeepbridApiError("Deepbrid returned an invalid media URL.")
        return f"/media?{parsed.query}"

    @staticmethod
    def _record_list(result: object, key: str) -> list[dict[str, object]]:
        if not isinstance(result, dict):
            raise DeepbridApiError(f"Deepbrid returned an invalid {key} response.")
        raw_items = result.get(key)
        if not isinstance(raw_items, list):
            raise DeepbridApiError(f"Deepbrid returned an invalid {key} list.")
        return [item for item in raw_items if isinstance(item, dict)]

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _parse_media_files(raw_files: object) -> tuple[AiMediaFile, ...]:
        if raw_files is None:
            return ()
        if not isinstance(raw_files, list):
            raise DeepbridApiError("Deepbrid returned invalid media file data.")
        files: list[AiMediaFile] = []
        for item in raw_files:
            if not isinstance(item, dict):
                raise DeepbridApiError("Deepbrid returned an invalid media file.")
            file_id = item.get("id")
            name = item.get("name") or item.get("nom")
            kind = item.get("kind") or ""
            mime = item.get("mime") or ""
            size = item.get("size") or item.get("taille") or 0
            url = item.get("url")
            if (
                isinstance(file_id, int)
                and isinstance(name, str)
                and isinstance(kind, str)
                and isinstance(mime, str)
                and isinstance(size, int)
                and isinstance(url, str)
            ):
                files.append(AiMediaFile(file_id, kind, name, mime, size, url))
            else:
                raise DeepbridApiError("Deepbrid returned an invalid media file.")
        return tuple(files)

    @staticmethod
    def _iter_sse_events(response: Iterable[bytes]) -> Iterator[tuple[str, str]]:
        event_name = ""
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield event_name, "\n".join(data_lines)
                event_name = ""
                data_lines = []
            elif line.startswith(":"):
                continue
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield event_name, "\n".join(data_lines)

    def _headers(
        self,
        content_type: str,
        *,
        accept: str = "application/json",
    ) -> dict[str, str]:
        if not self.api_key:
            raise DeepbridApiError("Deepbrid API key is missing.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
            "Content-Type": content_type,
            "User-Agent": "DeepbridCode/1.0",
        }

    def _raise_http_error(self, error: urllib.error.HTTPError) -> NoReturn:
        status = error.code
        payload = error.read().decode("utf-8", errors="replace")
        error.close()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {}
        code = data.get("code") if isinstance(data, dict) else None
        detail = data.get("message") if isinstance(data, dict) else payload
        detail = str(detail or "No response details").replace(
            self.api_key,
            "<redacted>",
        )
        if code is not None:
            code = str(code).replace(self.api_key, "<redacted>")
        raise DeepbridApiError(
            f"Deepbrid AI API returned HTTP {status}"
            f"{f' ({code})' if code else ''}: {detail or 'No response details'}"
        ) from error

    def upload(self, path: Path) -> int:
        if not path.is_file():
            raise DeepbridApiError(f"Attachment does not exist: {path}")

        boundary = f"----deepbrid-code-{uuid.uuid4().hex}"
        file_name = (
            path.name.replace("\\", "_")
            .replace('"', "_")
            .replace("\r", "_")
            .replace("\n", "_")
        )
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
        with path.open("rb") as file:
            body = header + file.read() + footer

        result = self._request(
            "POST",
            "/upload",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        if not isinstance(result, dict) or not isinstance(result.get("file"), dict):
            raise DeepbridApiError("Deepbrid returned an invalid file upload response.")
        file_id = result["file"].get("id")
        if not isinstance(file_id, int):
            raise DeepbridApiError("Deepbrid did not return an attachment ID.")
        return file_id

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: dict[str, object] | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> object:
        if not self.api_key:
            raise DeepbridApiError("Deepbrid API key is missing.")

        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{API_BASE}{endpoint}",
            data=body,
            headers=self._headers(content_type),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = response.status
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            self._raise_http_error(error)
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as error:
            raise DeepbridApiError(f"Could not reach the Deepbrid AI API: {error}") from error

        try:
            result = json.loads(payload)
        except json.JSONDecodeError as error:
            raise DeepbridApiError(
                f"Deepbrid returned invalid JSON (HTTP {status})."
            ) from error
        if isinstance(result, dict) and result.get("error") not in (None, 0, "0", False):
            code = result.get("code") or result.get("error")
            message = str(result.get("message") or "Deepbrid AI API request failed.")
            message = message.replace(self.api_key, "<redacted>")
            code = str(code).replace(self.api_key, "<redacted>")
            raise DeepbridApiError(f"{message} ({code})")
        return result
