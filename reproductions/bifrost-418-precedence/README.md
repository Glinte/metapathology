# Bifrost#418 precedence follow-up

[Bifrost PR #419](https://github.com/gobifrost/bifrost/pull/419) fixed the two
virtual-import cases reported in issue #418, but installs its finder with
`sys.meta_path.insert(0, finder)`. This reproduction shows the distinction
between preceding filesystem resolution and preceding every standard finder.

The workspace index is represented by a finder that claims CPython's frozen
`__hello__` module. Bifrost's current front insertion lets the virtual source
win. The control inserts the same finder immediately before `PathFinder`, so
`FrozenImporter` retains precedence while workspace modules still precede
filesystem modules.

Run from the repository root:

```powershell
.\reproductions\bifrost-418-precedence\reproduce.ps1
```

Expected output includes:

```text
expected standard origin: frozen
observed origin: workspace/__hello__.py
observed implementation: workspace
```

The control prints `observed origin: frozen` and exits successfully.
