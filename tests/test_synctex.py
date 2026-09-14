import gzip
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from texflux.synctex import (
    SyncTeXError,
    parse_synctex,
    read_synctex_file,
    serialize_synctex,
    write_synctex_file,
)

from .support import TempDirTestCase


class SyncTeXCodecTests(TempDirTestCase):
    def test_mixed_newlines_preserve_blank_lines_and_other_control_bytes(self):
        data = (
            b"SyncTeX Version:1\r\n\r\n\n\r"
            b"Input:1:C:/source:\xff.tex\r"
            b"Content:\n"
            b"z\x0b\x0c\x1c\x1d\x1e\x85\r\n"
            b"zlast"
        )
        document = parse_synctex(data)
        self.assertEqual(serialize_synctex(document), data)
        self.assertEqual(document.inputs[0].path, b"C:/source:\xff.tex")
        self.assertEqual(
            [line.newline for line in document.lines],
            [b"\r\n", b"\r\n", b"\n", b"\r", b"\r", b"\n", b"\r\n", b""],
        )

    def test_checked_in_fixture_round_trips_all_record_families(self):
        fixture = Path(__file__).with_name("fixtures") / "minimal.synctex"

        document = parse_synctex(fixture.read_bytes())
        output = serialize_synctex(document)
        reparsed = parse_synctex(output)

        self.assertEqual(document.container, "plain")
        self.assertEqual([item.tag for item in document.inputs], [1, 2, 2])
        self.assertEqual(
            [record.kind for record in reparsed.records],
            [
                b"!", b"{", b"[", b"(", b"v", b"h", b"k", b"g", b"r",
                b"$", b"x", b"c", b"?", b"<", b"f", b"z", b"%", b">",
                b")", b"]", b"}", b"!", b"!",
            ],
        )
        self.assertNotIn(b",=", output)
        self.assertIn(b"Count:19\n", output)
        self.assertIn(b"zopaque\n", output)
        self.assertIn(b"%comment\n", output)
        self.assertEqual(
            [
                (record.point.horizontal, record.point.vertical)
                for record in document.records
                if record.point is not None
            ],
            [
                (record.point.horizontal, record.point.vertical)
                for record in reparsed.records
                if record.point is not None
            ],
        )

    def test_parser_preserves_input_path_bytes_and_newline_style(self):
        data = (
            b"SyncTeX Version:1\r\n"
            b"Input:1:/tmp/\xff-source.tfx\r\n"
            b"Output:pdf\r\n"
            b"Magnification:1000\r\n"
            b"Unit:1\r\n"
            b"X Offset:0\r\n"
            b"Y Offset:0\r\n"
            b"Content:\r\n"
        )

        document = parse_synctex(data)

        self.assertEqual(document.version, 1)
        self.assertEqual(document.newline, b"\r\n")
        self.assertEqual(document.inputs[0].tag, 1)
        self.assertEqual(document.inputs[0].path, b"/tmp/\xff-source.tfx")
        self.assertEqual(document.container, "plain")

    def test_parser_models_records_forms_columns_and_unknown_bytes(self):
        data = (
            b"SyncTeX Version:2\n"
            b"Input:1:/tmp/source.tfx\n"
            b"Output:pdf\n"
            b"Magnification:1000\n"
            b"Unit:1\n"
            b"X Offset:0\n"
            b"Y Offset:0\n"
            b"Content:\n"
            b"!0\n"
            b"{1\n"
            b"[1,4,9:100,200:10,20,30\n"
            b"v1,4:110,=\n"
            b"h1,4:120,210:1,2,3\n"
            b"f7:130,=\n"
            b"<7\n"
            b"zopaque\n"
            b"%comment\n"
            b">\n"
            b"]\n"
            b"}\n"
            b"Postamble:\n"
            b"Count:0\n"
            b"Post scriptum:\n"
        )

        document = parse_synctex(data)

        self.assertEqual(
            [setting.name for setting in document.settings],
            [b"Output", b"Magnification", b"Unit", b"X Offset", b"Y Offset"],
        )
        self.assertEqual(
            [record.kind for record in document.records],
            [b"!", b"{", b"[", b"v", b"h", b"f", b"<", b"z", b"%", b">", b"]", b"}"],
        )
        box = next(record for record in document.records if record.kind == b"[")
        self.assertEqual(box.link.tag, 1)
        self.assertEqual(box.link.line, 4)
        self.assertEqual(box.link.column, 9)
        self.assertEqual(box.point.horizontal, 100)
        self.assertEqual(box.point.vertical, 200)
        compressed = next(record for record in document.records if record.kind == b"v")
        self.assertEqual(compressed.point.horizontal, 110)
        self.assertEqual(compressed.point.vertical, 200)
        self.assertTrue(compressed.is_compressed)
        form_ref = next(record for record in document.records if record.kind == b"f")
        self.assertEqual(form_ref.form_tag, 7)
        self.assertEqual(form_ref.point.vertical, 210)
        self.assertEqual(next(record for record in document.records if record.kind == b"z").raw, b"zopaque")

    def test_expansion_rejects_a_stale_point_span(self):
        document = parse_synctex(
            b"SyncTeX Version:2\n"
            b"Content:\n"
            b"h1,1:10,20\n"
            b"x1,1:30,=\n"
        )
        record = next(record for record in document.records if record.kind == b"x")
        start, _ = record.point_span

        with self.assertRaisesRegex(ValueError, "point span"):
            record.expanded_raw(record.raw[:start] + b"0" + record.raw[start:])

    def test_parser_rejects_malformed_and_corrupt_input(self):
        with self.assertRaises(SyncTeXError):
            parse_synctex(b"Content:\n")
        with self.assertRaises(SyncTeXError):
            parse_synctex(b"SyncTeX Version:1\nInput:1\n")
        with self.assertRaises(SyncTeXError):
            parse_synctex(b"SyncTeX Version:1\nContent:\nx1,1:10,=\n")
        with self.assertRaises(SyncTeXError):
            parse_synctex(gzip.compress(b"SyncTeX Version:1\n")[:-1])

    def test_file_helpers_preserve_a_missing_final_newline(self):
        document = parse_synctex(b"SyncTeX Version:1\nContent:")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.synctex"
            write_synctex_file(path, document)
            self.assertEqual(path.read_bytes(), b"SyncTeX Version:1\nContent:")
            self.assertEqual(read_synctex_file(path), document)

    def test_serializer_expands_points_and_regenerates_count_and_anchors(self):
        data = (
            b"SyncTeX Version:2\r\n"
            b"Input:1:/tmp/source.tfx\r\n"
            b"Content:\r\n"
            b"!999\r\n"
            b"{1\r\n"
            b"h1,1:10,20:1,2,3\r\n"
            b"x1,1:30,=\r\n"
            b"!999\r\n"
            b"}1\r\n"
            b"Postamble:\r\n"
            b"Count:999\r\n"
            b"!999\r\n"
            b"Post scriptum:\r\n"
        )

        output = serialize_synctex(parse_synctex(data))
        self.assertNotIn(b",=", output)
        self.assertIn(b"x1,1:30,20\r\n", output)
        self.assertIn(b"Count:5\r\n", output)
        self.assertNotIn(b"\n", output.replace(b"\r\n", b""))

        offset = 0
        anchor_origin = 0
        anchors = []
        for line in output.splitlines(keepends=True):
            body = line[:-2] if line.endswith(b"\r\n") else line
            if body.startswith(b"!") and body[1:].isdigit():
                anchors.append((int(body[1:]), offset, anchor_origin))
                anchor_origin = offset
            offset += len(line)
        self.assertTrue(anchors)
        self.assertTrue(all(value == start - origin for value, start, origin in anchors))

    def test_gzip_container_has_deterministic_empty_filename_metadata(self):
        plain = (
            b"SyncTeX Version:1\n"
            b"Input:1:/tmp/source.tfx\n"
            b"Content:\n"
            b"!0\n"
        )
        document = parse_synctex(gzip.compress(plain, mtime=123))

        first = serialize_synctex(document)
        second = serialize_synctex(parse_synctex(first))

        self.assertEqual(document.container, "gzip")
        self.assertEqual(first, second)
        self.assertEqual(
            first,
            serialize_synctex(parse_synctex(gzip.compress(plain, mtime=999))),
        )
        self.assertTrue(first.startswith(b"\x1f\x8b"))
        self.assertEqual(first[3], 0)
        self.assertEqual(int.from_bytes(first[4:8], "little"), 0)
        self.assertEqual(gzip.decompress(first), plain.replace(b"!0", b"!51"))

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is not installed")
    def test_toolchain_fixture_round_trips_with_tex_live(self):
        source = self.root / "minimal.tex"
        source.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Hello SyncTeX.\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-synctex=1",
                "-output-directory",
                str(self.root),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sync_path = self.root / "minimal.synctex.gz"
        original = sync_path.read_bytes()
        document = parse_synctex(original)
        self.assertEqual(document.container, "gzip")
        self.assertGreaterEqual(len(document.inputs), 2)
        self.assertNotIn(b",=", gzip.decompress(original))
        rewritten = serialize_synctex(document)
        self.assertEqual(gzip.decompress(rewritten), gzip.decompress(original))
        reparsed = parse_synctex(rewritten)
        self.assertEqual(
            [record.point for record in document.records if record.point is not None],
            [record.point for record in reparsed.records if record.point is not None],
        )

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex is not installed")
    def test_toolchain_fixture_models_forms_and_compressed_points(self):
        source = self.root / "form.tex"
        source.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\newbox\\formbox\n"
            "\\setbox\\formbox=\\hbox{Form text}\n"
            "\\pdfxform\\formbox\n"
            "\\pdfrefxform\\pdflastxform\n"
            "\\par Form output.\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-synctex=13",
                "-output-directory",
                str(self.root),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = parse_synctex((self.root / "form.synctex.gz").read_bytes())
        self.assertEqual(document.version, 13)
        self.assertTrue(any(record.kind == b"<" for record in document.records))
        self.assertTrue(any(record.kind == b">" for record in document.records))
        self.assertTrue(any(record.kind == b"f" for record in document.records))
        self.assertTrue(any(record.is_compressed for record in document.records))
        rewritten = serialize_synctex(document)
        rewritten_plain = gzip.decompress(rewritten)
        self.assertNotIn(b",=", rewritten_plain)
        self.assertEqual(document.count, parse_synctex(rewritten).count)

        offset = 0
        anchor_origin = 0
        for line in rewritten_plain.splitlines(keepends=True):
            body = line[:-1] if line.endswith(b"\n") else line
            if body.startswith(b"!") and body[1:].isdigit():
                self.assertEqual(int(body[1:]), offset - anchor_origin)
                anchor_origin = offset
            offset += len(line)


if __name__ == "__main__":
    unittest.main()
