"""Public attachment command discovery and dispatch."""

from pathlib import Path

from runtime.cli_support import run_cli


def test_attachment_commands_are_discoverable(tmp_path: Path) -> None:
    attach_help = run_cli("attach", "--help", cwd=tmp_path)
    stop_help = run_cli("stop", "--help", cwd=tmp_path)

    assert attach_help.returncode == 0
    assert "Start import monitoring in a running CPython 3.14+ process." in attach_help.stdout
    assert "--unsafe-explore-import-branches" in attach_help.stdout
    assert stop_help.returncode == 0
    assert "Stop, report, and uninstall a remote metapathology session." in stop_help.stdout
    assert "--discard" in stop_help.stdout


def test_attach_dispatch_requires_the_first_unescaped_token(tmp_path: Path) -> None:
    target = tmp_path / "attach"
    target.write_text("print('wrapper target ran')\n", encoding="utf-8")

    result = run_cli("--", target.name, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "wrapper target ran" in result.stdout
    assert "== metapathology report ==" in result.stderr


def test_attachment_pid_and_timeout_are_validated_by_the_cli(tmp_path: Path) -> None:
    invalid_pid = run_cli("attach", "0", cwd=tmp_path)
    invalid_timeout = run_cli("stop", "--wait-timeout", "-1", "123", cwd=tmp_path)

    assert invalid_pid.returncode == 2
    assert "PID must be a positive integer" in invalid_pid.stderr
    assert invalid_timeout.returncode == 2
    assert "timeout must be a finite positive number" in invalid_timeout.stderr
