# Attach to a running process

Use attachment when restarting a long-lived process under the wrapper would
hide the import problem or cost too much:

```console
python -m metapathology attach 12345 --report diagnosis.json
```

This starts monitoring inside process `12345` and returns after that process
acknowledges installation. It does not report imports that finished before the
acknowledged installation time. The initial snapshot still describes the
import state found at attachment.

Attachment requires CPython 3.14 or newer in both processes. Run the command
with the same CPython major and minor version, build configuration, and
operating-system user as the target. Prerelease versions must match exactly.
The target does not need metapathology installed: the controller verifies and
stages its own package for the session.

## Stop and collect the report

When the relevant operation is complete, run:

```console
python -m metapathology stop 12345
```

The stop command captures one immutable artifact, renders every requested
format from it, delivers the files, and uninstalls reversible instrumentation.
Reports include the attachment session and the target-observed installation
time. The program outcome is unknown because attachment does not wrap the
target's entry point.

If the target exits normally before `stop`, an exit handler makes a best-effort
attempt to preserve the report. This cannot recover from `os._exit()`, a fatal
interpreter error, forced process termination, or a signal that bypasses normal
Python shutdown. The handler was also registered after the application's
existing exit handlers, so it runs before them and cannot observe imports they
perform.

## Handle a pending command

Remote execution is asynchronous. The controller records `start_pending` or
`stop_pending` before scheduling target work and waits for an acknowledgement.
An interrupted or timed-out command exits with status 3 and retains its session
state.

Repeat the same command to reconcile the pending request. A repeated `stop`
waits for the existing request; it does not schedule another stop:

```console
python -m metapathology stop 12345
```

Use `stop PID --discard` only for terminal state or after the controller proves
that the original process no longer owns the PID. Discarding removes the local
session and any undelivered reports.

## Choose capture deliberately

The normal capture and check options accepted by the wrapper also work with
`attach`. Detailed capture wraps more import machinery and has the same
identity and performance costs described in [Choosing capture](capture.md).

Unsafe exploration must be enabled explicitly:

```console
python -m metapathology attach 12345 --unsafe-explore-import-branches
```

It invokes skipped third-party code inside the live target. Use it only when
the target is disposable or those side effects are acceptable.

## Security and local state

Attachment uses CPython's debugger interface, which grants code-execution
authority in the target. The session token correlates requests and detects
stale or mismatched state; it is not a security boundary.

The controller accepts same-user targets only. It stores private session state
and verified package archives under the current user's temporary directory.
Package archives remain available until no retained session references them,
because later imports, module specifications, and tracebacks can still depend
on the archive.

Output filenames receive the target PID before their extension, matching
automatic wrapper reports. For example, `--report diagnosis.json` writes
`diagnosis.12345.json`.
