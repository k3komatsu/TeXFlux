"""The external schema is a data contract, independent of Python node names."""

import contextlib
from dataclasses import replace
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import texflux
from texflux import (
    Argument, ArgumentLayout, AstCompilationResult, Block, GenericInvocation,
    GroupKind, compile_ast, compile_with_map, parse, render, serialize_ast,
)
from texflux.cli import main
from .support import TempDirTestCase


class ExternalAstTests(TempDirTestCase):
    def test_exact_unicode_raw_envelope_and_original_byte_hash(self):
        data = "日本😀\r\n\r\n".encode("utf-8")
        result = compile_ast(data.decode(), filename="slides.tfx", source_bytes=data)
        payload = json.loads(serialize_ast(result))
        def span(line, end):
            return {"source": 0, "start": {"line": line, "column": 1},
                    "end": {"line": line, "column": end}}
        self.assertEqual(payload["format"], "texflux-ast")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["root"], 0)
        self.assertEqual(payload["producer"]["name"], "texflux")
        self.assertEqual(payload["sources"], [{"id": 0, "file": "slides.tfx",
            "sha256": hashlib.sha256(data).hexdigest()}])
        self.assertEqual(payload["document"]["type"], "document")
        self.assertEqual(payload["document"]["body"]["nodes"], [
            {"type": "raw", "text": "日本😀", "span": span(1, 4)},
            {"type": "raw", "text": "", "span": span(2, 1)},
        ])
        compact = serialize_ast(result)
        self.assertEqual(compact.count("\n"), 1)
        self.assertIn("日本😀", compact)
        self.assertEqual(compact, serialize_ast(compile_ast(
            data.decode(), filename="slides.tfx", source_bytes=data)))
        self.assertEqual(json.loads(serialize_ast(result, pretty=True)), payload)
        self.assertTrue(serialize_ast(result, pretty=True).endswith("}\n"))

    def test_structures_and_all_argument_layouts(self):
        source = (
            "@unknown*{title}[opt]<2->: |\n"
            "    @{\\small}: |\n        \\item opaque\n"
            "\\foo:\n    - short\n    - {explicit}\n"
            "    -\n        long\n        value\n"
            "\\bar: |\n    body\n"
            "!before{\\vspace{1em}} >> \\padded\n"
        )
        result = compile_ast(source)
        nodes = json.loads(serialize_ast(result))["document"]["body"]["nodes"]
        container, command, long_command, prefix, padded = nodes
        self.assertEqual((container["form"], container["name"]), ("container", "unknown*"))
        self.assertEqual([a["kind"] for a in container["arguments"]],
                         ["required", "optional", "overlay"])
        self.assertTrue(all(a["layout"] == "inline" and a["value"]["type"] == "text"
                            for a in container["arguments"]))
        group = container["body"]["nodes"][0]
        self.assertEqual((group["type"], group["header"]), ("group", "\\small"))
        self.assertEqual(group["body"]["nodes"][0]["text"], "\\item opaque")
        self.assertEqual(command["form"], "command")
        self.assertNotIn("body", command)
        self.assertEqual([a["layout"] for a in command["arguments"]],
                         ["hugged", "explicit", "block"])
        self.assertTrue(all(a["value"]["type"] == "block" for a in command["arguments"]))
        self.assertEqual(len(long_command["arguments"]), 1)
        self.assertEqual(long_command["arguments"][0]["layout"], "block")
        # A standard flow macro leaves no trace of its own: its prefix value
        # is the raw TeX the author wrote, and !before itself exports nothing.
        self.assertEqual(prefix["text"], "\\vspace{1em}")
        self.assertEqual((padded["form"], padded["name"]), ("command", "padded"))

    def test_session_dependencies_scope_bindings_and_repeated_spans(self):
        core = self.write("core.tfxm", "!defmacro{inner}{x}: |\n    @box{!text{x}}: |\n        !param{x}\n")
        style = self.write("style.tfxm", "!macroimport{core.tfxm}\n!defmacro{outer}{x}: |\n    !inner{!text{x}}\n")
        child = self.write("child.tfx", "!flag{show}{off}\n!when{show} >> !outer{日本}\n".replace(
            "!flag", "!macroimport{style.tfxm}\n!flag", 1))
        source = ("!flag{show}{off}\n!when{show} >> !import{missing.tfx}\n"
                  "@frame: |\n    !import{child.tfx}(show=on)\n"
                  "    !import{./child.tfx}(show=on)\n")
        root = self.write("main.tfx", source)
        result = compile_ast(source, filename=str(root))
        payload = json.loads(serialize_ast(result))
        self.assertEqual([s["file"] for s in payload["sources"]],
                         [str(p) for p in (root, child, style, core)])
        for entry, path in zip(payload["sources"], (root, child, style, core)):
            self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        parent = payload["document"]["body"]["nodes"][0]
        first, second = parent["body"]["nodes"]
        self.assertEqual(first, second)
        self.assertEqual(parent["span"]["source"], 0)
        self.assertEqual(first["span"]["source"], 1)
        self.assertEqual(first["arguments"][0]["value"], {"type": "text", "text": "日本"})
        self.assertEqual(render(result.document), compile_with_map(source, filename=str(root)).text)

    def test_compile_with_map_uses_public_ast_path_and_ast_does_not_render(self):
        result = compile_ast("")
        self.assertEqual(json.loads(serialize_ast(result))["document"]["body"]["nodes"], [])
        with patch("texflux.render_with_provenance", side_effect=AssertionError("rendered")):
            compile_ast("raw\n")
        with patch("texflux.compile_ast", return_value=result) as compile_mock:
            compiled = compile_with_map("ignored", source_bytes=b"original", flags={"x": True})
        compile_mock.assert_called_once_with("ignored", filename="<string>",
                                            flags={"x": True}, source_bytes=b"original")
        self.assertEqual(compiled.sources, result.sources)

    def test_sources_include_modules_that_produce_no_nodes(self):
        self.write("unused.tfxm", "!defmacro{unused}: |\n    unused\n")
        self.write("empty.tfx", "!flag{show}{off}\n!when{show}: |\n    !unknown\n")
        source = "!macroimport{unused.tfxm}\n!import{empty.tfx}\n"
        result = compile_ast(source, filename=str(self.root / "main.tfx"))
        payload = json.loads(serialize_ast(result))
        self.assertEqual([Path(s["file"]).name for s in payload["sources"]],
                         ["main.tfx", "unused.tfxm", "empty.tfx"])
        self.assertEqual(payload["document"]["body"]["nodes"], [])

    def test_every_existing_golden_serializes_with_only_schema_nodes(self):
        def check(value):
            if isinstance(value, dict):
                if "type" in value:
                    self.assertIn(value["type"], {"document", "block", "raw", "invocation", "group", "text"})
                    if value["type"] != "text":
                        self.assertIn("span", value)
                self.assertNotIn("parts", value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for path in sorted(Path(__file__).with_name("golden").glob("*/input.tfx")):
            with self.subTest(case=path.parent.name):
                result = compile_ast(path.read_text(encoding="utf-8"), filename=str(path))
                check(json.loads(serialize_ast(result)))

    def test_serializer_rejects_syntax_nodes_and_noncanonical_arguments(self):
        result = compile_ast("raw")
        for source in ("@frame: |\n    x", "!before{1em}: |\n    x", "@frame >> \\foo"):
            with self.subTest(source=source), self.assertRaises((TypeError, ValueError)):
                serialize_ast(replace(result, document=parse(source)))
        span = result.document.span
        for kind, value, layout in (
            (GroupKind.BINDING, "x=on", ArgumentLayout.INLINE),
            (GroupKind.REQUIRED, "text", ArgumentLayout.BLOCK),
            (GroupKind.REQUIRED, Block((), span), ArgumentLayout.INLINE),
        ):
            node = GenericInvocation("foo", (Argument(kind, value, layout, span),), None, span)
            bad = replace(result.document, body=Block((node,), span))
            with self.assertRaises(ValueError):
                serialize_ast(AstCompilationResult(bad, result.sources))


class AstCliTests(TempDirTestCase):
    def test_real_stdout_is_utf8_even_with_ascii_python_stdio(self):
        path = self.write("slides.tfx", "日本😀\n")
        process = subprocess.run(
            [sys.executable, "-m", "texflux", "ast", str(path), "-o", "-"],
            env={**os.environ, "PYTHONIOENCODING": "ascii",
                 "PYTHONPATH": str(Path(texflux.__file__).resolve().parents[1])},
            capture_output=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("日本😀".encode("utf-8"), process.stdout)
        self.assertEqual(process.stdout.count(b"\n"), 1)
        self.assertEqual(process.stderr, b"")

    def test_file_stdout_pretty_flags_and_no_sidecar(self):
        path = self.write("slides.tfx", b"!flag{draft}{off}\r\n!when{draft}: |\r\n    YES\r\n")
        output = self.root / "slides.json"
        args = ["ast", str(path), "-o", str(output), "--flag", "draft"]
        self.assertEqual(main(args), 0)
        encoded = output.read_bytes()
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertFalse(Path(str(output) + ".tfxmap").exists())
        payload = json.loads(encoded)
        self.assertEqual(payload["document"]["body"]["nodes"][0]["text"], "YES")
        self.assertEqual(payload["sources"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        stdout = StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["ast", str(path), "-o", "-", "--flag", "draft", "--pretty"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_errors_preserve_output_and_report_diagnostics(self):
        path = self.write("slides.tfx", "!unknown\n")
        output = self.write("out.json", "keep")
        for args in (
            ["ast", str(path), "-o", str(output)],
            ["ast", str(path), "-o", str(path)],
            ["ast", str(path), "-o", str(output), "--flag", "x=yes"],
            ["ast", str(output), "-o", "-"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(StringIO()) as stderr:
                self.assertEqual(main(args), 1)
                self.assertTrue(stderr.getvalue())
                self.assertEqual(output.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
