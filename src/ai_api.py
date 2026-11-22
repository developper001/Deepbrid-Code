from __future__ import annotations

import json
import mimetypes
import urllib.parse
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


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
                messages.append(AiMessage(role, content))
        return messages

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
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": content_type,
                "User-Agent": "DeepbridCode/1.0",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                status = response.status
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            status = error.code
            payload = error.read().decode("utf-8", errors="replace")
            error.close()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {}
            code = data.get("code") if isinstance(data, dict) else None
            if code is not None:
                code = str(code).replace(self.api_key, "<redacted>")
            detail = data.get("message") if isinstance(data, dict) else payload
            detail = str(detail or "No response details").replace(
                self.api_key,
                "<redacted>",
            )
            raise DeepbridApiError(
                f"Deepbrid AI API returned HTTP {status}"
                f"{f' ({code})' if code else ''}: {detail or 'No response details'}"
            ) from error
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
