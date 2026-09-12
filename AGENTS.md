# AGENTS.md

## Project

TeXFlux is a Python 3.11+ OSS project: a TeX-first, indentation-based
preprocessor that removes structural LaTeX/Beamer boilerplate without
replacing TeX semantics.

There is no legacy compatibility target. The normative language definition is
texflux_tex_first_dsl_v1_spec.md, and doc/dsl.md is its Japanese reference.
Read the affected sections completely before changing the DSL.

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
- !items: is the itemize mini-grammar.
- !vpad{before}{after}: emits \vspace{before} before its suite and an
  optional \vspace{after} after it.
- !flag declares a build flag; !when and !unless keep or drop one payload,
  over one flag or over several folded by an [and]/[or] modifier.

Unknown environment names must render because environment names are not looked
up. Unknown special names must fail. Ordinary/raw TeX must remain opaque:
TeXFlux may scan a leading command line only when a top-level structural
token makes it a structural candidate. Group contents are never scanned for
TeXFlux operators.

Structured command rules are deterministic:

- A ': |' suite is one required long argument, including all of its
  normalized children.
- A ':' suite is one required argument per '-' entry.
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
- variables, expressions, arithmetic, or pattern matching;
- conditions beyond the declared on/off build flags described below: no
  comparisons, no arithmetic, and no value a flag can hold but on and off. A
  conditional folds a flat list of flag names with one [and]/[or]; that fold
  admits no parentheses, no fold inside a fold, and no negation of a single
  operand. Composing whole conditionals with >> is unrestricted;
- textual macros, interpolation into raw TeX, !splice, optional/default/keyword
  macro parameters, or macro recursion;
- !block, !arg, !body, or any other explicit-mode fallback construct: these
  existed once and were removed deliberately, so they must keep failing as
  unknown specials rather than coming back as aliases;
- YAML or Python-embedded authoring DSLs;
- implicit extension loading or a general plugin framework;
- renderer backend frameworks;
- runtime dependencies.

The in-process special registry is allowed. User extension loading is deferred;
its future contract remains AST-to-AST.

Source macros are part of v1. !defmacro is top-level only and stores a syntax
AST template; a call binds values by the existing value syntax; !param and
!each are template-only constructs. Expansion is an AST-to-AST pass between >>
desugaring and value consumption, so no macro construct reaches the renderer.
Template output is retargeted onto the call site, while !param output keeps its
call-site span.

Build flags are part of v1. !flag is top-level only and declares a boolean
with an on/off default that the compile command may override with --flag; an
override that no declaration matches, or that is not a real bool through the
Python API, is an error. !when and !unless take one or more declared flags and
one payload, written as a ': |' suite or as the rest of a >> composition, and
splice or drop that payload whole. A leading [and]/[or] group folds several
flags and is required whenever more than one is named; !unless[X] is the
negation of the whole !when[X] it mirrors, never of each operand.

Flags are collected between >> desugaring and macro collection, and
conditionals are resolved inside the macro expansion pass, so no flag construct
reaches the renderer. A dropped payload is never expanded and never normalized,
so disabling content that no longer compiles has to keep working. Exactly three
rules still reach inside one, because all three are checked before conditionals
resolve: the payload must parse, !flag must be top-level, and the stack-form
rules for !defmacro and !each hold. Do not add a fourth without deciding that
the "disable broken content" guarantee can afford it.

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

Work test-first:

1. Read the affected specification sections and existing tests.
2. Add the smallest failing unit or exact-output golden test.
3. Implement the smallest standard-library solution.
4. Run the focused test, then the full suite.
5. Confirm that no ParsedInvocation, SpecialInvocation, or Stack reaches the
   renderer.

Preserve unrelated user changes. Do not add dependencies when the standard
library is sufficient.

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
- a ': |' command suite produces exactly one long argument;
- no block-scalar marker is accepted after a suite colon;
- the removed !block, !arg and !body still fail as unknown specials;
- !vpad accepts one or two required inline groups and preserves suite order;
- >> is normalized before rendering and has no semantic terminal rule;
- !items preserves overlay, label, multiline, and nested-list behavior;
- !flag is rejected below the top level and as a >> segment, and a conditional
  naming an undeclared flag is an error rather than a silent removal;
- several flags without an [and]/[or] modifier are an error, not an implicit
  conjunction, and only the leading group is read as the modifier;
- a dropped conditional payload is neither expanded nor normalized;
- source spans survive desugaring and special expansion;
- exact-output golden tests cover the representative command/environment/special
  example, and any new golden covers a shape no existing case already covers --
  goldens are regenerated wholesale, so a redundant one is invisible for ever;
- no speculative plugin, backend, or TeX-semantic machinery was introduced.
