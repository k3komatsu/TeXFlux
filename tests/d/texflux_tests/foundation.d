/**
 * Agreement with the Python implementation on the rules under everything else.
 *
 * The modules themselves carry the unit tests that say what they mean. What is
 * checked here is narrower and cannot be checked there: that the whitespace
 * set, the decoder's complaints, the quoting of a path and the spelling of a
 * published document are the same in both implementations, down to the byte.
 * Every expectation comes from texflux_tests.fixtures, which is generated from
 * the other implementation rather than written by hand.
 */
module texflux_tests.foundation;

import std.algorithm : canFind;
import std.exception : collectExceptionMsg;
import std.format : format;
import std.range : iota;

import texflux.json;
import texflux.text;

import texflux_tests.fixtures;

private bool listedAsWhitespace(uint code)
{
    foreach (immutable range; whitespaceRanges)
        if (code >= range[0] && code <= range[1])
            return true;
    return false;
}

/// Both implementations call the same code points whitespace, all 1.1 million.
unittest
{
    foreach (immutable uint code; iota(0u, 0x11_0000u))
    {
        if (code >= 0xD800 && code <= 0xDFFF)
            continue;
        const expected = listedAsWhitespace(code);
        assert(isWhitespace(cast(dchar) code) == expected,
                format("U+%04X should%s be whitespace", code, expected ? "" : " not"));
    }
}

/// Both implementations call the same code points printable, all 1.1 million.
unittest
{
    auto expected = new bool[0x11_0000];
    foreach (immutable range; printableRanges)
        expected[range[0] .. range[1] + 1] = true;
    foreach (uint code; 0 .. 0x11_0000)
        assert(isPrintable(cast(dchar) code) == expected[code],
                format("U+%04X should%s be printable", code, expected[code] ? "" : " not"));
}

/// A bad byte is reported with the wording the other decoder uses.
unittest
{
    foreach (immutable testCase; decodeFailures)
    {
        assert(!isValidUtf8(testCase.data));
        const message = collectExceptionMsg!UnicodeDecodeError(decodeUtf8(testCase.data));
        assert(message == testCase.message,
                format("for %s expected %s but got %s", testCase.data,
                    testCase.message, message));
    }
}

/// A path inside an operating-system error is quoted the same way.
unittest
{
    foreach (immutable testCase; quotedCases)
        assert(quoted(testCase.text) == testCase.quoted,
                format("expected %s but got %s", testCase.quoted, quoted(testCase.text)));
}

/**
 * The document each JSON fixture was generated from.
 *
 * The fixture records only what the other implementation printed, so the value
 * itself has to be built here. Keep the two in step: a name added to the
 * generator needs a case added below.
 */
private JsonValue fixtureDocument(string name)
{
    switch (name)
    {
    case "empty object":
        return jsonObject();
    case "empty array":
        return jsonArray([]);
    case "flat":
        return jsonObject(member("format", "texflux-ast"), member("version", 1L),
                member("root", 0L));
    case "nested":
        return jsonObject(
                member("a", jsonObject(member("b", jsonArray([jsonOf(1L), jsonOf(2L), jsonOf(3L)])))),
                member("c", jsonArray([])));
    case "text":
        return jsonObject(member("text",
                "quote \" backslash \\ newline \n tab \t bell \x07 del \x7f"));
    case "unicode":
        return jsonObject(member("text", "日本\U0001f600"), member("name", "é"));
    case "bools":
        return jsonObject(member("yes", true), member("no", false),
                member("none", jsonNull()));
    case "deep":
        return jsonObject(member("a",
                jsonArray([jsonObject(member("b", jsonArray([jsonObject(member("c", 1L))])))])));
    case "array of objects":
        return jsonArray([
            jsonObject(member("id", 0L), member("file", "a.tfx")),
            jsonObject(member("id", 1L), member("file", "b.tfx")),
        ]);
    default:
        assert(false, "no document is built for the fixture named " ~ name);
    }
}

/// A published document is written exactly as the other implementation writes it.
unittest
{
    foreach (immutable testCase; jsonCases)
    {
        auto document = fixtureDocument(testCase.name);
        const compact = dumpJson(document, false);
        const pretty = dumpJson(document, true);
        assert(compact == testCase.compact,
                format("%s compact: expected %s but got %s", testCase.name,
                    testCase.compact, compact));
        assert(pretty == testCase.pretty,
                format("%s pretty: expected %s but got %s", testCase.name,
                    testCase.pretty, pretty));
    }
}

/// Every published document ends with exactly one newline and holds no other.
unittest
{
    foreach (immutable testCase; jsonCases)
    {
        assert(testCase.compact[$ - 1] == '\n');
        assert(!testCase.compact[0 .. $ - 1].canFind('\n'));
        assert(testCase.pretty[$ - 1] == '\n');
    }
}
