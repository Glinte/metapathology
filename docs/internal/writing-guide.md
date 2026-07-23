# Documentation writing guide

## Audience

Write public pages for a Python developer who can run a reproduction but may
not know importlib internals. Introduce import terms only when they help the
reader make a decision.

Internal pages may assume repository architecture and CPython import knowledge.

## Disclose information progressively

1. State the user-visible outcome or decision.
2. Give the smallest working command or example.
3. Explain the evidence needed to interpret it.
4. Link to internals, exhaustive configuration, or limitations.

Do not explain a mechanism merely because it exists. Do not hide a limitation
that could change a user's conclusion.

For every paragraph, ask which user question it answers:

- What does this result mean?
- Why might it explain my failure?
- How strong is the evidence?
- What should I inspect or change next?
- When could this be normal?

Delete prose that only describes the documentation or data model. For example,
readers interpreting a finding do not need to be told that findings have names
and subjects; they need the name decoded.

Finding documentation should normally cover meaning, consequence, next checks,
and harmless cases. Omit a heading only when it would have no useful content.

## Link or repeat

Repeat:

- one-sentence safety limits at the point of action;
- short defaults needed to understand an example; and
- the next decision a reader must make.

Link:

- exhaustive option and environment tables;
- schema field definitions;
- performance methodology;
- CPython behavior already documented authoritatively; and
- contributor architecture.

Prefer authoritative external sources for Python semantics and third-party
tool behavior. Keep concrete incident links when they demonstrate a real
failure pattern. Preserve measured results, including their environment and
limitations, instead of replacing them with unsupported qualitative claims.

Use an internal link for project behavior and an external link for Python or
third-party behavior. Do not maintain two exhaustive versions of the same
fact.

## Voice

Be direct, calm, and specific. Prefer “The check runs at report time” to
“It is important to note that...” Prefer concrete limits to reassurance.

Avoid:

- narrating obvious code;
- unexplained internal class names;
- calling a current-state check a prediction;
- treating every custom finder as a problem; and
- apologetic or promotional filler.

## Change checklist

- Update the nearest discovery surface only.
- Put low-level detail on a focused page.
- Keep names identical across CLI, API, environment, text, and JSON.
- Include defaults and unavailable behavior for new switches.
- Add migration guidance only for released public behavior.
- Verify links and build the strict documentation site.
