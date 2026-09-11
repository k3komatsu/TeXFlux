# TeXFlux

TeXFlux is a small TeX-first preprocessor for LaTeX and Beamer. It removes
structural boilerplate such as begin/end pairs, nested environments, long
brace arguments, and repeated itemize markup without replacing TeX semantics.

The mental model is intentionally small:

~~~text
\ = TeX command
@ = TeX environment
! = TeXFlux special
~~~

Ordinary source lines are raw TeX. TeXFlux does not parse, escape, or
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

Write a .tfx file:

~~~text
@frame{Hello, TeXFlux}:
    \vspace{-.5em}
    !items:
        - TeX remains raw
        -<2-> Structural boilerplate is shortened
~~~

Compile it:

~~~bash
texflux compile slides.tfx -o slides.tex
~~~

The module form is also available:

~~~bash
python -m texflux compile slides.tfx -o slides.tex
~~~

Use source comments when inspecting generated TeX:

~~~bash
texflux compile slides.tfx -o slides.tex --source-comments
~~~

With `--source-comments`, generated source lines are prefixed with comments
such as `% texflux: slides.tfx:1`.

TeXFlux compiles the complete input before writing the output. A failed
compile therefore leaves existing output and `.tfxmap` files unchanged. A
successful compile writes `OUTPUT.tex.tfxmap` beside the generated TeX. Input
and output must be different paths.

## TeX engine and LaTeX Workshop

TeXFlux does not wrap `latexmk` or add a watcher. Use the TeX recipe already
used by the project, with SyncTeX enabled, then remap the generated file:

~~~bash
texflux compile slides.tfx -o slides.tex
latexmk -pdf -synctex=1 slides.tex
texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
~~~

The remap command may be given more than one `--map`; use `--output` to write a
separate SyncTeX file instead of replacing the input:

~~~bash
texflux synctex remap slides.synctex.gz \
    --map slides.tex.tfxmap \
    --output slides.remapped.synctex.gz
~~~

For VS Code with LaTeX Workshop, associate `.tfx` buffers with the existing
LaTeX language ID so forward search recognizes them:

~~~json
{
  "files.associations": {
    "*.tfx": "latex"
  }
}
~~~

A minimal LaTeX Workshop tool and recipe configuration is:

~~~json
{
  "latex-workshop.latex.tools": [
    {
      "name": "texflux-compile",
      "command": "texflux",
      "args": ["compile", "%DOC_EXT%", "-o", "%DOC%.tex"]
    },
    {
      "name": "latexmk-synctex",
      "command": "latexmk",
      "args": ["-pdf", "-synctex=1", "%DOC%.tex"]
    },
    {
      "name": "texflux-remap",
      "command": "texflux",
      "args": [
        "synctex", "remap", "%DOC%.synctex.gz",
        "--map", "%DOC%.tex.tfxmap"
      ]
    }
  ],
  "latex-workshop.latex.recipes": [
    {
      "name": "TeXFlux",
      "tools": ["texflux-compile", "latexmk-synctex", "texflux-remap"]
    }
  ]
}
~~~

`%DOC_EXT%` is the authoring filename including `.tfx`; `%DOC%` is the
extensionless job name used for generated TeX and its SyncTeX/map artifacts.
These placeholders refer to LaTeX Workshop's detected root file, so configure
the `.tfx` document containing `\documentclass` as the root when working with
fragments. The paths above assume the default output directory beside the
generated TeX; if `latex-workshop.latex.outDir` is set, update the SyncTeX and
map paths in the final tool accordingly. Engines configured to write plain
`*.synctex` instead of `*.synctex.gz` need the corresponding plain filename in
the remap command and tool configuration.

Reverse search is extension-agnostic after remapping because SyncTeX returns
the `.tfx` path. Forward search requires the association above. A dedicated
TeXFlux language ID or editor extension is intentionally deferred.

If remapping reports a stale hash, re-run `texflux compile` and the TeX engine
from the same generated `.tex` file; do not edit generated TeX between those
steps. If no source is returned, check that the TeX engine was run with
`-synctex=1` or another positive SyncTeX option and that the `.synctex.gz`
file belongs to the same generated output. LaTeX is an external toolchain and
is not a TeXFlux runtime dependency.

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

The top-level trailing colon is reserved by TeXFlux. Thus
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
from texflux import compile_text

source = r"""@frame{API example}:
    Body in raw TeX
"""
tex = compile_text(source, filename="slides.tfx")
~~~

The pipeline stages are also available:

~~~python
from texflux import normalize, parse, render

document = normalize(parse(source, filename="slides.tfx"))
tex = render(document)
~~~

The renderer accepts canonical AST only. Syntax-only invocation, special, and
stack nodes are removed or expanded before rendering.

Use `compile_with_map` when generated fragments need source provenance. The
returned `CompilationResult.text` is the same TeX string as `compile_text`,
and `CompilationResult.rendered.fragments` contains generated ranges, source
spans, and rendering roles. `serialize_source_map` can serialize that result;
its `source_path` must match the filename used when compiling the source.

## What TeXFlux does not do

TeXFlux deliberately does not parse TeX, discover LaTeX packages, validate
command argument counts, escape user text, provide variables or expressions,
or load user-defined plugins implicitly. TeX semantics remain the responsibility
of the TeX toolchain.

The in-process special registry is an AST-to-AST extension point. A future
directive can turn !result into canonical nodes without returning a TeX string.

## Examples

The [examples/](examples/) directory contains runnable sources and exact output
for the small examples:

- [basic.tfx](examples/basic.tfx) — raw TeX, an environment, and items
- [structured.tfx](examples/structured.tfx) — explicit arguments, block,
  and environment body
- [stacked-items.tfx](examples/stacked-items.tfx) — pure stacking and nested items
- [content.tfx](examples/content.tfx) — converted real-world Beamer content
- [content.tex](examples/content.tex) — original TeX reference

Compile a small example from the repository root:

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/basic.tfx -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

Compile the converted content example:

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/content.tfx -o /tmp/content.generated.tex
~~~

## Development

Run the complete suite:

~~~bash
PYTHONPATH=src python3 -m unittest discover -v
~~~

The optional LaTeX integration tests skip when `pdflatex` is unavailable; the
end-to-end test additionally requires the `synctex` client. The normative
language definition is in
[texflux_tex_first_dsl_v1_spec.md](texflux_tex_first_dsl_v1_spec.md),
and the implementation phases are in [plan.md](plan.md).
