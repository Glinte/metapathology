"""Streaming text report file output: equivalence, fallback safety, JSON parity."""

import subprocess
from collections.abc import Callable

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


# Import a spread of stdlib modules so the report has a non-trivial timeline
# alongside the standard-finder attribution and snapshot sections.
_ACTIVITY = """
import json, base64, decimal, fractions, textwrap, difflib, calendar  # noqa: F401
"""


EQUIVALENCE = f"""
import metapathology

monitor = metapathology.install(report_at_exit=False)
{_ACTIVITY}

metapathology.write_report("streamed.txt")
buffered = metapathology.render_report(format="text")

with open("streamed.txt", encoding="utf-8") as handle:
    streamed = handle.read()

assert streamed == buffered, "streamed file must match the buffered render byte-for-byte"
assert streamed.endswith("\\n"), streamed[-40:]
assert "-- event timeline" in streamed, "expected a timeline section in this scenario"
print("OK")
"""


FALLBACK = """
import glob
import metapathology
from metapathology import _report

def boom(document, *, color=False):
    # Emit partial content, then fail mid-render like a real renderer bug.
    yield "PARTIAL-SENTINEL-1"
    yield "PARTIAL-SENTINEL-2"
    raise RuntimeError("boom-during-render")

_report.iter_report_lines = boom

monitor = metapathology.install(report_at_exit=False)
import json  # noqa: F401  (a little activity to capture)

metapathology.write_report("out.txt")

with open("out.txt", encoding="utf-8") as handle:
    content = handle.read()

assert "report generation failed:" in content, content
assert "RuntimeError" in content, content
assert "PARTIAL-SENTINEL" not in content, "partial content must never reach the file"
leftover = glob.glob(".out.txt.*") + glob.glob("*.tmp")
assert not leftover, leftover
print("OK")
"""


JSON_PARITY = """
import glob
import json
import metapathology

monitor = metapathology.install(report_at_exit=False)
import base64  # noqa: F401

metapathology.write_report("out.json", format="json")

with open("out.json", encoding="utf-8") as handle:
    written = handle.read()

# JSON is never streamed line-by-line; it must remain a single valid document
# written atomically, with no leftover temp file.
document = json.loads(written)
assert "findings" in document, document.keys()
assert written.endswith("\\n"), written[-40:]
assert not (glob.glob(".out.json.*") + glob.glob("*.tmp")), "no leftover temp file"
print("OK")
"""


def test_streamed_text_file_matches_buffered_render(run_python: RunPython) -> None:
    proc = run_python(EQUIVALENCE)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_mid_render_failure_falls_back_without_partial_content(run_python: RunPython) -> None:
    proc = run_python(FALLBACK)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_json_file_output_unaffected_by_streaming(run_python: RunPython) -> None:
    proc = run_python(JSON_PARITY)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
