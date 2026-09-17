/**
 * Publishing the canonical tree, for something other than TeX to read.
 *
 * A consumer that wants to turn a document into HTML, or into slides in a
 * browser, needs what the renderer is given rather than what it produces. This
 * exports exactly that: macros expanded, flags resolved, imports spliced,
 * values decided. Nothing here interprets TeX, and nothing re-scans anything.
 *
 * What it deliberately does not preserve is which syntax sugar the author
 * wrote. Two documents that mean the same thing export the same tree, which is
 * the point: a consumer reads meaning, not spelling.
 */
module texflux.external_ast;

import std.algorithm : map;
import std.array : array;
import std.sumtype : match;

import texflux : AstCompilationResult;
import texflux.ast;
import texflux.errors : CompilerDefect, ValueError;
import texflux.interchange : header, SpanEncoder, spanEncoder;
import texflux.json;

/**
 * Serialize one compiled document as the published AST.
 *
 * Text fragments are flattened to the opaque strings they already resolved to:
 * where a run came from belongs to the source map, which is what needs it.
 */
string serializeAst(AstCompilationResult result, bool pretty = false)
{
    auto encoder = Encoder(spanEncoder(result.sources));
    auto payload = header("texflux-ast", result.sources);
    auto document = jsonObject(member("type", "document"),
            member("body", encoder.block(result.document.body_)),
            member("span", encoder.span(result.document.span)));
    return dumpJson(jsonObject(payload ~ member("document", document)), pretty);
}

private struct Encoder
{
    SpanEncoder span;

    JsonValue block(Block* value)
    {
        return jsonObject(member("type", "block"),
                member("nodes", jsonArray(value.nodes.map!(child => node(child)).array)),
                member("span", span(value.span)));
    }

    JsonValue node(Node value)
    {
        return value.match!(
            (RawTex raw) => jsonObject(member("type", "raw"), member("text", raw.text),
                    member("span", span(raw.span))),
            (GenericInvocation invocation) {
                auto encoded = [
                    member("type", "invocation"),
                    member("form", invocation.body_ is null ? "command" : "container"),
                    member("name", invocation.name),
                    member("arguments", jsonArray(invocation.arguments
                        .map!(value_ => argument(value_)).array)),
                ];
                if (invocation.body_ !is null)
                    encoded ~= member("body", block(invocation.body_));
                return jsonObject(encoded ~ member("span", span(invocation.span)));
            },
            (BraceGroup group) => jsonObject(member("type", "group"),
                    member("header", group.headerRaw), member("body", block(group.body_)),
                    member("span", span(group.span))),
            (n) {
                throw new CompilerDefect("unsupported canonical AST node");
                return JsonValue.init;
            },
        );
    }

    /**
     * One argument, as its kind, its layout and its value.
     *
     * A binding list configures an import and is resolved away before a tree
     * is canonical, so meeting one here is a defect rather than a shape the
     * format has to describe.
     */
    JsonValue argument(Argument value)
    {
        if (value.kind == GroupKind.binding)
            throw new ValueError("unsupported canonical argument kind: "
                    ~ cast(string) value.kind);

        auto content = value.value.match!(
            (string text) {
                if (value.layout != ArgumentLayout.inline)
                    throw new ValueError("canonical argument layout does not match its value");
                return jsonObject(member("type", "text"), member("text", text));
            },
            (Block* nested) {
                if (value.layout == ArgumentLayout.inline)
                    throw new ValueError("canonical argument layout does not match its value");
                return block(nested);
            },
        );
        return jsonObject(member("kind", cast(string) value.kind),
                member("layout", cast(string) value.layout), member("value", content),
                member("span", span(value.span)));
    }
}
