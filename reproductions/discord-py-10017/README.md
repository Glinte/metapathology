# discord.py#10017 reproduction

This reproduces [discord.py#10017](https://github.com/Rapptz/discord.py/issues/10017)
with discord.py 2.4.0 on Python 3.12. The extension is imported normally and
then passed to `Bot.load_extension()`. That release executes a second module
object instead of reusing the entry already in `sys.modules`, producing two
distinct `ExtCog` classes with the same representation.

From the repository root on Windows:

```powershell
.\reproductions\discord-py-10017\reproduce.ps1
```

Both runs print `same repr: True` and `same class object: False`. No Discord
token or network connection is needed. The duplicate execution uses low-level
importlib utilities and therefore does not represent a second normal meta-path
claim, but the replacement module retains a valid ordinary-looking spec.
Metapathology currently stays silent about `ext`: this reproduction documents
a blind spot that cannot be fixed by treating every valid-spec module without
an instrumented custom-finder claim as manual, because the deliberately
unwrapped standard `PathFinder` loads normal modules that way too.
