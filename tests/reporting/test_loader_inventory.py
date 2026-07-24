"""Post-hoc loader inventory coverage."""

import zipfile
from pathlib import Path

from support import PythonRunner


def test_loader_inventory_groups_safe_module_metadata(python_runner: PythonRunner, tmp_path: Path) -> None:
    lazy_source = tmp_path / "lazy_target.py"
    lazy_source.write_text(
        "from pathlib import Path\nPath(__file__).with_suffix('.executed').write_text('ran')\n",
        encoding="utf-8",
    )
    archive = tmp_path / "inventory.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("inventory_zip.py", "VALUE = 1\n")
    python_runner.run_scenario_ok("reporting/loader_inventory.py", "inventory", str(lazy_source), str(archive))
