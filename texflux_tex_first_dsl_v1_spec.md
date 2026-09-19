# TeXFlux TeX-first DSL v1 normative specification

## 0. Status

This document is normative for the TeXFlux v1 language. The implementation,
tests, examples, README, and doc/dsl.md must agree with it.

TeXFlux is a TeX-first preprocessor. It preserves raw TeX and provides only
structural syntax for containers, values, and source provenance. It does not
parse TeX or infer command/environment signatures.

The D implementation, its tests, examples, and published formats are governed
by this document. Where this document is silent, the existing v1 fixture and
published format behavior remains authoritative; a new behavior must not be
invented merely to fill the silence.

The fixed mental model is:

~~~text
\ = TeX command
@ = structural container
! = TeXFlux special
~~~

The fixed suite model is:

~~~text
no suffix = one already-closed RHS value
::        = one multiline block value
:::       = one marked value per '-' or '+'; the next sibling marker is its boundary
~~~

## 1. Lexical model

Prefixes alone classify structural segments.

- A backslash segment is a TeX command.
- An at-sign segment is a structural container.
- An exclamation segment is a TeXFlux special.

At containers include named environment, anonymous transparent, and anonymous
literal-brace containers:

~~~text
@name
@
@::
@:::
@{}
@{RAW_TEX}
~~~

@{RAW_TEX} scans balanced braces and preserves RAW_TEX as leading opaque TeX.
@ and @:: never create a TeX wrapper. @{} and @{RAW_TEX} always create a
literal TeX brace group when completed.

Named environment names are not looked up and may include a trailing star and
other non-structural punctuation. Unknown names render normally. Unknown
special names fail with DirectiveError.

## 2. Physical lines and indentation

CRLF and CR are normalized to LF. Tabs are rejected outside a raw-mode region
and the body of a line-initial `!| ` raw line. Four ASCII spaces are the
structural indentation unit. A structural child must be at the suite base;
extra indentation is retained only for raw TeX lines. Structural candidates at
invalid indentation are parse errors.

Blank lines in a sequence suite are separators and do not create values. Blank
lines inside a sequence value block or a block suite are content. A trailing
blank run is returned to the enclosing block when the suite closes.

At the start of a block line (after spaces), `@@` and `!!` remove exactly
one leading character and emit the rest as raw TeX, without header scanning.
They are handled before the structural extra-indentation check, preserving
spaces beyond the block base. The same escapes apply at the start of a `-`
sequence marker payload: `- !!bar` supplies raw `!bar`. A `+` payload is an
opaque authored group and never applies the marker escape. Tabs remain invalid
on an escaped line.
Thus `!!` emits `!`, `!!!foo` emits `!!foo`, and `!!重要` emits `!重要`.
Unbalanced braces and trailing `:` or `>>` remain literal on escaped lines.

At the same position, `!|` followed by one ASCII space, or by the end of the
line, emits everything after that space as one raw TeX line. Because it strips
the whole marker rather than one prefix character, it is the only escape that
reaches a `\` line a top-level structural token would otherwise claim:
`!| \emph{x} >> \emph{y}`, or `!| \cmd::` when that spelling is meant as
TeX rather than as a suite marker (section 3). The marker and its one separating space are
removed and nothing else on the line is touched: further spaces are kept, and
every character up to the newline is emitted verbatim, tabs included. `!|`
followed by anything else is a parse error. A line consisting of `!|` alone
emits an empty raw line, which is content rather than a blank line and so is
never rewound to the enclosing block. The line is verbatim, so macro expansion
never interpolates it, and `!!| foo` writes the marker literally as `!| foo`.

The same escape applies to a `-` sequence marker payload, with two differences
from the block-line form. The first it shares with `@@` and `!!`: a payload is
right-stripped before the escape runs, so trailing spaces are not kept there.
The second is specific to `!| `, which is the only one of the three that ever
permits a tab: a tab stays invalid in a payload, because the whole-file tab
scan classifies a line by its own text and a leading `-` is a sequence marker
only inside a `::` suite. A tab in a sequence value is written in the entry's
continuation block instead, where the marker starts the line.

A `+` payload is an opaque authored group and never applies the escape. A
`!| ` line inside a `+` continuation block is likewise body text rather than an
escape, so the parser re-checks that block and rejects its tabs, while a
raw-mode region nested in the same block still allows them.

The two whole-line markers `!BEGIN_RAW_MODE` and `!END_RAW_MODE` delimit a
raw-mode region. A marker is recognized only when the line consists of that
marker after removing surrounding ASCII spaces. `!BEGIN_RAW_MODE` must be at
the current block base. The physical lines after BEGIN and before the matching
END at the same indentation are emitted as raw TeX lines; a line may be
shallower or deeper than the base, and at most the base's leading spaces are
removed. Tabs are allowed inside the region. The region does not scan headers,
expand `@@` / `!!`, close on dedentation, rewind blank lines, or interpolate
`!text{...}`. Its marker lines produce no nodes. A BEGIN/END marker spelling
inside the region, or an END at a different indentation, is literal text.
Raw-mode regions can be written where a block can be written, including a
sequence entry's continuation block and a macro template; they are not a
sequence payload or a `>>` segment, and a top-level `.tfxm` region remains
subject to macro-module purity.

## 3. Header scanner

The scanner treats group contents as opaque and balances:

~~~text
{...} required
[...] optional
<...> overlay
~~~

A `!` segment, and only a `!` segment, may carry one further group after all
of these: a trailing `(...)` binding list, which `!import` reads (section 12).
`(` opens no group anywhere else, so a command header, an environment name and
raw TeX all read it as ordinary text.

Only a depth-zero trailing suite marker and a space-separated >> are
structural. Group internal colon, pipe, and >> remain raw text. A lone colon
is not structural anywhere, so TeXFlux shares no single-character operator
with TeX prose (section 16). Structural header trailing TeX comments are
unsupported.

A depth-zero `>>` at the end of a structural header line continues that header
on the immediately following physical line (section 8). The newline replaces
the spaces after `>>`; at least one space before it is still required.

A suite suffix is either two or three adjacent colons, optionally followed
by spaces. The double colon selects block mode; the triple colon selects
sequence mode. Neither a single colon nor a pipe is part of suite syntax:

~~~text
\foo::
    BODY
\foo:::
    - ITEM
~~~

Any trailing token after this suffix is a ParseError. A pipe elsewhere in raw
TeX is not syntax.

A lone colon is TeX prose, never a suffix, so a command line that ends with
one is raw TeX: `\textbf{Note}:` and `\item Note:` both are, whatever sits
under them. A line is classified by reading that line alone, so no lookahead
decides what a line means, and indenting the line below one changes nothing
about it. A lone colon is equally inert in the middle of a line, so
`\texttt{std}:vector` and `\foo(x):` remain raw TeX; a `\` line whose lone
colon is followed by `>>` is still reserved, because `>>` is structural
wherever it is written. On an `@` or `!` header, which has no raw-TeX
fallback, a lone trailing colon is a malformed header.

`::`, `:::` and `>>` never appear in TeX prose, so each reserves its line
unconditionally: a failed scan on such a line is an error rather than raw
TeX, and `\foo(x)::`, `\texttt{std}::vector` and `\foo:: |` are all parse
errors. A reserved line also has to honour the marker it carries, so a command's
`::` requires an indented body and its `:::` requires at least one `-` or `+`
entry. Neither requirement is about the value: a bare `-` writes an empty
argument on purpose, and `\foo:::` above one renders `\foo{}`. What the rules
rule out is a marker with nothing under it at all, which is how
`\texttt{std}::` written as prose would otherwise become `\texttt{std}{}`.
An empty argument is also written `\foo{}` in raw TeX; `@` and `!` accept an
empty suite, so `@foo::` and `!foo::` stay valid.

At-sign classification has this precedence:

~~~text
@{...} -> literal brace container
@      -> transparent container
@name  -> named environment
~~~

Every stack segment must carry its own prefix. The scanner keeps the suffix mode
on the header/stack syntax node. No suffix is represented as no suite.

## 4. Syntax AST

The parser keeps syntax shape and source spans. The relevant syntax nodes are:

- RawTex(text, span)
- ParsedInvocation(kind, name, groups, suite, suite_mode, span)
- SpecialInvocation(name, groups, suite, suite_mode, span)
- Stack(segments, suite, suite_mode, span)
- SequenceEntry(value, marker_span, span, argument_kind)

A `SequenceEntry` records no measure of the shape it was written in, because
nothing downstream may read one.
- Block(nodes, span)

Invocation kinds include command, named environment, brace container, and
transparent container. SuiteMode is Sequence or Block. Every parsed
`SequenceEntry` value is a Block whose first line is the marker payload and whose
remaining lines continue until the next sibling marker. A `-` entry has
`argument_kind=None`; a `+` entry records the kind of its one authored `{...}`,
`[...]`, or `<...>` group.

A suffix-less Stack has no suite. A suffix-bearing Stack stores the suffix suite
on its rightmost segment after desugaring.

## 5. Sequence and block values

### 5.1 Sequence mode

A triple-colon suite contains marked sequence entries. A structured command
sequence must contain at least one entry:

~~~text
\foo:::
    - A
    - B
~~~

Direct raw child lines such as A and B without a marker are invalid. Each `-`
starts one generated required argument: its text after the marker is the first
line, and subsequent lines indented beyond the sequence base belong to that
value until the next sibling marker. A bare `-` is therefore the multiline
spelling. A `+` starts one explicit argument and must be followed by exactly
one balanced `{...}`, `[...]`, or `<...>` group. The group may span indented
continuation lines; its complete text is kept opaque and is never header-
scanned. The delimiter is part of the value, so `+ {x}` emits `{x}` rather
than a generated pair around it.

~~~text
\foo:::
    - short
      long line 1
      long line 2
    - @center::
        BODY
~~~

`-` value blocks and structural entry suites use the normal TeXFlux parser.
They are not opaque YAML scalars. A `+` group is the intentional exception:
all of its physical lines are raw TeX. The two whole-line raw-mode markers are
still recognized in a `+` continuation block at its continuation base; an
off-base marker is the same structural-indentation error as in a `-` block.
Marker and value spans are retained.
The spelling "- |" is not a block marker; it starts a value whose first line
is the raw character "|". A `+` with no group, an unbalanced group, or trailing
tokens after its group is a ParseError.

### 5.2 Block mode

A double-colon suite is exactly one block value:

~~~text
\foo::
    A
    @center::
        B
~~~

Its content is parsed as a normal TeXFlux block, including raw TeX, containers,
specials, stacks, and blank lines. An empty block suite is valid on a container
and on a special. A command's is not: it would emit a silent `{}`, and an
empty argument is raw TeX the author writes directly as `\foo{}`.

## 6. Value consumption

### 6.1 Command

A structured command consumes all suite entries as arguments after its compact
header groups. A `-` entry becomes one generated required argument. A `+`
entry contributes exactly the one required, optional, or overlay group it
contains, including its authored delimiters.

~~~text
\foo{COMPACT}:::
    - A
    - B
~~~

renders as:

~~~tex
\foo{COMPACT}{A}{B}
~~~

Mixed entries make the distinction explicit:

~~~text
\command:::
    - simple
    - {group as content}
    + {explicit required argument}
    + [explicit optional argument]
    + <2->
~~~

~~~tex
\command{simple}{{group as content}}{explicit required argument}[explicit optional argument]<2->
~~~

A block suite produces one long required argument:

~~~text
\foo::
    A
    B
~~~

renders as:

~~~tex
\foo{%
A
B
}
~~~

A structural sequence value is wrapped in one required argument. Literal brace
containers are not absorbed by that outer argument:

~~~text
\foo:::
    - @{}::
        A
~~~

renders conceptually as:

~~~tex
\foo{%
{%
A
}
}
~~~

A normal command line without suite or stack syntax remains raw TeX. A command
used as a closed stack terminal or structural sequence value is normalized as a
closed canonical command value.

### 6.2 Named environment

A block suite is the environment body. Compact groups remain begin arguments.

For a sequence suite, all entries except the final entry become begin
arguments and the final `-` entry becomes the body. Earlier entries may be
either generated required arguments (`-`) or explicit groups (`+`); the final
entry must be `-` so that body ownership is unambiguous:

~~~text
@myenv:::
    - ARG1
    - ARG2
    - @::
        BODY
~~~

renders as:

~~~tex
\begin{myenv}{ARG1}{ARG2}
BODY
\end{myenv}
~~~

An explicit argument can precede the generated arguments and body:

~~~text
@myenv:::
    + [opt]
    - ARG
    - @::
        BODY
~~~

~~~tex
\begin{myenv}[opt]{ARG}
BODY
\end{myenv}
~~~

A final `+` entry is a validation error because an environment sequence must
end with its generated body entry.

The final block value contributes its canonical nodes as the body. An empty
sequence environment is a validation error. An empty body is written as an
empty @:: value.

## 7. Anonymous containers

@:: is a transparent one-block value and emits only its body. The `@:::`
sequence form combines its values into one composite body without a wrapper.
An empty transparent sequence is valid and emits nothing. An empty sequence
for a literal brace container is valid and emits an empty literal brace group.
Use `@::` or `@{}::` when the empty block form should be explicit.

@{}:: and @{RAW_TEX}:: emit literal brace groups. RAW_TEX is emitted as
leading opaque TeX inside the braces. Literal braces remain in all contexts,
including command argument context.

Anonymous `@::` and `@{...}::` containers accept only `-` sequence entries. A
`+` entry is reserved for command arguments and named-environment begin
arguments, so using it in an anonymous sequence is a ValidationError.

## 8. Stack composition

>> is pure structural composition and is removed before rendering.

For a closed stack, the rightmost segment is a closed value and every segment
to its left receives one synthetic block value:

~~~text
@center >> \includegraphics{fig.pdf}
~~~

renders as:

~~~tex
\begin{center}
\includegraphics{fig.pdf}
\end{center}
~~~

An open stack gives its suffix to the rightmost segment and passes the completed
value to the left:

~~~text
@frame{Title} >> @center >> @{\small}::
    BODY
~~~

A suffix-less stack whose rightmost segment is an open container is invalid:

~~~text
@foo >> @center
~~~

All stack segments have complete prefixes. Stack semantics do not depend on
template or TeX knowledge.

A stack header may be split immediately after any `>>`:

~~~text
@hoge >>
@fuga >>
@fuge::
    foobarhoge
~~~

This is equivalent to `@hoge >> @fuga >> @fuge:` with the same suite. Keeping
`@hoge >> @fuga >>` on the first line is also valid. A closed terminal needs no
suite suffix, so this is valid too:

~~~text
@hoge >>
@fuga >> \foobar
~~~

Each continuation must be the immediately following physical line, must be
nonblank, at the same indentation as the first header line, and start with a complete
segment prefix. Blank lines, comments, missing segments, and changed indentation
are parse errors. Spaces after a trailing `>>` are allowed. Any suite suffix
belongs to the final segment on the final header line; its suite is indented
four spaces from the header's structural base as usual.

In a generated sequence entry beginning `- @hoge >>`, continuation lines align
with the `-` marker, without repeating it; the suite base remains four spaces
deeper than that marker. Such a value spans multiple physical lines, so the
existing multiline argument-brace layout applies. Raw TeX and escaped `@@` /
`!!` lines do not acquire continuation syntax. Header groups cannot be split
across lines; only a `+` entry's authored group may span its indented raw
continuation lines. Segments, groups, and suffixes retain their physical source
spans; the parser produces one ordinary Stack, with no new canonical node type.

## 9. Specials

Special handlers are in-process AST-to-AST transformations. They return
canonical node tuples, never TeX strings. Unknown specials fail.

`!import`, `!macroimport`, and `!bundleimport` are session constructs, and none
is a handler: they are resolved by the compilation session. `!import` and
`!macroimport` are module constructs of section 12; `!bundleimport` is the
archive-fragment construct of section 12.6.1. Being built-in names, none may
be redefined by `!defmacro`. The `!asset{...}` spelling is different: it is a
text-field resource marker described in section 10.7, not a special AST node.

### 9.1 Standard flow controls

Flow control is not built in. `!before`, `!after`, `!around`, `!off` and
`!drop` are ordinary source macros (section 10), defined in the one
compiler-bundled macro module of section 12.9, which every `.tfx` and `.tfxm`
sees without writing an import. Their names are reserved: defining one, in
either kind of module, is a validation error rather than a shadowing.

`!before`, `!after` and `!around` place one or two values around a payload:

~~~text
!before{\smallskip} >> \foo
!after{\smallskip} >> \foo
!around{\vspace{-1em}}{\vspace{2em}}::
    contents
~~~

`!around{A}{B}` is what `!before{A} >> !after{B} >>` composes to, and is kept
because a paired transformation is one operation. `!off` discards its first
value and keeps the payload, which is how one segment leaves a composition:

~~~text
\fuga >> !off{\foo{a}{b}} >> \hoge
~~~

is equivalent to `\fuga >> \hoge`. `!drop` takes the payload alone and emits
nothing:

~~~text
!drop >> \hoge >> \fuga
~~~

Every rule of section 10 applies to these five, and no rule of its own does.
A payload written with a `'::'` block suite and a payload written as the rest
of a `>>` composition are the same single value (section 10.2), and a `':::'`
sequence suite binds one value per `-` entry; as at any macro call, a `+`
entry is rejected (section 10.2). A wrong value count is the ordinary arity
diagnostic, and provenance follows section 15, so each value keeps the span
of the group or suite the author wrote it in.

`!drop` needs no primitive and has none. Its template is empty, so the value
it binds reaches no template position and nothing survives expansion. That is
also what keeps its phase behaviour: a discarded payload is never normalized
and never content-import-resolved, so `!drop:` around an `!import` opens no
file, and an unknown special or an unconsumable value shape inside one is
never reported.

A dropped payload is still a macro value, so it is expanded before it is
discarded. Expansion-stage errors inside one are therefore reported: a call
with the wrong number of values, a misplaced `!defmacro`, a stray `!param` or
`!each`, a recursion cycle, an interpolation error. That is deliberately
weaker than a dropped conditional payload, which section 11.5 never expands
at all. `!when`/`!unless` is the construct for disabling content that no
longer compiles; `!drop` discards content that still expands.

There is no spacing special. Spacing is the TeX the author chose, placed by
the generic combinators:

~~~text
!before{\vspace{-1em}}::
    contents
!around{\vspace{-1em}}{\vspace{2em}}::
    contents
~~~

so the language holds no handler that knows `\vspace`, and no interpolation
allow-list exists for one construct's arguments.

The standard surface is frozen to these five names. A new name SHOULD be
added only if all of the following hold: the operation is backend-independent;
it manipulates TeXFlux structural flow rather than TeX presentation; it is
broadly useful across unrelated projects; its semantics are stable and
unsurprising; it is not a one-to-one alias of an existing TeX command or
environment; it meaningfully improves composition with `>>`; and there is
evidence of repeated real-world use. No convenience combinator is added merely
because it abbreviates another short composition. Presentation helpers such
as a figure placement, a two-column layout, a colour or a font switch belong
in TeX source or in user macro modules, never here. Should the standard
surface ever grow substantially, the collision rule of section 12.9 may be
reconsidered as a separate language proposal; while it stays this small,
strict collision is simpler than any precedence layer.

### 9.2 Reserved former names

`!block`, `!arg`, `!body`, `!items` and `!vpad` are not part of v1 and are
not aliases: each fails as an unknown special, and none may return under any
spelling. In particular there is no list mini-grammar. A list is an ordinary
environment holding raw `\item` lines, which already carries every overlay,
optional label, continuation, and nesting:

~~~text
@itemize::
    \item<2->[Label] item
    @itemize::
        \item nested
~~~

An environment body is an ordinary block suite, so every rule in sections 2
through 7 applies to the lines inside it, `\item` included, and nothing is
exempt: `\item Summary:` is raw TeX, because a lone colon is no suite marker
and an indented block under one is raw TeX too (section 3), while a depth-zero
`::` would make the same line a structural candidate and so a parse error
(write `!| \item Summary::` for a literal one); a line beginning `@` or
`!` is a header unless `@@` or `!!` escapes it, inside `@verbatim` and
`@lstlisting` bodies too; `\item >> \foo` is a stack, which section 14
renders over three lines. A block suite keeps blank lines as document
content.

## 10. Source macros

A source macro is a structural AST macro, not a textual one. `!defmacro`
stores one template block of syntax AST; a call binds its values to the
template's parameters and instantiates a clone of it. TeXFlux never
substitutes source text or re-parses rendered TeX. Only explicit `!text` holes
interpolate bound text into opaque fields; inserted text is never rescanned
or parsed (section 10.6).

### 10.1 Definition

~~~text
!defmacro{name}{param}{...rest}::
    TEMPLATE
~~~

A definition takes a `::` block suite; a sequence suite is a validation
error. The first required group is the macro name, which uses the special-name
grammar so the macro is callable as `!name`. Every later required group is a
parameter. Definitions emit no TeX. Lines around a definition, including blank
lines, remain ordinary content.

Definitions are collected from the top level only. A `!defmacro` anywhere else,
including inside another macro's template, is a validation error, so macros
cannot be defined dynamically. A template may not contain `!import`,
`!macroimport`, or `!bundleimport` either, so content dependency discovery never depends on macro
expansion. Every definition is collected before any call is
expanded, which makes forward references valid:

~~~text
!foo >> \TextCA{A}

!defmacro{foo}{body}::
    @{\small} >> !param{body}
~~~

Parameter names match `[A-Za-z_][A-Za-z0-9_-]*`. A duplicate parameter, a
macro name that is reserved (`defmacro`, `param`, `text`, `each`,
`BEGIN_RAW_MODE`, `END_RAW_MODE`), a name
already taken by a built-in special, a name belonging to the standard flow
macros of section 9.1, and a duplicate macro definition are all validation
errors reported at the definition site.

### 10.2 Value binding

A macro call uses the existing value syntax; there is no macro-specific call
form. Values bind left to right: every required compact group first, then the
values its suite produces.

~~~text
!foo{A}{B}          two inline values
!foo::              one block value
!foo:::             one value per '-' or '+' entry (but '+' is rejected at macro calls)
!foo >> VALUE       the closed stack payload as one value
~~~

`!foo{A} >> \bar{B}` therefore binds `[A, \bar{B}]`. Optional and overlay
groups are not values and are rejected on a macro call. A `+` sequence entry is
also rejected there; explicit groups are only for command and named-environment
argument suites.

A non-rest parameter consumes exactly one value. A trailing `{...rest}`
parameter consumes every remaining value as a sequence and may consume none.
A rest parameter is optional, unique, and last; anything else is a validation
error. An arity mismatch is a macro error naming the macro, its expected
shape, the actual value count, and the call site.

### 10.3 !param

`!param{name}` substitutes the bound value's AST inside a template. It takes
exactly one required group and no suite. Using it outside a template, naming
an unbound parameter, or naming a rest parameter is a macro error. A rest
parameter is a sequence rather than a value, so it is reached only with
`!each`.

`!param` inserts AST and cannot splice into an opaque TeX group. An unescaped
`!param{` in a text field is an error; use `!text` for text interpolation
(section 10.6). For AST insertion, use structural form:

~~~text
@infobox:::
    - !param{title}
    - !param{body}
~~~

### 10.4 !each

~~~text
!each{rest-param}{item}::
    TEMPLATE
~~~

`!each` walks a rest parameter's values in source order, binds each one to
`item`, instantiates the template per iteration, and concatenates the
iterations into the enclosing block. An empty sequence produces nothing. It
requires a `::` suite, is valid only inside a template, and its first group
must name a rest parameter. An item name that shadows a bound parameter is a
macro error. `!each` may nest; each iteration binds its own item.

`!each` may be the rightmost segment of a stack when the stack's suffix is
`::`, because that segment keeps the block suffix and so still writes its own
template:

~~~text
@{\bfseries} >> !each{items}{item}::
    \item
    !param{item}
~~~

Anywhere else in a stack its suite would be synthetic, which is a validation
error. `!defmacro` can never be a stack segment: composing it would nest the
definition under the segments to its left, and a definition is a top-level
statement.

### 10.5 Expansion

A macro call is one value: the instantiated template block. A call therefore
composes with `>>` like any other segment.

~~~text
@center >> !smallred >> \TextCA{Important}
~~~

A template may call another macro, and forward references apply there too.
Recursion is forbidden. Direct and indirect cycles are detected explicitly and
reported as a macro error naming the chain, never by exhausting a depth limit:

~~~text
recursive macro expansion detected: foo -> bar -> foo
~~~

Expansion is an AST-to-AST pass that runs after `>>` desugaring and before
value consumption, so no macro construct reaches normalization or the
renderer. `!splice` is not part of v1.

### 10.6 Text interpolation

`text` is reserved. `!text{NAME}` is a text hole, whose name must fully match
`[A-Za-z_][A-Za-z0-9_-]*`. It reads one bound parameter in the current macro
frame. This does not change parameter declarations or the AST value model.
A value is text-extractable if and only if it consists of exactly one RawTex
node. Empty compact text is valid; an empty block, multiple nodes, or a
structural node is not. A rest parameter is not text-extractable; an `!each`
item is extractable when it satisfies the same one-RawTex rule.

Interpolation applies only to opaque text fields in a macro template:

- RawTex text;
- inline required, optional, and overlay groups of commands and containers;
- the header of a literal brace container;
- required compact values passed to a user macro, interpolated in the caller's
  frame before binding them to the callee. The standard flow macros of section
  9.1 are user macros in this respect, so `!before{\vspace{!text{gap}}}` is an
  ordinary call value.

`!text` in an AST position is an error; use `!param` for structural insertion.
An unescaped `!param{` in a text field is an error; use `!text` for text.
A text hole outside a template is an error, including a macro call's values
written outside any template. Macro and flag declarations retain their existing
name validation. Command, environment, and special names are fixed by parsing.
Condition groups, `!param` / `!each` name groups, binding lists, and the
groups of every remaining built-in special reject marker spellings, including
escaped ones: what is left of the registry names compiler metadata only.
Import paths remain static; interpolation never discovers or generates imports.

Text scanning is left-to-right, after raw-line escapes. A backslash-escaped
exclamation mark is ignored according to the existing backslash parity rule.
Otherwise `!!text{` and `!!param{` emit literal `!text{` and `!param{`, without
rescanning the emitted prefix. `!text{` closes at the first following `}`;
a missing close or invalid name is a macro error. `!param{` fails whether or
not it has a closing brace. Spaced `!text {x}`, `!textbf{x}`, and bare `!text`
are not markers. At the start of a source line, `!!text{x}` becomes RawTex
`!text{x}` and is interpolated; `!!!text{x}` becomes `!!text{x}` and emits
literal `!text{x}`. These two escape layers also apply to sequence payloads.

Expansion replaces holes with the bound text's fragments verbatim. Inserted
text is never scanned again or parsed, even if it spells markers or structural
operators. Existing fragments are final on subsequent expansion passes.
There is no AST rendering-to-string conversion. Checks occur only as expansion
visits a node: a dropped conditional payload is neither interpolated nor checked
for markers. No additional static traversal enters dropped payloads.

RawTex and inline Argument nodes may carry `parts`, and BraceGroup may carry
`header_parts`: tuples of TextFragment(text, span, scaffold=False). Concatenating
fragments must equal the associated string; Argument parts require a string
value. Invalid combinations raise ValueError. A node without parts maps its
whole field to its one span. Bound fragments keep their original spans
and scaffold flags through nested macro calls and module boundaries. Template
literals point at the call site with the `scaffold` rendering role. Errors in a
template point at its definition and include the expansion chain and call site.

Rendering fragments must produce exactly the same text as rendering their
concatenation. The source-map version remains 1; `scaffold` has rank 1, below
`content` at rank 0 for columnless SyncTeX. Distinct source lines contributing
multiple content fragments to one generated line remain ambiguous. A document
without holes or escapes renders exactly as it would without interpolation.

### 10.7 Asset markers

`!asset{PATH}` is recognized only while an opaque text field is scanned. It is
not a `SpecialInvocation`, is not recognized at the start of a structural
special line, and never reaches normalization or the renderer as a compiler
node. The marker validates a non-empty relative POSIX path, rejecting NUL,
backslash, absolute, UNC, and drive paths; `.` and `..` components are allowed.
The base is the module that authored the marker, not a macro call site. A
filesystem compilation preserves the authored path in TeX and verifies that
the resolved target is a regular file.

An asset path may contain `!text{NAME}` and no other interpolation. The value is
inserted once and is not rescanned. A literal `!` in the path is reserved and is
rejected in v1; there is no path-level escape for it. `!!asset{...}` emits a literal marker;
raw-mode lines and `!|` are verbatim. In a Bundle compilation the session
resolver returns a validated cache path, while the public AST and
`TextFragment` shape remain unchanged. Each active use creates a dependency
edge; identical canonical files share one payload but not their authored
edges.

## 11. Build flags

A build flag is a boolean that lets one source produce several versions of the
same document. `!flag` declares a flag and its default; `!when` and `!unless`
keep or drop a whole payload according to one flag, or to several folded by an
`[and]`/`[or]` modifier.

That flat fold is the whole conditional language. A fold admits no
parentheses, no fold inside a fold, and no negation of one of its operands,
and a flag holds nothing but on and off. Composing whole conditionals with
`>>` is unrestricted, and is how anything deeper than a single fold is
written.

### 11.1 Declaration

~~~text
!flag{draft}{off}
!flag{handout}{on}
~~~

A declaration takes exactly two required inline groups -- the flag name and
the default, spelled `on` or `off` -- and no suite. A flag name matches
`[A-Za-z][A-Za-z0-9_-]*`. `!flag` is a top-level statement: it may not appear
inside a suite and may not be a `>>` segment. Redeclaring a name is a
validation error that names the first declaration. A declaration emits no TeX.

Declarations are collected before any conditional is read, so a `!when` may
precede the `!flag` it names.

### 11.2 Overrides

A compile may override a declared default:

~~~text
texflux compile talk.tfx -o talk.tex --flag draft --flag handout=off
~~~

`--flag NAME` turns a flag on; `--flag NAME=on` and `--flag NAME=off` are
explicit. Overriding a name that no declaration matches is an error, as is
setting one flag twice or giving a value other than on or off. An override
therefore never silently does nothing.

### 11.3 !when and !unless

~~~text
!when{draft} >> \marginpar{re-measure before the talk}

!unless{handout}::
    \pause
    @{\small} >> \textit{Ask about the tail latency here.}
~~~

Each takes one or more required inline groups, every one naming a declared
flag, and a payload written either as a `'::'` block suite or as the rest of a
`>>` composition. A `':::'` sequence suite is a validation error, as is a
missing payload, a flag no declaration matches, or the same flag listed twice.

A kept payload is spliced into the enclosing block; it is not wrapped in a
group of its own.

### 11.4 [and] and [or]

A leading optional group folds several flags into one answer:

~~~text
!when[or]{draft}{internal} >> \marginpar{re-measure before the talk}
!when[and]{notes}{handout}::
    ...
~~~

The modifier is `[and]` or `[or]`, nothing else. It is required whenever a
conditional names more than one flag -- two flags without a modifier are a
validation error rather than an implicit conjunction -- and is permitted, as a
no-op, on a single flag. Only the leading group is read as a modifier; an
optional group anywhere else is read as a flag name and rejected as one.

`!unless[X]` is the negation of the whole `!when[X]` it mirrors, so "neither"
is spelled with `[or]` and "not both" with `[and]`:

~~~text
!when[and]{a}{b}     keeps while both are on
!when[or]{a}{b}      keeps while either is on
!unless[and]{a}{b}   keeps unless both are on
!unless[or]{a}{b}    keeps unless either is on, that is while neither is
~~~

A fold cannot negate one of its operands, so `a and not b` stays a
composition, and so does any deeper combination:

~~~text
!when{a} >> !unless{b} >> \note{...}
~~~

### 11.5 Resolution

Flags are resolved before macro expansion, and conditionals are resolved
during it, so no flag construct reaches normalization or the renderer. A
conditional inside a template is resolved when the template is instantiated.

A dropped payload is never expanded and never normalized, so content that no
longer compiles can be disabled and the document still builds. An unknown
special, a macro call of the wrong arity and a misplaced `!defmacro` all go
unreported inside one.

What a dropped payload must still satisfy is every rule checked before
conditionals are resolved. Its syntax must parse; `!flag` must be a top-level
statement; `!macroimport` must be one too, and may not be a `>>` segment; and
the template rules of section 10 hold, so `!defmacro` may not be a `>>`
segment, `!each` must own its `':'` suite, and a template may contain
neither `!import`, `!macroimport`, nor `!bundleimport`. Those four are the
complete list.

Every one of them is a purely syntactic question about where a line is
written, and none of them opens a file. Content that no longer compiles, and a
dependency that no longer exists, can both still be disabled.

A conditional keeps or drops statements, not arguments. A sequence entry whose
value is dropped remains an entry, and renders as an empty argument:

~~~text
\cmd:::
    - !when{draft} >> \x
    - tail
~~~

renders `\cmd{%` / `}{%` / `tail` / `}` while `draft` is off: the entry
is still an entry, and its generated braces enclose nothing.

## 12. Modules

One file is one module. A `.tfx` file is a content module and a `.tfxm` file
is a macro-definition module. Two different constructs compose them, and they
are never collapsed into one generic import.

~~~text
!macroimport{layout.tfxm}   macro namespace dependency; produces no TeX
!import{slide.tfx}          content dependency; produces canonical AST
~~~

### 12.1 Content modules

A `.tfx` file may state raw TeX, structural syntax, `!flag`, `!defmacro`,
`!macroimport`, `!import`, `!bundleimport`, conditionals, and ordinary content. `!import`
compiles it as one independent module instance and splices the canonical AST
it produces at the import site. The callee's local flags and macro names never
enter the caller's tables, so two modules may each declare `draft` or define
`!box` without colliding.

Parsing is cached by canonical path identity, but every content import is a
distinct instance, so importing one module twice produces its content twice.

### 12.2 Macro modules

A `.tfxm` file may state only `!defmacro`, `!macroimport`, comment lines and
blank lines. `!flag`, `!when`, `!unless`, `!import`, and `!bundleimport` are errors anywhere in
one, template interiors included: a macro module declares no flags, so a
conditional inside one would read the flags of whichever content module
instantiated it. This purity is what makes macro imports order-insensitive,
cacheable, and safe in a cyclic graph.

A macro module publicly exposes exactly the macros it defines itself. Macros
it obtained through `!macroimport` are private implementation dependencies and
are not re-exported. No `public`, `private` or `export` syntax exists.

A macro module must be self-contained with respect to its own macro-import
closure: a name used in one of its templates must be a template construct, a
built-in special, a standard flow name (section 9.1), a macro it defines, or
a macro it imports. A caller's
unrelated namespace never makes an otherwise invalid macro module valid.

### 12.3 !macroimport

~~~text
!macroimport{style-v2.tfxm}
~~~

It takes exactly one required inline group, no suite, and no binding list. It
is a top-level declaration and may not be a `>>` segment, for the same reason
`!flag` may not: a macro namespace that depended on a build flag would defeat
lexical scope. Importing the same module twice in one file is an error.

Macro definitions have lexical scope. A template's names resolve in the module
that defines it, never in the caller's. So given `main.tfx` importing `A.tfxm`
which imports `core.tfxm`, `A.tfxm`'s macros may call `core.tfxm`'s, while
`main.tfx` sees only what `A.tfxm` defines. Diamond imports are therefore
isolated by construction, and two content modules may depend on different
versions of the same macro library in one document.

Because scope is lexical, a macro's identity is its defining module together
with its name. Recursion detection follows that identity, so a template
calling a macro that some other module happens to give the same name is not a
cycle. An expansion chain is spelled with bare names while it stays inside one
module, and as `name@file` once it crosses one, because a bare name no longer
identifies a macro there.

Shadowing is not a rule. A duplicate visible macro name is an error, whether
it arises between two imports, between an import and a local definition, or
against a reserved name, a built-in special name, or a standard flow name. There is no "last import wins".

Cyclic macro imports are allowed while `.tfxm` stays pure. The compiler must
not traverse such a graph indefinitely.

### 12.4 !import

~~~text
!import{slide.tfx}
!import{quiz.tfx}(answers=on)
!import{quiz.tfx}(answers=$answers, memo=off)
~~~

It takes exactly one required inline group naming the path, and at most one
trailing `(...)` binding list. It produces content, so it takes no suite and
cannot wrap a `>>` payload; `@center >> !import{a.tfx}` and
`@{} >> !import{a.tfx}` are the ways to put imported content inside a
container or a TeX group.

`!import` is valid wherever a statement is: at the top level, inside a block
suite, inside a sequence value, and inside a conditional payload. A dropped
payload's import is never compiled and its file is never opened, so content
dependency graphs may be build-configuration dependent.

Writing `!import` inside a macro template is an error, so content dependency
discovery never depends on macro expansion. An `!import` passed to a macro as
a value is valid; a template that references that value twice produces two
module instances.

Content imports may repeat, but a cycle is an error. Detection operates on the
active content-import stack, not on "has this path ever been seen".

### 12.5 Import flag bindings

A binding list configures the imported module's declared flags:

~~~text
binding-list  ::= SP* binding (SP* "," SP* binding)* SP*
binding       ::= flag-name SP* "=" SP* binding-value
binding-value ::= "on" | "off" | "$" flag-name
~~~

`$name` forwards the caller's flag of that name. No expression language is
introduced. An unbound callee flag keeps the callee's own default; there is no
same-name inheritance, so cross-module configuration is always explicit. A
`--flag` override configures the build and so reaches the root module only.

An empty binding list, a malformed binding, a callee flag no declaration
matches, a `$name` the caller does not declare, and the same callee flag bound
twice are all errors.

### 12.6 Path resolution

Both imports resolve relative to the file that writes them, never to the
parent that imported that file, which is what makes a reusable component
portable. Paths are written with `/`, must be relative, and must carry the
extension their construct requires. A module's identity, for the parse cache,
the macro-module graph, cycle detection and dependency reporting, is a
normalized filesystem path.

### 12.6.1 !bundleimport

~~~text
!bundleimport{relative/path.tfxb}{frame:3}
~~~

`!bundleimport` takes exactly two required inline groups, no suite, no binding
list, and no text interpolation. The path is a relative POSIX `.tfxb` path;
NUL, backslash, absolute, drive, and UNC paths are rejected. It is valid in
content modules and active conditional payloads, but not in a macro template or
`.tfxm` file. A dropped conditional never opens the Bundle.

A Bundle is first verified against its manifest and archive limits. Its root is
then recompiled in a private session using the stored effective flags. Imports,
macro imports, assets, and nested Bundles are resolved only through manifest
edges and archive payloads. The root canonical document is indexed by its
direct top-level `GenericInvocation` frames: `frame:N` is one-based source
order, after macro expansion and active content imports. Nested frames and
dropped frames are not indexed. The selected canonical subtree is spliced into
the caller after caller macro expansion and before caller canonicalization.

The manifest index must agree with the recompiled frame count, selector, title,
source, and span. The callee's flags and macro namespace do not leak into the
caller. Active Bundle digests form a stack; re-entering one digest is a Bundle
cycle and carries the first import location as a related location. A Bundle
reader exposes a logical source identity `tfxb:<sha256>!/<logicalPath>` and a
separate physical cache path, so external AST and diagnostics remain logical
while source maps can point at an existing materialized file.

### 12.7 Compilation order

~~~text
 1. macro, flag and macro-import form validation
 2. pure >> desugaring
 3. build flag collection, then import bindings or --flag overrides
 4. macro import resolution and lexical environment construction, each
    environment seeded with the standard flow macros of section 12.9
 5. source macro collection
 6. conditional resolution and macro expansion
 7. content and Bundle import resolution, recursively
 8. value consumption and special expansion
 9. canonical AST validation
10. renderer
~~~

Module resolution is a frontend concern. No module construct reaches
normalization or the renderer, which continues to know only canonical AST.

### 12.8 Diagnostics and provenance

Imported canonical AST keeps the callee's source spans. Generated TeX
therefore maps back to the file that actually wrote each line, and imported
content is never retargeted onto the caller's `!import` line. A `.tfxmap`
accordingly carries several sources; a document without imports still carries
exactly one.

An error raised inside an imported module keeps its own span, so inverse
search reaches the line that is broken, and names the import site in its
message. Nested imports accumulate those sites innermost first. Each import
site the message names is also carried as a related location.

An error inside a macro module names its `!macroimport` sites the same way,
because a macro module is shared between documents and which import reached
it is what locates the problem. Each macro module records the one import that
first named it, so those records form a tree and the walk up it terminates. A
level written at the error's own line contributes nothing and is stepped over,
but does not end the walk: how that file itself entered the build is still
part of the answer. Where one module is named by several imports, the
recorded site is the shallowest, which is not necessarily the earliest in
reading order.

`ModuleError` covers import forms, path resolution, cycles, macro module
purity and self-containment, macro name conflicts between modules, and flag
bindings.

### 12.9 The bundled standard macro module

The standard flow macros of section 9.1 live in one macro module that ships
with the compiler and is versioned with it. It is an ordinary pure `.tfxm`
and obeys section 12.2 in full: only `!defmacro`, comments and blank lines,
no flags, no conditionals, no content, no imports of its own.

Every macro environment the session builds is seeded from it -- the root
module, every `.tfx` reached through `!import`, and every `.tfxm` reached
through `!macroimport` -- so a macro module's own templates may call these
names too. The model is an implicit import written before a module's explicit
ones; the compiler realizes it by seeding the environment, never by placing a
synthetic `!macroimport` node in a user's AST. There is no `prelude`
statement, no `std.*` namespace, and no way to select a different one.

Seeding is not a precedence layer. A standard name may not be defined or
imported into a scope where it is already visible, so there is no
local-over-import-over-standard order and no import-order effect (sections
10.1 and 12.3).

The module is resolved inside the compiler distribution, never against the
user's project, and is read at most once per compilation. Its identity for
lexical scope is a synthetic one; its filesystem path is not part of language
semantics, does not appear in source spans, and is not a document source, so
it is absent from `.tfxmap` and from the external AST's `sources`. Template
scaffolding it contributes is retargeted onto the call site like any macro's
(section 15), and its expansion leaves no trace in canonical AST: there is
no node type for a standard flow call. A bundled module that fails to parse
or validate is an installation defect rather than a document error, and is
reported as one.

Seeding is a property of the compilation session. Module-aware compilation --
`compileText`, `compileWithMap`, `compileAst`, `texflux.diagnostics.diagnose` and
the CLI -- is the normative public behaviour; the low-level `normalize()` sees no
standard macros, and a source calling one there fails as an unknown special.
A new entry point that compiles a document must go through the session so
that the standard names behave the same everywhere.

## 13. Normalization and renderer boundary

The compiler pipeline is, with the module steps section 12.7 states in full:

~~~text
physical lines
 -> syntax AST
 -> pure >> desugaring
 -> build flag collection
 -> macro import resolution
 -> source macro collection, conditional resolution and expansion
 -> content import resolution
 -> value consumption and special expansion
 -> canonical AST validation
 -> renderer
~~~

The canonical AST is RawTex, GenericInvocation, Argument, Block, and
BraceGroup. GenericInvocation with body=None is a command; body=Block is a
named environment. BraceGroup always renders literal braces.

No ParsedInvocation, SpecialInvocation, Stack, SequenceEntry, suite mode, or
special name may reach the renderer. The renderer knows only canonical AST.
No macro definition, call, parameter reference, `!each`, flag declaration or
conditional survives expansion, so the renderer knows nothing about macros or
build flags either.

## 14. Argument brace placement

A generated required brace is laid out one way and only one way. It opens
with `{%` on its own line, so the newline behind it reaches TeX as nothing
rather than as a space token, and it closes with `}` on the line after the
value. The value's shape decides nothing: a `-` that fits on its marker line
and a `-` that spans a whole environment render identically, and no parser
state travels to the renderer to say which one it was.

~~~text
\command:::
    - short
    - @center::
        figure
~~~

~~~tex
\command{%
short
}{%
\begin{center}
figure
\end{center}
}
~~~

Where the author breaks a line therefore changes nothing about what TeX
reads, which is the same guarantee every other construct gives. The newline
before the closing brace is one space token, present in every generated
argument; an author who must remove it ends the value's last line with `%` or
writes the argument as a `+` group.

The `-` marker always means "generate one required group". In particular, a
brace-looking value is content, not syntax:

~~~text
\command:::
    - {fooo
      bar
      }
~~~

~~~tex
\command{%
{fooo
bar
}
}
~~~

Use `+` when the group itself is the argument and its delimiters must be kept.
It accepts exactly one balanced required, optional, or overlay group, including
multiline groups, and copies the complete raw text without header scanning:

~~~text
\command:::
    + {fooo
      bar
      }
    + [opt]
    + <2->
~~~

~~~tex
\command{fooo
bar
}[opt]<2->
~~~

Multiple groups, an unbalanced group, or trailing tokens after a `+` group are
ParseErrors. The `+` group's delimiter is not generated a second time, and its
contents remain opaque TeX.

A generated brace always closes on its own line, so a comment inside the
value can never reach it. What follows an authored `+` group is another
matter: its delimiters are the author's, but where the next argument starts is
not, and a `%` on the group's last rendered line would comment that argument
out. Such a group therefore keeps its newline and the next argument begins
underneath; without a `%` the next argument follows the group on its line. An
escaped `\%` is not a comment, and the rule reads the rendered text rather
than the author's intent. Nothing is reported: the layout is the whole answer.

The newline after `\begin{env}` is untouched, because an environment body is
read in a vertical context where the space token is discarded.

## 15. Source spans and SyncTeX

Every major syntax/canonical node and diagnostic has file, one-based line, and
one-based column in a half-open SourceSpan. The implementation retains
provenance for:

- double-colon and triple-colon suite markers
- each sequence `-` / `+` marker and explicit group kind
- each sequence value block boundary
- @{} / @{RAW_TEX} headers
- @:: / @::: headers
- stack segments and closed terminals
- sequence/block value boundaries
- item metadata
- generated begin/end, argument braces, literal braces, and special expansion

Macro expansion retains provenance. Output substituted for a `!param`
reference keeps the span of the value passed at the call site, so inverse
search reaches the text the author wrote. Output cloned from a template is
retargeted onto the macro call site rather than the definition site. A macro
definition's own syntax or validation error points at the definition site, and
an expansion error names the offending template line, the expansion chain, and
the call site.

Rendering records generated spans and source spans. Source-map serialization and
SyncTeX remapping remain part of v1 and must not be removed or bypassed.

A `.tfxmap` records one entry per source file the compilation read: the root
always, and every imported module that contributed at least one mapping. The
root is id 0, so a document without imports serializes exactly what it did
before modules existed.

## 16. Errors and non-goals

ParseError covers malformed physical structure, headers, groups, suffixes,
indentation, and sequence markers. ValidationError covers invalid value
consumption, special/container contracts, and macro definition contracts,
including a definition that collides with a standard flow macro name.
DirectiveError covers unknown specials. MacroExpansionError covers macro
expansion: arity, unbound or misused parameters, shadowing, and recursion.
ModuleError covers the module system: import forms, path resolution, content
import cycles, macro module purity and self-containment, macro name conflicts
between modules, and import flag bindings. BundleError covers asset markers,
archives, Bundle manifests, selectors, cache materialization, and Bundle import
cycles. All diagnostics point to the
originating span. Two exceptions carry no span because neither describes a
place in a document. FlagError covers a command-line override that no
declaration matches. InternalError covers a broken TeXFlux installation --
today, the bundled standard macro module of section 12.9 failing to read or
validate -- and is a RuntimeError rather than a TeXFluxError, because nothing
the source says can cause it or fix it.

Every diagnostic carries a stable code and zero or more related locations. A
code is one uppercase letter for the diagnostic's kind followed by three
digits; it identifies the place a diagnostic is raised rather than its
wording, and is never renumbered or reused. A related location is a second
span the message already names in prose -- a first declaration, a macro
call site, an import that pulled a module in -- carried as structured data
beside the diagnostic. Diagnostics print as
`file:line:column: kind: message [CODE]`. The `texflux check` command and
the `texflux.diagnose` API report the same diagnostics as the
`texflux-diagnostics` version 1 format, whose members doc/diagnostics.md
defines.

v1 does not include:

- TeX or template parsing
- package, command, or environment discovery
- TeX argument-count or semantic validation
- automatic escaping
- variables, expressions, arithmetic, or pattern matching
- conditions beyond the declared on/off build flags of section 11: no
  comparisons, no arithmetic, and no value a flag can hold but on and off. A
  conditional folds a flat list of flag names with one [and]/[or]; that fold
  admits no parentheses, no fold inside a fold, and no negation of a single
  operand. Composing whole conditionals with >> is how anything deeper is
  written, and is not restricted
- source-to-source textual macros (m4/cpp), rescanning inserted text, token
  pasting, generated text sent back to the parser, and !splice
- optional, default, or keyword macro parameters, and macro recursion
- YAML/embedded scripting authoring
- implicit extension loading
- a package registry, version resolution, lockfiles, or remote module fetching
- a standard library. The bundled module of section 12.9 holds the five
  backend-independent flow controls and nothing else: presentation helpers,
  themes and layouts are ordinary user `.tfxm` modules
- public/private/export declarations, qualified macro names, or re-exports
- module parameters beyond the declared on/off build flags, dynamic import
  paths, and imports generated from macro templates
- automatic isolation of raw TeX macros across module boundaries
- renderer backend frameworks
- runtime dependencies
- structural trailing comments
- pipe-based suite markers such as `::|` and `:: |`
- any structural token TeX prose also writes. Every TeXFlux operator that can
  appear at the end or in the middle of a line is a repeated character --
  `::`, `:::`, `>>` -- so a single `:`, `>` or `|` always belongs to TeX. A
  future construct may not spend a one-character operator on the line

## 17. Conceptual grammar

~~~text
document          ::= statement*

suite-suffix      ::= sequence-suffix | block-suffix
sequence-suffix   ::= ":::" SP*
block-suffix      ::= "::" SP*

sequence-suite    ::= sequence-entry*
sequence-entry   ::= ("-" SP* generated-block)
                    | ("+" SP* explicit-group)
generated-block   ::= first-line continuation-line*
explicit-group   ::= one balanced "{...}", "[...]", or "<...>"
                    with optional indented raw continuation lines
first-line        ::= inline-value | structural-expression
continuation-line ::= indented TeXFlux line

structural-expression
                  ::= segment (stack-separator segment)* suite-suffix?
stack-separator   ::= SP+ ">>" (SP+ | SP* NEWLINE SAME-INDENT)

segment           ::= command-segment
                    | named-container-segment
                    | brace-container-segment
                    | transparent-container-segment
                    | special-segment

command-segment  ::= "\\" command-name group*
named-container-segment
                  ::= "@" environment-name group*
brace-container-segment
                  ::= "@{" balanced-raw-tex "}"
transparent-container-segment
                  ::= "@"
special-segment  ::= "!" special-name group* binding-list?

flag-declaration  ::= "!flag" "{" flag-name "}" "{" ("on" | "off") "}"
combinator        ::= "[" ("and" | "or") "]"
conditional       ::= ("!when" | "!unless") combinator? "{" flag-name "}"+
                      (block-suffix | SP+ ">>" SP+ segment ...)

macro-import      ::= "!macroimport" "{" module-path "}"
content-import    ::= "!import" "{" module-path "}" binding-list?
binding-list      ::= "(" SP* binding (SP* "," SP* binding)* SP* ")"
binding           ::= flag-name SP* "=" SP* ("on" | "off" | "$" flag-name)

macro-definition  ::= "!defmacro" "{" macro-name "}" parameter* block-suffix
parameter         ::= "{" ("..." )? parameter-name "}"
macro-call        ::= special-segment
param-reference   ::= "!param" "{" parameter-name "}"
each-construct    ::= "!each" "{" parameter-name "}" "{" parameter-name "}"
                      block-suffix
bundle-import     ::= "!bundleimport" "{" bundle-path "}" "{" frame-selector "}"
~~~

This grammar is conceptual; item metadata and balanced raw groups are scanned by
the handwritten parser. A sequence entry continues until the next sibling `-`
or `+` marker. Its normative distinctions are generated required values from
`-`, authored groups from `+`, the double-colon block, and suffix-less closed
values. A lone colon is not a suffix, so a command line ending in one is raw
TeX; a command segment carrying a block-suffix requires a suite body
(section 3).

## 18. Executable examples

Exact-output examples live in [tests/golden](tests/golden) and
[examples](examples). [tests/d/texflux_tests/golden.d](tests/d/texflux_tests/golden.d) checks their
output byte for byte. Argument-brace layout is defined in section 14.
