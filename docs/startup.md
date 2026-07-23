# Start monitoring at the right time

Monitoring can only explain activity that happens after installation.

## Prefer the wrapper

```console
python -m metapathology app.py
```

This installs the monitor before target code runs and is the right choice for
most reproductions.

## Finders installed before monitoring

Python may process `.pth` files while starting the interpreter. Editable
installs commonly use them to add a finder. By the time the CLI module starts,
that finder already exists.

Metapathology includes it in the initial `sys.meta_path` snapshot and can
attribute later calls to it. It cannot recover where or when the earlier
installation happened.

If the installation stack matters, install the optional startup file with the
same interpreter or virtual environment as the target:

```console
python -m metapathology.site_bootstrap install
```

The command creates an owned `00_metapathology_early.pth` file in
site-packages. It remains inactive unless you set:

```console
METAPATHOLOGY_EARLY_BOOTSTRAP=1 \
METAPATHOLOGY_REPORT=diagnosis.json \
python app.py
```

In PowerShell:

```powershell
$env:METAPATHOLOGY_EARLY_BOOTSTRAP = "1"
$env:METAPATHOLOGY_REPORT = "diagnosis.json"
python app.py
```

Inspect or remove the file with:

```console
python -m metapathology.site_bootstrap status
python -m metapathology.site_bootstrap remove
```

All three commands accept `--site-packages DIR` and are idempotent. The manager
refuses to change a file it does not own. Installing the package normally does
not create this startup file.

Python processes `.pth` files in filename and site-directory order; see the
[`site` module documentation](https://docs.python.org/3/library/site.html).
The `00_...` file can observe later files in the same directory, but not files
processed earlier, other site directories already processed, or startup with
`-S`. The report lists earlier `.pth` files it knows about. Python 3.15
deprecates executable `.pth` lines, so the manager rejects 3.15 and newer.

Early startup records much more interpreter activity than the normal wrapper.
Use it only when the initial snapshot is not enough.

## Library installation

For notebooks, embedded interpreters, or test configuration:

```python
import metapathology

metapathology.install()
```

Call it before importing the packages involved in the reproduction.

## Frozen applications

Frozen applications have tool-specific startup sequences. See
[Frozen applications](frozen.md) for activation and packaging requirements.
