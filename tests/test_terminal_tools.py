from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.terminal_tools import (
    ALLOWED_TERMINAL_COMMANDS,
    MAX_TERMINAL_OUTPUT,
    TerminalCommandResult,
    add_terminal_tool_instructions,
    load_terminal_command_whitelist,
    parse_terminal_tool_request,
    run_allowed_terminal_command,
)


class TerminalToolTests(unittest.TestCase):
    def test_terminal_request_parser_accepts_only_exact_allowlisted_requests(self) -> None:
        command = ALLOWED_TERMINAL_COMMANDS[1]
        self.assertEqual(
            parse_terminal_tool_request(
                f'{{"deepbrid_tool":"terminal","command":"{command}"}}'
            ),
            command,
        )
        self.assertIsNone(parse_terminal_tool_request("Here is a regular answer."))
        self.assertIsNone(
            parse_terminal_tool_request(
                '{"deepbrid_tool":"other","command":"git status --short --branch"}'
            )
        )
        with self.assertRaisesRegex(ValueError, "not in your enabled whitelist"):
            parse_terminal_tool_request(
                '{"deepbrid_tool":"terminal","command":"python -c \\"print(1)\\""}'
            )
        with self.assertRaisesRegex(ValueError, "invalid terminal tool request"):
            parse_terminal_tool_request(
                '{"deepbrid_tool":"terminal","command":"ls","extra":"value"}'
            )

    def test_tool_protocol_is_added_only_to_the_api_prompt(self) -> None:
        prompt = add_terminal_tool_instructions("Review this project.")
        self.assertTrue(prompt.startswith("Review this project."))
        self.assertIn("Do not add arguments or shell operators", prompt)
        self.assertIn("python --version", prompt)

    def test_saved_whitelist_is_limited_to_supported_commands(self) -> None:
        self.assertEqual(
            load_terminal_command_whitelist(None),
            ALLOWED_TERMINAL_COMMANDS,
        )
        self.assertEqual(
            load_terminal_command_whitelist('["python --version","ls"]'),
            ("ls", "python --version"),
        )
        with self.assertRaisesRegex(ValueError, "unsupported commands"):
            load_terminal_command_whitelist('["python arbitrary.py"]')

        with self.assertRaisesRegex(ValueError, "enabled whitelist"):
            parse_terminal_tool_request(
                '{"deepbrid_tool":"terminal","command":"git status --short --branch"}',
                ("ls",),
            )

    def test_ls_lists_only_the_open_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "z-file.txt").write_text("ignored", encoding="utf-8")
            (root / "a-directory").mkdir()

            result = run_allowed_terminal_command("ls", root)

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.working_directory, root.resolve())
            self.assertEqual(result.stdout, "a-directory/\nz-file.txt")
            self.assertEqual(result.stderr, "")

    def test_process_commands_use_fixed_arguments_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            completed = SimpleNamespace(returncode=1, stdout="working tree", stderr="warning")
            with patch("src.terminal_tools.subprocess.run", return_value=completed) as run:
                result = run_allowed_terminal_command(
                    "git status --short --branch",
                    root,
                )

            self.assertEqual(
                run.call_args.args[0],
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "status",
                    "--short",
                    "--branch",
                ],
            )
            self.assertEqual(run.call_args.kwargs["cwd"], root.resolve())
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertEqual(result, TerminalCommandResult(
                "git status --short --branch",
                root.resolve(),
                1,
                "working tree",
                "warning",
            ))

    def test_unapproved_command_is_rejected_before_process_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("src.terminal_tools.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "not in your enabled whitelist"):
                    run_allowed_terminal_command("python my_script.py", Path(temporary_directory))
            run.assert_not_called()

    def test_runner_rejects_commands_disabled_in_the_saved_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("src.terminal_tools.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "enabled whitelist"):
                    run_allowed_terminal_command(
                        "git status --short --branch",
                        Path(temporary_directory),
                        ("ls",),
                    )
            run.assert_not_called()

    def test_process_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            completed = SimpleNamespace(
                returncode=0,
                stdout="x" * (MAX_TERMINAL_OUTPUT + 1),
                stderr="",
            )
            with patch("src.terminal_tools.subprocess.run", return_value=completed):
                result = run_allowed_terminal_command(
                    "python --version",
                    Path(temporary_directory),
                )
            self.assertTrue(result.stdout.endswith("... output truncated"))
            self.assertLess(len(result.stdout), MAX_TERMINAL_OUTPUT + 40)


if __name__ == "__main__":
    unittest.main()
