from __future__ import annotations

import fnmatch
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SAFE_COMMAND_ARGUMENTS = {
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
DEFAULT_TERMINAL_COMMAND_WHITELIST = (
    "ls",
    "ls *",
    *SAFE_COMMAND_ARGUMENTS,
)
MAX_TERMINAL_OUTPUT = 16_000


@dataclass(frozen=True)
class TerminalCommandResult:
    command: str
    working_directory: Path
    exit_code: int
    stdout: str
    stderr: str


def _tokenize_command(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("A terminal command cannot be empty.")
    if any(ord(character) < 32 for character in command):
        raise ValueError("Control characters are not allowed in terminal commands.")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as error:
        raise ValueError(f"Invalid terminal command syntax: {error}") from error
    if not tokens or any(
        any(character in token for character in ";|&><`$")
        for token in tokens
    ):
        raise ValueError("Shell operators are not allowed in terminal commands.")
    return tokens


def normalize_terminal_command_whitelist(
    patterns: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    if not isinstance(patterns, (tuple, list)) or any(
        not isinstance(pattern, str) for pattern in patterns
    ):
        raise ValueError("The terminal whitelist must contain command patterns.")

    normalized: list[str] = []
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        tokens = _tokenize_command(pattern)
        supported = (
            tokens == ["ls"]
            or (
                len(tokens) == 2
                and tokens[0] == "ls"
                and tokens[1] in {"-a", "--all", "*"}
            )
            or pattern in SAFE_COMMAND_ARGUMENTS
        )
        if not supported:
            raise ValueError(
                f"Unsupported terminal whitelist pattern: {pattern!r}. "
                "Only the built-in safe commands and ls option patterns are supported."
            )
        if pattern in normalized:
            raise ValueError(f"Duplicate terminal whitelist pattern: {pattern!r}.")
        normalized.append(pattern)
    return tuple(normalized)


def load_terminal_command_whitelist(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_TERMINAL_COMMAND_WHITELIST

    stripped = value.strip()
    if not stripped:
        return ()
    if stripped.startswith("["):
        try:
            saved_patterns = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ValueError("The saved terminal whitelist is invalid JSON.") from error
        if not isinstance(saved_patterns, list):
            raise ValueError("The saved terminal whitelist must be a JSON list.")
        return normalize_terminal_command_whitelist(saved_patterns)
    return normalize_terminal_command_whitelist(value.splitlines())


def _match_whitelist_pattern(
    command_tokens: list[str],
    whitelist: tuple[str, ...] | list[str],
) -> bool:
    for pattern in normalize_terminal_command_whitelist(whitelist):
        pattern_tokens = _tokenize_command(pattern)
        if len(command_tokens) == len(pattern_tokens) and all(
            fnmatch.fnmatchcase(token, pattern_token)
            for token, pattern_token in zip(command_tokens, pattern_tokens)
        ):
            return True
    return False


def terminal_command_matches_whitelist(
    command: str,
    whitelist: tuple[str, ...] | list[str],
) -> bool:
    return _match_whitelist_pattern(_tokenize_command(command), whitelist)


def add_terminal_tool_instructions(
    prompt: str,
    whitelist: tuple[str, ...] | list[str] = DEFAULT_TERMINAL_COMMAND_WHITELIST,
) -> str:
    patterns = normalize_terminal_command_whitelist(whitelist)
    if not patterns:
        raise ValueError("Add at least one command pattern to the Settings whitelist.")
    instructions = f"""\
You may request a local development command when needed. This is a limited
client-side protocol, not native function calling. To request a command, reply
with only this JSON object and no Markdown:
{{"deepbrid_tool":"terminal","command":"EXACT_COMMAND"}}
Allowed patterns from the user Settings are exactly: {", ".join(repr(pattern) for pattern in patterns)}.
An asterisk (*) matches one argument; it is not shell expansion. Only the
application's built-in safe operations can run. Do not use shell operators or
request scripts. The user must approve each command. After a result is
provided, continue your answer or request another matching command. If no
command is needed, answer normally."""
    return f"{prompt}\n\n{instructions}"


def parse_terminal_tool_request(
    content: str,
    whitelist: tuple[str, ...] | list[str] = DEFAULT_TERMINAL_COMMAND_WHITELIST,
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
    if not isinstance(command, str):
        raise ValueError("The requested command must be a string.")
    command_tokens = _tokenize_command(command)
    if not _match_whitelist_pattern(command_tokens, whitelist):
        raise ValueError("The requested command does not match your Settings whitelist.")
    return command


def run_allowed_terminal_command(
    command: str,
    working_directory: Path,
    whitelist: tuple[str, ...] | list[str] = DEFAULT_TERMINAL_COMMAND_WHITELIST,
) -> TerminalCommandResult:
    command_tokens = _tokenize_command(command)
    if not _match_whitelist_pattern(command_tokens, whitelist):
        raise ValueError("The requested command does not match your Settings whitelist.")
    root = working_directory.resolve()
    if not root.is_dir():
        raise ValueError(f"Project folder does not exist: {root}")

    if command_tokens[0] == "ls":
        options = command_tokens[1:]
        if options not in ([], ["-a"], ["--all"]):
            raise ValueError("Only ls, ls -a, and ls --all are supported.")
        show_hidden = bool(options)
        entries = sorted(root.iterdir(), key=lambda path: path.name.casefold())
        if not show_hidden:
            entries = [path for path in entries if not path.name.startswith(".")]
        lines = [
            f"{path.name}{'/' if path.is_dir() else ''}"
            for path in entries[:200]
        ]
        if len(entries) > 200:
            lines.append(f"... and {len(entries) - 200} more entries")
        return TerminalCommandResult(command, root, 0, "\n".join(lines), "")

    canonical_command = " ".join(command_tokens)
    if canonical_command not in SAFE_COMMAND_ARGUMENTS:
        raise ValueError("The command is not supported by the safe command runner.")
    if canonical_command == "python -m unittest discover -s tests -v":
        if (root / "tests").is_symlink():
            raise ValueError("The tests directory cannot be a symbolic link.")
    elif canonical_command == "python -m compileall -q src tests":
        if any((root / directory).is_symlink() for directory in ("src", "tests")):
            raise ValueError("The source and tests directories cannot be symbolic links.")
    try:
        result = subprocess.run(
            SAFE_COMMAND_ARGUMENTS[canonical_command],
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
