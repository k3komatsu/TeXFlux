import unittest

from beamercraft.ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    GenericInvocation,
    GroupKind,
    InvocationKind,
    RawTex,
    SourceLocation,
)


class AstTests(unittest.TestCase):
    def test_nodes_are_frozen_and_keep_locations(self):
        loc = SourceLocation("slides.bmc", 3, 5)
        raw = RawTex("BODY", loc)
        argument = Argument(GroupKind.REQUIRED, "A", ArgumentLayout.INLINE, loc)
        node = GenericInvocation("foo", (argument,), Block((raw,), loc), loc)

        self.assertEqual(node.loc, loc)
        self.assertEqual(node.body.nodes, (raw,))
        with self.assertRaises(AttributeError):
            raw.text = "changed"

    def test_brace_group_is_a_canonical_node_with_a_location(self):
        loc = SourceLocation("slides.bmc", 8, 5)
        group = BraceGroup(Block((RawTex("BODY", loc),), loc), loc)

        self.assertEqual(group.loc, loc)
        self.assertEqual(group.body.nodes[0].text, "BODY")

    def test_invocation_kind_is_explicit_in_syntax_ast(self):
        self.assertEqual(InvocationKind.COMMAND.value, "command")
        self.assertEqual(InvocationKind.ENVIRONMENT.value, "environment")


if __name__ == "__main__":
    unittest.main()
