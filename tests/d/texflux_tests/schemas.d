/**
 * A deliberately small JSON Schema 2020-12 checker for the two published
 * TeXFlux documents.  It is test code, not a general-purpose schema library:
 * the implementation accepts exactly the assertion vocabulary used by the
 * checked-in AST and diagnostics schemas and rejects a typo in either schema.
 */
module texflux_tests.schemas;

import std.algorithm : canFind;
import std.array : array;
import std.file : readText;
import std.json : JSONType, JSONValue, parseJSON;
import std.path : buildPath;
import std.range : repeat;
import std.string : startsWith;
import std.utf : byDchar;

import texflux : compileAst;
import texflux.diagnostics : diagnose;
import texflux.external_ast : serializeAst;
import texflux.diagnostics : serializeDiagnostics;
import std.file : SpanMode, dirEntries, exists, mkdirRecurse, rmdirRecurse, tempDir, write;

private enum astSchemaPath = "schemas/texflux-ast-v1.schema.json";
private enum diagnosticsSchemaPath = "schemas/texflux-diagnostics-v1.schema.json";

private immutable string[] assertionKeywords = [
    "$ref", "type", "const", "enum", "required", "properties", "items",
    "prefixItems", "minItems", "minimum", "minLength", "maxLength", "pattern",
    "oneOf", "allOf", "if", "then", "else", "not",
];

private immutable string[] annotationKeywords = [
    "$schema", "$defs", "title", "description", "$comment",
];

private bool has(JSONValue value, string key)
{
    if (value.type != JSONType.object)
        return false;
    auto members = value.objectNoRef;
    return (key in members) !is null;
}

private JSONValue get(JSONValue value, string key)
{
    assert(value.type == JSONType.object);
    auto members = value.objectNoRef;
    auto found = key in members;
    assert(found !is null, "missing JSON member '" ~ key ~ "'");
    return *found;
}

private bool typeMatches(JSONValue value, string expected)
{
    final switch (expected)
    {
    case "object": return value.type == JSONType.object;
    case "array": return value.type == JSONType.array;
    case "string": return value.type == JSONType.string;
    case "integer": return value.type == JSONType.integer || value.type == JSONType.uinteger;
    case "number": return value.type == JSONType.integer || value.type == JSONType.uinteger
            || value.type == JSONType.float_;
    case "boolean": return value.type == JSONType.true_ || value.type == JSONType.false_;
    case "null": return value.type == JSONType.null_;
    }
}

private bool equal(JSONValue left, JSONValue right)
{
    if ((left.type == JSONType.integer || left.type == JSONType.uinteger || left.type == JSONType.float_)
            && (right.type == JSONType.integer || right.type == JSONType.uinteger
                || right.type == JSONType.float_))
    {
        const real a = left.type == JSONType.integer ? cast(real) left.integer
            : left.type == JSONType.uinteger ? cast(real) left.uinteger : left.floating;
        const real b = right.type == JSONType.integer ? cast(real) right.integer
            : right.type == JSONType.uinteger ? cast(real) right.uinteger : right.floating;
        return a == b;
    }
    if (left.type != right.type)
        return false;
    final switch (left.type)
    {
    case JSONType.null_: return true;
    case JSONType.true_:
    case JSONType.false_: return left.boolean == right.boolean;
    case JSONType.string: return left.str == right.str;
    case JSONType.integer: return left.integer == right.integer;
    case JSONType.uinteger: return left.uinteger == right.uinteger;
    case JSONType.float_: return left.floating == right.floating;
    case JSONType.array:
        if (left.array.length != right.array.length)
            return false;
        foreach (index, child; left.array)
            if (!equal(child, right.array[index]))
                return false;
        return true;
    case JSONType.object:
        auto a = left.objectNoRef;
        auto b = right.objectNoRef;
        if (a.length != b.length)
            return false;
        foreach (key, child; a)
        {
            auto other = key in b;
            if (other is null || !equal(child, *other))
                return false;
        }
        return true;
    }
}

private size_t codePoints(string text)
{
    size_t result;
    foreach (dchar _; text.byDchar)
        ++result;
    return result;
}

private bool patternMatches(string text, string pattern)
{
    // The checked-in schemas intentionally use only three regular-expression
    // shapes.  Keeping this tiny matcher avoids making a test depend on a
    // regex engine whose syntax is broader than the schema contract.
    if (pattern == "^[0-9a-f]{64}$")
    {
        if (text.length != 64)
            return false;
        foreach (char c; text)
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')))
                return false;
        return true;
    }
    if (pattern == "^[A-Z][0-9]{3}$")
    {
        if (text.length != 4 || text[0] < 'A' || text[0] > 'Z')
            return false;
        foreach (char c; text[1 .. $])
            if (c < '0' || c > '9')
                return false;
        return true;
    }
    if (pattern == "^[a-z]+$")
    {
        if (text.length == 0)
            return false;
        foreach (char c; text)
            if (c < 'a' || c > 'z')
                return false;
        return true;
    }
    assert(false, "unsupported test-schema pattern: " ~ pattern);
    return false;
}

private JSONValue resolveRef(JSONValue root, string reference)
{
    assert(reference.startsWith("#/$defs/"), "unsupported schema reference: " ~ reference);
    const name = reference[8 .. $];
    auto defs = get(root, "$defs");
    auto members = defs.objectNoRef;
    auto found = name in members;
    assert(found !is null, "unresolved schema reference: " ~ reference);
    return *found;
}

private void inspectSchema(JSONValue schema, JSONValue root)
{
    assert(schema.type == JSONType.object, "schema node is not an object");
    foreach (key, _; schema.objectNoRef)
        assert(assertionKeywords.canFind(key) || annotationKeywords.canFind(key),
                "unknown schema assertion keyword: " ~ key);

    if (has(schema, "$ref"))
    {
        auto reference = get(schema, "$ref");
        assert(reference.type == JSONType.string, "schema $ref must be a string");
        resolveRef(root, reference.str);
    }
    if (has(schema, "$defs"))
    {
        auto defs = get(schema, "$defs");
        assert(defs.type == JSONType.object, "schema $defs must be an object");
        foreach (_, child; defs.objectNoRef)
            inspectSchema(child, root);
    }
    if (has(schema, "properties"))
    {
        auto properties = get(schema, "properties");
        assert(properties.type == JSONType.object, "schema properties must be an object");
        foreach (_, child; properties.objectNoRef)
            inspectSchema(child, root);
    }
    if (has(schema, "items"))
        inspectSchema(get(schema, "items"), root);
    if (has(schema, "prefixItems"))
    {
        auto prefixItems = get(schema, "prefixItems");
        assert(prefixItems.type == JSONType.array, "schema prefixItems must be an array");
        foreach (child; prefixItems.array)
            inspectSchema(child, root);
    }
    foreach (keyword; ["oneOf", "allOf"])
        if (has(schema, keyword))
        {
            auto branches = get(schema, keyword);
            assert(branches.type == JSONType.array,
                    "schema " ~ keyword ~ " must be an array");
            foreach (branch; branches.array)
                inspectSchema(branch, root);
        }
    foreach (keyword; ["if", "then", "else", "not"])
        if (has(schema, keyword))
            inspectSchema(get(schema, keyword), root);
}

private bool valid(JSONValue value, JSONValue schema, JSONValue root);

private bool valid(JSONValue value, JSONValue schema, JSONValue root)
{
    assert(schema.type == JSONType.object, "schema node is not an object");
    foreach (key, _; schema.objectNoRef)
    {
        if (!assertionKeywords.canFind(key) && !annotationKeywords.canFind(key))
            assert(false, "unknown schema assertion keyword: " ~ key);
    }

    bool result = true;
    if (has(schema, "$ref"))
        result = valid(value, resolveRef(root, get(schema, "$ref").str), root) && result;

    if (has(schema, "type"))
    {
        auto type = get(schema, "type");
        assert(type.type == JSONType.string, "schema type must be a string");
        result = typeMatches(value, type.str) && result;
    }
    if (has(schema, "const"))
        result = equal(value, get(schema, "const")) && result;
    if (has(schema, "enum"))
    {
        auto choices = get(schema, "enum");
        assert(choices.type == JSONType.array, "schema enum must be an array");
        bool found;
        foreach (choice; choices.array)
            found = found || equal(value, choice);
        result = found && result;
    }
    if (has(schema, "required"))
    {
        auto required = get(schema, "required");
        assert(required.type == JSONType.array, "schema required must be an array");
        if (value.type == JSONType.object)
            foreach (name; required.array)
            {
                assert(name.type == JSONType.string);
                result = has(value, name.str) && result;
            }
    }
    if (has(schema, "properties") && value.type == JSONType.object)
    {
        foreach (name, childSchema; get(schema, "properties").objectNoRef)
        {
            auto members = value.objectNoRef;
            auto found = name in members;
            if (found !is null)
                result = valid(*found, childSchema, root) && result;
        }
    }
    size_t prefixLength;
    if (has(schema, "prefixItems") && value.type == JSONType.array)
    {
        auto schemas = get(schema, "prefixItems");
        assert(schemas.type == JSONType.array);
        foreach (index, childSchema; schemas.array)
            if (index < value.array.length)
                result = valid(value.array[index], childSchema, root) && result;
        prefixLength = schemas.array.length;
    }
    if (has(schema, "items") && value.type == JSONType.array)
        foreach (index, child; value.array)
            if (index >= prefixLength)
                result = valid(child, get(schema, "items"), root) && result;
    if (has(schema, "minItems") && value.type == JSONType.array)
        result = value.array.length >= get(schema, "minItems").integer && result;
    if (has(schema, "minimum") && (value.type == JSONType.integer
            || value.type == JSONType.uinteger || value.type == JSONType.float_))
    {
        const real minimum = get(schema, "minimum").type == JSONType.integer
            ? cast(real) get(schema, "minimum").integer : cast(real) get(schema, "minimum").uinteger;
        const real number = value.type == JSONType.integer ? cast(real) value.integer
            : value.type == JSONType.uinteger ? cast(real) value.uinteger : value.floating;
        result = number >= minimum && result;
    }
    if (has(schema, "minLength") && value.type == JSONType.string)
        result = codePoints(value.str) >= get(schema, "minLength").integer && result;
    if (has(schema, "maxLength") && value.type == JSONType.string)
        result = codePoints(value.str) <= get(schema, "maxLength").integer && result;
    if (has(schema, "pattern") && value.type == JSONType.string)
        result = patternMatches(value.str, get(schema, "pattern").str) && result;
    if (has(schema, "oneOf"))
    {
        auto branches = get(schema, "oneOf");
        assert(branches.type == JSONType.array);
        size_t matches;
        foreach (branch; branches.array)
            matches += valid(value, branch, root);
        result = matches == 1 && result;
    }
    if (has(schema, "allOf"))
    {
        auto branches = get(schema, "allOf");
        assert(branches.type == JSONType.array);
        foreach (branch; branches.array)
            result = valid(value, branch, root) && result;
    }
    if (has(schema, "if"))
    {
        const condition = valid(value, get(schema, "if"), root);
        if (condition && has(schema, "then"))
            result = valid(value, get(schema, "then"), root) && result;
        if (!condition && has(schema, "else"))
            result = valid(value, get(schema, "else"), root) && result;
    }
    if (has(schema, "not"))
        result = !valid(value, get(schema, "not"), root) && result;
    return result;
}

private JSONValue schema(string path)
{
    return parseJSON(readText(path));
}

private void assertSchemaSelfConsistent(JSONValue value)
{
    assert(value.type == JSONType.object);
    assert(get(value, "$schema").str == "https://json-schema.org/draft/2020-12/schema");
    // Walk every nested subschema so local references and assertion spellings
    // are checked even when a particular branch is not reached by a sample.
    inspectSchema(value, value);
}

private JSONValue astPayload()
{
    enum source = "@frame{title}::\n    \\foo::\n        text\n";
    return parseJSON(serializeAst(compileAst(source, "schema.tfx")));
}

private JSONValue diagnosticsPayload()
{
    return parseJSON(serializeDiagnostics(diagnose("\\bad::\n", "schema.tfx")));
}

private JSONValue parseErrorPayload()
{
    return parseJSON(serializeDiagnostics(diagnose("@frame{x}:\n    @foo{bad\n",
            "schema.tfx")));
}

private JSONValue relatedDiagnosticsPayload()
{
    const root = buildPath(tempDir(), "texflux-schema-related");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const broken = buildPath(root, "broken.tfx");
    const middle = buildPath(root, "mid.tfx");
    const main = buildPath(root, "main.tfx");
    write(broken, "!defmacro{m}{x}{x}::\n    A\n");
    write(middle, "!import{broken.tfx}\n");
    write(main, "!import{mid.tfx}\n");
    return parseJSON(serializeDiagnostics(diagnose(readText(main), main)));
}

unittest
{
    auto ast = schema(astSchemaPath);
    auto diagnostics = schema(diagnosticsSchemaPath);
    assertSchemaSelfConsistent(ast);
    assertSchemaSelfConsistent(diagnostics);
    assert(valid(astPayload(), ast, ast));
    assert(valid(parseJSON(serializeAst(compileAst("", "empty.tfx"))), ast, ast));
    assert(valid(diagnosticsPayload(), diagnostics, diagnostics));
    auto clean = parseJSON(serializeDiagnostics(diagnose("@frame{x}::\n    body\n",
            "clean.tfx")));
    assert(valid(clean, diagnostics, diagnostics));
    assert(clean["diagnostics"].array.length == 0);
    auto empty = parseJSON(serializeDiagnostics(diagnose("", "empty.tfx")));
    assert(valid(empty, diagnostics, diagnostics));
    auto parseError = parseErrorPayload();
    assert(valid(parseError, diagnostics, diagnostics));
    assert(parseError["diagnostics"].array.length == 1);
    auto related = relatedDiagnosticsPayload();
    assert(valid(related, diagnostics, diagnostics));
    assert(related["diagnostics"].array[0]["related"].array.length != 0);
}

unittest
{
    auto ast = schema(astSchemaPath);
    foreach (entry; dirEntries("tests/golden", SpanMode.shallow))
    {
        if (!entry.isDir)
            continue;
        auto input = buildPath(entry.name, "input.tfx");
        if (exists(input))
            assert(valid(parseJSON(serializeAst(compileAst(readText(input), input))), ast, ast),
                    "golden schema rejected " ~ input);
    }
}

unittest
{
    auto ast = schema(astSchemaPath);
    auto value = astPayload();
    // Unknown members and invocation names are explicitly open in v1.
    value["future_metadata"] = true;
    value["document"]["body"]["nodes"][0]["name"] = "unknown*";
    assert(valid(value, ast, ast));

    // Every replacement mutation from the published AST schema contract.
    auto invalid = astPayload();
    invalid["format"] = "other";
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["version"] = true;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["sources"] = parseJSON("[]");
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["sources"][0]["id"] = 1;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["sources"][0]["sha256"] = "A".repeat(64).array;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["sources"][0]["sha256"] = "0".repeat(63).array;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["type"] = "item";
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["body"] = JSONValue(null);
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["body"]["nodes"][0]["body"] = JSONValue(null);
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    auto replacement = astPayload();
    invalid["document"]["body"]["nodes"][0]["body"]["nodes"][0]["body"] =
        replacement["document"]["body"];
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["arguments"][0]["kind"] = "binding";
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["arguments"][0]["layout"] = "block";
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["body"]["nodes"][0]["arguments"][0]["layout"] =
        "inline";
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["span"]["source"] = -1;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["span"]["start"]["line"] = 0;
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0]["span"]["end"]["column"] = 1.5;
    assert(!valid(invalid, ast, ast));

    // Every deletion mutation from the published AST schema contract.
    invalid = astPayload();
    invalid["document"].objectNoRef.remove("span");
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0].objectNoRef.remove("body");
    assert(!valid(invalid, ast, ast));
    invalid = astPayload();
    invalid["document"]["body"]["nodes"][0].objectNoRef.remove("span");
    assert(!valid(invalid, ast, ast));
}

unittest
{
    auto diagnostics = schema(diagnosticsSchemaPath);
    auto value = diagnosticsPayload();
    value["producer"]["future"] = true;
    assert(valid(value, diagnostics, diagnostics));
    value["producer"]["name"] = "other";
    assert(!valid(value, diagnostics, diagnostics));

    auto warning = diagnosticsPayload();
    warning["diagnostics"] = parseJSON(
        `[{"severity":"warning","kind":"future","code":"X999","message":"reserved","span":{"source":0,"start":{"line":1,"column":1},"end":{"line":1,"column":1}},"related":[]}]`);
    assert(valid(warning, diagnostics, diagnostics));
    warning["diagnostics"][0]["severity"] = "fatal";
    assert(!valid(warning, diagnostics, diagnostics));
}

unittest
{
    auto diagnostics = schema(diagnosticsSchemaPath);

    auto zeroWidth = parseErrorPayload();
    zeroWidth["diagnostics"][0]["span"]["end"] =
        zeroWidth["diagnostics"][0]["span"]["start"];
    assert(valid(zeroWidth, diagnostics, diagnostics));

    auto multiline = parseErrorPayload();
    multiline["diagnostics"][0]["span"]["end"]["line"] =
        multiline["diagnostics"][0]["span"]["start"]["line"].integer + 3;
    assert(valid(multiline, diagnostics, diagnostics));

    // Every replacement mutation from the published diagnostics schema contract.
    auto invalid = parseErrorPayload();
    invalid["format"] = "other";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["version"] = 2;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["version"] = true;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["root"] = 1;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["sources"] = parseJSON("[]");
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["sources"][0]["id"] = 1;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["sources"][0]["sha256"] = "0".repeat(63).array;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["sources"][0]["sha256"] = "A".repeat(64).array;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["severity"] = "hint";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["severity"] = "info";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["code"] = "p1";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["code"] = "PP01";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["kind"] = "Parse";
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["span"]["start"]["line"] = 0;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["span"]["source"] = -1;
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0]["span"]["end"]["column"] = 1.5;
    assert(!valid(invalid, diagnostics, diagnostics));

    // Every deletion mutation from the published diagnostics schema contract.
    invalid = parseErrorPayload();
    invalid["diagnostics"][0].objectNoRef.remove("related");
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0].objectNoRef.remove("code");
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["diagnostics"][0].objectNoRef.remove("span");
    assert(!valid(invalid, diagnostics, diagnostics));
    invalid = parseErrorPayload();
    invalid["sources"][0].objectNoRef.remove("sha256");
    assert(!valid(invalid, diagnostics, diagnostics));
}
