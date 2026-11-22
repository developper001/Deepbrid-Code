from __future__ import annotations

import sqlite3
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Protocol

from .settings import (
    API_KEY,
    AUTO_OPEN_CHROME,
    DARK_THEME,
    DIAGNOSTICS_ENABLED,
    REMOVE_IMAGES_AFTER_SEND,
    WRAP_TEXT,
)


class SettingsBackend(Protocol):
    def set_bool(self, name: str, value: bool) -> None: ...
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class SettingsDialog:
    def __init__(
        self,
        parent: tk.Tk,
        settings: SettingsBackend,
        auto_open_chrome: bool,
        on_auto_open_changed: Callable[[bool], None],
        dark_theme: bool,
        on_dark_theme_changed: Callable[[bool], None],
        wrap_text: bool,
        on_wrap_text_changed: Callable[[bool], None],
        remove_images_after_send: bool,
        on_remove_images_after_send_changed: Callable[[bool], None],
        diagnostics_enabled: bool,
        on_diagnostics_enabled_changed: Callable[[bool], None],
        output_log_path: str,
        on_output_logging_changed: Callable[[str], None],
        api_key: str = "",
        on_api_key_changed: Callable[[str], None] | None = None,
        on_get_api_key: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_auto_open_changed = on_auto_open_changed
        self.on_dark_theme_changed = on_dark_theme_changed
        self.on_wrap_text_changed = on_wrap_text_changed
        self.on_remove_images_after_send_changed = on_remove_images_after_send_changed
        self.on_diagnostics_enabled_changed = on_diagnostics_enabled_changed
        self.on_output_logging_changed = on_output_logging_changed
        self.on_api_key_changed = on_api_key_changed
        self.on_get_api_key = on_get_api_key
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Settings")
        self.dialog.geometry("640x560")
        self.dialog.minsize(560, 500)
        self.dialog.transient(parent)

        frame = ttk.Frame(self.dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        navigation = ttk.Frame(frame, padding=(0, 0, 16, 0))
        navigation.grid(row=0, column=0, sticky="ns")
        ttk.Label(navigation, text="Sections").pack(anchor="w", pady=(0, 8))
        ttk.Label(
            navigation,
            text="Startup",
            relief="solid",
            padding=(10, 6),
        ).pack(fill="x")
        ttk.Label(
            navigation,
            text="Appearance",
            relief="solid",
            padding=(10, 6),
        ).pack(fill="x", pady=(6, 0))
        ttk.Label(
            navigation,
            text="Diagnostics",
            relief="solid",
            padding=(10, 6),
        ).pack(fill="x", pady=(6, 0))

        content = ttk.Frame(frame, padding=12, relief="groove")
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text="Startup", font=("TkDefaultFont", 14, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 12),
        )
        self.status = tk.StringVar(master=self.dialog)
        self.enabled = tk.BooleanVar(master=self.dialog, value=auto_open_chrome)
        ttk.Checkbutton(
            content,
            text="Open Deepbrid AI Chat in Chrome when Deepbrid Code starts",
            variable=self.enabled,
            command=self._save,
        ).grid(row=1, column=0, sticky="w")
        ttk.Label(
            content,
            text=(
                "Uses the app's separate Chrome profile. If you have not signed in, "
                "Deepbrid will show its login page. You can still open the chat manually "
                "from the app."
            ),
            wraplength=420,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Separator(content).grid(row=3, column=0, sticky="ew", pady=(16, 12))
        ttk.Label(content, text="Appearance", font=("TkDefaultFont", 12, "bold")).grid(
            row=4,
            column=0,
            sticky="w",
            pady=(0, 8),
        )
        self.dark_theme = tk.BooleanVar(master=self.dialog, value=dark_theme)
        self.wrap_text = tk.BooleanVar(master=self.dialog, value=wrap_text)
        ttk.Checkbutton(
            content,
            text="Use dark mode",
            variable=self.dark_theme,
            command=self._save_dark_theme,
        ).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(
            content,
            text="Wrap text in file preview",
            variable=self.wrap_text,
            command=self._save_wrap_text,
        ).grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.remove_images_after_send = tk.BooleanVar(
            master=self.dialog,
            value=remove_images_after_send,
        )
        ttk.Checkbutton(
            content,
            text="Remove images from context after sending",
            variable=self.remove_images_after_send,
            command=self._save_remove_images_after_send,
        ).grid(row=7, column=0, sticky="w", pady=(6, 0))
        ttk.Separator(content).grid(row=8, column=0, sticky="ew", pady=(14, 10))
        ttk.Label(content, text="Diagnostics", font=("TkDefaultFont", 12, "bold")).grid(
            row=9,
            column=0,
            sticky="w",
            pady=(0, 6),
        )
        self.output_log_path = tk.StringVar(
            master=self.dialog,
            value=output_log_path,
        )
        self.output_log_status = tk.StringVar(master=self.dialog)
        self.output_log_status.set(
            "Diagnostics are enabled for this file."
            if diagnostics_enabled and output_log_path.strip()
            else "Choose a file path to enable diagnostics logging."
            if diagnostics_enabled
            else "Diagnostics file logging is disabled."
        )
        self.diagnostics_enabled = tk.BooleanVar(
            master=self.dialog,
            value=diagnostics_enabled,
        )
        ttk.Checkbutton(
            content,
            text="Enable diagnostics file logging",
            variable=self.diagnostics_enabled,
            command=self._save_diagnostics_enabled,
        ).grid(row=10, column=0, sticky="w")
        ttk.Label(content, text="Diagnostics file path (kept when logging is disabled)").grid(
            row=11,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        log_path_controls = ttk.Frame(content)
        log_path_controls.grid(row=12, column=0, sticky="ew", pady=(6, 0))
        log_path_controls.columnconfigure(0, weight=1)
        ttk.Entry(
            log_path_controls,
            textvariable=self.output_log_path,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            log_path_controls,
            text="Browse...",
            command=self._browse_output_log,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            log_path_controls,
            text="Save",
            command=self._save_output_logging,
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            log_path_controls,
            text="Clear",
            command=self._clear_output_log_path,
        ).grid(row=0, column=3, padx=(8, 0))
        self.output_log_status_label = ttk.Label(
            content,
            textvariable=self.output_log_status,
        )
        self.output_log_status_label.grid(
            row=13,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Separator(content).grid(row=14, column=0, sticky="ew", pady=(14, 10))
        ttk.Label(content, text="Deepbrid AI API", font=("TkDefaultFont", 12, "bold")).grid(
            row=15,
            column=0,
            sticky="w",
            pady=(0, 6),
        )
        ttk.Label(
            content,
            text="API key for direct, official AI requests (Premium required).",
            wraplength=420,
            justify="left",
        ).grid(row=16, column=0, sticky="w")
        api_key_controls = ttk.Frame(content)
        api_key_controls.grid(row=17, column=0, sticky="ew", pady=(6, 0))
        api_key_controls.columnconfigure(0, weight=1)
        self.api_key = tk.StringVar(master=self.dialog, value=api_key)
        self.api_key_entry = ttk.Entry(
            api_key_controls,
            textvariable=self.api_key,
            show="*",
        )
        self.api_key_entry.grid(row=0, column=0, sticky="ew")

        def toggle_api_key_visibility() -> None:
            visible = self.api_key_entry.cget("show") == "*"
            self.api_key_entry.configure(show="" if visible else "*")
            api_key_visibility.configure(text="Hide" if visible else "Show")

        api_key_visibility = ttk.Button(
            api_key_controls,
            text="Show",
            command=toggle_api_key_visibility,
        )
        api_key_visibility.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(
            api_key_controls,
            text="Get API key",
            command=self.on_get_api_key or (lambda: None),
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            api_key_controls,
            text="Save",
            command=self._save_api_key,
        ).grid(row=0, column=3, padx=(8, 0))
        self.api_key_status = tk.StringVar(
            master=self.dialog,
            value="Direct API is used when an API key is saved." if api_key else "Chrome fallback is active.",
        )
        ttk.Label(content, textvariable=self.api_key_status).grid(
            row=18,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Label(content, textvariable=self.status).grid(
            row=19,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Button(frame, text="Close", command=self.dialog.destroy).grid(
            row=1,
            column=1,
            sticky="e",
            pady=(12, 0),
        )
        self.dialog.grab_set()
        self.saved_enabled = auto_open_chrome
        self.saved_dark_theme = dark_theme
        self.saved_wrap_text = wrap_text
        self.saved_remove_images_after_send = remove_images_after_send
        self.saved_diagnostics_enabled = diagnostics_enabled
        self.saved_output_log_path = output_log_path
        self.saved_api_key = api_key

    def _save(self) -> None:
        enabled = self.enabled.get()
        try:
            self.settings.set_bool(AUTO_OPEN_CHROME, enabled)
        except (OSError, sqlite3.Error) as error:
            self.enabled.set(self.saved_enabled)
            self.status.set(f"Could not save setting: {error}")
            return
        self.saved_enabled = enabled
        self.on_auto_open_changed(enabled)
        self.status.set("Saved.")

    def _save_dark_theme(self) -> None:
        enabled = self.dark_theme.get()
        try:
            self.settings.set_bool(DARK_THEME, enabled)
        except (OSError, sqlite3.Error) as error:
            self.dark_theme.set(self.saved_dark_theme)
            self.status.set(f"Could not save setting: {error}")
            return
        self.saved_dark_theme = enabled
        self.on_dark_theme_changed(enabled)
        self.dialog.configure(
            background="#202522" if enabled else "#f0f2f0",
        )
        self.status.set("Saved.")

    def _save_wrap_text(self) -> None:
        enabled = self.wrap_text.get()
        try:
            self.settings.set_bool(WRAP_TEXT, enabled)
        except (OSError, sqlite3.Error) as error:
            self.wrap_text.set(self.saved_wrap_text)
            self.status.set(f"Could not save setting: {error}")
            return
        self.saved_wrap_text = enabled
        self.on_wrap_text_changed(enabled)
        self.status.set("Saved.")

    def _save_remove_images_after_send(self) -> None:
        enabled = self.remove_images_after_send.get()
        try:
            self.settings.set_bool(REMOVE_IMAGES_AFTER_SEND, enabled)
        except (OSError, sqlite3.Error) as error:
            self.remove_images_after_send.set(self.saved_remove_images_after_send)
            self.status.set(f"Could not save setting: {error}")
            return
        self.saved_remove_images_after_send = enabled
        self.on_remove_images_after_send_changed(enabled)
        self.status.set("Saved.")

    def _save_diagnostics_enabled(self) -> None:
        enabled = self.diagnostics_enabled.get()
        try:
            self.settings.set_bool(DIAGNOSTICS_ENABLED, enabled)
            self.on_diagnostics_enabled_changed(enabled)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.diagnostics_enabled.set(self.saved_diagnostics_enabled)
            try:
                self.settings.set_bool(
                    DIAGNOSTICS_ENABLED,
                    self.saved_diagnostics_enabled,
                )
            except (OSError, sqlite3.Error) as rollback_error:
                self.status.set(
                    f"Could not save diagnostics setting: {error}; "
                    f"could not restore its previous value: {rollback_error}"
                )
                return
            self.status.set(f"Could not save diagnostics setting: {error}")
            return
        self.saved_diagnostics_enabled = enabled
        if enabled and not self.output_log_path.get().strip():
            self.output_log_status.set("Choose a file path to enable diagnostics logging.")
        else:
            self.output_log_status.set(
                "Diagnostics are enabled for this file."
                if enabled
                else "Diagnostics file logging is disabled."
            )
        self.status.set("Saved.")

    def _save_output_logging(self) -> None:
        path = self.output_log_path.get()
        try:
            self.on_output_logging_changed(path)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.output_log_path.set(self.saved_output_log_path)
            self.output_log_status.set(f"Could not save output log setting: {error}")
            return
        self.saved_output_log_path = path
        self.output_log_status.set(
            "Diagnostics are enabled for this file."
            if self.diagnostics_enabled.get() and path.strip()
            else "Choose a file path to enable diagnostics logging."
            if self.diagnostics_enabled.get()
            else "Diagnostics file logging is disabled."
        )

    def _browse_output_log(self) -> None:
        selected = filedialog.asksaveasfilename(
            parent=self.dialog,
            title="Choose console log file",
            initialfile=self.output_log_path.get() or "deepbrid-code.log",
            defaultextension=".log",
            filetypes=(("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*")),
        )
        if not selected:
            return
        self.output_log_path.set(selected)
        self._save_output_logging()

    def _clear_output_log_path(self) -> None:
        self.output_log_path.set("")
        self._save_output_logging()

    def _save_api_key(self) -> None:
        key = self.api_key.get().strip()
        if self.on_api_key_changed is None:
            return
        try:
            self.on_api_key_changed(key)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.api_key.set(self.saved_api_key)
            self.api_key_status.set(f"Could not save API key: {error}")
            return
        self.saved_api_key = key
        self.api_key_status.set(
            "Direct API is active." if key else "Chrome fallback is active."
        )
