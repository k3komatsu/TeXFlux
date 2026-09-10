# Beamercraft

Beamercraft is a small TeX-first preprocessor for LaTeX and Beamer. It removes
structural boilerplate such as begin/end pairs, nested environments, long
brace arguments, and repeated itemize markup without replacing TeX semantics.

The mental model is intentionally small:

~~~text
\ = TeX command
@ = TeX environment
! = Beamercraft special
~~~

Ordinary source lines are raw TeX. Beamercraft does not parse, escape, or
normalize TeX content.

## Requirements and installation

- Python 3.11 or newer
- No runtime dependencies
- A LaTeX installation is optional

From a checkout:

~~~bash
python -m pip install .
~~~

For development:

~~~bash
python -m pip install -e .
~~~

## Quick start

Write a .bmc file:

~~~text
@frame{Hello, Beamercraft}:
    \vspace{-.5em}
    !items:
        - TeX remains raw
        -<2-> Structural boilerplate is shortened
~~~

Compile it:

~~~bash
beamercraft slides.bmc -o slides.tex
~~~

The module form is also available:

~~~bash
python -m beamercraft slides.bmc -o slides.tex
~~~

Use source comments when inspecting generated TeX:

~~~bash
beamercraft slides.bmc -o slides.tex --source-comments
~~~

Beamercraft compiles the complete input before writing the output. A failed
compile therefore leaves an existing output file unchanged. Input and output
must be different paths.

## Syntax at a glance

Normal DSL nesting uses four ASCII spaces.

- \foo{A} — raw TeX command, emitted unchanged
- @foo{A}: with a suite — environment named foo
- @foo{A} without a suite — syntax error
- \foo: with a suite — structured command; the whole suite is one long argument
- !block: — one actual TeX brace group
- !items: — itemize sugar
- !vpad{before}{after}: — vertical padding around a suite; `after` is optional
- \foo >> @bar >> !block: — pure nested-suite desugaring
- @@foo — raw @foo at a structural position

The top-level trailing colon is reserved by Beamercraft. Thus
\textbf{注意}: is a structured-command header and requires an indented suite.
When a depth-0 colon follows a structural segment, it is reserved immediately;
the colon must be the final non-space token or the line is a syntax error.
Malformed backslash headers that end in a depth-0 colon are also structural
candidates and produce a syntax error. Therefore `\textbf{注意}: 本文` and
`\verb|x|:` are not raw TeX in v1. Spaces before a terminal colon are accepted.
Group-internal colons and >> remain raw group content.

## Commands and environments

TeX commands normally stay unchanged:

~~~text
\vspace{-1em}
\headuline{CA}{タイトル}
\includegraphics[width=.8\textwidth]{fig.pdf}
\TextCA{foo}
~~~

An environment uses @ and a suite:

~~~text
@center:
    BODY
~~~

~~~tex
\begin{center}
BODY
\end{center}
~~~

Environment names are not looked up. Names such as @align*: and unknown names
are accepted when their structure is valid.

## Structured command arguments

The complete indented suite of a structured command is one required long
argument. Every statement in that suite is rendered in order inside the same
argument:

~~~text
\foo:
    \bar
    @baz:
        BODY
~~~

~~~tex
\foo{
\bar
\begin{baz}
BODY
\end{baz}
}
~~~

Compact groups remain before the one suite argument:

~~~text
\foo{A}:
    X
    Y
~~~

~~~tex
\foo{A}{
X
Y
}
~~~

!block is still a literal brace group. It is not absorbed by the command
argument, so using it in an implicit command suite intentionally produces
nested braces:

~~~text
\foo:
    !block:
        \small
        Local TeX scope
~~~

~~~tex
\foo{
{
\small
Local TeX scope
}
}
~~~

!block always creates its own brace group, including when it appears inside a
command's long argument.

## Vertical padding

`!vpad` emits a leading `\vspace` and, when a second group is present, a
trailing `\vspace` around its suite:

~~~text
!vpad{-1em}{2em}:
    contents
~~~

~~~tex
\vspace{-1em}
contents
\vspace{2em}
~~~

The second required inline group is optional. With one group, only the leading
spacing is emitted. `!vpad` can also be used as a `>>` segment, for example
`@frame{Title} >> !vpad{-.7em} >> \singlecolumn[.11]:`.

## Explicit fallback forms

Use !arg only when a command needs multiple long arguments. Each explicit
!arg contributes one required argument:

~~~text
\foo:
    !arg:
        ARG1
    !arg:
        ARG2
~~~

!arg{...} is also supported as one inline required argument, and inline and
block forms may be mixed in source order. Once an explicit !arg appears,
ordinary suite statements are not allowed alongside it.

An environment can make long arguments and its body explicit:

~~~text
@myenv:
    !arg:
        VERY LONG ARG
    !body:
        Environment body
~~~

The direct children of an explicit command must all be !arg. The direct
children of an explicit environment must be !arg or !body; !body is optional,
unique, and last. Implicit and explicit modes cannot be mixed.

!arg around !block intentionally produces two brace groups:

~~~text
\foo:
    !arg:
        !block:
            A
~~~

~~~tex
\foo{
{
A
}
}
~~~

## Stacking and items

>> is only structural sugar. Every segment carries its own prefix:

~~~text
@frame{Title} >> @center >> !items:
    -<1->[A] First item
    -<2-> Second item
~~~

This becomes nested environment/itemize AST before rendering. It is not a
renderer feature and does not impose a special final-segment rule.

The !items mini-grammar keeps item text raw and supports overlays, optional
labels, continuation lines, and nested lists:

~~~text
!items:
    -<2->[A] first line
      continuation
        - nested item
~~~

Continuation lines use at least two spaces beyond the item depth; nested list
levels use four spaces.

## Python API

~~~python
from beamercraft import compile_text

source = r"""@frame{API example}:
    Body in raw TeX
"""
tex = compile_text(source, filename="slides.bmc")
~~~

The pipeline stages are also available:

~~~python
from beamercraft import normalize, parse, render

document = normalize(parse(source, filename="slides.bmc"))
tex = render(document)
~~~

The renderer accepts canonical AST only. Syntax-only invocation, special, and
stack nodes are removed or expanded before rendering.

## What Beamercraft does not do

Beamercraft deliberately does not parse TeX, discover LaTeX packages, validate
command argument counts, escape user text, provide variables or expressions,
or load user-defined plugins implicitly. TeX semantics remain the responsibility
of the TeX toolchain.

The in-process special registry is an AST-to-AST extension point. A future
directive can turn !result into canonical nodes without returning a TeX string.

## Examples

The [examples/](examples/) directory contains runnable sources and exact output
for the small examples:

- [basic.bmc](examples/basic.bmc) — raw TeX, an environment, and items
- [structured.bmc](examples/structured.bmc) — explicit arguments, block,
  and environment body
- [stacked-items.bmc](examples/stacked-items.bmc) — pure stacking and nested items
- [content.bmc](examples/content.bmc) — converted real-world Beamer content
- [content.tex](examples/content.tex) — original TeX reference

Compile a small example from the repository root:

~~~bash
PYTHONPATH=src python3 -m beamercraft examples/basic.bmc -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

Compile the converted content example:

~~~bash
PYTHONPATH=src python3 -m beamercraft examples/content.bmc -o /tmp/content.generated.tex
~~~

## Development

Run the complete suite:

~~~bash
PYTHONPATH=src python3 -m unittest discover -v
~~~

The optional LaTeX integration test skips when pdflatex is unavailable. The
normative language definition is in
[beamercraft_tex_first_dsl_v1_spec.md](beamercraft_tex_first_dsl_v1_spec.md),
and the implementation phases are in [plan.md](plan.md).
