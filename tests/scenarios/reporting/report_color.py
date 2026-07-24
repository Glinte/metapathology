"""Isolated child scenarios extracted from tests/reporting/test_report_color.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "invalid_install_color_does_not_instrument_import_state":
    import sys
    import metapathology

    before = sys.meta_path
    try:
        metapathology.install(report_color="sometimes")
    except ValueError as exception:
        assert "unknown color mode" in str(exception)
    else:
        raise AssertionError("invalid color accepted")
    assert sys.meta_path is before
    assert metapathology.get_monitor() is not None
    assert not metapathology.get_monitor().enabled

elif _scenario == "explicit_render_color_preserves_plain_content":
    import io, re, sys
    import metapathology

    metapathology.install(report_at_exit=False)

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    finder = Finder()
    sys.meta_path.append(finder)
    sys.meta_path.remove(finder)
    plain = metapathology.render_report()
    colored = metapathology.render_report(color=True)
    assert "\x1b[" not in plain
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in colored
    assert "\x1b[32m+" in colored
    assert "\x1b[1;31m-" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored) == plain

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    stream = Terminal()
    metapathology.write_report(stream)
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in stream.getvalue()
else:
    raise ValueError(f"unknown scenario: {_scenario}")
