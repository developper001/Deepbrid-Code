from __future__ import annotations

import json
import mimetypes
import uuid
import urllib.error
import urllib.request
from pathlib import Path


API_BASE = "https://www.deepbrid.com/api/v1/ai"
MODEL_IDS = {
    "GPT-6.1 Sol · Best": "gpt-6.1-sol",
    "GPT-6 Luna · Fast": "gpt-6-luna",
    "Grok 4.7": "grok-4.7",
    "Gemini 3.8 Flash · Fast": "gemini-3.8-flash",
    "Gemini 4 Argon": "gemini-4-argon",
}


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

    def chat(
        self,
        message: str,
        *,
        model: str | None = None,
        use_web: bool = False,
        conversation_id: int | None = None,
        attachment_paths: list[Path] | None = None,
    ) -> tuple[str, int | None]:
        file_ids: list[int] = []
        for path in attachment_paths or []:
            file_ids.append(self.upload(path))

        body: dict[str, object] = {
            "message": message,
            "stream": False,
            "use_web": use_web,
        }
        if model:
            body["model"] = MODEL_IDS.get(model, model)
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
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
        if isinstance(returned_conversation_id, int):
            next_conversation_id = returned_conversation_id
        else:
            next_conversation_id = conversation_id
        return content, next_conversation_id

    def upload(self, path: Path) -> int:
        if not path.is_file():
            raise DeepbridApiError(f"Attachment does not exist: {path}")

        boundary = f"----deepbrid-code-{uuid.uuid4().hex}"
        file_name = path.name
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
            detail = data.get("message") if isinstance(data, dict) else payload
            raise DeepbridApiError(
                f"Deepbrid AI API returned HTTP {status}"
                f"{f' ({code})' if code else ''}: {detail or 'No response details'}"
            ) from error
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as error:
            raise DeepbridApiError(f"Could not reach the Deepbrid AI API: {error}") from error

        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise DeepbridApiError(
                f"Deepbrid returned invalid JSON (HTTP {status})."
            ) from error
