# TeXFlux

TeXFlux is a small TeX-first preprocessor for LaTeX and Beamer. It removes
structural boilerplate without parsing or changing TeX semantics.

The v1 language has three prefixes:

~~~text
\ = TeX command
@ = structural container
! = TeXFlux special transformation
~~~

Ordinary TeX is opaque. TeXFlux uses four ASCII spaces for structural
indentation, keeps source spans, and has no runtime dependencies.

## Installation and CLI

TeXFlux requires Python 3.11 or newer:

~~~bash
python3 -m pip install .
texflux compile slides.tfx -o slides.tex
~~~

The generated .tex.tfxmap file contains source provenance. To use SyncTeX,
run the TeX engine with SyncTeX enabled and remap the result:

~~~bash
latexmk -pdf -synctex=1 slides.tex
texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
~~~

LaTeX is optional and is never a TeXFlux runtime dependency.

## Syntax at a glance

The suffix determines how the right-hand side produces values:

~~~text
suffix absent = an already-closed value
:            = one block value per '-'; the next sibling '-' is its boundary
: |          = one multiline block value
~~~

Commands consume every sequence value as a required argument:

~~~text
\foo{COMPACT}:
    - A
    - B
~~~

~~~tex
\foo{COMPACT}{
A
}{
B
}
~~~

Use : | for one multiline argument or an environment body:

~~~text
\foo: |
    A
    @center: |
        B

@frame{Title}: |
    Hello
~~~

~~~tex
\foo{
A
\begin{center}
B
\end{center}
}
\begin{frame}{Title}
Hello
\end{frame}
~~~

An environment sequence consumes all but its last value as required arguments;
the last value is its body:

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY
~~~

@: is a transparent container. @{RAW_TEX}: | is a literal TeX brace container
and always emits its own braces:

~~~text
@{\small}: |
    BODY

\foo:
    - @{}: |
        A
~~~

~~~tex
{
\small
BODY
}
\foo{
{
A
}
}
~~~

>> composes complete prefix-bearing segments. A suffix is optional when the
rightmost segment is already closed:

~~~text
@center >> \includegraphics{fig.pdf}
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

The built-in specials are:

- !items: converts the generic sequence into itemize; overlays, labels,
  continuation lines, nested lists, and bare '-' multiline item values are
  supported.
- !vpad{before} or !vpad{before}{after}: | inserts \vspace around a block
  value.

The former !block, !arg, and !body constructs are removed. Use @{}, generic
sequence entries, and the last-value environment rule instead.

## Python API

~~~python
from texflux import compile_text, normalize, parse, render

source = """@frame{API example}: |
    Body in raw TeX
"""
tex = compile_text(source, filename="slides.tfx")
assert tex == render(normalize(parse(source, filename="slides.tfx")))
~~~

The renderer accepts canonical AST only; syntax-only stack, suite, and special
nodes are normalized before rendering.

## Examples

The examples/ directory contains source/output pairs:

- basic.tfx — raw TeX, a block environment, and items
- structured.tfx — sequence values and literal groups
- stacked-items.tfx — closed stacking and nested items
- content.tfx — a converted real-world Beamer content example

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/basic.tfx -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

The user-facing language reference is doc/dsl.md; the normative definition is
texflux_tex_first_dsl_v1_spec.md.

## Non-goals

TeXFlux does not parse TeX, discover packages or command signatures, escape
text, provide variables/expressions, or load user plugins implicitly. Specials
are an in-process AST-to-AST extension point.

## Development

~~~bash
PYTHONPATH=src python3 -m unittest discover
~~~
