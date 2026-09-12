"""Fixtures for tests that need real files on disk.

Compilation, source-map serialization, SyncTeX remapping and the CLI all read
and write actual paths, so most of their tests opened a temporary directory by
hand. One fixture keeps that setup out of the test bodies.
"""

from pathlib import Path
import tempfile
import unittest

from texflux import CompilationResult, compile_with_map, serialize_source_map


class TempDirTestCase(unittest.TestCase):
    """A TestCase whose ``self.root`` is a fresh directory per test."""

    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, name: str, data: bytes | str) -> Path:
        """Write one file under ``self.root`` and return its path."""

        path = self.root / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return path

    def read(self, name: str) -> str:
        """Read one UTF-8 file under ``self.root``."""

        return (self.root / name).read_text(encoding="utf-8")

    def compile_to_disk(
        self,
        source: str,
        **kwargs: object,
    ) -> tuple[Path, CompilationResult]:
        """Compile ``source``, writing in.tfx, out.tex and out.tex.tfxmap.

        Returns the map path and the result, which is what every test of the
        provenance artefacts needs to look at.
        """

        source_path = self.write("in.tfx", source)
        generated_path = self.root / "out.tex"
        map_path = self.root / "out.tex.tfxmap"
        result = compile_with_map(source, filename=str(source_path), **kwargs)
        generated = result.text.encode("utf-8")
        map_path.write_text(
            serialize_source_map(
                result,
                generated_path=generated_path,
                map_path=map_path,
                generated_bytes=generated,
            ),
            encoding="utf-8",
            newline="\n",
        )
        generated_path.write_bytes(generated)
        return map_path, result


__all__ = ["TempDirTestCase"]
