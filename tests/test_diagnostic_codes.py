"""Every diagnostic owns a code, and the published table says which one.

A code identifies the place a diagnostic is raised rather than its wording,
so it has to be unique, stable and documented. These checks read the source
itself, because a new ``raise`` is exactly the thing that would otherwise
ship without one, and they tie each code to the row that publishes it, so a
pair of codes cannot quietly swap places.
"""

import ast
from collections import Counter
from pathlib import Path
import re
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "src" / "texflux"
DESIGN = Path(__file__).resolve().parents[1] / "doc" / "diagnostics.md"

#: What a diagnostic is built with: the five error classes, the warning, and
#: the three helpers that build one on their caller's behalf.
BUILDERS = frozenset({
    "ParseError",
    "ValidationError",
    "DirectiveError",
    "MacroExpansionError",
    "ModuleError",
    "RenderWarning",
    "_error",
    "warn",
})

CODE = re.compile(r"^[PVDEMW][0-9]{3}$")

#: The highest number each kind has ever used. A new diagnostic takes the
#: next one and bumps this; a deleted one leaves its number behind as a gap,
#: because a code that has been published is never reused or renumbered.
HIGHEST = {"P": 37, "V": 43, "D": 1, "E": 19, "M": 30, "W": 2}

#: A hole in a message: a template's ``{name}`` and a table row's ellipsis
#: stand for the same thing, so both reduce to nothing before comparison.
_HOLE = re.compile(r"\{[^{}]*\}|<[^<>]*>|…|\.\.\.")

#: One published row: its code, where it is raised, and the rest of the row.
_ROW = re.compile(r"^\| ([PVDEMW][0-9]{3}) \| ([^|]*) \| (.*) \|$", re.M)


def skeleton(text):
    """Reduce a message to the words a raise site and its row must share."""

    text = text.replace("`", " ").replace("|", " ")
    return re.sub(r"[^a-z0-9]+", " ", _HOLE.sub(" ", text).lower()).strip()


def first_message(node):
    """The literal part of a builder call's message, or ``None``."""

    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.JoinedStr):
        return "".join(
            part.value
            for part in first.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def builder_calls():
    """Every call that builds a diagnostic, with the file and line it is on."""

    for path in sorted(SOURCE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name in BUILDERS:
                yield path.name, node.lineno, name, node


def code_argument(node):
    """The ``code`` keyword of one builder call, or ``None`` if it has none."""

    for keyword in node.keywords:
        if keyword.arg == "code":
            return keyword.value
    return None


def owned_codes():
    """Each literal code, as ``code -> (file, line, message)``."""

    found = {}
    for file, line, _, node in builder_calls():
        value = code_argument(node)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.setdefault(value.value, []).append(
                (file, line, first_message(node))
            )
    return found


def published_rows():
    """Each published row, as ``code -> (file, row text)``."""

    text = DESIGN.read_text(encoding="utf-8")
    rows = {}
    for code, where, rest in _ROW.findall(text):
        match = re.search(r"([a-z_]+\.py)", where)
        rows[code] = (match.group(1) if match else None, rest)
    return rows


class DiagnosticCodeTests(unittest.TestCase):
    def test_every_diagnostic_is_built_with_a_code(self):
        for file, line, name, node in builder_calls():
            with self.subTest(where=f"{file}:{line}", builder=name):
                self.assertIsNotNone(
                    code_argument(node),
                    f"{file}:{line}: {name}(...) is missing code=",
                )

    def test_a_code_is_a_literal_or_a_deliberate_forward(self):
        # Three helpers pass their caller's code through, and two sites
        # forward the code of the error they re-wrap. Everything else owns
        # a literal, so a typo cannot invent a code at run time.
        for file, line, name, node in builder_calls():
            value = code_argument(node)
            with self.subTest(where=f"{file}:{line}", builder=name):
                if isinstance(value, ast.Constant):
                    self.assertIsInstance(value.value, str)
                elif isinstance(value, ast.Name):
                    self.assertEqual(value.id, "code")
                elif isinstance(value, ast.Attribute):
                    self.assertEqual(value.attr, "code")
                else:
                    self.fail(
                        f"{file}:{line}: code= must be a literal, 'code', "
                        f"or '<error>.code'; got {ast.dump(value)}"
                    )

    def test_every_literal_code_is_well_formed(self):
        for code, places in owned_codes().items():
            with self.subTest(code=code):
                self.assertRegex(code, CODE, f"{code} at {places[0]}")

    def test_no_code_is_used_twice(self):
        duplicated = {
            code: [f"{file}:{line}" for file, line, _ in places]
            for code, places in owned_codes().items()
            if len(places) > 1
        }
        self.assertEqual(duplicated, {})

    def test_no_code_goes_past_the_highest_one_ever_issued(self):
        # Gaps are expected: a deleted diagnostic keeps its number out of
        # circulation. What must never happen is a number appearing above
        # the pinned mark without that mark being raised to match.
        numbers = {}
        for code in owned_codes():
            numbers.setdefault(code[0], set()).add(int(code[1:]))
        self.assertEqual(sorted(numbers), sorted(HIGHEST))
        for letter, used in sorted(numbers.items()):
            with self.subTest(letter=letter):
                self.assertLessEqual(max(used), HIGHEST[letter])
                self.assertGreaterEqual(min(used), 1)

    def test_every_kind_still_has_the_codes_it_had(self):
        # A deletion is allowed but has to be deliberate, so the count is
        # pinned beside the high-water mark rather than derived from it.
        counted = Counter(code[0] for code in owned_codes())
        self.assertEqual(
            dict(sorted(counted.items())),
            {"P": 37, "V": 43, "D": 1, "E": 19, "M": 30, "W": 2},
        )

    def test_the_source_and_the_published_table_list_the_same_codes(self):
        self.assertEqual(set(owned_codes()), set(published_rows()))

    def test_each_code_is_published_against_the_site_that_owns_it(self):
        # Set equality alone would let two codes swap places, which is the
        # one mistake a mechanical assignment is most likely to make.
        rows = published_rows()
        for code, places in sorted(owned_codes().items()):
            file, line, message = places[0]
            published_file, row = rows[code]
            with self.subTest(code=code, where=f"{file}:{line}"):
                self.assertEqual(
                    published_file,
                    file,
                    f"{code} is raised in {file} but published against "
                    f"{published_file}",
                )
                if message is None:
                    continue
                words = [word for word in skeleton(message).split(" ") if word]
                opening = " ".join(words[:5])
                if not opening:
                    continue
                self.assertIn(
                    opening,
                    skeleton(row),
                    f"{code} at {file}:{line} says {opening!r}, which its "
                    f"published row does not",
                )


if __name__ == "__main__":
    unittest.main()
