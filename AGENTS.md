# AGENTS.md

## Project

TeXFlux is a D/LDC 1.43+ OSS project: a TeX-first, indentation-based
preprocessor that removes structural LaTeX/Beamer boilerplate without
replacing TeX semantics. It has no runtime dependencies and no legacy
compatibility target.

The normative language definition is texflux_tex_first_dsl_v1_spec.md, and
doc/dsl.md is its Japanese reference. Read the affected sections completely
before changing the DSL. Design rationale lives in doc/module-system.md,
doc/string-interpolation.md, doc/raw-mode.md, doc/diagnostics.md and
doc/external-ast.md; handoff.md records deferred items and known residuals.

Japanese documentation may be polished with the ja-doc-polish skill once its
facts are settled. Gemini owns the prose there; every fact stays Claude's
responsibility, and the skill's checks exist because Gemini silently invents
specifics and drops qualifiers such as ちょうど and のみ.

## Repository workflow and release guardrails

The following GitHub repository settings are operational constraints for this
project. They were configured in the GitHub UI on 2026-09-19.

- The active `main protection` ruleset targets the default `main` branch.
  Pull requests are required before merging, no approving review is required,
  and these exact checks are required: `Linux minimum`, `Platform smoke
  (macOS arm64)`, `Platform smoke (Windows x86_64)`, and `Linux latest LDC`.
  Deleting `main` and force-pushing it are blocked.
- The active `version tag protection` ruleset targets `v*`. Creating a new
  version tag is allowed, but updating, deleting, or force-updating a matching
  tag is blocked. Never reuse a published version tag.
- GitHub Release immutability is enabled. Treat published release tags and
  assets as permanent; publish a new version when an artifact must change.
- Actions use the selected-action policy: GitHub-created actions are allowed,
  `dlang-community/setup-dlang@*` is allowlisted, Marketplace verified
  creators are not broadly enabled, and every action must be referenced by a
  full-length commit SHA. The repository default `GITHUB_TOKEN` permission is
  read-only, and Actions cannot create or approve pull requests.

The normal change and release flow is:

When the user asks Codex to handle a repository change end to end, Codex may own
the pull-request lifecycle: create the work branch, commit and push it, open the
pull request, monitor the four required checks, diagnose and fix failures with
follow-up commits, and merge after all required checks pass. Stop and ask the
user if there is a merge conflict, an approval or permission request, or another
condition that cannot be resolved safely. Creating version tags and publishing
releases remains a separate action that requires an explicit request.

1. Update `texfluxVersion` in `source/texflux/package.d` and keep
   `texflux --version`, the tag, and release artifacts identical.
2. Work on a branch, run the required local D checks, push the branch, and
   open a pull request.
3. Merge only after all four required checks pass. Direct pushes to `main`
   are not the normal path and are rejected by the ruleset.
4. After the version change is on `main`, create and push a new `vX.Y.Z` tag
   without force-update or deletion.
5. Let `release.yml` validate the tag, run the release test, build the five
   native archives, generate `SHA256SUMS`, attest the artifacts, and publish
   the GitHub Release. Verify the assets, checksum, version output, and
   attestation after publication.
6. Synchronize the separate Homebrew tap after the TeXFlux Release is
   verified. The tap is `k3komatsu/homebrew-tap`; its source Formula must
   point to the new `vX.Y.Z` source archive and its SHA-256 must be updated.
   The tap's scheduled `autobump.yml` should create the Formula PR, and its
   `tests.yml` must pass before publication.

For a Formula change that needs a Bottle, do not merge it with naive GitHub
Auto-merge before publishing the Bottle. Review the PR and its exact head SHA,
then run the tap's generated `publish.yml`/`brew pr-pull` flow with that SHA.
Confirm that the Bottle and Formula update are published before checking
`brew install k3komatsu/tap/texflux` or `brew upgrade` and `texflux --version`.
The Homebrew source Formula and Bottle are separate from TeXFlux's GitHub
Release archives. Do not add a cross-repository PAT or release credential for
this normal flow. Keep the Formula test deriving the expected version from the
Formula version rather than hard-coding an old literal such as `0.3.0`.

If a workflow starts using a new third-party Action, first add its repository
to the GitHub Actions allowlist and reference a full commit SHA. Do not add
secrets, PATs, or cross-repository release credentials for the normal flow.

## Source-of-truth rules

The v1 mental model is fixed:

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
- The suite markers are `::` (block) and `:::` (sequence). A lone `:` is not
  a structural token anywhere, so a `\` line ending in one is raw TeX whether
  or not its header scans, and its meaning never depends on the next line.
- \foo >> ..., \foo:: and \foo::: are structural command candidates, and each
  claims its line unconditionally. A command's `::` requires an indented body
  and its `:::` requires an entry, so `\texttt{std}::` cannot become `{}`.
- @foo:: is an environment. An @foo header without a suite is an error. A
  container and a special may both take an empty suite; a command may not.
- !foo is a TeXFlux special construct.
- At the start of a block line or sequence payload, @@ and !! strip exactly
  one prefix character and produce RawTex without header scanning. In blocks,
  this precedes the structural extra-indentation check and preserves spaces
  beyond the base; it does not bypass the tab prohibition.
- At the same two positions, `!| ` strips the whole marker and one space and
  emits the rest of the line as one verbatim RawTex. It stays inside the
  prefix-only rule, so it adds no hard-coded name. It is the only escape that
  reaches a `\` line a top-level structural token would claim, which is why it
  exists. Unlike `@@` / `!!` it is raw, not an escape: it is never
  interpolated, and its body may contain tabs. The tab exemption is decided by
  a whole-file line predicate, so it covers only a line whose first non-space
  characters are the marker -- not a `- !| ` payload, and not a `!|` line
  inside an opaque `+` group body.
- `!BEGIN_RAW_MODE` and `!END_RAW_MODE` are an intentional exception to the
  prefix-only rule: the physical-line parser recognizes these two complete
  names as raw-region markers. Raw mode adds no canonical AST node and no
  raw-mode-specific knowledge to normalize or the renderer.
- A top-level trailing `::`, `:::` or `>>` is reserved by TeXFlux
  unconditionally. A lone colon reserves nothing, except that a `>>` behind
  one still reserves the line (spec section 3). Every TeXFlux operator that
  can appear at the end or the middle of a line is a repeated character, so
  no one-character operator is shared with TeX prose.
- >> is pure single-child-suite desugaring.
- !before, !after, !around, !off and !drop are the standard flow controls.
  They are ordinary source macros defined in the bundled source/texflux/prelude.tfxm,
  not compiler specials, and every .tfx and .tfxm sees them without an import.
  Their names cannot be redefined. There is no spacing special: spacing is
  the author's own TeX behind !before or !around.
- !flag declares a build flag; !when and !unless keep or drop one payload,
  over one flag or over several folded by an [and]/[or] modifier.
- !macroimport loads a .tfxm macro module; !import compiles a .tfx content
  module and splices its canonical AST.
- A '!' segment, and only a '!' segment, may end with one (...) binding list.
  '(' opens no group anywhere else.

Unknown environment names must render because environment names are not looked
up. Unknown special names must fail. Ordinary/raw TeX must remain opaque:
TeXFlux may scan a leading command line only when a top-level structural
token makes it a structural candidate. Group contents are never scanned for
TeXFlux operators.

Structured command rules are deterministic:

- A '::' suite is one required long argument, including all of its
  normalized children.
- A ':::' suite is one required argument per '-' entry and one authored group
  per '+' entry.
- A suite marker accepts no trailing block-scalar marker.

Every diagnostic carries a code and its related locations. A code is one
uppercase letter for the kind -- P parse, V validation, D directive, E macro
expansion, M module -- and three digits, and it belongs to one construction
site rather than to a wording, so two sites sharing a message still get two
codes. From v1 on codes are never renumbered or reused (the numbering was
compacted once before v1): a new diagnostic takes the next free number for
its letter and is added to the table in doc/diagnostics.md, which
tests/d/texflux_tests/diagnostic_codes.d checks against the source and against every
code the other documents cite. Two helpers and two re-wrap sites forward a
code rather than owning one; nothing else may. The report format reserves
severity "warning", but no site produces one and there is no W letter.
Wherever a message names a second location in prose,
that location is also carried as a RelatedLocation, so an editor can jump to
it. diagnose() never raises a TeXFluxError, and `texflux check` prints the
same line `compile` does.

Every major syntax/canonical node and diagnostic keeps file, 1-based line, and
1-based column. Normalization must remove syntax-only nodes before rendering.
Special handlers are AST-to-AST transformations; they must not return TeX
strings. The renderer consumes canonical AST and knows nothing about special
directives or >>.

## v1 boundaries

Use the D standard library (Phobos) at runtime. Keep the handwritten physical-line
parser, header scanner, normalization/special expansion, and renderer
separate, but do not add abstraction layers for hypothetical future use.
Every entry point that compiles a document -- CLI, compileText,
compileWithMap, compileAst, diagnose -- goes through CompilationSession,
so the standard macros and module resolution behave the same everywhere; the
low-level pipeline.normalize() deliberately sees neither.

Do not add in v1:

- a TeX or template parser;
- command/environment discovery or mandatory registries;
- automatic escaping, normalization, or TeX argument-count validation;
- variables, expressions, arithmetic, or pattern matching;
- conditions beyond the declared on/off build flags described below: no
  comparisons, no arithmetic, and no value a flag can hold but on and off;
- !splice, optional/default/keyword macro parameters, or macro recursion;
- textual macros in the m4/cpp sense: a source-to-source string preprocessor,
  rescanning of interpolated text, token pasting, or any path from generated
  text back to the parser;
- !block, !arg, !body, !items, !vpad, or any other explicit-mode fallback,
  list mini-grammar or spacing special, under any spelling: these names must
  keep failing as unknown specials rather than coming back as aliases
  (spec section 9.2). A list is an ordinary @itemize environment holding raw
  \item lines;
- hard-coded parser recognition of any name other than `!BEGIN_RAW_MODE` and
  `!END_RAW_MODE`. That exception must remain confined to the physical-line
  parser; do not add raw-mode-specific nodes or knowledge to the canonical
  AST, normalize, or renderer;
- a package registry, version resolution, lockfiles, or remote fetching;
- public/private/export syntax, qualified macro names, or re-exports;
- module parameters beyond the declared on/off flags, dynamic import paths, or
  imports generated from macro templates;
- YAML or embedded scripting authoring DSLs;
- implicit extension loading or a general plugin framework;
- renderer backend frameworks;
- runtime dependencies;
- general language machinery added solely to move an operation out of the
  core. !drop is an empty template rather than an !empty primitive for this
  reason.

The in-process special registry is allowed but must stay at its current two
entries: the !import and !macroimport guards. A reusable operation expressible
as a source macro does not get a handler. A built-in special's groups are
compiler metadata and are never interpolated; a new built-in would have to
declare a group as output text explicitly before a marker in it could be
allowed. User extension loading is deferred; its future contract remains
AST-to-AST.

The bundled prelude is one pure .tfxm read as a package resource, loaded once
per CompilationSession and seeded into every module environment. A failure to
read or validate it is an InternalError, not a TeXFluxError: it has no span and
reports a broken install rather than a broken document. Do not inject
a synthetic !macroimport node, do not resolve it against the user's project, and
do not let its path reach spans, source maps or the external AST's sources. Do
not add a standard name without meeting the criteria in section 9.1 of
texflux_tex_first_dsl_v1_spec.md.

Source macros are part of v1. !defmacro is top-level only and stores a syntax
AST template; a call binds values by the existing value syntax; !param and
!each are template-only constructs. Expansion is an AST-to-AST pass between >>
desugaring and value consumption, so no macro construct reaches the renderer.
Template output is retargeted onto the call site, while !param output keeps its
call-site span.

!text{name} interpolates one bound value into a textual field of a template.
It is not a textual macro: the inserted text is never rescanned and never
parsed, and compiler metadata -- flag names, macro names, import paths,
structural names -- stays static.

Build flags are part of v1. !flag is top-level only and declares a boolean
with an on/off default that the compile command may override with --flag; an
override that no declaration matches, or that is not a real bool through the
D API, is an error. !when and !unless take one or more declared flags and
one payload, written as a '::' suite or as the rest of a >> composition, and
splice or drop that payload whole. A leading [and]/[or] group folds several
flags and is required whenever more than one is named; that fold admits no
parentheses, no fold inside a fold, and no negation of a single operand.
!unless[X] is the negation of the whole !when[X] it mirrors, never of each
operand. Composing whole conditionals with >> is unrestricted.

Flags are collected between >> desugaring and macro collection, and
conditionals are resolved inside the macro expansion pass, so no flag construct
reaches the renderer. A dropped payload is never expanded and never normalized,
so disabling content that no longer compiles has to keep working. Exactly four
rules still reach inside one, because all four are checked before conditionals
resolve: the payload must parse, !flag must be top-level, !macroimport must be
top-level and not a >> segment, and the template rules for !defmacro and !each
hold -- which include that a template contains neither !import nor
!macroimport. Every one of them asks only where a line is written and none of
them opens a file. Do not add a fifth without deciding that the "disable broken
content" guarantee can afford it.

Modules are part of v1; doc/module-system.md is the design and spec section 12
is normative. One file is one module. A .tfx is a content module whose flags
and macros never leak through !import; a .tfxm is a pure macro module whose
own !macroimports stay private and non-transitive, so macro definitions have
lexical scope. Keep .tfxm declarative: no flags, no conditionals, no content,
no !import. Module resolution belongs to the compilation session in modules.d
and must stay out of pipeline.normalize() and the renderer.

## The D implementation

The D implementation under source/texflux is the only product implementation. Its observable
behaviour is governed by the v1 specification, the checked-in golden files, and
tests/regression/v1.jsonl. Keep the handwritten physical-line parser, header scanner,
desugaring/normalization, macro expansion, and renderer separate; do not add abstraction
layers for hypothetical future use.

Before finishing a change, run all of these after enabling LDC:

~~~bash
source ~/dlang/ldc-1.43.0/activate
dub test
dub build
dub build -c library
dub build --build=release
dub build -c update-regression
~~~

Rules specific to the D side:

- A diagnostic code is the first literal argument at every construction site. The D audit in
  tests/d/texflux_tests/diagnostic_codes.d checks the 129 codes, their messages, the published
  table, and the per-letter high-water marks. Helpers that forward a caller code are listed
  explicitly in that test.
- Published JSON is written by texflux.json/interchange.d. Do not use std.json for output:
  it sorts members and uses different indentation and escaping. std.json is allowed for input.
- Do not use std.uni.isWhite or std.string.strip where v1 semantics differ; texflux.text
  owns whitespace, UTF-8 decoding, and code-point column rules.
- A table whose walk order affects diagnostics uses texflux.ordered.OrderedMap. The parser and
  header scanner operate on dstring so offsets are source columns.
- @safe stops at the AST boundary where Phobos SumType assignment is @system. Do not add
  unsafe casts merely to make AST passes appear @safe.
- The bundled prelude is the compile-time resource source/texflux/prelude.tfxm. It is loaded
  once per CompilationSession, seeded into each module environment, and never appears in
  spans, source maps, or the external AST.
- The in-process special registry remains limited to the import and macroimport guards.
  Reusable behaviour belongs in source macros, not new compiler specials.
- Canonical rendering accepts only canonical AST. ParsedInvocation, SpecialInvocation,
  SequenceEntry, Stack, binding arguments, and flag/macro/module syntax nodes must be removed
  before render.
- The fixed regression manifest is normally read-only. To accept an intentional output change,
  review the complete diff and then run
  dub run -c update-regression -- --accept-current. Never update it as part of ordinary tests.

Every entry point that compiles a document -- CLI, compileText, compileWithMap, compileAst,
diagnose -- goes through CompilationSession, so standard macros and module resolution agree.
The low-level pipeline.normalize deliberately sees neither session directives nor module loading.

Do not add a TeX/template parser, registries, automatic escaping or TeX validation, variables or
expressions, new condition forms, textual rescanning, recursive macros, dynamic imports, package
registries, remote fetching, plugins, renderer backends, runtime dependencies, or a generic
schema/snapshot framework. The v1 boundaries in the specification are exhaustive.
