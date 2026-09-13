import gzip
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from texflux.cli import main

from .support import TempDirTestCase
from texflux.remap import RemapError, remap_synctex, remap_synctex_file


def _write_map(
    root: Path,
    *,
    mappings: list[dict[str, object]],
    generated: bytes = b"generated\n",
    source_files: dict[int, tuple[str, bytes]] | None = None,
    name: str = "out.tex.tfxmap",
    generated_name: str = "out.tex",
) -> Path:
    if source_files is None:
        source_files = {0: ("source.tfx", b"source\n")}
    generated_path = root / generated_name
    generated_path.write_bytes(generated)
    sources = []
    for source_id, (filename, data) in source_files.items():
        source_path = root / filename
        source_path.write_bytes(data)
        sources.append(
            {
                "id": source_id,
                "path": filename,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    map_path = root / name
    map_path.write_text(
        json.dumps(
            {
                "format": "texflux-source-map",
                "version": 1,
                "generated": {
                    "path": generated_name,
                    "sha256": hashlib.sha256(generated).hexdigest(),
                },
                "sources": sources,
                "mappings": mappings,
            }
        ),
        encoding="utf-8",
    )
    return map_path


def _mapping(
    start: tuple[int, int],
    end: tuple[int, int],
    source_line: int,
    *,
    source_id: int = 0,
    role: str = "content",
) -> dict[str, object]:
    return {
        "generated": {
            "start": {"line": start[0], "column": start[1]},
            "end": {"line": end[0], "column": end[1]},
        },
        "source": {
            "id": source_id,
            "start": {"line": source_line, "column": 1},
            "end": {"line": source_line, "column": 2},
        },
        "role": role,
    }


def _sync(*records: bytes) -> bytes:
    return (
        b"SyncTeX Version:1\n"
        b"Input:1:out.tex\n"
        b"Input:2:handwritten.tex\n"
        b"Content:\n"
        b"!0\n"
        + b"".join(record + b"\n" for record in records)
        + b"Postamble:\n"
        + b"Count:0\n"
    )


class SyncTeXRemapTests(TempDirTestCase):
    def remap(self, synctex: bytes, *map_paths: Path) -> bytes:
        """Remap ``synctex`` against maps written under this test's directory."""

        return remap_synctex(
            synctex,
            map_paths=list(map_paths),
            synctex_path=self.root / "out.synctex",
        )

    def test_interpolation_without_columns_prefers_the_value_line(self):
        map_path, _ = self.compile_to_disk(
            "!defmacro{m}{x}: |\n    \\foo{pre-!text{x}-post}\n!m: |\n    VALUE\n"
        )
        rewritten = self.remap(_sync(b"v1,1:100,200:3,4,5"), map_path)
        self.assertIn(b"v3,4:100,200:3,4,5\n", rewritten)

    def test_an_escaped_marker_in_a_value_still_remaps_without_columns(self):
        # The caller's own escape is content, so it outranks the template
        # literal on the same generated line instead of tying with it.
        map_path, _ = self.compile_to_disk(
            "!defmacro{m}{x}: |\n    prefix !text{x}\n!m: |\n    !!!text{literal}\n"
        )
        rewritten = self.remap(_sync(b"v1,1:100,200:3,4,5"), map_path)
        self.assertIn(b"v3,4:100,200:3,4,5\n", rewritten)

    def test_remaps_generated_input_to_tfx_source(self):
        generated = b"\\hbox{Generated}\n"
        source = b"first\nsecond\n"
        synctex = (
            b"SyncTeX Version:1\n"
            b"Input:1:out.tex\n"
            b"Input:2:handwritten.tex\n"
            b"Content:\n"
            b"!0\n"
            b"v1,1:100,200:3,4,5\n"
            b"Postamble:\n"
            b"Count:2\n"
        )

        generated_path = self.root / "out.tex"
        source_path = self.root / "source.tfx"
        map_path = self.root / "out.tex.tfxmap"
        generated_path.write_bytes(generated)
        source_path.write_bytes(source)
        map_path.write_text(
            json.dumps(
                {
                    "format": "texflux-source-map",
                    "version": 1,
                    "generated": {
                        "path": "out.tex",
                        "sha256": hashlib.sha256(generated).hexdigest(),
                    },
                    "sources": [
                        {
                            "id": 0,
                            "path": "source.tfx",
                            "sha256": hashlib.sha256(source).hexdigest(),
                        }
                    ],
                    "mappings": [
                        {
                            "generated": {
                                "start": {"line": 1, "column": 1},
                                "end": {"line": 1, "column": 18},
                            },
                            "source": {
                                "id": 0,
                                "start": {"line": 2, "column": 1},
                                "end": {"line": 2, "column": 7},
                            },
                            "role": "content",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        rewritten = self.remap(synctex, map_path)

        self.assertIn(f"Input:3:{source_path}\n".encode(), rewritten)
        self.assertIn(b"Input:2:handwritten.tex\n", rewritten)
        self.assertIn(b"v3,2:100,200:3,4,5\n", rewritten)
        self.assertNotIn(b"v1,2:100,200:3,4,5\n", rewritten)

    def test_hash_mismatch_is_a_hard_error(self):
        generated = b"generated\n"
        source = b"source\n"
        map_path = _write_map(
            self.root,
            generated=generated,
            source_files={0: ("source.tfx", source)},
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        (self.root / "out.tex").write_bytes(b"stale\n")

        with self.assertRaisesRegex(RemapError, "generated file hash"):
            self.remap(_sync(b"v1,1:100,200:3,4,5"), map_path)

    def test_source_hash_mismatch_and_unmatched_generated_input_are_hard_errors(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        (self.root / "source.tfx").write_bytes(b"changed\n")
        with self.assertRaisesRegex(RemapError, "source file hash"):
            self.remap(_sync(b"v1,1:100,200:3,4,5"), map_path)

        (self.root / "source.tfx").write_bytes(b"source\n")
        with self.assertRaisesRegex(RemapError, "not present in SyncTeX"):
            self.remap(
                _sync(b"v1,1:100,200:3,4,5").replace(
                    b"Input:1:out.tex", b"Input:1:other.tex"
                ),
                map_path,
            )

    def test_every_targeted_source_link_must_have_a_mapping(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )

        with self.assertRaisesRegex(RemapError, "generated line 2"):
            self.remap(
                _sync(
                    b"v1,1:100,200:3,4,5",
                    b"h1,2:100,200:3,4,5",
                ),
                map_path,
            )

    def test_column_mapping_omits_target_column_but_preserves_unrelated_column(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 5), (1, 8), 7)],
        )
        rewritten = self.remap(
                        _sync(
                            b"v1,1,6:100,200:3,4,5",
                            b"h2,1,7:100,200:3,4,5",
                        ),
                        map_path,
                    )

        self.assertIn(b"v3,7:100,200:3,4,5\n", rewritten)
        self.assertIn(b"h2,1,7:100,200:3,4,5\n", rewritten)

    def test_non_positive_column_uses_line_fallback_and_line_zero_fails(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 7)],
        )
        rewritten = self.remap(_sync(b"v1,1,-1:100,200:3,4,5"), map_path)
        with self.assertRaisesRegex(RemapError, "invalid generated line"):
            self.remap(_sync(b"v1,0:100,200:3,4,5"), map_path)

        self.assertIn(b"v3,7:100,200:3,4,5\n", rewritten)

    def test_new_inputs_are_inserted_before_preamble_settings(self):
        sync = (
            b"SyncTeX Version:1\n"
            b"Input:1:out.tex\n"
            b"Output:pdf\n"
            b"Magnification:1000\n"
            b"Unit:1\n"
            b"X Offset:0\n"
            b"Y Offset:0\n"
            b"Content:\n"
            b"!0\n"
            b"v1,1:100,200:3,4,5\n"
            b"Postamble:\n"
            b"Count:2\n"
        )
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 7)],
        )
        rewritten = self.remap(sync, map_path)

        self.assertLess(rewritten.index(b"Input:2:"), rewritten.index(b"Output:"))
        self.assertIn(b"v2,7:100,200:3,4,5\n", rewritten)

    def test_same_generated_path_under_multiple_tags_rewrites_each_tag(self):
        sync = _sync(b"v1,1:100,200:3,4,5", b"h3,1:100,200:3,4,5").replace(
            b"Input:2:handwritten.tex\n",
            b"Input:2:handwritten.tex\nInput:3:out.tex\n",
        )
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 7)],
        )
        rewritten = self.remap(sync, map_path)

        self.assertIn(b"v4,7:100,200:3,4,5\n", rewritten)
        self.assertIn(b"h4,7:100,200:3,4,5\n", rewritten)

    def test_line_only_mapping_uses_role_priority_and_rejects_ambiguity(self):
        generated = b"generated\n"
        priority_map = _write_map(
            self.root,
            generated=generated,
            source_files={
                0: ("content.tfx", b"content\n"),
                1: ("open.tfx", b"open\n"),
            },
            mappings=[
                _mapping((1, 1), (1, 3), 8, source_id=0, role="content"),
                _mapping((1, 3), (1, 5), 9, source_id=1, role="open"),
            ],
        )
        rewritten = self.remap(_sync(b"v1,1:100,200:3,4,5"), priority_map)
        ambiguous_map = _write_map(
            self.root,
            generated=generated,
            source_files={
                0: ("left.tfx", b"left\n"),
                1: ("right.tfx", b"right\n"),
            },
            mappings=[
                _mapping((1, 1), (1, 3), 8, source_id=0),
                _mapping((1, 3), (1, 5), 9, source_id=1),
            ],
            name="ambiguous.tfxmap",
        )

        with self.assertRaisesRegex(RemapError, "ambiguous source mappings"):
            self.remap(_sync(b"v1,1:100,200:3,4,5"), ambiguous_map)

        self.assertIn(b"v3,8:100,200:3,4,5\n", rewritten)

    def test_existing_source_tags_are_reused_and_new_tags_are_deterministic(self):
        generated = b"generated\n"
        source = self.root / "source.tfx"
        source.write_bytes(b"source\n")
        map_path = _write_map(
            self.root,
            generated=generated,
            source_files={0: ("source.tfx", b"source\n"), 1: ("new.tfx", b"new\n")},
            mappings=[
                _mapping((1, 1), (1, 10), 1, source_id=0),
                _mapping((2, 1), (2, 10), 2, source_id=1),
            ],
        )
        sync = (
            _sync(
                b"v1,1:100,200:3,4,5",
                b"h1,2:100,200:3,4,5",
            ).replace(b"Input:2:handwritten.tex\n", f"Input:3:{source}\n".encode())
        )
        rewritten = self.remap(sync, map_path)

        self.assertIn(b"Input:3:", rewritten)
        self.assertIn(f"Input:4:{self.root / 'new.tfx'}\n".encode(), rewritten)
        self.assertIn(b"v3,1:100,200:3,4,5\n", rewritten)
        self.assertIn(b"h4,2:100,200:3,4,5\n", rewritten)

    def test_two_maps_target_two_inputs_and_share_a_new_source_tag(self):
        first = _write_map(
            self.root,
            generated=b"first\n",
            generated_name="first.tex",
            source_files={0: ("shared.tfx", b"shared\n")},
            mappings=[_mapping((1, 1), (1, 10), 3)],
            name="first.tfxmap",
        )
        second = _write_map(
            self.root,
            generated=b"second\n",
            generated_name="second.tex",
            source_files={0: ("shared.tfx", b"shared\n")},
            mappings=[_mapping((2, 1), (2, 10), 4)],
            name="second.tfxmap",
        )
        sync = _sync(
            b"v1,1:100,200:3,4,5",
            b"h2,2:100,200:3,4,5",
        ).replace(
            b"Input:1:out.tex\nInput:2:handwritten.tex",
            b"Input:1:first.tex\nInput:2:second.tex",
        )
        rewritten = self.remap(sync, first, second)

        self.assertIn(b"v3,3:100,200:3,4,5\n", rewritten)
        self.assertIn(b"h3,4:100,200:3,4,5\n", rewritten)
        self.assertEqual(rewritten.count(b"Input:3:"), 1)

    def test_duplicate_maps_for_one_generated_input_are_rejected(self):
        kwargs = {
            "generated": b"generated\n",
            "source_files": {0: ("source.tfx", b"source\n")},
            "mappings": [_mapping((1, 1), (1, 10), 1)],
        }
        first = _write_map(self.root, **kwargs)
        second = _write_map(self.root, **kwargs, name="second.tfxmap")

        with self.assertRaisesRegex(RemapError, "multiple source maps"):
            self.remap(_sync(b"v1,1:100,200:3,4,5"), first, second)

    def test_output_is_atomic_and_in_place_replacement_preserves_mode(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        sync_path = self.root / "out.synctex"
        original = _sync(b"v1,1:100,200:3,4,5")
        sync_path.write_bytes(original)
        sync_path.chmod(0o640)
        output_path = self.root / "rewritten.synctex"

        remap_synctex_file(
            sync_path,
            map_paths=[map_path],
            output_path=output_path,
        )
        self.assertEqual(sync_path.read_bytes(), original)
        self.assertIn(b"v3,1:", output_path.read_bytes())

        remap_synctex_file(sync_path, map_paths=[map_path])
        self.assertIn(b"v3,1:", sync_path.read_bytes())
        self.assertEqual(sync_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(list(self.root.glob(".out.synctex.*.tmp")), [])

    def test_concurrent_change_aborts_before_replace(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        sync_path = self.root / "out.synctex"
        original = _sync(b"v1,1:100,200:3,4,5")
        sync_path.write_bytes(original)
        actual_remap = remap_synctex

        def remap_then_mutate(*args, **kwargs):
            result = actual_remap(*args, **kwargs)
            sync_path.write_bytes(original + b"changed\n")
            return result

        with mock.patch("texflux.remap.remap_synctex", remap_then_mutate):
            with self.assertRaisesRegex(RemapError, "changed during"):
                remap_synctex_file(sync_path, map_paths=[map_path])
        self.assertEqual(sync_path.read_bytes(), original + b"changed\n")
        self.assertEqual(list(self.root.glob(".out.synctex.*.tmp")), [])

    def test_new_input_after_a_truncated_final_input_gets_a_separator(self):
        map_path = _write_map(
            self.root,
            mappings=[],
        )
        sync = b"SyncTeX Version:1\nInput:1:out.tex"
        rewritten = self.remap(sync, map_path)

        self.assertIn(b"Input:1:out.tex\nInput:2:", rewritten)

    def test_cli_supports_repeatable_map_and_output(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        sync_path = self.root / "out.synctex"
        sync_path.write_bytes(_sync(b"v1,1:100,200:3,4,5"))
        output_path = self.root / "result.synctex"

        result = main(
            [
                "synctex",
                "remap",
                str(sync_path),
                "--map",
                str(map_path),
                "--output",
                str(output_path),
            ]
        )
        rewritten = output_path.read_bytes()

        self.assertEqual(result, 0)
        self.assertIn(b"v3,1:", rewritten)

    def test_gzip_remap_preserves_the_container(self):
        map_path = _write_map(
            self.root,
            mappings=[_mapping((1, 1), (1, 10), 1)],
        )
        compressed = gzip.compress(_sync(b"v1,1:100,200:3,4,5"), mtime=321)
        rewritten = remap_synctex(
            compressed,
            map_paths=[map_path],
            synctex_path=self.root / "out.synctex.gz",
        )

        self.assertTrue(rewritten.startswith(b"\x1f\x8b"))
        self.assertIn(b"v3,1:", gzip.decompress(rewritten))


if __name__ == "__main__":
    unittest.main()
