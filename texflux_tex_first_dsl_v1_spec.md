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
\foo{COMPACT}{
A
}{
B
}
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
\begin{myenv}{
ARG1
}{
ARG2
}
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

The old !block, !arg, and !body constructs are removed and are not aliases.

## 10. Normalization and renderer boundary

The compiler pipeline is:

~~~text
physical lines
 -> syntax AST
 -> pure >> desugaring
 -> value consumption and special expansion
 -> canonical AST validation
 -> renderer
~~~

The canonical AST is RawTex, GenericInvocation, Argument, Block, BraceGroup,
and Item. GenericInvocation with body=None is a command; body=Block is a named
environment. BraceGroup always renders literal braces.

No ParsedInvocation, SpecialInvocation, Stack, SequenceEntry, suite mode, or
special name may reach the renderer. The renderer knows only canonical AST.

## 11. Source spans and SyncTeX

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

Rendering records generated spans and source spans. Source-map serialization and
SyncTeX remapping remain part of v1 and must not be removed or bypassed.

## 12. Errors and non-goals

ParseError covers malformed physical structure, headers, groups, suffixes,
indentation, and sequence markers. ValidationError covers invalid value
consumption and special/container contracts. DirectiveError covers unknown
specials. All diagnostics point to the originating span.

v1 does not include:

- TeX or template parsing
- package, command, or environment discovery
- TeX argument-count or semantic validation
- automatic escaping
- variables, expressions, loops, conditions, or source macros
- YAML/Python embedded authoring
- implicit extension loading
- renderer backend frameworks
- runtime dependencies
- structural trailing comments
- block-scalar suffixes other than : |

## 13. Conceptual grammar

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
~~~

This grammar is conceptual; item metadata and balanced raw groups are scanned by
the handwritten parser. A sequence-block continues until the next sibling '-'.
Its normative distinctions are the explicit '-' block sequence, the single
: | block, and suffix-less closed values.

## 14. Representative goldens

~~~text
\foo:
    - A
    - B
~~~

~~~tex
\foo{
A
}{
B
}
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
\begin{myenv}{
ARG1
}{
ARG2
}
BODY1
BODY2
\end{myenv}
~~~

~~~text
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

The generated structure is frame -> center -> literal brace group -> BODY.
