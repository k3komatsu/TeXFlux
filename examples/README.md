# TeXFlux examples

The small examples are complete .tfx inputs with byte-for-byte .tex outputs.

| Input | Demonstrates |
| --- | --- |
| [basic.tfx](basic.tfx) | Raw TeX, an environment, !items, and an overlay |
| [structured.tfx](structured.tfx) | !arg, !body, !block, and one long command argument |
| [stacked-items.tfx](stacked-items.tfx) | Prefix-explicit >> stacking and nested items |
| [content.tfx](content.tfx) | A converted real-world Beamer content file |
| [content.tex](content.tex) | The original TeX reference |

From the repository root:

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/basic.tfx -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

content.tfx intentionally keeps the original TeX commands, comments, formulas,
and external asset paths. Only structural boilerplate is shortened. It is a
TeXFlux conversion of content.tex, not a generated-output golden.

~~~bash
PYTHONPATH=src python3 -m texflux compile examples/content.tfx -o /tmp/content.generated.tex
~~~
