import unittest

from texflux.ast import (
    Argument,
    ArgumentLayout,
    Block,
    BraceGroup,
    GenericInvocation,
    GroupKind,
    InvocationKind,
    RawTex,
    SourcePosition,
    SourceSpan,
    TextFragment,
    plain_text,
)


class AstTests(unittest.TestCase):
    def test_positions_advance_by_unicode_characters_and_lf(self):
        start = SourcePosition(3, 5)
        for text, expected in (
            ("", SourcePosition(3, 5)),
            ("日本語", SourcePosition(3, 8)),
            ("A\n", SourcePosition(4, 1)),
            ("\n日本\n語", SourcePosition(5, 2)),
        ):
            with self.subTest(text=text):
                self.assertEqual(start.advance(text), expected)

    def test_nodes_are_frozen_and_keep_spans(self):
        span = SourceSpan(
            "slides.tfx",
            SourcePosition(3, 5),
            SourcePosition(3, 9),
        )
        raw = RawTex("BODY", span)
        argument = Argument(GroupKind.REQUIRED, "A", ArgumentLayout.INLINE, span)
        node = GenericInvocation("foo", (argument,), Block((raw,), span), span)

        self.assertEqual(node.span, span)
        self.assertEqual(node.body.nodes, (raw,))
        with self.assertRaises(AttributeError):
            raw.text = "changed"

    def test_brace_group_is_a_canonical_node_with_a_span(self):
        span = SourceSpan(
            "slides.tfx",
            SourcePosition(8, 5),
            SourcePosition(8, 9),
        )
        group = BraceGroup(Block((RawTex("BODY", span),), span), span)

        self.assertEqual(group.span, span)
        self.assertEqual(group.body.nodes[0].text, "BODY")

    def test_text_fragments_must_match_their_field(self):
        span = SourceSpan("m.tfx", SourcePosition(1, 1), SourcePosition(1, 4))
        parts = (TextFragment("pre", span, True), TextFragment("A", span))
        self.assertEqual(plain_text(parts), "preA")
        self.assertEqual(RawTex("preA", span, parts).parts, parts)
        self.assertEqual(Argument(GroupKind.REQUIRED, "preA", ArgumentLayout.INLINE,
                                  span, parts).parts, parts)
        self.assertEqual(BraceGroup(Block((), span), span, "preA", parts).header_parts, parts)
        for create in (
            lambda: RawTex("wrong", span, parts),
            lambda: Argument(GroupKind.REQUIRED, "wrong", ArgumentLayout.INLINE, span, parts),
            lambda: Argument(GroupKind.REQUIRED, Block((), span), ArgumentLayout.BLOCK, span, parts),
            lambda: BraceGroup(Block((), span), span, "wrong", parts),
        ):
            with self.subTest(create=create):
                with self.assertRaises(ValueError):
                    create()

    def test_invocation_kind_is_explicit_in_syntax_ast(self):
        self.assertEqual(InvocationKind.COMMAND.value, "command")
        self.assertEqual(InvocationKind.ENVIRONMENT.value, "environment")


if __name__ == "__main__":
    unittest.main()
