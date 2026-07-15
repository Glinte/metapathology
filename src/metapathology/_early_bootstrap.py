"""Minimal activation path imported by the generated early-site ``.pth``."""

import os

import metapathology

_ACTIVATION_SOURCE = "environment:METAPATHOLOGY_EARLY_BOOTSTRAP"


def activate(bootstrap_path: str) -> None:
    """Install monitoring and attach safe startup provenance."""
    absolute = os.path.abspath(bootstrap_path)
    directory = os.path.dirname(absolute)
    basename = os.path.basename(absolute)
    monitor = metapathology.install()
    try:
        earlier_pth_files = tuple(
            sorted(
                name
                for name in os.listdir(directory)
                if type(name) is str and not name.startswith(".") and name.endswith(".pth") and name < basename
            )
        )
    except Exception as exc:
        earlier_pth_files = ()
        monitor._record_internal_error("early_site_bootstrap_provenance", exc)
    monitor._set_early_site_bootstrap(absolute, directory, _ACTIVATION_SOURCE, earlier_pth_files)
