# Frozen and embedded applications

`python -m metapathology frozen-app.exe` cannot diagnose a frozen executable:
it starts metapathology in an outer Python process, while the executable owns a
different embedded interpreter and different import machinery. Generate a
startup file and bundle it into the executable instead.

All integrations activate the same stdlib-only runtime code and use the normal
configuration variables. A useful starting point is:

```text
METAPATHOLOGY_REPORT=diagnostic.txt
METAPATHOLOGY_DEEP=1
```

Prefer only the relevant `METAPATHOLOGY_DEEP_*` variables when the default
evidence is sufficient. Automatic filenames include the process ID, so child
processes do not overwrite the parent report.

## PyInstaller

Generate an ordered PyInstaller runtime hook:

```console
python -m metapathology.frozen_bootstrap generate pyinstaller metapathology-rthook.py
pyinstaller --runtime-hook metapathology-rthook.py app.py
```

The generated hook imports metapathology, so PyInstaller analyzes and collects
the package. Explicit runtime hooks execute after PyInstaller establishes its
frozen importer and before the application entry script. The frozen importer
therefore appears in the initial snapshot; its installation cannot be
attributed.

## Nuitka

Nuitka does not provide the same documented universal runtime-hook interface.
Generate a launcher for an importable application module and compile that
launcher as the entry point:

```console
python -m metapathology.frozen_bootstrap generate nuitka metapathology-launcher.py --module app
python -m nuitka --mode=standalone --include-module=app metapathology-launcher.py
```

Use `--mode=onefile` instead when diagnosing one-file startup. The launcher
activates monitoring and then imports and calls `main()` from the named module.
Use `--callable NAME` when the entry callable has another name. This static
import is intentional: Nuitka's compiled-module loader does not provide the
`get_code()` operation required by `runpy`. The application must therefore
expose an importable, no-argument entry callable. Code that relies on a
source-file `__main__` identity or unusual multiprocessing re-entry should use
an application-owned activation call instead.

## cx_Freeze

Generate a cx_Freeze initialization script:

```console
python -m metapathology.frozen_bootstrap generate cx-freeze metapathology-init.py
cxfreeze --script app.py --init-script metapathology-init.py --includes metapathology
```

For a setup script, pass the generated path as
`Executable(script="app.py", init_script="metapathology-init.py")` and include
`metapathology` in the `build_exe` `includes` option. The generated file
implements cx_Freeze's initialization-script `run(name)` protocol after
activation; it is not merely a top-level import fragment.

## Embedded or application-owned CPython

Generate the plain activation file:

```console
python -m metapathology.frozen_bootstrap generate embedded metapathology-bootstrap.py
```

Import or execute it immediately after initializing CPython and establishing
the host's required import machinery, but before importing application code.
An application that can edit its entry point may equivalently call:

```python
import metapathology

metapathology.activate_frozen("embedded", __file__)
```

## Generation and failure behavior

Generated files are deterministic Python source and are safe to inspect or
check in. Generation refuses to replace an existing path; pass `--force` only
after deciding that the existing regular file may be overwritten.

Activation is best effort. Ordinary activation errors are reduced to a short
message on standard error and application startup continues. Automatic report
writes are atomic. An invalid or unwritable destination is recorded internally
when possible and suppressed by the exit-report boundary.

The report records the named integration, generated file, and `after freezer
initialization` observation boundary. It does not claim to have witnessed
finders or path hooks installed by the bootloader before that boundary.
