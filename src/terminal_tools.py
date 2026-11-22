from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ALLOWED_TERMINAL_COMMANDS = (
    "ls",
    "git status --short --branch",
    "python --version",
    "python -m unittest discover -s tests -v",
    "python -m compileall -q src tests",
)
MAX_TERMINAL_OUTPUT = 16_000


@dataclass(frozen=True)
class TerminalCommandResult:
    command: str
    working_directory: Path
    exit_code: int
    stdout: str
    stderr: str


def normalize_terminal_commands(
    commands: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    if not isinstance(commands, (tuple, list)) or any(
        not isinstance(command, str) for command in commands
    ):
        raise ValueError("The terminal command whitelist must be a list of command names.")
    if len(set(commands)) != len(commands):
        raise ValueError("The terminal command whitelist contains duplicates.")
    unknown_commands = set(commands).difference(ALLOWED_TERMINAL_COMMANDS)
    if unknown_commands:
        raise ValueError(
            "The terminal command whitelist contains unsupported commands: "
            + ", ".join(sorted(unknown_commands))
        )
    selected = set(commands)
    return tuple(
        command for command in ALLOWED_TERMINAL_COMMANDS if command in selected
    )


def load_terminal_command_whitelist(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ALLOWED_TERMINAL_COMMANDS
    try:
        commands = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("The saved terminal command whitelist is invalid JSON.") from error
    if not isinstance(commands, list):
        raise ValueError("The saved terminal command whitelist must be a JSON list.")
    return normalize_terminal_commands(commands)


def add_terminal_tool_instructions(
    prompt: str,
    allowed_commands: tuple[str, ...] = ALLOWED_TERMINAL_COMMANDS,
) -> str:
    commands = normalize_terminal_commands(allowed_commands)
    if not commands:
        raise ValueError("At least one terminal command must be enabled.")
    instructions = f"""\
You may request a local development command when needed. This is a limited
client-side tool protocol, not native function calling. To request a command,
respond with only this JSON object and no Markdown:
{{"deepbrid_tool":"terminal","command":"EXACT_COMMAND"}}
Allowed commands are exactly: {", ".join(repr(command) for command in commands)}.
Do not add arguments or shell operators. The user must approve each command.
After a result is provided, continue your answer or request another allowed
command. If no command is needed, answer normally."""
    return f"{prompt}\n\n{instructions}"


def parse_terminal_tool_request(
    content: str,
    allowed_commands: tuple[str, ...] = ALLOWED_TERMINAL_COMMANDS,
) -> str | None:
    try:
        result = json.loads(content.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict) or result.get("deepbrid_tool") != "terminal":
        return None
    if set(result) != {"deepbrid_tool", "command"}:
        raise ValueError("The assistant returned an invalid terminal tool request.")
    command = result.get("command")
    enabled_commands = normalize_terminal_commands(allowed_commands)
    if not isinstance(command, str) or command not in enabled_commands:
        raise ValueError("The requested command is not in your enabled whitelist.")
    return command


def run_allowed_terminal_command(
    command: str,
    working_directory: Path,
    allowed_commands: tuple[str, ...] = ALLOWED_TERMINAL_COMMANDS,
) -> TerminalCommandResult:
    if command not in normalize_terminal_commands(allowed_commands):
        raise ValueError("The requested command is not in your enabled whitelist.")
    root = working_directory.resolve()
    if not root.is_dir():
        raise ValueError(f"Project folder does not exist: {root}")

    if command == "ls":
        entries = sorted(root.iterdir(), key=lambda path: path.name.casefold())
        lines = [
            f"{path.name}{'/' if path.is_dir() else ''}"
            for path in entries[:200]
        ]
        if len(entries) > 200:
            lines.append(f"... and {len(entries) - 200} more entries")
        return TerminalCommandResult(command, root, 0, "\n".join(lines), "")

    command_arguments = {
        "git status --short --branch": [
            "git",
            "-c",
            "core.fsmonitor=false",
            "status",
            "--short",
            "--branch",
        ],
        "python --version": [sys.executable, "--version"],
        "python -m unittest discover -s tests -v": [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        "python -m compileall -q src tests": [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "src",
            "tests",
        ],
    }
    if command == "python -m unittest discover -s tests -v":
        if (root / "tests").is_symlink():
            raise ValueError("The tests directory cannot be a symbolic link.")
    elif command == "python -m compileall -q src tests":
        if any((root / directory).is_symlink() for directory in ("src", "tests")):
            raise ValueError("The source and tests directories cannot be symbolic links.")
    try:
        result = subprocess.run(
            command_arguments[command],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        stdout = _partial_output(error.stdout)
        stderr = _partial_output(error.stderr)
        timeout_message = "Command timed out after 180 seconds."
        return TerminalCommandResult(
            command,
            root,
            124,
            _truncate(stdout),
            _truncate(f"{stderr}\n{timeout_message}".strip()),
        )
    except OSError as error:
        raise RuntimeError(f"Could not run the approved command: {error}") from error

    return TerminalCommandResult(
        command,
        root,
        result.returncode,
        _truncate(result.stdout),
        _truncate(result.stderr),
    )


def _partial_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _truncate(output: str) -> str:
    if len(output) <= MAX_TERMINAL_OUTPUT:
        return output
    return output[:MAX_TERMINAL_OUTPUT] + "\n... output truncated"
