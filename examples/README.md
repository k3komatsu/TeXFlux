# TeXFlux examples

The small examples are complete .tfx inputs with byte-for-byte .tex outputs.

| Input | Demonstrates |
| --- | --- |
| [basic.tfx](basic.tfx) | Raw TeX, a block environment, !items, and an overlay |
| [structured.tfx](structured.tfx) | Sequence values, block values, and literal groups |
| [stacked-items.tfx](stacked-items.tfx) | Closed >> stacking and nested items |
| [macros.tfx](macros.tfx) | Wrapper, two-argument, and variadic source macros |
| [content.tfx](content.tfx) | A converted real-world Beamer content file |
| [content.tex](content.tex) | The generated TeX output golden |

From the repository root:

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/basic.tfx -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

content.tfx keeps TeX commands opaque while showing sequence values, block
values, literal groups, and itemize expansion. It is paired with content.tex
as an exact generated-output golden.

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/content.tfx -o /tmp/content.generated.tex
~~~
