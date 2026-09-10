# AGENTS.md

## Project

Beamercraft is a new Python 3.11+ OSS project: a TeX-first, indentation-based
preprocessor that removes structural LaTeX/Beamer boilerplate without replacing
TeX semantics.

There is no legacy implementation, compatibility target, or previous design to
infer. Do not invent one.

## Sources of truth

Read these files completely before designing or changing the DSL:

1. `beamercraft_tex_first_dsl_v1_spec.md` — normative v1 language specification.
2. `plan.md` — approved implementation architecture, test plan, and the pending
   specification clarifications agreed after the initial spec was written.

If they conflict, do not silently choose a behavior. The approved clarifications
in `plan.md` must first be incorporated into the normative specification; after
that, the specification remains authoritative.

## Non-negotiable invariants

- Ordinary source lines are raw TeX.
- `@foo` is an unregistered generic TeX command.
- `@foo:` is a generic TeX environment unless its suite structurally defines a
  long-form command with `@!arg` and no `@!body`.
- Determine command versus environment only from source structure. Never inspect
  templates, command registries, packages, or known LaTeX names.
- Unknown `@foo` names must render; unknown `@!foo` names must fail.
- `@!foo` is the Beamercraft special namespace.
- Do not parse, validate, normalize, or escape inline/raw TeX.
- `>>` is syntax sugar for a single path of nested containers and must disappear
  during normalization.
- Special directives expand AST to AST. They must not return TeX strings.
- The TeX renderer consumes canonical AST and must not know special directives.
- Preserve file, 1-based line, and 1-based column on major AST nodes and errors.

## v1 boundaries

Use the Python standard library at runtime. Prefer small handwritten line and
header scanners over a parser generator. Keep the parser, normalization/special
expansion, and renderer responsibilities separate, but do not add abstraction
layers or files only for hypothetical future use.

Do not add any of the following in v1 unless the specification is explicitly
revised:

- a TeX parser or template parser
- LaTeX command/environment discovery or mandatory registries
- automatic escaping or argument-count/package validation
- variables, expressions, loops, conditions, or a source macro language
- YAML or Python-embedded authoring DSLs
- implicit extension loading or a general plugin framework
- a renderer backend framework
- runtime dependencies

The internal special-directive registry is allowed. User extension loading is
deferred; its future contract must remain explicit and AST-to-AST.

## Approved syntax clarifications

Before implementation, update the normative spec and its examples to state:

- `@!arg:` always creates a multiline block argument.
- `@!arg{...}` creates one inline required argument and is valid only as a direct
  child in a structured generic invocation.
- Inline and block `@!arg` forms may be mixed in source order.
- `@!body{...}` is not part of v1.
- Normal DSL nesting uses four spaces. The `@!items` mini-grammar separately uses
  a two-space continuation prefix and four-space nested-list levels, as detailed
  in `plan.md`.
- Compact and long forms normalize to the same canonical node kinds while keeping
  source location and argument layout metadata.

## Implementation workflow

Work test-first in the phases listed in `plan.md`. For each phase:

1. Read the affected spec sections and existing tests.
2. Add the smallest failing unit or golden test that fixes the intended behavior.
3. Implement the smallest standard-library solution that passes it.
4. Run the focused test, then the full suite.
5. Check that no syntax-only `Stack`/`SpecialInvocation` nodes reach the renderer.

Use `apply_patch` for manual edits. Preserve unrelated user changes. Do not add a
dependency when the standard library is sufficient.

Once the package exists, the baseline verification command is:

```bash
python -m unittest discover
```

LaTeX integration tests must detect the toolchain and skip when unavailable;
LaTeX is never a Beamercraft runtime dependency.

## Review checklist

Before finishing a DSL change, verify:

- an unknown generic name still works without registration
- command/environment selection still uses structure only
- raw TeX remains opaque
- `@!` and generic namespaces remain distinct
- structured `@!arg`/`@!body` ordering and placement are validated
- `>>` inside argument groups is not treated as stacking
- normalization removes syntax sugar before rendering
- errors point to the originating source location
- unit and exact-output golden tests cover the change
- no speculative plugin, backend, or TeX-semantic machinery was introduced
