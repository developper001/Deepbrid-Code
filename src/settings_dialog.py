from __future__ import annotations

import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Protocol

from .ai_api import DeepbridApiClient, DeepbridApiError
from .settings import (
    DARK_THEME,
    DIAGNOSTICS_ENABLED,
    REMOVE_IMAGES_AFTER_SEND,
    SecretStorageError,
    WRAP_TEXT,
)
from .terminal_tools import (
    DEFAULT_TERMINAL_COMMAND_WHITELIST,
    normalize_terminal_command_whitelist,
)


class SettingsBackend(Protocol):
    def set(self, name: str, value: str) -> None: ...
    def set_bool(self, name: str, value: bool) -> None: ...
    def set_secret(self, name: str, value: str) -> None: ...


class SettingsDialog:
    def __init__(
        self,
        parent: tk.Tk,
        settings: SettingsBackend,
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
        terminal_command_whitelist: tuple[str, ...] = DEFAULT_TERMINAL_COMMAND_WHITELIST,
        on_terminal_whitelist_changed: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_dark_theme_changed = on_dark_theme_changed
        self.on_wrap_text_changed = on_wrap_text_changed
        self.on_remove_images_after_send_changed = on_remove_images_after_send_changed
        self.on_diagnostics_enabled_changed = on_diagnostics_enabled_changed
        self.on_output_logging_changed = on_output_logging_changed
        self.on_api_key_changed = on_api_key_changed
        self.terminal_command_whitelist = normalize_terminal_command_whitelist(
            terminal_command_whitelist
        )
        self.on_terminal_whitelist_changed = on_terminal_whitelist_changed
        self.terminal_whitelist_dialog: TerminalWhitelistDialog | None = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Settings")
        self.dialog.geometry("640x560")
        self.dialog.minsize(560, 500)
        self.dialog.transient(parent)
        self.api_key_test_events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.api_key_test_active = False
        self.api_key_test_after_id: str | None = None

        frame = ttk.Frame(self.dialog, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)

        navigation = ttk.Frame(frame, padding=(0, 0, 16, 0))
        navigation.grid(row=0, column=0, sticky="ns")
        ttk.Label(navigation, text="Sections").pack(anchor="w", pady=(0, 8))
        ttk.Label(
            navigation,
            text="Deepbrid API",
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

        style = ttk.Style(self.dialog)
        self.settings_canvas = tk.Canvas(
            frame,
            background=style.lookup("TFrame", "background"),
            highlightthickness=0,
            borderwidth=0,
        )
        self.settings_canvas.grid(row=0, column=1, sticky="nsew")
        self.settings_scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.settings_canvas.yview,
        )
        self.settings_scrollbar.grid(row=0, column=2, sticky="ns")
        self.settings_canvas.configure(yscrollcommand=self.settings_scrollbar.set)
        content = ttk.Frame(self.settings_canvas, padding=12, relief="groove")
        settings_window = self.settings_canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
        )
        content.bind(
            "<Configure>",
            lambda _event: self.settings_canvas.configure(
                scrollregion=self.settings_canvas.bbox("all")
            ),
        )
        self.settings_canvas.bind(
            "<Configure>",
            lambda event: self.settings_canvas.itemconfigure(
                settings_window,
                width=event.width,
            ),
        )
        self.dialog.bind("<MouseWheel>", self._scroll_settings, add="+")
        self.dialog.bind("<Button-4>", self._scroll_settings, add="+")
        self.dialog.bind("<Button-5>", self._scroll_settings, add="+")
        self.dialog.bind("<Destroy>", self._on_dialog_destroy, add="+")
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        ttk.Label(content, text="Deepbrid AI API", font=("TkDefaultFont", 14, "bold")).grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 8),
        )
        self.status = tk.StringVar(master=self.dialog)
        ttk.Label(
            content,
            text=(
                "A Premium API key is required. Requests use Deepbrid's official API; "
                "the key is stored in your operating-system credential store."
            ),
            wraplength=420,
            justify="left",
        ).grid(row=1, column=0, sticky="w")
        self.api_key = tk.StringVar(master=self.dialog, value=api_key)
        api_key_controls = ttk.Frame(content)
        api_key_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        api_key_controls.columnconfigure(0, weight=1)
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
            text="Save",
            command=self._save_api_key,
        ).grid(row=0, column=2, padx=(8, 0))
        self.api_key_test_button = ttk.Button(
            api_key_controls,
            text="Test",
            command=self._test_api_key,
        )
        self.api_key_test_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(
            content,
            text="Get a key at https://www.deepbrid.com/devices",
            wraplength=420,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.api_key_status = tk.StringVar(
            master=self.dialog,
            value="API ready." if api_key else "Add your API key to use Deepbrid AI.",
        )
        ttk.Label(content, textvariable=self.api_key_status).grid(
            row=4,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Separator(content).grid(row=5, column=0, sticky="ew", pady=(14, 10))
        ttk.Label(content, text="Appearance", font=("TkDefaultFont", 12, "bold")).grid(
            row=6,
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
        ).grid(row=7, column=0, sticky="w")
        ttk.Checkbutton(
            content,
            text="Wrap text in file preview",
            variable=self.wrap_text,
            command=self._save_wrap_text,
        ).grid(row=8, column=0, sticky="w", pady=(6, 0))
        self.remove_images_after_send = tk.BooleanVar(
            master=self.dialog,
            value=remove_images_after_send,
        )
        ttk.Checkbutton(
            content,
            text="Remove images from context after sending",
            variable=self.remove_images_after_send,
            command=self._save_remove_images_after_send,
        ).grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.terminal_whitelist_button = ttk.Button(
            content,
            text="Command whitelist...",
            command=self._open_terminal_whitelist,
            state="normal" if on_terminal_whitelist_changed else "disabled",
        )
        self.terminal_whitelist_button.grid(
            row=9,
            column=1,
            sticky="e",
            padx=(8, 0),
        )
        ttk.Separator(content).grid(row=10, column=0, sticky="ew", pady=(14, 10))
        ttk.Label(content, text="Diagnostics", font=("TkDefaultFont", 12, "bold")).grid(
            row=11,
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
        ).grid(row=12, column=0, sticky="w")
        ttk.Label(content, text="Diagnostics file path (kept when logging is disabled)").grid(
            row=13,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        log_path_controls = ttk.Frame(content)
        log_path_controls.grid(row=14, column=0, sticky="ew", pady=(6, 0))
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
            row=15,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        ttk.Label(content, textvariable=self.status).grid(
            row=16,
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
        self.saved_dark_theme = dark_theme
        self.saved_wrap_text = wrap_text
        self.saved_remove_images_after_send = remove_images_after_send
        self.saved_diagnostics_enabled = diagnostics_enabled
        self.saved_output_log_path = output_log_path
        self.saved_api_key = api_key

    def _scroll_settings(self, event: tk.Event) -> str:
        if getattr(event, "delta", 0):
            units = int(-event.delta / 120)
            if not units:
                units = -1 if event.delta > 0 else 1
        else:
            units = {4: -1, 5: 1}.get(getattr(event, "num", None), 0)
        if units:
            self.settings_canvas.yview_scroll(units, "units")
        return "break"

    def _on_dialog_destroy(self, event: tk.Event) -> None:
        if event.widget is self.dialog and self.api_key_test_after_id is not None:
            try:
                self.dialog.after_cancel(self.api_key_test_after_id)
            except tk.TclError:
                pass
            self.api_key_test_after_id = None

    def _test_api_key(self) -> None:
        if self.api_key_test_active:
            return
        api_key = self.api_key.get().strip()
        if not api_key:
            self.api_key_status.set("Enter an API key first.")
            return
        self.api_key_test_active = True
        self.api_key_test_button.configure(state="disabled")
        self.api_key_status.set("Checking access to Deepbrid AI models and assistants...")
        worker = threading.Thread(
            target=self._test_api_key_worker,
            args=(api_key,),
            name="deepbrid-api-key-test",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as error:
            self.api_key_test_active = False
            self.api_key_test_button.configure(state="normal")
            self.api_key_status.set(f"Could not start API key test: {error}")
            return
        self.api_key_test_after_id = self.dialog.after(
            100,
            self._poll_api_key_test_events,
        )

    def _test_api_key_worker(self, api_key: str) -> None:
        try:
            client = DeepbridApiClient(api_key)
            client.list_models()
            client.list_assistants()
        except (DeepbridApiError, OSError, RuntimeError, ValueError) as error:
            self.api_key_test_events.put(("error", str(error)))
        else:
            self.api_key_test_events.put(
                ("success", "API key works with the models and assistants endpoints.")
            )

    def _poll_api_key_test_events(self) -> None:
        self.api_key_test_after_id = None
        while True:
            try:
                _event, message = self.api_key_test_events.get_nowait()
            except queue.Empty:
                break
            self.api_key_test_active = False
            self.api_key_test_button.configure(state="normal")
            self.api_key_status.set(message)
        if self.api_key_test_active:
            try:
                self.api_key_test_after_id = self.dialog.after(
                    100,
                    self._poll_api_key_test_events,
                )
            except tk.TclError:
                self.api_key_test_active = False

    def _open_terminal_whitelist(self) -> None:
        if self.on_terminal_whitelist_changed is None:
            return
        self.terminal_whitelist_dialog = TerminalWhitelistDialog(
            self.dialog,
            self.terminal_command_whitelist,
            self._save_terminal_whitelist,
        )

    def _save_terminal_whitelist(self, patterns: tuple[str, ...]) -> None:
        if self.on_terminal_whitelist_changed is None:
            return
        normalized = normalize_terminal_command_whitelist(patterns)
        if not normalized:
            raise ValueError("Add at least one command pattern to the whitelist.")
        self.on_terminal_whitelist_changed(normalized)
        self.terminal_command_whitelist = normalized
        self.status.set("Saved terminal command whitelist.")

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
        self.settings_canvas.configure(
            background=ttk.Style(self.dialog).lookup("TFrame", "background"),
        )
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
        except (OSError, ValueError, sqlite3.Error, SecretStorageError) as error:
            self.api_key.set(self.saved_api_key)
            self.api_key_status.set(f"Could not save API key: {error}")
            return
        self.saved_api_key = key
        self.api_key_status.set(
            "API key saved." if key else "API key removed."
        )


class TerminalWhitelistDialog:
    def __init__(
        self,
        parent: tk.Toplevel,
        whitelist_patterns: tuple[str, ...],
        on_save: Callable[[tuple[str, ...]], None],
    ) -> None:
        self.on_save = on_save
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Terminal command whitelist")
        self.dialog.geometry("680x380")
        self.dialog.minsize(600, 340)
        self.dialog.transient(parent)

        frame = ttk.Frame(self.dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=(
                "Enter one command pattern per line. An asterisk (*) matches "
                "one argument, so `ls *` matches `ls -a`. This is not shell "
                "expansion. Only the app's built-in safe operations are supported; "
                "custom executables and scripts cannot be added. Each run still "
                "requires your approval."
            ),
            wraplength=640,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))
        editor_frame = ttk.Frame(frame)
        editor_frame.pack(fill="both", expand=True)
        self.pattern_editor = tk.Text(editor_frame, height=8, wrap="none")
        pattern_scrollbar = ttk.Scrollbar(
            editor_frame,
            orient="vertical",
            command=self.pattern_editor.yview,
        )
        self.pattern_editor.configure(yscrollcommand=pattern_scrollbar.set)
        self.pattern_editor.pack(side="left", fill="both", expand=True)
        pattern_scrollbar.pack(side="right", fill="y")
        self.pattern_editor.insert("1.0", "\n".join(whitelist_patterns))

        self.status = tk.StringVar(master=self.dialog)
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.dialog.destroy).pack(
            side="right",
        )
        ttk.Button(buttons, text="Save", command=self._save).pack(
            side="right",
            padx=(0, 8),
        )
        self.dialog.grab_set()

    def _save(self) -> None:
        try:
            patterns = normalize_terminal_command_whitelist(
                self.pattern_editor.get("1.0", "end-1c").splitlines()
            )
        except ValueError as error:
            self.status.set(str(error))
            return
        if not patterns:
            self.status.set("Add at least one command pattern to the whitelist.")
            return
        try:
            self.on_save(patterns)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.set(f"Could not save terminal command whitelist: {error}")
            return
        self.dialog.destroy()
