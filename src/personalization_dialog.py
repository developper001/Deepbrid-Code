from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .ai_api import AiMemory, AiPrompt, DeepbridApiClient, DeepbridApiError


class PersonalizationDialog:
    def __init__(
        self,
        parent: tk.Tk,
        api_key: str,
        insert_prompt: Callable[[str], None],
        theme_colors: dict[str, str],
    ) -> None:
        self.api_key = api_key
        self.insert_prompt = insert_prompt
        self.theme_colors = theme_colors
        self.window = tk.Toplevel(parent)
        self.window.title("Saved prompts and memories")
        self.window.geometry("760x520")
        self.window.minsize(620, 420)
        self.window.transient(parent)
        self.window.configure(background=theme_colors["background"])
        self.events: queue.Queue[tuple[str, str, object, str]] = queue.Queue()
        self.records: dict[str, dict[str, AiPrompt | AiMemory]] = {
            "prompts": {},
            "memories": {},
        }
        self.selected_ids: dict[str, str | None] = {
            "prompts": None,
            "memories": None,
        }
        self.busy: set[str] = set()
        self.trees: dict[str, ttk.Treeview] = {}
        self.titles: dict[str, ttk.Entry] = {}
        self.editors: dict[str, tk.Text] = {}
        self.statuses: dict[str, tk.StringVar] = {}
        self.buttons: dict[str, list[ttk.Button]] = {}
        self._after_id: str | None = None

        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_tab(notebook, "prompts", "Saved prompts")
        self._build_tab(notebook, "memories", "Memories")
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self._load("prompts")
        self._load("memories")
        self._after_id = self.window.after(100, self._poll_events)

    def _build_tab(
        self,
        notebook: ttk.Notebook,
        collection: str,
        title: str,
    ) -> None:
        tab = ttk.Frame(notebook, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        notebook.add(tab, text=title)

        split = ttk.Panedwindow(tab, orient="horizontal")
        split.grid(row=0, column=0, sticky="nsew")
        list_panel = ttk.Frame(split, padding=(0, 0, 8, 0))
        edit_panel = ttk.Frame(split)
        list_panel.columnconfigure(0, weight=1)
        list_panel.rowconfigure(0, weight=1)
        edit_panel.columnconfigure(0, weight=1)
        edit_panel.rowconfigure(2, weight=1)
        split.add(list_panel, weight=1)
        split.add(edit_panel, weight=3)

        tree = ttk.Treeview(list_panel, show="tree", selectmode="browse")
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, name=collection: self._select_record(name),
        )
        self.trees[collection] = tree

        row = 0
        if collection == "prompts":
            ttk.Label(edit_panel, text="Title").grid(
                row=row,
                column=0,
                sticky="w",
            )
            title_entry = ttk.Entry(edit_panel)
            title_entry.grid(row=row + 1, column=0, sticky="ew", pady=(2, 8))
            self.titles[collection] = title_entry
            row += 2
        ttk.Label(
            edit_panel,
            text="Prompt text" if collection == "prompts" else "Memory",
        ).grid(row=row, column=0, sticky="w")
        editor = tk.Text(
            edit_panel,
            wrap="word",
            height=12,
            background=self.theme_colors["field"],
            foreground=self.theme_colors["foreground"],
            insertbackground=self.theme_colors["foreground"],
            selectbackground=self.theme_colors["selection"],
        )
        editor.grid(row=row + 1, column=0, sticky="nsew", pady=(2, 8))
        editor_scrollbar = ttk.Scrollbar(
            edit_panel,
            orient="vertical",
            command=editor.yview,
        )
        editor.configure(yscrollcommand=editor_scrollbar.set)
        editor_scrollbar.grid(row=row + 1, column=1, sticky="ns", pady=(2, 8))
        self.editors[collection] = editor

        actions = ttk.Frame(edit_panel)
        actions.grid(row=row + 2, column=0, sticky="ew")
        buttons = [
            ttk.Button(actions, text="New", command=lambda: self._new(collection)),
            ttk.Button(actions, text="Save", command=lambda: self._save(collection)),
            ttk.Button(actions, text="Delete", command=lambda: self._delete(collection)),
        ]
        for button in buttons:
            button.pack(side="left", padx=(0, 6))
        if collection == "prompts":
            buttons.append(
                ttk.Button(
                    actions,
                    text="Insert into message",
                    command=self._insert_selected_prompt,
                )
            )
            buttons[-1].pack(side="left", padx=(0, 6))
        else:
            buttons.append(
                ttk.Button(
                    actions,
                    text="Clear all memories...",
                    command=self._clear_memories,
                )
            )
            buttons[-1].pack(side="left", padx=(0, 6))
        self.buttons[collection] = buttons

        status = tk.StringVar(master=self.window, value="Loading...")
        ttk.Label(edit_panel, textvariable=status).grid(
            row=row + 3,
            column=0,
            sticky="w",
            pady=(8, 0),
        )
        self.statuses[collection] = status

    def _load(self, collection: str) -> None:
        self._start_request(collection, "list")

    def _new(self, collection: str) -> None:
        self.selected_ids[collection] = None
        self.trees[collection].selection_remove(
            *self.trees[collection].selection()
        )
        if collection == "prompts":
            self.titles[collection].delete(0, "end")
        editor = self.editors[collection]
        editor.delete("1.0", "end")
        self.statuses[collection].set(
            "Enter a title and prompt text."
            if collection == "prompts"
            else "Enter a memory to save."
        )

    def _select_record(self, collection: str) -> None:
        selection = self.trees[collection].selection()
        if not selection:
            return
        record_id = selection[0]
        record = self.records[collection].get(record_id)
        if record is None:
            return
        self.selected_ids[collection] = record_id
        if isinstance(record, AiPrompt):
            self.titles[collection].delete(0, "end")
            self.titles[collection].insert(0, record.title)
            content = record.body
        else:
            content = record.content
        editor = self.editors[collection]
        editor.delete("1.0", "end")
        editor.insert("1.0", content)

    def _save(self, collection: str) -> None:
        content = self.editors[collection].get("1.0", "end-1c").strip()
        if collection == "prompts":
            title = self.titles[collection].get().strip()
            if not title or not content:
                self.statuses[collection].set(
                    "A saved prompt needs both a title and prompt text."
                )
                return
            values = {"title": title, "body": content}
        else:
            if not content:
                self.statuses[collection].set("Memory content cannot be empty.")
                return
            values = {"content": content}
        record_id = self.selected_ids[collection]
        action = "update" if record_id is not None else "create"
        self._start_request(collection, action, record_id, values)

    def _delete(self, collection: str) -> None:
        record_id = self.selected_ids[collection]
        if record_id is None:
            self.statuses[collection].set("Select an item to delete.")
            return
        if not messagebox.askyesno(
            "Delete item",
            "Delete the selected item from your Deepbrid account?",
            parent=self.window,
        ):
            return
        self._start_request(collection, "delete", record_id)

    def _clear_memories(self) -> None:
        if not messagebox.askyesno(
            "Clear all memories",
            "Permanently delete all memories saved to your Deepbrid account?",
            parent=self.window,
        ):
            return
        self._start_request("memories", "delete-all")

    def _insert_selected_prompt(self) -> None:
        record_id = self.selected_ids["prompts"]
        record = self.records["prompts"].get(record_id or "")
        if isinstance(record, AiPrompt):
            self.insert_prompt(record.body)

    def _start_request(
        self,
        collection: str,
        action: str,
        record_id: str | None = None,
        values: dict[str, str] | None = None,
    ) -> None:
        if collection in self.busy:
            return
        self.busy.add(collection)
        for button in self.buttons.get(collection, []):
            button.configure(state="disabled")
        self.statuses[collection].set("Working with Deepbrid...")
        worker = threading.Thread(
            target=self._request_worker,
            args=(collection, action, record_id, values or {}),
            name=f"deepbrid-{collection}",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError as error:
            self.busy.discard(collection)
            for button in self.buttons[collection]:
                button.configure(state="normal")
            self.statuses[collection].set(f"Could not start request: {error}")

    def _request_worker(
        self,
        collection: str,
        action: str,
        record_id: str | None,
        values: dict[str, str],
    ) -> None:
        try:
            client = DeepbridApiClient(self.api_key)
            if action == "create":
                if collection == "prompts":
                    client.create_prompt(values["title"], values["body"])
                else:
                    client.create_memory(values["content"])
            elif action == "update" and record_id is not None:
                if collection == "prompts":
                    client.update_prompt(
                        record_id,
                        values["title"],
                        values["body"],
                    )
                else:
                    client.update_memory(record_id, values["content"])
            elif action == "delete" and record_id is not None:
                if collection == "prompts":
                    client.delete_prompt(record_id)
                else:
                    client.delete_memory(record_id)
            elif action == "delete-all" and collection == "memories":
                client.delete_all_memories()
            elif action != "list":
                raise ValueError(f"Unknown {collection} action: {action}")

            records = (
                client.list_prompts()
                if collection == "prompts"
                else client.list_memories()
            )
            self.events.put((collection, "success", records, action))
        except (DeepbridApiError, OSError, RuntimeError, ValueError, KeyError) as error:
            self.events.put((collection, "error", str(error), action))

    def _poll_events(self) -> None:
        self._after_id = None
        while True:
            try:
                collection, event, payload, action = self.events.get_nowait()
            except queue.Empty:
                break
            self.busy.discard(collection)
            for button in self.buttons[collection]:
                button.configure(state="normal")
            if event == "error":
                self.statuses[collection].set(str(payload))
                continue
            expected_type = AiPrompt if collection == "prompts" else AiMemory
            if not isinstance(payload, list) or not all(
                isinstance(item, expected_type) for item in payload
            ):
                self.statuses[collection].set(
                    f"Deepbrid returned invalid {collection} data."
                )
                continue
            self.records[collection] = {
                item.prompt_id if isinstance(item, AiPrompt) else item.memory_id: item
                for item in payload
            }
            tree = self.trees[collection]
            tree.delete(*tree.get_children())
            for item in payload:
                item_id = (
                    item.prompt_id if isinstance(item, AiPrompt) else item.memory_id
                )
                label = (
                    item.title
                    if isinstance(item, AiPrompt)
                    else item.content.replace("\n", " ")[:70]
                )
                tree.insert("", "end", iid=item_id, text=label)
            if action != "list":
                self._new(collection)
            self.statuses[collection].set(f"{len(payload)} {collection} loaded.")
        if self.window.winfo_exists():
            self._after_id = self.window.after(100, self._poll_events)

    def _close(self) -> None:
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.window.destroy()
