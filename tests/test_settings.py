from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.settings import (
    API_KEY,
    DARK_THEME,
    DIAGNOSTICS_ENABLED,
    LAST_PROJECT,
    TERMINAL_COMMAND_WHITELIST,
    TERMINAL_TOOLS_ENABLED,
    WRAP_TEXT,
    SettingsStore,
)


class SettingsStoreTests(unittest.TestCase):
    def test_persists_boolean_and_last_project_values_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory) / "settings.sqlite3")
            self.assertTrue(store.get_bool(WRAP_TEXT, default=True))
            self.assertFalse(store.get_bool(DIAGNOSTICS_ENABLED, default=False))
            self.assertFalse(store.get_bool(TERMINAL_TOOLS_ENABLED, default=False))

            store.set_bool(DARK_THEME, True)
            store.set_bool(WRAP_TEXT, False)
            store.set_bool(DIAGNOSTICS_ENABLED, True)
            store.set_bool(TERMINAL_TOOLS_ENABLED, True)
            store.set(
                TERMINAL_COMMAND_WHITELIST,
                '["ls","python --version"]',
            )
            store.set(LAST_PROJECT, "C:/projects/example")

            reopened = SettingsStore(store.database_path)
            self.assertTrue(reopened.get_bool(DARK_THEME, default=False))
            self.assertFalse(reopened.get_bool(WRAP_TEXT, default=True))
            self.assertTrue(reopened.get_bool(DIAGNOSTICS_ENABLED, default=False))
            self.assertTrue(reopened.get_bool(TERMINAL_TOOLS_ENABLED, default=False))
            self.assertEqual(
                reopened.get(TERMINAL_COMMAND_WHITELIST),
                '["ls","python --version"]',
            )
            self.assertEqual(reopened.get(LAST_PROJECT), "C:/projects/example")

    def test_invalid_boolean_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory) / "settings.sqlite3")
            store.set(WRAP_TEXT, "yes")

            with self.assertRaisesRegex(ValueError, "must be 'true' or 'false'"):
                store.get_bool(WRAP_TEXT, default=True)

    def test_api_key_uses_operating_system_credential_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory) / "settings.sqlite3")
            secrets: dict[str, str] = {}

            def read_secret(_service: str, name: str) -> str | None:
                return secrets.get(name)

            def write_secret(_service: str, name: str, value: str) -> None:
                secrets[name] = value

            def remove_secret(_service: str, name: str) -> None:
                secrets.pop(name, None)

            with (
                patch("src.settings.keyring.get_password", side_effect=read_secret),
                patch("src.settings.keyring.set_password", side_effect=write_secret),
                patch(
                    "src.settings.keyring.delete_password",
                    side_effect=remove_secret,
                ) as delete_secret,
            ):
                store.set_secret(API_KEY, "test-api-key")
                self.assertEqual(store.get_secret(API_KEY), "test-api-key")
                store.set_secret(API_KEY, "")
                self.assertIsNone(store.get_secret(API_KEY))

            delete_secret.assert_called_once_with("Deepbrid Code", API_KEY)

    def test_legacy_plaintext_api_key_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory) / "settings.sqlite3")
            with store._connect() as connection:
                connection.execute(
                    "INSERT INTO app_settings (name, value) VALUES (?, ?)",
                    (API_KEY, "legacy-api-key"),
                )
            secrets: dict[str, str] = {}
            with (
                patch(
                    "src.settings.keyring.get_password",
                    side_effect=lambda _service, name: secrets.get(name),
                ),
                patch(
                    "src.settings.keyring.set_password",
                    side_effect=lambda _service, name, value: secrets.__setitem__(name, value),
                ),
            ):
                self.assertEqual(store.get_secret(API_KEY), "legacy-api-key")

            with store._connect() as connection:
                row = connection.execute(
                    "SELECT value FROM app_settings WHERE name = ?",
                    (API_KEY,),
                ).fetchone()
            self.assertIsNone(row)

    def test_api_key_cannot_be_read_or_written_as_a_plain_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = SettingsStore(Path(temporary_directory) / "settings.sqlite3")
            with self.assertRaisesRegex(ValueError, "get_secret"):
                store.get(API_KEY)
            with self.assertRaisesRegex(ValueError, "set_secret"):
                store.set(API_KEY, "test-api-key")


if __name__ == "__main__":
    unittest.main()
