"""Validate the external contract with the optional development validator."""

import copy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from texflux import compile_ast, serialize_ast


@unittest.skipIf(Draft202012Validator is None, "install jsonschema to validate the AST schema")
class AstSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        schema = json.loads((cls.root / "schemas/texflux-ast-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def payload(self):
        source = "@frame{title}:\n    \\foo:\n        text\n"
        return json.loads(serialize_ast(compile_ast(source)))

    def test_all_golden_outputs_and_empty_document(self):
        self.validator.validate(json.loads(serialize_ast(compile_ast(""))))
        for path in sorted((self.root / "tests/golden").glob("*/input.tfx")):
            with self.subTest(case=path.parent.name):
                result = compile_ast(path.read_text(encoding="utf-8"), filename=str(path))
                self.validator.validate(json.loads(serialize_ast(result)))

    def test_unknown_members_and_names_are_allowed(self):
        value = self.payload()
        def extend(obj):
            if isinstance(obj, dict):
                for child in list(obj.values()):
                    extend(child)
                obj["future_metadata"] = True
            elif isinstance(obj, list):
                for child in obj:
                    extend(child)
        extend(value)
        value["document"]["body"]["nodes"][0]["name"] = "unknown*"
        self.validator.validate(value)

    def test_invalid_contracts_are_rejected(self):
        base = self.payload()
        container = ("document", "body", "nodes", 0)
        command = container + ("body", "nodes", 0)
        inline = container + ("arguments", 0)
        long_argument = command + ("arguments", 0)
        for path, replacement in (
            (("format",), "other"),
            (("version",), 2),
            (("version",), True),
            (("root",), 1),
            (("sources",), []),
            (("sources", 0, "id"), 1),
            (("sources", 0, "sha256"), "A" * 64),
            (("sources", 0, "sha256"), "0" * 63),
            (container + ("type",), "item"),
            (container + ("body",), None),
            (command + ("body",), None),
            (command + ("body",), base["document"]["body"]),
            (inline + ("kind",), "binding"),
            (inline + ("layout",), "block"),
            (long_argument + ("layout",), "inline"),
            (container + ("span", "source"), -1),
            (container + ("span", "start", "line"), 0),
            (container + ("span", "end", "column"), 1.5),
        ):
            with self.subTest(path=path, replacement=replacement):
                value = copy.deepcopy(base)
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                self.assertFalse(self.validator.is_valid(value))
        for path in (("document", "span"), container + ("body",), inline + ("span",)):
            with self.subTest(missing=path):
                value = copy.deepcopy(base)
                target = value
                for key in path[:-1]:
                    target = target[key]
                del target[path[-1]]
                self.assertFalse(self.validator.is_valid(value))


if __name__ == "__main__":
    unittest.main()
