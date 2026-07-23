# Frozen and embedded applications

Do not run a frozen executable as the target of an outer
`python -m metapathology` process. The executable owns a different embedded
interpreter and different import state. Generate a startup file and include it
in the executable instead.

Configure reporting through environment variables, for example:

```text
METAPATHOLOGY_REPORT=diagnosis.json
METAPATHOLOGY_CAPTURE_LOADER_CALLS=1
```

Environment report names are automatically made process-safe, so child
processes do not overwrite the parent's report.

## PyInstaller

Generate a runtime hook:

```console
python -m metapathology.frozen_bootstrap generate pyinstaller metapathology-rthook.py
pyinstaller --runtime-hook metapathology-rthook.py app.py
```

PyInstaller runs explicit
[runtime hooks](https://pyinstaller.org/en/latest/hooks.html#changing-runtime-behavior)
after it establishes the frozen importer and before the application entry
script. The frozen importer therefore appears in the initial snapshot;
metapathology cannot show the code that installed it.

## Nuitka

Generate a launcher for an importable application module and compile the
launcher:

```console
python -m metapathology.frozen_bootstrap generate nuitka metapathology-launcher.py --module app
python -m nuitka --mode=standalone --include-module=app metapathology-launcher.py
```

Use `--mode=onefile` for a one-file reproduction. See Nuitka's
[standalone and one-file documentation](https://nuitka.net/user-documentation/user-manual.html).

The application module must expose a no-argument `main()` function. Use
`--callable NAME` for another name. The generated launcher uses a static import
because Nuitka's compiled-module loader does not supply the `get_code()`
operation needed by `runpy`.

If the application depends on source-file `__main__` behavior or unusual
multiprocessing startup, add an application-owned activation call instead.

## cx_Freeze

Generate an initialization script:

```console
python -m metapathology.frozen_bootstrap generate cx-freeze metapathology-init.py
cxfreeze --script app.py --init-script metapathology-init.py --includes metapathology
```

In a setup script, pass the generated path as
`Executable(script="app.py", init_script="metapathology-init.py")` and include
`metapathology` in the `build_exe` `includes` option. The generated file
implements cx_Freeze's initialization-script `run(name)` protocol.

## Embedded or application-owned CPython

Generate a plain startup file:

```console
python -m metapathology.frozen_bootstrap generate embedded metapathology-bootstrap.py
```

Import or execute it after the host has initialized CPython and installed the
import machinery it needs, but before importing application code.

An editable entry point can call the API directly:

```python
import metapathology

metapathology.activate_frozen("embedded", __file__)
```

`activate_frozen()` requires both the integration name and the startup-file
path; it is not a zero-argument replacement for `install()`.

## Generated files and failures

Generated files are deterministic Python source and are safe to inspect or
check in. Generation refuses to replace an existing file unless `--force` is
passed.

Activation is best effort. An ordinary activation error produces a short
message on standard error and lets application startup continue. Report writes
remain atomic.

The report records the integration, startup file, and that monitoring began
after freezer initialization. It does not claim to have observed finders or
path hooks installed earlier by the bootloader.
