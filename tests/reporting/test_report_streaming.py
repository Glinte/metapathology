"""Streaming text report file output: equivalence, fallback safety, JSON parity."""

from support import PythonRunner


def test_streamed_text_file_matches_buffered_render(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_streaming.py", "equivalence")


def test_mid_render_failure_falls_back_without_partial_content(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_streaming.py", "fallback")


def test_json_file_output_unaffected_by_streaming(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_streaming.py", "json_parity")
