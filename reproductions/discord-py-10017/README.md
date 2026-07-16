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
Default monitoring labels this low-level identity transition unobservable
because the second execution emits no ordinary import event. Run
`python -m metapathology --deep-path-hooks --deep-path-entry-finders
--deep-loaders invoke.py` to capture both loader executions;
the report then explains that `SourceFileLoader` executed a separate module
object for `ext` while linking the two observed boundaries.
