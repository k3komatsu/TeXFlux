# TeXFlux Standard Flow Macros / Implicit Prelude Specification

**Status:** Draft specification  
**Target:** TeXFlux post-0.1 refactoring  
**Repository:** `k3komatsu/TeXFlux`  
**Scope:** Standard flow controls, implicit macro-module loading, removal of convenience specials from the compiler core

---

## 1. Purpose

This specification defines how TeXFlux shall provide a very small set of standard flow controls without turning each convenience operation into a compiler-level special directive.

The main goals are:

1. keep the TeXFlux language core small;
2. express reusable flow operations using TeXFlux's own macro system whenever possible;
3. make the standard flow operations available in every `.tfx` and `.tfxm` module without requiring boilerplate imports;
4. reuse the existing macro-module and lexical-scope architecture rather than introduce a new library/package mechanism;
5. remove TeX-specific convenience operations such as `!vpad` from the language core;
6. avoid introducing a general-purpose standard library until actual demand justifies one.

The standard flow API is:

```text
!before{prefix} >> BODY
!after{suffix} >> BODY
!around{prefix}{suffix} >> BODY
!off{ignored} >> BODY
!drop >> BODY
```

All five are ordinary macros supplied by a compiler-bundled macro module. No compiler primitive is left behind: see §5.5 for why `drop` needs none.

---

## 2. Design principle

TeXFlux shall distinguish three concepts.

### 2.1 Language primitives

Language primitives exist because ordinary TeXFlux source cannot implement their semantics.

Examples include:

- parsing and indentation;
- `>>` composition;
- `!defmacro`;
- `!param`;
- `!text`;
- `!each`;
- build flags and conditionals;
- `!import`;
- `!macroimport`;
- source provenance and module resolution.

A feature SHOULD NOT be a language primitive merely because it is convenient.

### 2.2 Standard flow macros

A standard flow macro is an ordinary structural macro whose implementation can be expressed using existing TeXFlux macro semantics.

The compiler bundles these definitions and makes them automatically visible to every source module.

They are not a separate programming language feature and do not introduce a new import syntax.

### 2.3 User macro modules

Project-, laboratory-, theme-, or author-specific abstractions remain ordinary `.tfxm` modules imported explicitly with `!macroimport`.

Examples that SHOULD remain user-level rather than standard include:

- figure-left / figure-right layouts;
- image-frame templates;
- Beamer theme helpers;
- project-specific colors;
- slide templates;
- fixed column ratios;
- conference- or laboratory-specific formatting.

The standard flow module MUST NOT become a miscellaneous convenience library.

---

## 3. Non-goals

This change does **not** introduce:

- a `std.*` namespace;
- a package manager;
- a new `prelude` statement;
- a new source-level import syntax;
- separate standard-library versioning;
- Beamer-specific standard helpers;
- TeX styling conventions;
- a mechanism for selecting different preludes;
- a public user-configurable prelude search path;
- dynamic macro imports;
- import-order-dependent shadowing.

The word *prelude* may be used informally or internally, but there is no new source-language construct named `prelude`.

---

## 4. Bundled macro module

The TeXFlux package SHALL contain one compiler-owned macro module.

Recommended path:

```text
src/texflux/prelude.tfxm
```

An alternative internal filename such as `builtins.tfxm` is acceptable, but the module has the same semantics defined here.

It is distributed as part of the TeXFlux Python package and versioned together with TeXFlux itself.

It MUST NOT be resolved relative to the user's project directory.

Conceptually, each module behaves as if it had an implicit import before its explicit imports:

```text
<implicit compiler import of TeXFlux prelude>

!macroimport{user-library.tfxm}

...module contents...
```

This is a conceptual model only. The compiler SHOULD NOT inject a synthetic `!macroimport{...}` syntax node into the user's AST.

---

## 5. Standard flow API

### 5.1 `!before`

#### Signature

```text
!before{prefix}: |
    BODY
```

or equivalently in a stack:

```text
!before{prefix} >> BODY
```

#### Semantics

```text
before(prefix, body) = prefix body
```

`prefix` is emitted immediately before the body.

#### Canonical macro definition

```text
!defmacro{before}{prefix}{body}: |
    !param{prefix}
    !param{body}
```

#### Example

Input:

```text
!before{\smallskip} >> \foo
```

Equivalent structural result:

```text
\smallskip
\foo
```

The macro performs structural AST composition. It MUST NOT concatenate source strings and MUST NOT reparse inserted text.

---

### 5.2 `!after`

#### Signature

```text
!after{suffix}: |
    BODY
```

or:

```text
!after{suffix} >> BODY
```

#### Semantics

```text
after(suffix, body) = body suffix
```

#### Canonical macro definition

```text
!defmacro{after}{suffix}{body}: |
    !param{body}
    !param{suffix}
```

#### Example

```text
!after{\smallskip} >> \foo
```

is structurally equivalent to:

```text
\foo
\smallskip
```

---

### 5.3 `!around`

#### Signature

```text
!around{prefix}{suffix}: |
    BODY
```

or:

```text
!around{prefix}{suffix} >> BODY
```

#### Semantics

```text
around(prefix, suffix, body) = prefix body suffix
```

#### Canonical macro definition

```text
!defmacro{around}{prefix}{suffix}{body}: |
    !param{prefix}
    !param{body}
    !param{suffix}
```

#### Equivalence

The following are semantically equivalent:

```text
!around{A}{B} >> BODY
```

and:

```text
!before{A} >>
!after{B} >>
BODY
```

`!around` is retained despite being composable from `!before` and `!after` because paired before/after transformation is a common and conceptually atomic operation.

No further convenience combinators SHOULD be added merely because they can abbreviate another short composition.

---

### 5.4 `!off`

#### Signature

```text
!off{ignored}: |
    BODY
```

or:

```text
!off{ignored} >> BODY
```

#### Semantics

```text
off(ignored, body) = body
```

The first value is accepted but discarded.

#### Canonical macro definition

```text
!defmacro{off}{ignored}{body}: |
    !param{body}
```

#### Typical use

```text
\outer >> !off{\middle{A}} >> \inner
```

removes the selected wrapper-like fragment while leaving the remainder of the pipeline.

`!off` is backend-independent. It does not know anything about TeX command syntax beyond receiving ordinary macro values.

---

### 5.5 `!drop`

#### Signature

```text
!drop: |
    BODY
```

or:

```text
!drop >> BODY
```

#### Semantics

```text
drop(body) = ∅
```

The body produces no canonical output.

The body is still parsed according to ordinary TeXFlux syntax. Macro-expansion behavior MUST remain compatible with the existing `!drop` semantics, while later canonical normalization and content-import expansion of the discarded result do not occur.

#### Canonical macro definition

```text
!defmacro{drop}{body}: |
```

The template is empty. That is not a new language feature: a `': |'` suite with no lines under it already parses to an empty block, and a bound value that no template position reads already contributes nothing. `!drop` therefore needs no empty-template syntax, no hidden `!empty`, and no compiler handler.

The phase behavior follows from the same fact rather than from a special case. A macro call reads its payload as one value; `drop`'s template writes it nowhere, so the payload never reaches normalization or content-import resolution. §8's guarantees hold for exactly the reason they held before.

Therefore:

```text
before  -> prelude macro
after   -> prelude macro
around  -> prelude macro
off     -> prelude macro
drop    -> prelude macro
```

The compiler special registry keeps no flow-control entry at all.

---

## 6. Implicit loading semantics

### 6.1 General rule

Before building the effective macro environment of any source module, the compiler SHALL make the standard prelude macros available.

This applies independently to:

- the root `.tfx` file;
- every `.tfx` file reached through `!import`;
- every `.tfxm` file reached through `!macroimport`.

Thus, a user macro module may itself use the standard flow macros without explicitly importing them:

```text
!defmacro{compact}{body}: |
    !before{\smallskip} >>
    !after{\smallskip} >>
    !param{body}
```

### 6.2 No source AST injection

The compiler SHOULD implement this by seeding the macro environment, not by rewriting user source to contain an extra import declaration.

In pseudocode:

```python
prelude = session.standard_macros()

explicit_imports = resolve_user_macro_imports(module)
imported = merge_macro_imports(explicit_imports)

environment = merge_standard_macros(prelude, imported)
environment = collect_local_macros(module, imported=environment)
```

The exact implementation may differ, but externally visible behavior MUST match this model.

### 6.3 Load once, expose everywhere

The bundled `prelude.tfxm` SHOULD be parsed and collected at most once per `CompilationSession`.

Its immutable macro definitions may then be reused in every module environment.

The compiler MUST NOT reread the package resource once per imported module.

---

## 7. Namespace and collision rules

To minimize changes to the existing module system, standard macro names behave like automatically imported names.

The initial implementation SHALL use strict collision rules.

The following names are standard names:

```text
before
after
around
off
drop
```

A local macro with one of these names is invalid:

```text
!defmacro{before}{x}{body}: |
    ...
```

Likewise, an explicitly imported macro module MUST NOT export one of these names into a scope where the standard name is already visible.

The diagnostic SHOULD identify both the attempted definition/import and the standard macro name.

Recommended wording:

```text
macro '!before' conflicts with a TeXFlux standard flow macro
```

This deliberately avoids adding a special "weak prelude" shadowing rule.

### 7.1 Why no shadowing?

Allowing:

```text
local > explicit imports > prelude
```

would be defensible, but would introduce another name-resolution precedence rule solely for a handful of names.

With only five stable standard flow controls, strict collision is simpler:

- no import-order effects;
- no hidden replacement of standard behavior;
- no special precedence layer;
- easier diagnostics;
- easier VS Code completion and navigation.

If the set of standard macros becomes substantially larger in the future, shadowing policy MAY be reconsidered as a separate language proposal.

---

## 8. `!drop` and evaluation order

`!drop` requires attention because discarding a subtree interacts with compiler phases. It is an ordinary macro (§5.5), so what follows describes what its empty template already guarantees rather than a handler contract.

The intended observable behavior is:

```text
!drop >> PAYLOAD
```

produces no rendered TeX.

The compiler MUST preserve the current phase-order property that content imports contained only inside the discarded result are not subsequently compiled.

Example:

```text
!drop: |
    !import{missing.tfx}
```

SHOULD NOT require `missing.tfx` to exist if the import node is discarded before content-import resolution.

Macro/template validation that already occurs before dropping MAY still report errors according to the normal phase ordering.

The migration MUST include regression tests for:

- nested macro calls inside `!drop`;
- unknown special directives inside the payload;
- `!when` / `!unless` inside the payload;
- `!import` inside the payload;
- malformed structural syntax inside the payload;
- source-map behavior;
- `>>` stack payloads.

The implementation MUST NOT silently change `!drop` from a structural discard into a build-time dependency traversal. Binding the payload as a macro value does not: expansion of a call's value happens before binding, exactly as it did inside the old handler's suite, and nothing downstream of expansion ever receives it.

---

## 9. `!vpad` removal and migration

`!vpad` SHALL NOT belong to the standard flow API.

It is TeX/LaTeX-specific convenience and can be expressed using the generic flow controls.

Existing:

```text
!vpad{-1em}: |
    BODY
```

becomes:

```text
!before{\vspace{-1em}}: |
    BODY
```

Existing:

```text
!vpad{-1em}{0.5em}: |
    BODY
```

becomes:

```text
!around{\vspace{-1em}}{\vspace{0.5em}}: |
    BODY
```

or equivalently:

```text
!before{\vspace{-1em}} >>
!after{\vspace{0.5em}} >>
BODY
```

This is preferable because:

- the language no longer knows about `\vspace`;
- no special interpolation allow-list is needed only for `!vpad`;
- spacing remains ordinary TeX chosen by the author;
- the same combinators work with any other command or structural fragment.

### 9.1 Compatibility strategy

Because TeXFlux is still pre-1.0, direct removal is acceptable if the project is willing to make a breaking DSL revision.

If a compatibility period is desired:

1. introduce `before`, `after`, and `around`;
2. mark `!vpad` deprecated;
3. emit a targeted migration diagnostic;
4. remove the `!vpad` special in the next planned DSL revision.

A permanent compatibility alias is NOT recommended, because the purpose of this refactoring is to remove TeX-specific compiler special cases.

---

## 10. Compiler architecture

### 10.1 Recommended package layout

```text
src/texflux/
    ast.py
    parser.py
    macros.py
    modules.py
    normalize.py
    ...
    prelude.tfxm
```

No separate standard-library package is required.

### 10.2 CompilationSession ownership

`CompilationSession` SHALL own the loaded standard macro definitions.

Conceptually:

```python
class CompilationSession:
    def __init__(...):
        self._standard_macros = load_standard_macro_module()
```

The standard module is immutable during the session.

### 10.3 Synthetic module identity

The compiler SHOULD assign the bundled module a stable synthetic identity such as:

```text
texflux:prelude
```

rather than exposing its Python installation path.

This identity is useful internally for:

- lexical macro scope;
- recursive-expansion identity;
- diagnostics;
- tests.

The exact package filesystem path MUST NOT be part of language semantics.

### 10.4 Lexical environment

Definitions in `prelude.tfxm` resolve macro names in the prelude's own lexical environment.

For example, if `around` is later implemented using `before` and `after`, those references SHALL resolve to the standard definitions, not to unrelated macros in the caller.

This follows the existing TeXFlux rule that macro templates resolve names in the module where they were defined.

### 10.5 User macro environments

For every user module `M`, the effective environment is conceptually:

```text
standard flow macros
+ explicit macros directly imported by M
+ macros locally defined by M
```

Name collisions are rejected as described in §7.

Private/transitive behavior of ordinary `.tfxm` imports remains unchanged.

---

## 11. Interaction with `.tfxm` purity rules

The bundled prelude SHOULD satisfy the same purity restrictions as an ordinary `.tfxm` wherever possible.

It SHOULD contain only:

- `!defmacro`;
- comments;
- blank lines.

It SHOULD NOT depend on:

- build flags;
- `!import`;
- user project paths;
- caller-local macros;
- dynamic state.

Ideally, the standard prelude imports no other macro module.

This makes the module:

- deterministic;
- cacheable;
- independent of the calling document;
- safe to load once per compilation session.

All five standard operations, `drop` included, live in this file. Nothing about them is external to it.

---

## 12. Provenance and source mapping

Expansion of standard macros SHALL obey the same provenance rules as ordinary macros.

Template scaffolding introduced by a standard macro SHALL map to the macro call site.

Values supplied by the caller SHALL retain their existing source spans.

Example:

```text
!before{\smallskip} >> \foo
```

The generated `\smallskip` fragment originates semantically from the `!before{...}` call/value site, not from the installed `prelude.tfxm` path.

This preserves useful:

- diagnostics;
- `.tfxmap` mappings;
- SyncTeX remapping;
- future editor navigation.

The compiler SHOULD NOT expose the physical installed path of `prelude.tfxm` in ordinary source maps.

---

## 13. External AST behavior

The exported canonical AST SHALL contain only the expansion result.

There SHALL be no special AST node such as:

```text
StandardBefore
StandardAfter
PreludeCall
```

For example:

```text
!before{\foo} >> \bar
```

must export the same canonical AST that the equivalent ordinary macro expansion would produce.

The existence of `prelude.tfxm` is therefore an implementation/source-language concern and does not extend the canonical AST schema.

### 13.1 `sources`

The bundled prelude SHOULD NOT be listed as a normal user input source in the external AST `sources` array.

Reasons:

- it is part of the compiler distribution;
- it is identical for every compilation of a given TeXFlux version;
- generated scaffolding is retargeted to the call site;
- including it would create noise for external consumers.

The TeXFlux producer version is sufficient to identify which standard definitions were used.

---

## 14. Diagnostics

### 14.1 Macro-call errors

Because `before`, `after`, `around`, and `off` are real macros, their arity diagnostics SHOULD use the normal macro diagnostic path.

Examples:

```text
!before
```

should report that `!before` expects two values (`prefix`, `body`) and received an insufficient number.

```text
!around{A} >> B
```

should report the normal macro arity error.

No dedicated Python-side diagnostic code is required for these operations.

### 14.2 Name collisions

Collisions with standard macros SHOULD have a specific, stable error message or diagnostic code.

Recommended future diagnostic code:

```text
TFX-MACRO-STANDARD-NAME-CONFLICT
```

### 14.3 Internal prelude failures

If the compiler-shipped `prelude.tfxm` fails to parse or validate, this is an installation/compiler defect, not a user source error.

The CLI SHOULD report it distinctly, for example:

```text
texflux: internal error: bundled prelude is invalid
```

Tests and packaging should make this condition effectively impossible in released builds.

---

## 15. Public Python API

Functions that compile a full TeXFlux module through `CompilationSession` SHALL receive the standard flow macros automatically.

This includes normal CLI compilation and module-aware public APIs.

Care is required for lower-level APIs such as a direct `normalize(document, registry=...)` call.

Two acceptable policies exist:

### Recommended policy

Treat module-aware compilation as the normative public behavior.

Low-level normalization functions remain low-level and MAY require the caller to provide an environment explicitly.

### Alternative

Seed standard macros in all convenience compilation APIs, including single-text compilation.

Whichever policy is chosen, the following user-facing entry points MUST behave consistently:

```text
texflux compile
texflux ast
compile_with_map(...)
compile_text(...)   # if documented as full-language compilation
```

A test MUST ensure that a source using `!before` behaves the same through the CLI and through the documented Python compilation API.

---

## 16. Performance

The prelude is tiny and MUST have negligible runtime cost.

Recommended implementation:

1. read package resource once per `CompilationSession`;
2. parse once;
3. validate once;
4. collect macro definitions once;
5. reuse immutable `MacroDefinition` objects across module-environment construction.

Do not synthesize and parse an implicit `!macroimport` statement separately for every module.

The conceptual model is "implicit macroimport"; the efficient implementation is "seed every environment from one cached standard definition table."

---

## 17. Exact standard surface

The initial standard flow surface is frozen to:

```text
before
after
around
off
drop
```

New names SHOULD be added only if all of the following hold:

1. the operation is backend-independent;
2. it manipulates TeXFlux structural flow rather than TeX presentation;
3. it is broadly useful across unrelated projects;
4. its semantics are stable and unsurprising;
5. it is not merely a one-to-one alias of an existing TeX command/environment;
6. it meaningfully improves composition with `>>`;
7. there is evidence of repeated real-world use.

Examples that do **not** meet the bar by default:

```text
vpad
figure-left
figure-right
imageframe
columns2
bold
red
center
```

These belong in TeX source, user macro modules, or project/theme libraries.

---

## 18. Migration from current built-in specials

### 18.1 `!off`

Current compiler handler:

```text
special -> normalize payload -> splice payload
```

Target:

```text
ordinary standard macro -> bind ignored/body -> expand body
```

Required regression property:

```text
old output == new output
```

for valid existing inputs.

### 18.2 `!vpad`

Remove from compiler special registry after migration/deprecation policy is satisfied.

Also remove any macro-expansion special case whose only purpose is permitting text interpolation in `!vpad` arguments.

### 18.3 `!drop`

Remove `_drop_handler` and define `drop` in `prelude.tfxm` with an empty template.

Its documented syntax and semantics do not change. The regression suite of §8 is what proves the phase behavior survived the move.

### 18.4 Target special registry

After this refactoring, the registry should be substantially smaller.

Conceptually:

```python
BUILTIN_DIRECTIVES = {
    "import": _module_guard("import"),
    "macroimport": _module_guard("macroimport"),
}
```

`off`, `vpad` and `drop` should no longer need handlers.

`before`, `after`, and `around` never receive handlers.

If module constructs are later moved out of this registry internally, that is a separate refactoring.

---

## 19. Required tests

### 19.1 Basic behavior

Test:

```text
!before{A} >> B
```

Expected order:

```text
A
B
```

Test:

```text
!after{B} >> A
```

Expected order:

```text
A
B
```

Test:

```text
!around{A}{C} >> B
```

Expected order:

```text
A
B
C
```

Test:

```text
!off{A} >> B
```

Expected:

```text
B
```

Test:

```text
!drop >> B
```

Expected: no output nodes.

### 19.2 Composition

Test nested forms such as:

```text
!before{A} >>
!after{D} >>
!around{B}{C} >>
X
```

Expected structural order:

```text
A
B
X
C
D
```

The exact formatting should follow normal renderer rules.

### 19.3 Block form

All applicable operations MUST behave consistently between block and stack payload forms:

```text
!before{A}: |
    B
```

and:

```text
!before{A} >> B
```

### 19.4 Imported content modules

A `.tfx` reached through `!import` MUST see the standard flow macros without declaring an import.

### 19.5 Macro modules

A `.tfxm` reached through `!macroimport` MUST be able to use the standard flow macros inside its templates.

### 19.6 Lexical scope

A standard macro implemented in terms of another standard macro MUST resolve that dependency in the standard module's lexical environment.

### 19.7 Collision

Local definition:

```text
!defmacro{before}{x}{body}: |
    ...
```

must fail.

An explicit imported macro named `before` must likewise fail when merged into the module environment.

### 19.8 Module privacy

Implicit standard macros MUST NOT alter the existing non-transitive visibility rules for ordinary macro imports.

### 19.9 Provenance

Generated fragments from standard macro scaffolding MUST map to the call site.

Caller-provided values MUST keep their original spans.

### 19.10 External AST

Standard macro calls MUST be absent after canonical expansion.

No new external AST node type is permitted.

### 19.11 `drop`

Regression tests MUST cover the evaluation-order cases listed in §8.

### 19.12 Golden examples

Existing golden examples that use `!off`, `!drop`, or `!vpad` should be migrated and compared byte-for-byte where semantics are intended to remain identical.

---

## 20. Documentation changes

The language documentation SHOULD stop classifying `!off` as a built-in special.

Recommended organization:

```text
Special language constructs
    !defmacro
    !param
    !text
    !each
    !flag
    !when
    !unless
    !import
    !macroimport

Standard flow controls
    !before
    !after
    !around
    !off
    !drop
```

There is no implementation split left to explain: all five are macros.

`!vpad` should be documented only in migration notes after its removal.

---

## 21. Recommended implementation sequence

1. Add `src/texflux/prelude.tfxm`.
2. Define `before`, `after`, `around`, and `off` using ordinary `!defmacro`.
3. Add a `CompilationSession` loader/cache for the bundled module.
4. Seed every `.tfx` and `.tfxm` macro environment with these definitions.
5. Add strict collision detection against standard macro names.
6. Verify lexical-scope behavior.
7. Remove `_off_handler` from `normalize.py`.
8. Add `before` / `after` / `around` tests.
9. Migrate tests and examples from built-in `off` to standard-macro `off` with byte-identical output.
10. Replace `vpad` usages with `before` / `around`.
11. Remove `_vpad_handler`, `_vspace`, and `vpad`-specific interpolation exceptions.
12. Remove `_drop_handler`, define `drop` with an empty template, and verify its regression suite.
13. Update README and DSL specification.
14. Verify `texflux compile`, `texflux ast`, source maps, SyncTeX remapping, and Python APIs.
15. Only after this refactor stabilizes, proceed with the public Diagnostics API / VS Code integration.

---

## 22. Acceptance criteria

The implementation is complete when all of the following are true:

- `!before`, `!after`, `!around`, `!off` and `!drop` are implemented as ordinary TeXFlux macros.
- Users do not write an explicit import to use them.
- Every `.tfx` and `.tfxm` module sees them.
- Standard macro definitions are loaded once per compilation session.
- No new public import/prelude syntax exists.
- Existing lexical macro-scope rules remain intact.
- Standard names cannot accidentally be redefined.
- `!vpad` no longer requires a compiler special handler in the target state.
- `!drop` retains its existing observable semantics, including its phase behavior.
- The special registry holds no flow-control handler.
- No new canonical AST node types are introduced.
- External AST and source maps do not expose the installed package path of `prelude.tfxm`.
- CLI and documented Python APIs agree on standard-macro availability.
- Existing non-related golden tests remain unchanged.

---

## 23. Final design summary

The design deliberately stops short of creating a general standard library.

TeXFlux has one tiny compiler-bundled macro module that is implicitly available when every module environment is constructed.

```text
Language core
    syntax
    >>
    macros
    flags
    modules
    provenance

Implicit standard macro module
    before
    after
    around
    off
    drop

User/project macro modules
    everything opinionated or domain-specific
```

From the user's perspective, the standard flow vocabulary is simply:

```text
before
after
around
off
drop
```

The architectural rule is:

> If a reusable operation can be written as an ordinary TeXFlux macro, it should not receive a compiler special handler merely for convenience.

At the same time:

> TeXFlux should not add new general language machinery solely to move an operation out of the core.

`drop` is the case that tests the rule and passes it: the existing macro calculus already expresses it, because an empty template is an empty template and a value nothing reads is discarded. No new machinery was needed.

This keeps the implementation small without creating an unnecessary `stdlib` or first-class `prelude` subsystem.
