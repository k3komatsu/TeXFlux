"""Validate the diagnostics contract with the optional development validator."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from texflux import diagnose, serialize_diagnostics


@unittest.skipIf(
    Draft202012Validator is None,
    "install jsonschema to validate the diagnostics schema",
)
class DiagnosticsSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        schema_path = cls.root / "schemas/texflux-diagnostics-v1.schema.json"
        schema = json.loads(schema_path.read_text())
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def payload(self, source, **kwargs):
        # Round-trip through JSON, exactly what a real consumer sees: the
        # serialized text reloaded, never the dataclasses themselves.
        report = diagnose(source, **kwargs)
        return json.loads(serialize_diagnostics(report))

    def test_a_clean_document_validates_with_no_diagnostics(self):
        value = self.payload("@frame{t}::\n    body\n")
        self.validator.validate(value)
        self.assertEqual(value["diagnostics"], [])

    def test_the_empty_document_validates(self):
        self.validator.validate(self.payload(""))

    def test_a_parse_error_validates(self):
        value = self.payload("@frame{x}:\n    @foo{bad\n")
        self.validator.validate(value)
        errors = [d for d in value["diagnostics"] if d["severity"] == "error"]
        self.assertEqual(len(errors), 1)

    def test_a_render_warning_validates(self):
        value = self.payload("\\foo:::\n    - a % trailing\n")
        self.validator.validate(value)
        warnings = [d for d in value["diagnostics"] if d["severity"] == "warning"]
        self.assertEqual(len(warnings), 1)

    def test_an_import_chain_validates_and_has_related_locations(self):
        # main imports mid imports broken; broken's invalid macro definition
        # chains through both importers as related locations, so this is the
        # one case that exercises span.source values other than 0.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.tfx").write_text(
                "!defmacro{m}{x}{x}::\n    A\n", encoding="utf-8"
            )
            (root / "mid.tfx").write_text("!import{broken.tfx}\n", encoding="utf-8")
            main_path = root / "main.tfx"
            main_path.write_text("!import{mid.tfx}\n", encoding="utf-8")
            value = self.payload(
                main_path.read_text(encoding="utf-8"), filename=str(main_path)
            )
        self.validator.validate(value)
        self.assertTrue(any(d["related"] for d in value["diagnostics"]))

    def test_unknown_members_are_allowed_everywhere(self):
        value = self.payload("@frame{t}::\n    body\n")

        def extend(obj):
            if isinstance(obj, dict):
                for child in list(obj.values()):
                    extend(child)
                obj["future_metadata"] = True
            elif isinstance(obj, list):
                for child in obj:
                    extend(child)

        extend(value)
        self.validator.validate(value)

    def test_zero_width_and_multiline_spans_validate(self):
        # The format explicitly permits both: an empty '!' line produces a
        # zero-width span, and suite/document spans can cross several lines.
        value = self.payload("@frame{x}:\n    @foo{bad\n")
        start = value["diagnostics"][0]["span"]["start"]

        zero_width = copy.deepcopy(value)
        zero_width["diagnostics"][0]["span"]["end"] = dict(start)
        self.validator.validate(zero_width)

        multiline = copy.deepcopy(value)
        multiline["diagnostics"][0]["span"]["end"]["line"] = start["line"] + 3
        self.validator.validate(multiline)

    def test_invalid_contracts_are_rejected(self):
        base = self.payload("@frame{x}:\n    @foo{bad\n")
        diag = ("diagnostics", 0)
        for path, replacement in (
            (("format",), "other"),
            (("version",), 2),
            (("version",), True),
            (("root",), 1),
            (("sources",), []),
            (("sources", 0, "id"), 1),
            (("sources", 0, "sha256"), "0" * 63),
            (("sources", 0, "sha256"), "A" * 64),
            (diag + ("severity",), "hint"),
            (diag + ("severity",), "info"),
            (diag + ("code",), "p1"),
            (diag + ("code",), "PP01"),
            (diag + ("kind",), "Parse"),
            (diag + ("span", "start", "line"), 0),
            (diag + ("span", "source"), -1),
            (diag + ("span", "end", "column"), 1.5),
        ):
            with self.subTest(path=path, replacement=replacement):
                value = copy.deepcopy(base)
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                self.assertFalse(self.validator.is_valid(value))
        for path in (
            diag + ("related",),
            diag + ("code",),
            diag + ("span",),
            ("sources", 0, "sha256"),
        ):
            with self.subTest(missing=path):
                value = copy.deepcopy(base)
                target = value
                for key in path[:-1]:
                    target = target[key]
                del target[path[-1]]
                self.assertFalse(self.validator.is_valid(value))


if __name__ == "__main__":
    unittest.main()
