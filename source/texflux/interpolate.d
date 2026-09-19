/**
 * Putting one bound value into a text field, without ever rescanning it.
 *
 * `!text{name}` is the only way a macro parameter reaches a textual position,
 * and it is deliberately not a textual macro: what it inserts is never parsed,
 * never scanned for further markers, and never able to become structure. A
 * value holding `::` stays the four characters the caller wrote.
 *
 * That restraint is what keeps the compiler predictable. Compiler metadata --
 * flag names, macro names, import paths, structural names -- is static, and
 * the marker is refused outright in each of those positions rather than
 * quietly producing something that depends on what a caller passed.
 *
 * Fields are scanned as code points, because a marker's span is measured in
 * columns.
 */
module texflux.interpolate;

import std.algorithm : canFind, countUntil, startsWith;
import std.conv : to;
import std.sumtype : get, has;
import std.typecons : Nullable, nullable;

import texflux.ast : RawTex;
import texflux.assets : AssetReference, AssetResolver, validateAssetPath;
import texflux.errors : BundleError, MacroExpansionError;
import texflux.macros : expansionError, Frame, isParameterName;
import texflux.source : plainText, SourcePosition, SourceSpan, SourceText, TextFragment;
import texflux.syntax : isEscaped;

/// The two markers a text field is scanned for, and nothing else.
private enum textMarker = "!text{"d;

/// ditto
private enum paramMarker = "!param{"d;

/// The resource marker is still a text-field marker, not a syntax node.
private enum assetMarker = "!asset{"d;

private BundleError malformedAsset(SourceSpan span, string message)
{
    return new BundleError("B001", message, span);
}

/** Find the closing brace of an asset path, including nested !text groups. */
private ptrdiff_t matchingAssetBrace(dstring text, size_t from) @safe pure
{
    size_t depth = 1;
    foreach (index; from .. text.length)
    {
        if (text[index] == '{')
            ++depth;
        else if (text[index] == '}' && --depth == 0)
            return cast(ptrdiff_t) index;
    }
    return -1;
}

private void validateAssetMarkers(dstring path, SourceSpan span)
{
    size_t index;
    while (index < path.length)
    {
        const at = path[index .. $].countUntil('!');
        if (at < 0)
            return;
        const marker = index + at;
        if (!path[marker .. $].startsWith(textMarker))
            throw malformedAsset(span,
                    "!asset paths may contain only !text{...} interpolation");
        const closeAt = path[marker + textMarker.length .. $].countUntil('}');
        if (closeAt < 0)
            throw malformedAsset(span, "unterminated !text{...} in !asset path");
        index = marker + textMarker.length + closeAt + 1;
    }
}

/**
 * One range inside a field's own span.
 *
 * A retargeted node can carry a span shorter than the text it holds, so a
 * computed range that would reach past the end falls back to the whole span
 * rather than inverting.
 */
SourceSpan markerSpan(SourceSpan origin, size_t offset, size_t length) @safe pure
{
    const start = SourcePosition(origin.start.line, origin.start.column + cast(int) offset);
    const end = SourcePosition(start.line, start.column + cast(int) length);
    if (end > origin.end)
        return origin;
    return SourceSpan(origin.file, start, end);
}

/**
 * Read one bound parameter as text.
 *
 * Text-extractable means exactly one raw node: anything structural has to go
 * through `!param`, which puts it in the tree rather than in a string.
 */
SourceText textValue(string name, Frame* frame)
{
    if (name in frame.sequences)
        throw new MacroExpansionError("E001",
                "'" ~ name ~ "' is a rest parameter; use !each to access its values",
                frame.callSpan);
    auto value = name in frame.values;
    if (value is null)
        throw new MacroExpansionError("E002", "unknown macro parameter '" ~ name ~ "'",
                frame.callSpan);

    // Text-extractable is exactly one raw node, so the two ways of failing
    // that are one diagnostic: a code names a place, not a wording.
    if (value.length != 1 || !(*value)[0].has!RawTex)
        throw new MacroExpansionError("E003", "macro parameter '" ~ name
                ~ "' is not a text value; use !param for structural values", frame.callSpan);
    auto raw = (*value)[0].get!RawTex;
    if (!raw.parts.isNull)
        return raw.parts.get;
    return [TextFragment(raw.text, raw.span)];
}

/// Compiler metadata is static, escaped marker spellings included.
void rejectMarkers(string text, SourceSpan span, string where)
{
    if (text.canFind("!text{") || text.canFind("!param{"))
        throw new MacroExpansionError("E004",
                "interpolation is not allowed in " ~ where, span);
}

/**
 * Interpolate one field, or report that it holds nothing to interpolate.
 *
 * Returning nothing rather than the same text is what lets the caller leave
 * the node completely untouched: a document with no marker in it renders
 * byte for byte as though this pass did not exist.
 *
 * `origin` is where the field was written and is what a diagnostic points at;
 * `target` is where the node now claims to be, which is the call site once a
 * template has been retargeted; `offset` is where the text starts inside its
 * own span, which is one column in for a group's contents and none for a raw
 * line.
 */
Nullable!SourceText interpolate(string text, SourceSpan origin, SourceSpan target,
        size_t offset, Frame* lookup, AssetResolver assets = null)
{
    if (!text.canFind('!'))
        return Nullable!SourceText.init;

    const scanned = text.to!dstring;
    TextFragment[] parts;
    size_t index = 0;
    size_t last = 0;
    bool found = false;

    // Only a template's own literals are scaffolding. The same text written at
    // a call site is the caller's content and keeps the higher rank.
    void literal(dstring value)
    {
        if (value.length != 0)
            parts ~= TextFragment(value.to!string, target, lookup !is null);
    }

    while (true)
    {
        const at = scanned[index .. $].countUntil('!');
        if (at < 0)
            break;
        const j = index + at;

        if (scanned.isEscaped(j))
        {
            index = j + 1;
            continue;
        }

        // A doubled marker writes the marker itself. What that produces is
        // never scanned again, which is what makes the escape final.
        const escape = scanned[j .. $].startsWith("!" ~ textMarker) ? textMarker
            : scanned[j .. $].startsWith("!" ~ paramMarker) ? paramMarker
            : scanned[j .. $].startsWith("!" ~ assetMarker) ? assetMarker : ""d;
        if (escape.length != 0)
        {
            literal(scanned[last .. j]);
            literal(escape);
            index = last = j + 1 + escape.length;
            found = true;
            continue;
        }

        if (scanned[j .. $].startsWith(textMarker))
        {
            const from = j + textMarker.length;
            const closeAt = scanned[from .. $].countUntil('}');
            if (closeAt < 0)
                throw expansionError("E005", "unterminated !text{...}",
                        markerSpan(origin, offset + j, textMarker.length), lookup);
            const close = from + closeAt;
            const name = scanned[from .. close].to!string;
            const span = markerSpan(origin, offset + j, close + 1 - j);
            if (!isParameterName(name))
                throw expansionError("E006",
                        "invalid !text parameter name '" ~ name ~ "'", span, lookup);
            if (lookup is null)
                throw expansionError("E007",
                        "!text is only valid inside a macro template", span, lookup);
            literal(scanned[last .. j]);
            try
                parts ~= textValue(name, lookup);
            catch (MacroExpansionError error)
                throw expansionError(error.code, error.message, span, lookup);
            index = last = close + 1;
            found = true;
            continue;
        }

        if (scanned[j .. $].startsWith(paramMarker))
            throw expansionError("E008", "!param cannot be used inside a text field;"
                    ~ " use !text for text interpolation",
                    markerSpan(origin, offset + j, paramMarker.length), lookup);

        if (scanned[j .. $].startsWith(assetMarker))
        {
            const from = j + assetMarker.length;
            const closeAt = matchingAssetBrace(scanned, from);
            const span = closeAt < 0
                ? markerSpan(origin, offset + j, assetMarker.length)
                : markerSpan(origin, offset + j, cast(size_t) closeAt + 1 - j);
            if (closeAt < 0)
                throw malformedAsset(span, "unterminated !asset{...}");

            const rawPath = scanned[from .. cast(size_t) closeAt];
            validateAssetMarkers(rawPath, span);

            // Reuse the text scanner for the one permitted nested marker, but
            // do not let a nested asset invoke the resolver a second time.
            const nested = interpolate(rawPath.to!string, origin, target,
                    offset + j + assetMarker.length, lookup);
            const path = nested.isNull ? rawPath.to!string : plainText(nested.get);
            validateAssetPath(path, span);
            const rendered = assets is null ? path
                : assets(AssetReference(path, span, target, origin.file, lookup !is null));

            literal(scanned[last .. j]);
            parts ~= TextFragment(rendered, span, lookup !is null);
            index = last = cast(size_t) closeAt + 1;
            found = true;
            continue;
        }

        index = j + 1;
    }

    if (!found)
        return Nullable!SourceText.init;
    literal(scanned[last .. $]);
    return parts.nullable;
}
