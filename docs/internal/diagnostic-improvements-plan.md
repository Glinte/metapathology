# Diagnostic improvements plan

This plan turns lessons from the mosquito-cfd, rules_python, and Bifrost
investigations into concrete metapathology work. It is deliberately staged:
each stage must preserve import outcomes, remain reversible, and keep captured
evidence distinct from report-time inference.

## 1. Observe `sys.path` changes

Status: implemented.

Add an independently toggleable, opt-in instrumented-list observer for
`sys.path`. Record every supported list mutation with its caller stack and a
copied string-only snapshot. Detect direct reassignment at the next import
audit boundary. Restore a plain list on uninstall.

This mechanism is opt-in because replacing `sys.path` is a broader
compatibility surface than the existing default `sys.meta_path` and
`sys.path_hooks` observers. Event retention is exhaustive and therefore grows
with the number of observed mutations, matching the existing list monitors.

## 2. Explain namespace selection

Status: implemented.

Use captured deep path-entry calls to synthesize a concise explanation when
an earlier path entry contributes a namespace portion but a later path entry
provides the regular package or module that wins. The explanation must name
both paths and the selected origin without implying that namespace portions
are final claims by themselves. When an exact descendant import subsequently
fails, promote the explanation to an actionable correlated finding.

## 3. Explain repeated loader execution

Status: implemented.

Promote two captured `exec_module` calls for the same module name and loader,
when they execute different module objects, into an explicit repeated-load
finding. Link both event sequence numbers and distinguish this from an
ordinary `importlib.reload`, which normally reuses the module object.

Also correlate a captured earlier successful attempt with a later failed
attempt when PathFinder selected the same loader type and normalized origin.
This broader `repeated-load-failure` finding covers loader boundaries that
cannot safely be wrapped, including native extension loaders.

## 4. Reduce weak temporal warnings

Status: implemented with a stricter exact-attempt boundary. Path-specific
correlation can be added later if a real case needs warnings for mutations
that precede an attempt.

Stop emitting `failed-after-mutation` for every later failure in the process.
Require a mutation to overlap the failed import's relevant resolution state:
meta-path changes are relevant globally; path-hook and importer-cache changes
must affect a path searched by that attempt. If exact relevance cannot be
established, keep the timeline evidence but omit the warning.

## 5. Make child-process support discoverable

Status: implemented by cross-linking the existing bootstrap from CLI help and
the public usage material.

The environment-gated early-site bootstrap already propagates to child
processes and writes PID-safe reports. Cross-link it from the CLI help and the
nearest limitations/report surfaces instead of adding a second bootstrap
mechanism.

## Delivery and verification

Each behavioral stage gets subprocess tests before implementation. Public
text and JSON reports remain projections of the same report document; schema
changes update the typed schema, generated JSON Schema, and documentation in
the same change. Run formatting, linting, both configured type checkers, the
full test suite, and package build checks before completion.
