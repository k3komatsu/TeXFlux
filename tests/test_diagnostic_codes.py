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


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "texflux"
PORTED_SOURCE = ROOT / "source" / "texflux"
DESIGN = ROOT / "doc" / "diagnostics.md"

#: Every tracked document that may cite a code: all of doc/, recursively,
#: and every top-level Markdown file. AGENTS.md is untracked.
DOCUMENTS = (
    *sorted((ROOT / "doc").rglob("*.md")),
    *sorted(path for path in ROOT.glob("*.md") if path.name != "AGENTS.md"),
)

#: What a diagnostic is built with: the five error classes and the one name,
#: ``_error``, that ``parser.HeaderScanner`` and ``macros`` each give the
#: helper building one on its caller's behalf.
BUILDERS = frozenset({
    "ParseError",
    "ValidationError",
    "DirectiveError",
    "MacroExpansionError",
    "ModuleError",
    "_error",
})

CODE = re.compile(r"^[PVDEM][0-9]{3}$")

#: What builds a diagnostic in the D implementation. The five classes are
#: constructed with ``new``; ``fail`` and ``expansionError`` are the two names
#: given to a helper that builds one on its caller's behalf, as ``_error`` is
#: here. All of them spell the code as the first argument, which is what makes
#: them findable from here.
PORTED_BUILDERS = frozenset({
    "ParseError",
    "ValidationError",
    "DirectiveError",
    "MacroExpansionError",
    "ModuleError",
    "fail",
    "expansionError",
})

#: A call to one of those, up to and including its opening parenthesis. A
#: class is always constructed with ``new``, and the helper is always thrown
#: where it is called, so requiring one of those words is what separates a
#: construction site from the declaration of the helper itself.
_PORTED_CALL = re.compile(
    r"(?:new|throw)\s+(" + "|".join(sorted(PORTED_BUILDERS)) + r")\s*\("
)

#: A unittest block, which demonstrates a diagnostic rather than raising one.
_PORTED_UNITTEST = re.compile(r"(?<![A-Za-z0-9_])unittest\s*\{")

#: The highest number each kind has ever used. A new diagnostic takes the
#: next one and bumps this; a deleted one leaves its number behind as a gap,
#: because a code that has been published is never reused or renumbered.
#: The numbering was compacted once, before the v1 release, when the two
#: unreachable sites then numbered P018 and M022 were deleted and every
#: code above each moved down by one; from v1 on nothing is renumbered.
HIGHEST = {"P": 37, "V": 43, "D": 1, "E": 19, "M": 29}

#: A hole in a message: a template's ``{name}`` and a table row's ellipsis
#: stand for the same thing, so both reduce to nothing before comparison.
_HOLE = re.compile(r"\{[^{}]*\}|<[^<>]*>|…|\.\.\.")

#: One published row: its code, where it is raised, and the rest of the row.
_ROW = re.compile(r"^\| ([PVDEM][0-9]{3}) \| ([^|]*) \| (.*) \|$", re.M)

#: A code cited in prose. ASCII lookarounds rather than ``\b``: kana and
#: kanji count as word characters, so ``\b`` would skip a code glued to one.
#: W is left out on purpose: W001 and W002 were retired before v1 and
#: survive only as history in the freeze review.
_CITED = re.compile(r"(?<![A-Za-z0-9_])[PVDEM][0-9]{3}(?![A-Za-z0-9_])")


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


def ported_arguments(text, opening):
    """Split one D call's arguments, keeping strings and nesting intact."""

    arguments = []
    current = ""
    depth = 0
    index = opening
    while index < len(text):
        character = text[index]
        if character in "\"`":
            literal, index = ported_literal(text, index)
            current += literal
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                arguments.append(current)
                return arguments, index + 1
        elif character == "," and depth == 1:
            arguments.append(current)
            current = ""
            index += 1
            continue
        if depth >= 1 and not (depth == 1 and character == "("):
            current += character
        index += 1
    return arguments, index


def ported_literal(text, index):
    """One D string literal, returned whole so a comma inside it is safe."""

    quote = text[index]
    end = index + 1
    while end < len(text):
        if text[end] == "\\" and quote == '"':
            end += 2
            continue
        if text[end] == quote:
            return text[index:end + 1], end + 1
        end += 1
    return text[index:], len(text)


def ported_strings(argument):
    """The literal parts of one argument, concatenated."""

    parts = []
    index = 0
    while index < len(argument):
        character = argument[index]
        if character not in "\"`":
            index += 1
            continue
        literal, index = ported_literal(argument, index)
        body = literal[1:-1] if len(literal) >= 2 else ""
        if literal.startswith('"'):
            body = body.replace('\\"', '"').replace("\\\\", "\\")
        parts.append(body)
    return "".join(parts)


def without_unittests(text):
    """The source with its unittest blocks blanked out, newlines kept.

    A D module keeps its tests beside the code they test, and one of them
    builds a diagnostic to show what a chained one looks like. That is an
    example rather than a construction site, so it must not be read as one.
    Blanking rather than deleting keeps every remaining line number right.
    """

    for match in reversed(list(_PORTED_UNITTEST.finditer(text))):
        opening = text.index("{", match.start())
        depth = 0
        index = opening
        while index < len(text):
            character = text[index]
            if character in "\"`":
                _, index = ported_literal(text, index)
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    index += 1
                    break
            index += 1
        body = text[match.start():index]
        text = text[:match.start()] + re.sub(r"[^\n]", " ", body) + text[index:]
    return text


def ported_calls():
    """Every D call that builds a diagnostic, with where it is written."""

    for path in sorted(PORTED_SOURCE.rglob("*.d")):
        text = without_unittests(path.read_text(encoding="utf-8"))
        for match in _PORTED_CALL.finditer(text):
            arguments, _ = ported_arguments(text, match.end() - 1)
            line = text.count("\n", 0, match.start()) + 1
            yield path.name, line, match.group(1), arguments


def ported_codes():
    """Each literal code the D implementation owns, with its message."""

    found = {}
    for file, line, _, arguments in ported_calls():
        if not arguments:
            continue
        first = arguments[0].strip()
        if not (first.startswith('"') and first.endswith('"')):
            continue
        message = ported_strings(arguments[1]) if len(arguments) > 1 else None
        found.setdefault(first[1:-1], []).append((file, line, message))
    return found


def published_rows():
    """Each published row, as ``code -> (file, row text)``."""

    text = DESIGN.read_text(encoding="utf-8")
    rows = {}
    for code, where, rest in _ROW.findall(text):
        match = re.search(r"([a-z_]+\.py)", where)
        rows[code] = (match.group(1) if match else None, rest)
    return rows


def cited_codes():
    """Each code a document cites, as ``(file:line, code)``."""

    for path in DOCUMENTS:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            for code in _CITED.findall(line):
                yield f"{path.relative_to(ROOT)}:{number}", code


class DiagnosticCodeTests(unittest.TestCase):
    def test_every_diagnostic_is_built_with_a_code(self):
        for file, line, name, node in builder_calls():
            with self.subTest(where=f"{file}:{line}", builder=name):
                self.assertIsNotNone(
                    code_argument(node),
                    f"{file}:{line}: {name}(...) is missing code=",
                )

    def test_a_code_is_a_literal_or_a_deliberate_forward(self):
        # Two helpers pass their caller's code through, and two sites
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
        self.assertLessEqual(set(numbers), set(HIGHEST))
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
            {"D": 1, "E": 19, "M": 29, "P": 37, "V": 43},
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

    def test_no_code_has_two_published_rows(self):
        # published_rows() is a dict, so a duplicated row would pass the
        # set comparison unnoticed; a renumbering is exactly when one appears.
        text = DESIGN.read_text(encoding="utf-8")
        counted = Counter(code for code, _, _ in _ROW.findall(text))
        self.assertEqual({code: n for code, n in counted.items() if n > 1}, {})

    def test_every_code_a_document_cites_is_published(self):
        # Only doc/diagnostics.md is cross-checked row by row; the other
        # documents cite codes in prose, which is where a renumbering rots.
        published = set(published_rows())
        stale = sorted(
            f"{where}: {code}"
            for where, code in cited_codes()
            if code not in published
        )
        self.assertEqual(stale, [])


@unittest.skipUnless(
    PORTED_SOURCE.is_dir(), "the D implementation is not present in this checkout"
)
class PortedDiagnosticCodeTests(unittest.TestCase):
    """The same rules, applied to the D implementation.

    A document that fails has to fail the same way whichever implementation
    compiled it, so the two own exactly the same codes and say the same thing
    at each of them. Where the code is raised is not compared: the published
    table names a Python file, and the D module that raises it has a different
    name and sometimes a different shape.
    """

    def test_every_diagnostic_is_built_with_a_code(self):
        for file, line, name, arguments in ported_calls():
            with self.subTest(where=f"{file}:{line}", builder=name):
                self.assertTrue(arguments, f"{file}:{line}: {name}() has no arguments")
                first = arguments[0].strip()
                self.assertTrue(
                    (first.startswith('"') and first.endswith('"'))
                    or first in ("code", "error.code"),
                    f"{file}:{line}: the first argument must be a literal code, "
                    f"'code', or 'error.code'; got {first!r}",
                )

    def test_every_literal_code_is_well_formed(self):
        for code, places in ported_codes().items():
            with self.subTest(code=code):
                self.assertRegex(code, CODE, f"{code} at {places[0]}")

    def test_no_code_is_used_twice(self):
        duplicated = {
            code: [f"{file}:{line}" for file, line, _ in places]
            for code, places in ported_codes().items()
            if len(places) > 1
        }
        self.assertEqual(duplicated, {})

    def test_both_implementations_own_the_same_codes(self):
        # Neither may invent a code the other does not raise, and neither may
        # quietly drop one: a document that fails in one has to fail the same
        # way in the other.
        self.assertEqual(sorted(ported_codes()), sorted(owned_codes()))

    def test_every_kind_still_has_the_codes_it_had(self):
        counted = Counter(code[0] for code in ported_codes())
        self.assertEqual(
            dict(sorted(counted.items())),
            {"D": 1, "E": 19, "M": 29, "P": 37, "V": 43},
        )

    def test_each_code_says_what_its_published_row_says(self):
        rows = published_rows()
        for code, places in sorted(ported_codes().items()):
            file, line, message = places[0]
            if message is None:
                continue
            words = [word for word in skeleton(message).split(" ") if word]
            opening = " ".join(words[:5])
            if not opening:
                continue
            with self.subTest(code=code, where=f"{file}:{line}"):
                self.assertIn(
                    opening,
                    skeleton(rows[code][1]),
                    f"{code} at {file}:{line} says {opening!r}, which its "
                    f"published row does not",
                )


if __name__ == "__main__":
    unittest.main()
