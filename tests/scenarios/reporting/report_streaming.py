"""Isolated child scenarios extracted from tests/reporting/test_report_streaming.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "equivalence":
    import metapathology

    metapathology.install(report_at_exit=False)

    import base64  # noqa: F401
    import calendar  # noqa: F401
    import decimal  # noqa: F401
    import difflib  # noqa: F401
    import fractions  # noqa: F401
    import json  # noqa: F401
    import textwrap  # noqa: F401

    metapathology.write_report("streamed.txt")
    buffered = metapathology.render_report()

    with open("streamed.txt", encoding="utf-8") as handle:
        streamed = handle.read()

    assert streamed == buffered, "streamed file must match the buffered render byte-for-byte"
    assert streamed.endswith("\n"), streamed[-40:]
    assert "-- event timeline" in streamed, "expected a timeline section in this scenario"

elif _scenario == "fallback":
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

elif _scenario == "json_parity":
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
    assert written.endswith("\n"), written[-40:]
    assert not (glob.glob(".out.json.*") + glob.glob("*.tmp")), "no leftover temp file"
else:
    raise ValueError(f"unknown scenario: {_scenario}")
