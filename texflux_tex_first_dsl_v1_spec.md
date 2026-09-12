# TeXFlux TeX-first DSL v1 normative specification

## 0. Status

This document is normative for the TeXFlux v1 language. The implementation,
tests, examples, README, and doc/dsl.md must agree with it.

TeXFlux is a Python 3.11+ TeX-first preprocessor. It preserves raw TeX and
provides only structural syntax for containers, values, itemize, and source
provenance. It does not parse TeX or infer command/environment signatures.

The fixed mental model is:

~~~text
\ = TeX command
@ = structural container
! = TeXFlux special
~~~

The fixed suite model is:

~~~text
no suffix = one already-closed RHS value
:         = one block value per '-'; the next sibling '-' is its boundary
: |       = one multiline block value

A value that starts on the next line renders with its braces on their own
lines; a value that starts where its marker is renders with braces that hug
it. ': |' and a bare '-' take the first form, '- value' the second.
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
@:
@{}
@{RAW_TEX}
~~~

@{RAW_TEX} scans balanced braces and preserves RAW_TEX as leading opaque TeX.
@ and @: never create a TeX wrapper. @{} and @{RAW_TEX} always create a
literal TeX brace group when completed.

Named environment names are not looked up and may include a trailing star and
other non-structural punctuation. Unknown names render normally. Unknown
special names fail with DirectiveError.

## 2. Physical lines and indentation

CRLF and CR are normalized to LF. Tabs are rejected. Four ASCII spaces are the
structural indentation unit. A structural child must be at the suite base;
extra indentation is retained only for raw TeX lines. Structural candidates at
invalid indentation are parse errors.

Blank lines in a sequence suite are separators and do not create values. Blank
lines inside a sequence value block or a block suite are content. A trailing
blank run is returned to the enclosing block when the suite closes.

At a structural position, @@ emits one raw @ and does not create a directive.

## 3. Header scanner

The scanner treats group contents as opaque and balances:

~~~text
{...} required
[...] optional
<...> overlay
~~~

Only depth-zero trailing colon and space-separated >> are structural. Group
internal colon, pipe, and >> remain raw text. Structural header trailing TeX
comments are unsupported.

A suite suffix is exactly a colon optionally followed by spaces and one pipe.
The pipe is optional whitespace-separated syntax, not a raw scalar marker:

~~~text
:
:|
: |
~~~

Any trailing token after this suffix is a ParseError. A pipe elsewhere in raw
TeX is not syntax.

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
- SequenceEntry(value, marker_span, span)
- Block(nodes, span)

Invocation kinds include command, named environment, brace container, and
transparent container. SuiteMode is Sequence or Block. Every parsed
SequenceEntry value is a Block whose first line is the marker payload and whose
remaining lines continue until the next sibling marker.

A suffix-less Stack has no suite. A suffix-bearing Stack stores the suffix suite
on its rightmost segment after desugaring.

## 5. Sequence and block values

### 5.1 Sequence mode

A plain colon suite contains explicit sequence entries. A structured command
sequence must contain at least one entry:

~~~text
\foo:
    - A
    - B
~~~

Direct raw child lines such as A and B without - are invalid. Each '-' starts
one block value. Text after the marker is its first line; subsequent lines
indented beyond the sequence base belong to that value until the next sibling
marker. A bare '-' is therefore the multiline spelling.

~~~text
\foo:
    - short
      long line 1
      long line 2
    - @center: |
        BODY
~~~

The sequence value blocks and structural entry suites use the normal TeXFlux
parser. They are not opaque YAML scalars. The marker span and value span are
retained. The spelling "- |" is not a block marker; it starts a value whose
first line is the raw character "|".

### 5.2 Block mode

A colon-pipe suite is exactly one block value:

~~~text
\foo: |
    A
    @center: |
        B
~~~

Its content is parsed as a normal TeXFlux block, including raw TeX, containers,
specials, stacks, and blank lines. An empty block suite is valid.

## 6. Value consumption

### 6.1 Command

A structured command consumes all suite values as required arguments. Compact
header groups are emitted first.

~~~text
\foo{COMPACT}:
    - A
    - B
~~~

renders as:

~~~tex
\foo{COMPACT}{A}{B}
~~~

A block suite produces one long required argument:

~~~text
\foo: |
    A
    B
~~~

renders as:

~~~tex
\foo{
A
B
}
~~~

A structural sequence value is wrapped in one required argument. Literal brace
containers are not absorbed by that outer argument:

~~~text
\foo:
    - @{}: |
        A
~~~

renders conceptually as:

~~~tex
\foo{
{
A
}
}
~~~

A normal command line without suite or stack syntax remains raw TeX. A command
used as a closed stack terminal or structural sequence value is normalized as a
closed canonical command value.

### 6.2 Named environment

A block suite is the environment body. Compact groups remain begin arguments.

For a sequence suite, all values except the final value become required block
arguments and the final value becomes the body:

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY
~~~

renders as:

~~~tex
\begin{myenv}{ARG1}{ARG2}
BODY
\end{myenv}
~~~

The final block value contributes its canonical nodes as the body. An empty
sequence environment is a validation error. An empty body is written as an
empty @: | value.

## 7. Anonymous containers

@: | is a transparent one-block value and emits only its body. A transparent
sequence form combines its values into one composite body without a wrapper.
An empty transparent sequence is valid and emits nothing. An empty sequence
for a literal brace container is valid and emits an empty literal brace group.
Use `@: |` or `@{}: |` when the empty block form should be explicit.

@{}: | and @{RAW_TEX}: | emit literal brace groups. RAW_TEX is emitted as
leading opaque TeX inside the braces. Literal braces remain in all contexts,
including command argument context.

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
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

A suffix-less stack whose rightmost segment is an open container is invalid:

~~~text
@foo >> @center
~~~

All stack segments have complete prefixes. Stack semantics do not depend on
template or TeX knowledge.

## 9. Specials

Special handlers are in-process AST-to-AST transformations. They return
canonical node tuples, never TeX strings. Unknown specials fail.

The v1 built-ins are:

- !items, which accepts a sequence suite and converts item values to itemize.
- !vpad, which accepts one or two required inline groups and a block suite.
- !off, which ignores one required inline group and splices its block suite.
- !drop, which accepts no groups and discards its block suite.

!items preserves overlay, optional label, continuation, nested-list, and raw
item behavior. Its canonical suffix is plain colon. A bare '-' followed by
deeper lines is a multiline raw item value, with the next sibling '-' as its
boundary.

~~~text
!items:
    - A
    -<2->[Label] item
    -
        multiline item
~~~

!vpad's canonical form is:

~~~text
!vpad{-1em}{2em}: |
    contents
~~~

It emits a canonical vspace command before the normalized body and, when the
second group exists, after it. A sequence suite, invalid group kind/count, or
missing suite is a validation error.

!off removes one wrapper-like fragment from a composition. Its required inline
group is opaque and ignored, while its block payload is normalized and spliced
in place:

~~~text
\fuga >> !off{\foo{a}{b}} >> \hoge
~~~

is equivalent to `\fuga >> \hoge`. !off requires exactly one required inline
group and a block payload, supplied either by `': |'` or by the rest of a `>>`
composition.

!drop emits no nodes and does not normalize its block payload:

~~~text
!drop >> \hoge >> \fuga
~~~

The example emits no TeX content. !drop accepts no groups and requires a block
payload, supplied either by `': |'` or by the rest of a `>>` composition. A
sequence suite, missing payload, or invalid group shape for either construct is
a validation error.

The old !block, !arg, and !body constructs are removed and are not aliases.

## 10. Source macros

A source macro is a structural AST macro, not a textual one. `!defmacro`
stores one template block of syntax AST; a call binds its values to the
template's parameters and instantiates a clone of it. TeXFlux never
substitutes source text, never re-parses rendered TeX, and never interpolates
a parameter into a raw TeX group.

### 10.1 Definition

~~~text
!defmacro{name}{param}{...rest}: |
    TEMPLATE
~~~

A definition takes a `: |` block suite; a sequence suite is a validation
error. The first required group is the macro name, which uses the special-name
grammar so the macro is callable as `!name`. Every later required group is a
parameter. Definitions emit no TeX. Lines around a definition, including blank
lines, remain ordinary content.

Definitions are collected from the top level only. A `!defmacro` anywhere else,
including inside another macro's template, is a validation error, so macros
cannot be defined dynamically. Every definition is collected before any call is
expanded, which makes forward references valid:

~~~text
!foo >> \TextCA{A}

!defmacro{foo}{body}: |
    @{\small} >> !param{body}
~~~

Parameter names match `[A-Za-z_][A-Za-z0-9_-]*`. A duplicate parameter, a
macro name that is reserved (`defmacro`, `param`, `each`), a name already
taken by a built-in special, and a duplicate macro definition are all
validation errors reported at the definition site.

### 10.2 Value binding

A macro call uses the existing value syntax; there is no macro-specific call
form. Values bind left to right: every required compact group first, then the
values its suite produces.

~~~text
!foo{A}{B}          two inline values
!foo: |             one block value
!foo:               one block value per '-'
!foo >> VALUE       the closed stack payload as one value
~~~

`!foo{A} >> \bar{B}` therefore binds `[A, \bar{B}]`. Optional and overlay
groups are not values and are rejected on a macro call.

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

Because a group's contents stay opaque raw TeX, a parameter cannot be
interpolated into one. The same opacity applies to an `!items` suite, whose
item text is raw, so a `!param` written there stays literal. Structural form
is used instead:

~~~text
@infobox:
    - !param{title}
    - !param{body}
~~~

### 10.4 !each

~~~text
!each{rest-param}{item}: |
    TEMPLATE
~~~

`!each` walks a rest parameter's values in source order, binds each one to
`item`, instantiates the template per iteration, and concatenates the
iterations into the enclosing block. An empty sequence produces nothing. It
requires a `: |` suite, is valid only inside a template, and its first group
must name a rest parameter. An item name that shadows a bound parameter is a
macro error. `!each` may nest; each iteration binds its own item.

`!each` may be the rightmost segment of a stack when the stack's suffix is
`: |`, because that segment keeps the suffix and so still writes its own
template:

~~~text
@{\bfseries} >> !each{items}{item}: |
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

!unless{handout}: |
    \pause
    @{\small} >> \textit{Ask about the tail latency here.}
~~~

Each takes one or more required inline groups, every one naming a declared
flag, and a payload written either as a `': |'` suite or as the rest of a `>>`
composition. A sequence suite is a validation error, as is a missing payload,
a flag no declaration matches, or the same flag listed twice.

A kept payload is spliced into the enclosing block; it is not wrapped in a
group of its own.

### 11.4 [and] and [or]

A leading optional group folds several flags into one answer:

~~~text
!when[or]{draft}{internal} >> \marginpar{re-measure before the talk}
!when[and]{notes}{handout}: |
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
statement; and the two stack-form rules of section 10 hold, so `!defmacro` may
not be a `>>` segment and `!each` must own its `': |'` suite. Those three are
the complete list.

A conditional keeps or drops statements, not arguments. A sequence entry whose
value is dropped remains an entry, and renders as an empty argument:

~~~text
\cmd:
    - !when{draft} >> \x
    - tail
~~~

renders `\cmd{}{tail}` while `draft` is off. An `!items` entry payload is raw
item text, so no `!` construct -- a conditional included -- is read inside one;
a conditional list is written by wrapping the whole `!items`.

## 12. Normalization and renderer boundary

The compiler pipeline is:

~~~text
physical lines
 -> syntax AST
 -> pure >> desugaring
 -> build flag collection
 -> source macro collection, conditional resolution and expansion
 -> value consumption and special expansion
 -> canonical AST validation
 -> renderer
~~~

The canonical AST is RawTex, GenericInvocation, Argument, Block, BraceGroup,
and Item. GenericInvocation with body=None is a command; body=Block is a named
environment. BraceGroup always renders literal braces.

No ParsedInvocation, SpecialInvocation, Stack, SequenceEntry, suite mode, or
special name may reach the renderer. The renderer knows only canonical AST.
No macro definition, call, parameter reference, `!each`, flag declaration or
conditional survives expansion, so the renderer knows nothing about macros or
build flags either.

## 13. Argument brace placement

A sequence value's own shape decides where its generated braces go. A value
confined to one line renders with braces that hug it; a value that needs more
than one line renders with the braces on their own lines. Nothing is marked:
the compact case stays compact and the structural case stays readable.

~~~text
\command:
    - short
    - @center: |
        figure
~~~

~~~tex
\command{short}{
\begin{center}
figure
\end{center}
}
~~~

An author who wants a different layout writes the braces instead. When a
value is raw TeX that begins with `{` and ends with the matching `}`, those
braces are the argument's own and the text is copied verbatim, so each brace
sits exactly where it was written:

~~~text
\command:
    - {fooo
      bar
      }
    - {
      a
      c}
~~~

~~~tex
\command{fooo
bar
}{
a
c}
~~~

The scan only locates the matching brace; it never interprets the contents.
A value that does not scan as one balanced group keeps a generated pair, so a
mistake shows up as a visible extra brace rather than as a changed argument
count. A value that must itself be a TeX group is written over several lines,
which takes the generated form. The rule reads the value after macro
expansion, so a macro that expands to one balanced group supplies the
argument's braces in exactly the same way.

A hugged closing brace is unsafe after a TeX comment, which would swallow it.
When the value's last rendered line is entirely a comment, the closing brace
keeps its own line and TeXFlux warns. When that line merely contains an
unescaped `%`, TeXFlux warns and leaves the brace hugged, because moving it
would require rewriting the author's TeX. An escaped `\%` is not a comment.
Warnings name the value's span and never fail the compilation.

## 14. Source spans and SyncTeX

Every major syntax/canonical node and diagnostic has file, one-based line, and
one-based column in a half-open SourceSpan. The implementation retains
provenance for:

- colon and pipe suite markers
- each sequence -
- each sequence value block boundary
- @{} / @{RAW_TEX} headers
- @: headers
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

## 15. Errors and non-goals

ParseError covers malformed physical structure, headers, groups, suffixes,
indentation, and sequence markers. ValidationError covers invalid value
consumption, special/container contracts, and macro definition contracts.
DirectiveError covers unknown specials. MacroExpansionError covers macro
expansion: arity, unbound or misused parameters, shadowing, and recursion. All
diagnostics point to the originating span. FlagError is the one exception: it
covers a command-line override that no declaration matches, which has no span
in the document to point at.

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
- textual macros, parameter interpolation into raw TeX, and !splice
- optional, default, or keyword macro parameters, and macro recursion
- YAML/Python embedded authoring
- implicit extension loading
- renderer backend frameworks
- runtime dependencies
- structural trailing comments
- block-scalar suffixes other than : |

## 16. Conceptual grammar

~~~text
document          ::= statement*

suite-suffix      ::= ":" SP* ("|")?

sequence-suite    ::= sequence-entry*
sequence-entry   ::= "-" SP* sequence-block
sequence-block    ::= first-line continuation-line*
first-line        ::= inline-value | structural-expression
continuation-line ::= indented TeXFlux line

structural-expression
                  ::= segment (SP+ ">>" SP+ segment)* suite-suffix?

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
special-segment  ::= "!" special-name group*

block-suffix      ::= ":" SP* "|"

flag-declaration  ::= "!flag" "{" flag-name "}" "{" ("on" | "off") "}"
combinator        ::= "[" ("and" | "or") "]"
conditional       ::= ("!when" | "!unless") combinator? "{" flag-name "}"+
                      (block-suffix | SP+ ">>" SP+ segment ...)

macro-definition  ::= "!defmacro" "{" macro-name "}" parameter* block-suffix
parameter         ::= "{" ("..." )? parameter-name "}"
macro-call        ::= special-segment
param-reference   ::= "!param" "{" parameter-name "}"
each-construct    ::= "!each" "{" parameter-name "}" "{" parameter-name "}"
                      block-suffix
~~~

This grammar is conceptual; item metadata and balanced raw groups are scanned by
the handwritten parser. A sequence-block continues until the next sibling '-'.
Its normative distinctions are the explicit '-' block sequence, the single
: | block, and suffix-less closed values.

## 17. Representative goldens

~~~text
\foo:
    - A
    - B
~~~

~~~tex
\foo{A}{B}
~~~

~~~text
\foo: |
    A

    B
~~~

~~~tex
\foo{
A

B
}
~~~

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY1
        BODY2
~~~

~~~tex
\begin{myenv}{ARG1}{ARG2}
BODY1
BODY2
\end{myenv}
~~~

~~~text
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

The generated structure is frame -> center -> literal brace group -> BODY.
