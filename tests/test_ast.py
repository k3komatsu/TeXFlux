import unittest

from beamercraft.ast import (
    Argument,
    ArgumentLayout,
    Block,
    GenericInvocation,
    GroupKind,
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


if __name__ == "__main__":
    unittest.main()
