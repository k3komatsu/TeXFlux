# AGENTS.md

## Project

TeXFlux is a Python 3.11+ OSS project: a TeX-first, indentation-based
preprocessor that removes structural LaTeX/Beamer boilerplate without
replacing TeX semantics.

There is no legacy compatibility target. The normative language definition is
texflux_tex_first_dsl_v1_spec.md; the TeX-first DSL implementation plan is
plan.md, and the approved source-map/SyncTeX expansion plan is
texflux_source_map_synctex_plan.md. Read the relevant plans completely before
changing the DSL.

## Source-of-truth rules

The current v1 mental model is fixed:

~~~text
\ = TeX command
@ = TeX environment
! = TeXFlux special construct
~~~

Do not reintroduce prefix-based command/environment inference. Do not inspect
templates, package metadata, command registries, or environment registries to
decide the shape of an invocation.

The approved syntax is:

- Ordinary TeX commands such as \vspace{...} are raw TeX.
- \foo: and \foo >> ...: are structural command candidates.
- @foo: is an environment. An @foo header without a suite is an error.
- !foo is a TeXFlux special construct.
- A top-level trailing colon is reserved by TeXFlux.
- >> is pure single-child-suite desugaring.
- !block: always emits one actual TeX brace group.
- !items: is the itemize mini-grammar.
- !vpad{before}{after}: emits \vspace{before} before its suite and an
  optional \vspace{after} after it.
- !arg and !body are explicit fallback forms only.

Unknown environment names must render because environment names are not looked
up. Unknown special names must fail. Ordinary/raw TeX must remain opaque:
TeXFlux may scan a leading command line only when a top-level structural
token makes it a structural candidate. Group contents are never scanned for
TeXFlux operators.

Structured command rules are deterministic:

- In implicit mode, the complete indented suite is one required long
  argument, including all of its normalized children.
- !block always remains its own brace group; an implicit command suite wraps it
  in the command argument, so the braces are intentionally nested.
- A direct !arg or !body selects explicit mode; implicit and explicit
  children may not be mixed.
- Command explicit mode accepts only direct !arg children.
- Environment explicit mode accepts only direct !arg and !body children;
  !body is optional, unique, and last.
- A suite marker accepts no trailing block-scalar marker.

Every major syntax/canonical node and diagnostic keeps file, 1-based line, and
1-based column. Normalization must remove syntax-only nodes before rendering.
Special handlers are AST-to-AST transformations; they must not return TeX
strings. The renderer consumes canonical AST and knows nothing about special
directives or >>.

## v1 boundaries

Use the Python standard library at runtime. Keep the handwritten physical-line
parser, header scanner, normalization/special expansion, and renderer
separate, but do not add abstraction layers for hypothetical future use.

Do not add in v1:

- a TeX or template parser;
- command/environment discovery or mandatory registries;
- automatic escaping, normalization, or TeX argument-count validation;
- variables, expressions, loops, conditions, or a source macro language;
- YAML or Python-embedded authoring DSLs;
- implicit extension loading or a general plugin framework;
- renderer backend frameworks;
- runtime dependencies.

The in-process special registry is allowed. User extension loading is deferred;
its future contract remains AST-to-AST.

## Agent and review budget

Use Claude/Opus or another external reviewer only when the user requests it.
For a change task that includes an Opus review, use this completion loop:
Opus review -> implement the actionable fixes -> run the focused and full
verification -> Opus re-review. Repeat the loop until the review reports no
remaining actionable findings (or explicitly records any accepted residual
risk), then run the final verification and commit the completed changes. Do
not commit before this loop is complete. A review-only request remains
read-only unless the user separately asks for changes.
After such a review has started, silence, elapsed time, an empty poll result,
or the absence of intermediate output is not evidence that it stopped. Never
cancel, interrupt, or kill the running review based on those observations;
only an explicit user stop request permits interruption. Preserve and poll the
existing process/session until the review tool explicitly reports completion,
failure, or a need for attention. Do not start a duplicate review or retry
while the original may still be running. If the tool explicitly reports a
failure, report that result without guessing whether the underlying process
has stopped.

## Implementation workflow

Work test-first in the phases in plan.md and, for source-map/SyncTeX work,
texflux_source_map_synctex_plan.md:

1. Read the affected specification sections and existing tests.
2. Add the smallest failing unit or exact-output golden test.
3. Implement the smallest standard-library solution.
4. Run the focused test, then the full suite.
5. Confirm that no ParsedInvocation, SpecialInvocation, or Stack reaches the
   renderer.

Use apply_patch for manual edits. Preserve unrelated user changes. Do not add
dependencies when the standard library is sufficient.

Baseline verification:

~~~bash
python -m unittest discover
~~~

LaTeX integration tests must detect the toolchain and skip when unavailable;
LaTeX is never a TeXFlux runtime dependency.

## Review checklist

Before finishing a DSL change, verify:

- \foo, @foo, and !foo are classified solely by their prefix;
- an ordinary \command remains raw unless it has a top-level structural form;
- an unknown environment still renders without registration;
- an unknown special fails;
- top-level colon and >> are deterministic;
- group-internal colon and >> remain opaque;
- starred environment names work;
- implicit command suites produce exactly one long argument;
- command and environment explicit modes reject mixing;
- no block-scalar marker is accepted after a suite colon;
- !block always has exactly its own brace group;
- !vpad accepts one or two required inline groups and preserves suite order;
- !arg around !block retains the intentional double brace;
- >> is normalized before rendering and has no semantic terminal rule;
- !items preserves overlay, label, multiline, and nested-list behavior;
- source locations survive desugaring and special expansion;
- exact-output golden tests cover the representative command/environment/special
  example;
- no speculative plugin, backend, or TeX-semantic machinery was introduced.
