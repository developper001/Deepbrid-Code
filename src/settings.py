from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import keyring
from keyring.errors import KeyringError


DARK_THEME = "dark_theme"
LAST_PROJECT = "last_project"
WRAP_TEXT = "wrap_text"
OUTPUT_LOG_PATH = "output_log_path"
DIAGNOSTICS_ENABLED = "diagnostics_enabled"
REMOVE_IMAGES_AFTER_SEND = "remove_images_after_send"
API_KEY = "api_key"
KEYRING_SERVICE = "Deepbrid Code"


class SecretStorageError(RuntimeError):
    pass


class SettingsStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS app_settings "
                "(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def get(self, name: str) -> str | None:
        if name == API_KEY:
            raise ValueError("API keys must be read with get_secret().")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE name = ?",
                (name,),
            ).fetchone()
        return str(row[0]) if row else None

    def set(self, name: str, value: str) -> None:
        if name == API_KEY:
            raise ValueError("API keys must be stored with set_secret().")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (name, value),
            )

    def get_secret(self, name: str) -> str | None:
        try:
            secret = keyring.get_password(KEYRING_SERVICE, name)
        except KeyringError as error:
            raise SecretStorageError(
                f"Could not access the operating-system credential store: {error}"
            ) from error
        if secret:
            with self._connect() as connection:
                connection.execute("DELETE FROM app_settings WHERE name = ?", (name,))
            return secret

        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None

        legacy_secret = str(row[0])
        self.set_secret(name, legacy_secret)
        if self.get_secret(name) != legacy_secret:
            raise SecretStorageError("Could not verify API key migration to the credential store.")
        with self._connect() as connection:
            connection.execute("DELETE FROM app_settings WHERE name = ?", (name,))
        return legacy_secret

    def set_secret(self, name: str, value: str) -> None:
        try:
            if value:
                keyring.set_password(KEYRING_SERVICE, name, value)
                if keyring.get_password(KEYRING_SERVICE, name) != value:
                    raise SecretStorageError("Could not verify the saved API key.")
            else:
                if keyring.get_password(KEYRING_SERVICE, name) is not None:
                    keyring.delete_password(KEYRING_SERVICE, name)
        except KeyringError as error:
            raise SecretStorageError(
                f"Could not access the operating-system credential store: {error}"
            ) from error
        with self._connect() as connection:
            connection.execute("DELETE FROM app_settings WHERE name = ?", (name,))

    def get_bool(self, name: str, default: bool) -> bool:
        value = self.get(name)
        if value is None:
            return default
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError(f"Saved setting {name!r} must be 'true' or 'false', not {value!r}.")

    def set_bool(self, name: str, value: bool) -> None:
        self.set(name, "true" if value else "false")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
