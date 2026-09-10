# Beamercraft examples

The small examples are complete .bmc inputs with byte-for-byte .tex outputs.

| Input | Demonstrates |
| --- | --- |
| [basic.bmc](basic.bmc) | Raw TeX, an environment, !items, and an overlay |
| [structured.bmc](structured.bmc) | !arg, !body, !block, and one long command argument |
| [stacked-items.bmc](stacked-items.bmc) | Prefix-explicit >> stacking and nested items |
| [content.bmc](content.bmc) | A converted real-world Beamer content file |
| [content.tex](content.tex) | The original TeX reference |

From the repository root:

~~~bash
PYTHONPATH=src python3 -m beamercraft examples/basic.bmc -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
~~~

content.bmc intentionally keeps the original TeX commands, comments, formulas,
and external asset paths. Only structural boilerplate is shortened. It is a
Beamercraft conversion of content.tex, not a generated-output golden.

~~~bash
PYTHONPATH=src python3 -m beamercraft examples/content.bmc -o /tmp/content.generated.tex
~~~
