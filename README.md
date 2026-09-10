# Beamercraft

Beamercraft is a small TeX-first preprocessor for structural LaTeX boilerplate.
Ordinary lines remain raw TeX; only `@` directives and indentation are handled.

```text
@frame{Title}:
    @!items:
        - First
        -<2-> Second
```

```bash
beamercraft slides.bmc -o content.tex
python -m beamercraft slides.bmc -o content.tex --source-comments
```

The library entry point is `beamercraft.compile_text(source, filename=..., source_comments=...)`.
Beamercraft does not parse or compile LaTeX and has no runtime dependencies.
