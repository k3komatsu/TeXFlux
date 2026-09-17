/**
 * The JSON the published formats are written in.
 *
 * All three -- the source map, the external AST and the diagnostics report --
 * are compared byte for byte by their consumers and by this project's own
 * tests, so the writer here fixes every detail the format leaves open: members
 * keep the order they were added in, a compact document separates with `,` and
 * `:` alone, a pretty one indents by two spaces, text passes through as UTF-8
 * with only the characters JSON cannot hold escaped, and a document ends with
 * exactly one newline.
 *
 * `std.json` cannot be the writer, for four independent reasons: it stores an
 * object as an associative array and so prints members in sorted order rather
 * than the order the format specifies, it indents by four spaces, it escapes
 * `/` -- which every path in a source map contains -- and it escapes the
 * delete character with an uppercase hex spelling. Any one of those would be a
 * difference in every published byte.
 *
 * Reading JSON carries no such constraint, so the source-map loader does use
 * the standard library's parser.
 */
module texflux.json;

import std.array : appender, Appender;
import std.conv : to;
import std.format : format;
import std.range : repeat;
import std.sumtype : match, SumType, This;
import std.typecons : Tuple;

/**
 * A JSON document, or any value inside one.
 *
 * An object is a sequence rather than a map because the order its members were
 * added in is part of the output.
 */
alias JsonValue = SumType!(
    typeof(null),
    bool,
    long,
    string,
    This[],
    Tuple!(string, "key", This, "value")[],
);

/// One member of a JSON object, which keeps the position it was added at.
alias JsonMember = Tuple!(string, "key", JsonValue, "value");

/// The JSON null.
JsonValue jsonNull() @safe pure nothrow
{
    return JsonValue(null);
}

/// A JSON value of the type the argument spells.
JsonValue jsonOf(T)(T value) @safe pure nothrow
        if (is(T : bool) || is(T : long) || is(T : string))
{
    static if (is(T : bool))
        return JsonValue(cast(bool) value);
    else static if (is(T : string))
        return JsonValue(cast(string) value);
    else
        return JsonValue(cast(long) value);
}

/// A JSON array.
JsonValue jsonArray(JsonValue[] items) @safe pure nothrow
{
    return JsonValue(items);
}

/// A JSON object whose members print in the order given here.
JsonValue jsonObject(JsonMember[] members...) @safe pure nothrow
{
    return JsonValue(members.dup);
}

/// One member, for the object builder above.
JsonMember member(T)(string key, T value) @safe pure nothrow
{
    static if (is(T : JsonValue))
        return JsonMember(key, value);
    else
        return JsonMember(key, jsonOf(value));
}

/**
 * One published document: the value, and the newline every format ends with.
 *
 * A pretty document differs from a compact one only in its whitespace, so a
 * consumer that parses either reads the same thing.
 */
string dumpJson(JsonValue value, bool pretty = false) @safe pure
{
    auto output = appender!string();
    output.writeValue(value, pretty, 0);
    output.put('\n');
    return output.data;
}

///
@safe pure unittest
{
    assert(dumpJson(jsonObject(member("version", 1L))) == `{"version":1}` ~ "\n");
    assert(dumpJson(jsonObject(member("version", 1L)), true)
            == "{\n  \"version\": 1\n}\n");
    assert(dumpJson(jsonObject()) == "{}\n", "an empty object stays on one line");
    assert(dumpJson(jsonArray([]), true) == "[]\n");
}

/// Members print in the order they were added, never sorted.
@safe pure unittest
{
    const document = jsonObject(member("zebra", 1L), member("apple", 2L));
    assert(dumpJson(document) == `{"zebra":1,"apple":2}` ~ "\n");
}

private void writeValue(ref Appender!string output, JsonValue value, bool pretty, size_t depth) @safe pure
{
    value.match!(
        (typeof(null) _) => output.put("null"),
        (bool flag) => output.put(flag ? "true" : "false"),
        (long number) => output.put(number.to!string),
        (string text) => output.writeText(text),
        (JsonValue[] items) => output.writeArray(items, pretty, depth),
        (JsonMember[] members) => output.writeObject(members, pretty, depth),
    );
}

private void writeArray(ref Appender!string output, JsonValue[] items, bool pretty, size_t depth) @safe pure
{
    if (items.length == 0)
    {
        output.put("[]");
        return;
    }
    output.put('[');
    foreach (index, item; items)
    {
        if (index != 0)
            output.put(',');
        output.writeIndent(pretty, depth + 1);
        output.writeValue(item, pretty, depth + 1);
    }
    output.writeIndent(pretty, depth);
    output.put(']');
}

private void writeObject(ref Appender!string output, JsonMember[] members, bool pretty, size_t depth) @safe pure
{
    if (members.length == 0)
    {
        output.put("{}");
        return;
    }
    output.put('{');
    foreach (index, entry; members)
    {
        if (index != 0)
            output.put(',');
        output.writeIndent(pretty, depth + 1);
        output.writeText(entry.key);
        output.put(pretty ? ": " : ":");
        output.writeValue(entry.value, pretty, depth + 1);
    }
    output.writeIndent(pretty, depth);
    output.put('}');
}

private void writeIndent(ref Appender!string output, bool pretty, size_t depth) @safe pure
{
    if (!pretty)
        return;
    output.put('\n');
    output.put(' '.repeat(depth * 2));
}

/**
 * One JSON string.
 *
 * Text is written as UTF-8 rather than escaped into ASCII, so a Japanese
 * heading stays readable in a published AST. Only what JSON cannot hold
 * literally is escaped, and a control character without a short spelling takes
 * the six-character form with lowercase hex digits.
 */
private void writeText(ref Appender!string output, string text) @safe pure
{
    output.put('"');
    size_t plain = 0;
    foreach (index, char c; text)
    {
        string escape;
        switch (c)
        {
        case '"':
            escape = `\"`;
            break;
        case '\\':
            escape = `\\`;
            break;
        case '\b':
            escape = `\b`;
            break;
        case '\f':
            escape = `\f`;
            break;
        case '\n':
            escape = `\n`;
            break;
        case '\r':
            escape = `\r`;
            break;
        case '\t':
            escape = `\t`;
            break;
        default:
            if (c >= 0x20)
                continue;
            escape = format(`\u%04x`, cast(uint) c);
            break;
        }
        output.put(text[plain .. index]);
        output.put(escape);
        plain = index + 1;
    }
    output.put(text[plain .. $]);
    output.put('"');
}

/// Only what JSON cannot hold literally is escaped.
@safe pure unittest
{
    assert(dumpJson(jsonOf("日本")) == "\"日本\"\n", "text stays UTF-8");
    assert(dumpJson(jsonOf("a\"b\\c")) == `"a\"b\\c"` ~ "\n");
    assert(dumpJson(jsonOf("tab\there")) == `"tab\there"` ~ "\n");
    assert(dumpJson(jsonOf("bell\x07")) == "\"bell\\u0007\"\n");
    assert(dumpJson(jsonOf("del\x7f")) == "\"del\x7f\"\n", "only the controls below a space");
}
