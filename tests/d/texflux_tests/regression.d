/**
 * The frozen, D-only regression corpus.
 *
 * The JSONL file is the frozen v1 corpus and contains every input and every
 * observable result. This test deliberately drives the
 * public CLI callback in-process: a subprocess would test the wrapper rather
 * than the library and would make the test depend on a build artifact.
 */
module texflux_tests.regression;

import std.algorithm : canFind, sort;
import std.array : appender, Appender;
import std.conv : to;
import std.file : chdir, exists, getcwd, mkdirRecurse, read, readText, remove,
    rename, rmdirRecurse, tempDir, write;
import std.format : format;
import std.json : JSONType, JSONValue, parseJSON;
import std.path : buildPath, dirName, isAbsolute;
import std.process : thisProcessID;
import std.range : repeat;
import std.string : endsWith, replace, split, startsWith;
import std.sumtype : match;
import std.typecons : Nullable, nullable;

import texflux.ast : Argument, Block, BraceGroup, Document, GenericInvocation,
    GroupKind, Node, ParsedInvocation, RawTex, SequenceEntry, SpecialInvocation,
    Stack, SuiteMode;
import texflux.cli : CommandStreams, run;
import texflux.errors : FlagError, NestingError, TeXFluxError;
import texflux.parser : parse;
import texflux.pipeline : normalize;
import texflux.render : render;
import texflux.source : SourceSpan, SourceText;
import texflux.json : JsonValue, dumpJson, jsonArray, jsonNull, jsonObject, member;

private enum fixturePath = "tests/regression/v1.jsonl";
private enum expectedSourceCommit = "a54d7ee218b7ff2bc7e521ff6fc5db40c27f65a0";
private enum expectedCases = 905;
private enum expectedOutcomes = 9101;
private immutable string[] checkNames = [
    "ast", "ast-dotted-path", "ast-pretty", "check", "check-json", "check-stdin",
    "check-unknown-flags", "compile", "compile-comments", "compile-flags", "remap",
    "remap-bad-version", "syntax", "tex", "tex-comments",
];

struct FixtureCase
{
    string name;
    string filename;
    string[string] files;
    JSONValue outcomes;
}

struct Fixture
{
    FixtureCase[] cases;
    size_t outcomes;
}

private JSONValue field(JSONValue object, string name)
{
    assert(object.type == JSONType.object, "expected JSON object");
    auto found = name in object.objectNoRef;
    assert(found !is null, "missing JSON member '" ~ name ~ "'");
    return *found;
}

private string textField(JSONValue object, string name)
{
    auto value = field(object, name);
    assert(value.type == JSONType.string, "JSON member '" ~ name ~ "' is not text");
    return value.str;
}

private long integerField(JSONValue object, string name)
{
    auto value = field(object, name);
    assert(value.type == JSONType.integer || value.type == JSONType.uinteger,
            "JSON member '" ~ name ~ "' is not an integer");
    return value.type == JSONType.integer ? value.integer : cast(long) value.uinteger;
}

private bool validRelativePath(string path)
{
    if (path.length == 0 || path.isAbsolute || path.canFind('\\'))
        return false;
    foreach (part; path.split("/"))
        if (part.length == 0 || part == "." || part == "..")
            return false;
    return true;
}

private Fixture loadFixture()
{
    Fixture result;
    bool[string] seenChecks;
    auto lines = readText(fixturePath).split("\n");
    assert(lines.length > 1 && lines[$ - 1].length == 0,
            "regression fixture must end with one LF");
    auto header = parseJSON(lines[0]);
    assert(textField(header, "record") == "header");
    assert(textField(header, "format") == "texflux-regression");
    assert(integerField(header, "version") == 1);
    assert(textField(header, "source_commit") == expectedSourceCommit);
    assert(integerField(header, "case_count") == expectedCases);
    assert(integerField(header, "outcome_count") == expectedOutcomes);
    assert(textField(header, "workspace_token") == "${WORKSPACE}");
    assert(textField(header, "producer_version_token") == "${TEXFLUX_VERSION}");

    string[string] seen;
    foreach (line; lines[1 .. $ - 1])
    {
        auto record = parseJSON(line);
        assert(textField(record, "record") == "case");
        auto name = textField(record, "name");
        assert(name !in seen, "duplicate regression case '" ~ name ~ "'");
        seen[name] = name;
        FixtureCase current;
        current.name = name;
        current.filename = textField(record, "filename");
        assert(validRelativePath(current.filename), "invalid root path in " ~ name);
        auto fileValues = field(record, "files");
        assert(fileValues.type == JSONType.array, "files is not an array in " ~ name);
        foreach (value; fileValues.arrayNoRef)
        {
            auto path = textField(value, "path");
            assert(validRelativePath(path), "invalid file path in " ~ name);
            assert(path !in current.files, "duplicate file path in " ~ name);
            current.files[path] = textField(value, "text");
        }
        assert(current.filename in current.files, "root file missing in " ~ name);
        current.outcomes = field(record, "outcomes");
        assert(current.outcomes.type == JSONType.array, "outcomes is not an array in " ~ name);
        foreach (outcome; current.outcomes.arrayNoRef)
        {
            const check = textField(outcome, "check");
            assert(checkNames.canFind(check), "unknown check '" ~ check ~ "' in " ~ name);
            seenChecks[check] = true;
            assert(field(outcome, "stdout").type == JSONType.string);
            assert(field(outcome, "stderr").type == JSONType.string);
            auto files = field(outcome, "files");
            assert(files.type == JSONType.array);
            foreach (file; files.arrayNoRef)
            {
                assert(validRelativePath(textField(file, "path")));
                auto content = field(file, "text");
                assert(content.type == JSONType.string || content.type == JSONType.null_);
            }
            ++result.outcomes;
        }
        result.cases ~= current;
    }
    assert(result.cases.length == expectedCases,
            format("expected %d cases, found %d", expectedCases, result.cases.length));
    assert(result.outcomes == expectedOutcomes,
            format("expected %d outcomes, found %d", expectedOutcomes, result.outcomes));
    foreach (check; checkNames)
        assert(check in seenChecks, "regression fixture has no '" ~ check ~ "' outcome");
    return result;
}

private size_t skipString(string text, size_t at)
{
    assert(text[at] == '"');
    ++at;
    while (at < text.length)
    {
        if (text[at] == '\\')
            at += 2;
        else if (text[at++] == '"')
            return at;
    }
    assert(false, "unterminated JSON string");
    return at;
}

private size_t skipWhitespace(string text, size_t at)
{
    while (at < text.length && (text[at] == ' ' || text[at] == '\n'
            || text[at] == '\r' || text[at] == '\t'))
        ++at;
    return at;
}

private size_t skipValue(string text, size_t at)
{
    at = skipWhitespace(text, at);
    assert(at < text.length, "missing JSON value");
    if (text[at] == '"')
        return skipString(text, at);
    if (text[at] == '{')
    {
        ++at;
        while (true)
        {
            at = skipWhitespace(text, at);
            if (text[at] == '}')
                return at + 1;
            at = skipString(text, at);
            at = skipWhitespace(text, at);
            assert(text[at++] == ':');
            at = skipValue(text, at);
            at = skipWhitespace(text, at);
            if (text[at] == '}')
                return at + 1;
            assert(text[at++] == ',');
        }
    }
    if (text[at] == '[')
    {
        ++at;
        while (true)
        {
            at = skipWhitespace(text, at);
            if (text[at] == ']')
                return at + 1;
            at = skipValue(text, at);
            at = skipWhitespace(text, at);
            if (text[at] == ']')
                return at + 1;
            assert(text[at++] == ',');
        }
    }
    while (at < text.length && !",]}".canFind(text[at])
            && !" \n\r\t".canFind(text[at]))
        ++at;
    return at;
}

/** Replace only producer.version and retain compact/pretty whitespace exactly. */
private string normalizeProducerVersion(string text)
{
    auto at = skipWhitespace(text, 0);
    if (at >= text.length || text[at] != '{')
        return text;
    ++at;
    while (true)
    {
        at = skipWhitespace(text, at);
        if (text[at] == '}')
            return text;
        const keyStart = at;
        at = skipString(text, at);
        const key = text[keyStart .. at];
        at = skipWhitespace(text, at);
        assert(text[at++] == ':');
        at = skipWhitespace(text, at);
        const valueStart = at;
        const valueEnd = skipValue(text, at);
        if (key == `"producer"` && text[valueStart] == '{')
        {
            auto inner = valueStart + 1;
            while (true)
            {
                inner = skipWhitespace(text, inner);
                if (text[inner] == '}')
                    break;
                const innerKeyStart = inner;
                inner = skipString(text, inner);
                const innerKey = text[innerKeyStart .. inner];
                inner = skipWhitespace(text, inner);
                assert(text[inner++] == ':');
                inner = skipWhitespace(text, inner);
                const innerValueStart = inner;
                const innerValueEnd = skipValue(text, inner);
                if (innerKey == `"version"` && text[innerValueStart] == '"')
                    return text[0 .. innerValueStart] ~ `"${TEXFLUX_VERSION}"`
                        ~ text[innerValueEnd .. $];
                inner = skipWhitespace(text, innerValueEnd);
                if (text[inner] == ',')
                    ++inner;
                else
                    break;
            }
        }
        at = skipWhitespace(text, valueEnd);
        if (text[at] == ',')
            ++at;
        else
            return text;
    }
}

private string normalizeSynctex(string text)
{
    size_t written;
    size_t anchorOrigin;
    auto output = appender!string();
    size_t start;
    while (start < text.length)
    {
        size_t bodyEnd = start;
        while (bodyEnd < text.length && text[bodyEnd] != '\n' && text[bodyEnd] != '\r')
            ++bodyEnd;
        size_t after = bodyEnd;
        if (after < text.length && text[after] == '\r')
            ++after;
        if (after < text.length && text[after] == '\n')
            ++after;
        auto body = text[start .. bodyEnd];
        auto ending = text[bodyEnd .. after];
        bool anchor = body.length > 1 && body[0] == '!';
        for (size_t index = 1; anchor && index < body.length; ++index)
            anchor = body[index] >= '0' && body[index] <= '9';
        if (anchor)
        {
            body = "!" ~ (written - anchorOrigin).to!string;
            anchorOrigin = written;
        }
        output.put(body);
        output.put(ending);
        written += body.length + ending.length;
        start = after;
    }
    return output.data;
}

private string normalizeWorkspace(string text, string workspace, bool jsonLike = false,
        bool synctex = false)
{
    string[] aliases = [workspace];
    auto parent = dirName(workspace);
    if (parent.length != 0 && parent != "/")
        aliases ~= parent;
    while (parent.length != 0 && parent != "/")
    {
        if (parent.endsWith("texflux-regression-" ~ to!string(thisProcessID)))
        {
            aliases ~= parent;
            break;
        }
        parent = dirName(parent);
    }
    foreach (spelling; aliases.dup)
    {
        if (spelling.startsWith("/private/"))
            aliases ~= spelling[8 .. $];
        else if (spelling.startsWith("/var/"))
            aliases ~= "/private" ~ spelling;
    }
    aliases.sort!((a, b) => a.length > b.length);
    foreach (spelling; aliases)
        text = text.replace(spelling, "${WORKSPACE}");
    if (synctex)
        text = normalizeSynctex(text);
    return jsonLike ? normalizeProducerVersion(text) : text;
}

private string quote(string text)
{
    auto output = appender!string();
    output.put('"');
    foreach (char c; text)
    {
        switch (c)
        {
        case '"': output.put("\\\""); break;
        case '\\': output.put("\\\\"); break;
        case '\n': output.put("\\n"); break;
        case '\r': output.put("\\r"); break;
        case '\t': output.put("\\t"); break;
        default: output.put(c);
        }
    }
    output.put('"');
    return output.data;
}

private string spanText(SourceSpan span)
{
    return format("%s:%d:%d-%d:%d", span.file, span.start.line, span.start.column,
            span.end.line, span.end.column);
}

private string optionalSpanText(Nullable!SourceSpan span)
{
    return span.isNull ? "-" : spanText(span.get);
}

private string modeText(Nullable!SuiteMode mode)
{
    return mode.isNull ? "-" : cast(string) mode.get;
}

private string kindText(Nullable!GroupKind kind)
{
    return kind.isNull ? "-" : cast(string) kind.get;
}

private void indent(ref Appender!string output, size_t depth)
{
    output.put(' '.repeat(depth * 2));
}

private void writeDocument(ref Appender!string output, Document document)
{
    output.put(format("document %s\n", spanText(document.span)));
    writeBlock(output, document.body_, 1);
}

private void writeBlock(ref Appender!string output, Block* block, size_t depth)
{
    indent(output, depth);
    output.put(format("block %s\n", spanText(block.span)));
    foreach (node; block.nodes)
        writeNode(output, node, depth + 1);
}

private void writeNode(ref Appender!string output, Node node, size_t depth)
{
    node.match!(
        (RawTex raw) {
            indent(output, depth);
            output.put(format("raw %s verbatim=%s text=%s\n", spanText(raw.span),
                    raw.verbatim ? "1" : "0", quote(raw.text)));
            writeParts(output, raw.parts, depth + 1);
        },
        (ParsedInvocation invocation) {
            indent(output, depth);
            output.put(format("parsed %s kind=%s name=%s suiteMode=%s suiteSpan=%s\n",
                    spanText(invocation.span), cast(string) invocation.kind,
                    quote(invocation.name), modeText(invocation.suiteMode),
                    optionalSpanText(invocation.suiteSpan)));
            writeGroups(output, invocation.groups, depth + 1);
            writeSuite(output, invocation.suite, depth + 1);
        },
        (SpecialInvocation invocation) {
            indent(output, depth);
            output.put(format("special %s name=%s suiteMode=%s suiteSpan=%s\n",
                    spanText(invocation.span), quote(invocation.name),
                    modeText(invocation.suiteMode), optionalSpanText(invocation.suiteSpan)));
            writeGroups(output, invocation.groups, depth + 1);
            writeSuite(output, invocation.suite, depth + 1);
        },
        (SequenceEntry entry) {
            indent(output, depth);
            output.put(format("entry %s marker=%s argumentKind=%s\n", spanText(entry.span),
                    spanText(entry.markerSpan), kindText(entry.argumentKind)));
            writeBlock(output, entry.value, depth + 1);
        },
        (Stack composition) {
            indent(output, depth);
            output.put(format("stack %s suiteMode=%s suiteSpan=%s\n",
                    spanText(composition.span), modeText(composition.suiteMode),
                    optionalSpanText(composition.suiteSpan)));
            indent(output, depth + 1);
            output.put("segments\n");
            foreach (segment; composition.segments)
                writeNode(output, segment, depth + 2);
            writeSuite(output, composition.suite, depth + 1);
        },
        (GenericInvocation invocation) {
            indent(output, depth);
            output.put(format("invocation %s name=%s\n", spanText(invocation.span),
                    quote(invocation.name)));
            writeGroups(output, invocation.arguments, depth + 1);
            writeSuite(output, invocation.body_, depth + 1, "body");
        },
        (BraceGroup group) {
            indent(output, depth);
            output.put(format("brace %s header=%s\n", spanText(group.span),
                    quote(group.headerRaw)));
            writeParts(output, group.headerParts, depth + 1);
            writeBlock(output, group.body_, depth + 1);
        },
    );
}

private void writeGroups(ref Appender!string output, Argument[] groups, size_t depth)
{
    foreach (group; groups)
    {
        indent(output, depth);
        output.put(format("group kind=%s layout=%s %s\n", cast(string) group.kind,
                cast(string) group.layout, spanText(group.span)));
        group.value.match!(
            (string text) {
                indent(output, depth + 1);
                output.put(format("text=%s\n", quote(text)));
            },
            (Block* nested) { writeBlock(output, nested, depth + 1); },
        );
        writeParts(output, group.parts, depth + 1);
    }
}

private void writeSuite(ref Appender!string output, Block* suite, size_t depth,
        string label = "suite")
{
    if (suite is null)
        return;
    indent(output, depth);
    output.put(label ~ "\n");
    writeBlock(output, suite, depth + 1);
}

private void writeParts(ref Appender!string output, Nullable!SourceText parts, size_t depth)
{
    if (parts.isNull)
        return;
    indent(output, depth);
    output.put("parts\n");
    foreach (fragment; parts.get)
    {
        indent(output, depth + 1);
        output.put(format("fragment %s scaffold=%s text=%s\n", spanText(fragment.span),
                fragment.scaffold ? "1" : "0", quote(fragment.text)));
    }
}

private string[] cliArguments(string check, string input)
{
    switch (check)
    {
    case "compile":
    case "compile-comments":
    case "compile-flags":
        auto args = ["compile", input, "-o", "out.tex"];
        if (check == "compile-comments")
            args ~= "--source-comments";
        if (check == "compile-flags")
            args ~= ["--flag", "draft", "--flag", "handout=off"];
        return args;
    case "ast": return ["ast", input, "-o", "-"];
    case "ast-pretty": return ["ast", input, "-o", "-", "--pretty"];
    case "ast-dotted-path": return ["ast", "./" ~ input, "-o", "-"];
    case "check": return ["check", input];
    case "check-json": return ["check", input, "--format", "json", "--pretty"];
    case "check-stdin": return ["check", "-", "--stdin-filename", input, "--format", "json"];
    case "check-unknown-flags":
        return ["check", input, "--flag", "zulu", "--flag", "nn"];
    case "remap":
    case "remap-bad-version":
        return ["synctex", "remap", "work.synctex", "--map", "generated.tex.tfxmap"];
    default: assert(false, "unknown CLI check"); return null;
    }
}

private struct ActualFile
{
    bool exists;
    string text;
}

private struct ActualOutcome
{
    int status;
    string stdout;
    string stderr;
    ActualFile[string] files;
}

private void writeCaseFiles(FixtureCase current, string directory)
{
    foreach (path, text; current.files)
    {
        auto target = buildPath(directory, path);
        mkdirRecurse(dirName(target));
        write(target, text);
    }
}

private string readUtf8(string path)
{
    import texflux.text : decodeUtf8;
    return decodeUtf8(cast(const(ubyte)[]) read(path));
}

private ActualOutcome callCli(string[] args, FixtureCase current, string directory)
{
    string stdoutText;
    string stderrText;
    immutable(ubyte)[] input = cast(immutable(ubyte)[]) current.files[current.filename];
    CommandStreams streams;
    streams.write = (const(ubyte)[] data) { stdoutText ~= cast(string) data; };
    streams.writeError = (const(ubyte)[] data) { stderrText ~= cast(string) data; };
    streams.readInput = () { return input; };
    ActualOutcome result;
    result.status = run(args, streams);
    result.stdout = stdoutText;
    result.stderr = stderrText;
    return result;
}

private string synctexFixture(string here)
{
    return "SyncTeX Version:1\nInput:1:" ~ here ~ "/generated.tex\nOutput:pdf\n"
        ~ "Magnification:1000\nUnit:1\nX Offset:0\nY Offset:0\nContent:\n!999\n"
        ~ "{1\n[1,4:100,200:10,20,30\nv1,4:120,=:1,2,3\nh1,6,3:130,210\n]"
        ~ "\n}1\n!999\nPostamble:\nCount:6\n!999\nPost scriptum:\n";
}

private ActualOutcome runOutcome(FixtureCase current, string check, string directory)
{
    mkdirRecurse(directory);
    writeCaseFiles(current, directory);
    const oldDirectory = getcwd();
    chdir(directory);
    scope (exit)
    {
        chdir(oldDirectory);
        rmdirRecurse(directory);
    }

    if (check == "remap" || check == "remap-bad-version")
    {
        // The corpus harness deliberately ignores setup status: malformed
        // corpus cases still exercise the same final remap failure path.
        callCli(["compile", current.filename, "-o", "generated.tex"], current, directory);
        const here = getcwd();
        const fixture = check == "remap" ? synctexFixture(here)
            : "SyncTeX Version:abc\nInput:1:" ~ here ~ "/generated.tex\nContent:\n";
        write(buildPath(directory, "work.synctex"), fixture);
    }

    if (check == "syntax")
    {
        ActualOutcome result;
        try
        {
            auto document = parse(readText(current.filename), current.filename);
            auto output = appender!string();
            writeDocument(output, document);
            result.stdout = output.data;
        }
        catch (TeXFluxError error)
        {
            result.stdout = "error " ~ error.code ~ " " ~ error.span.location ~ " "
                ~ quote(error.message) ~ "\n";
        }
        result.status = 0;
        return result;
    }
    if (check == "tex" || check == "tex-comments")
    {
        ActualOutcome result;
        try
            result.stdout = render(normalize(parse(readText(current.filename), current.filename)),
                    check == "tex-comments");
        catch (TeXFluxError error)
            result.stdout = "error " ~ error.code ~ " " ~ error.span.location ~ " "
                ~ error.message ~ "\n";
        catch (FlagError error)
            result.stdout = "flag error " ~ error.msg ~ "\n";
        catch (NestingError _)
            result.stdout = "nesting error\n";
        result.status = 0;
        return result;
    }

    auto result = callCli(cliArguments(check, current.filename), current, directory);
    string[] outputNames;
    if (check == "compile" || check == "compile-comments" || check == "compile-flags")
        outputNames = ["out.tex", "out.tex.tfxmap"];
    else if (check == "remap" || check == "remap-bad-version")
        outputNames = ["work.synctex"];
    foreach (name; outputNames)
    {
        auto path = buildPath(directory, name);
        ActualFile file;
        file.exists = exists(path);
        if (file.exists)
            file.text = readUtf8(path);
        result.files[name] = file;
    }
    return result;
}

private JsonValue outputFile(FixtureCase current, string check, ActualOutcome actual, string path,
        string workspace)
{
    auto found = path in actual.files;
    if (found is null || !found.exists)
        return jsonObject(member("path", path), member("text", jsonNull()));
    const normalized = normalizeWorkspace(found.text, workspace,
            path.endsWith(".tfxmap"), path.endsWith(".synctex"));
    return jsonObject(member("path", path), member("text", normalized));
}

private JsonValue outcomeRecord(FixtureCase current, string check, ActualOutcome actual,
        string workspace, JSONValue expected)
{
    string[] paths;
    foreach (value; field(expected, "files").arrayNoRef)
        paths ~= textField(value, "path");
    paths.sort();
    JsonValue[] files;
    foreach (path; paths)
        files ~= outputFile(current, check, actual, path, workspace);
    const jsonLike = check.startsWith("ast") || check == "check-json" || check == "check-stdin";
    return jsonObject(member("check", check), member("status", cast(long) actual.status),
            member("stdout", normalizeWorkspace(actual.stdout, workspace, jsonLike)),
            member("stderr", normalizeWorkspace(actual.stderr, workspace)),
            member("files", jsonArray(files)));
}

private JsonValue caseRecord(FixtureCase current, size_t index, string workspaceRoot)
{
    string[] paths = current.files.keys;
    paths.sort();
    JsonValue[] files;
    foreach (path; paths)
        files ~= jsonObject(member("path", path), member("text", current.files[path]));

    JsonValue[] outcomes;
    size_t outcomeIndex;
    foreach (expected; current.outcomes.arrayNoRef)
    {
        const check = textField(expected, "check");
        const directory = buildPath(workspaceRoot,
                format("%04d-%04d-%s", index, outcomeIndex, check));
        auto actual = runOutcome(current, check, directory);
        outcomes ~= outcomeRecord(current, check, actual, directory, expected);
        ++outcomeIndex;
    }
    return jsonObject(member("record", "case"), member("name", current.name),
            member("filename", current.filename), member("files", jsonArray(files)),
            member("outcomes", jsonArray(outcomes)));
}

/**
 * Rebuild the frozen corpus with the current D implementation.
 *
 * This is intentionally an explicit operation used by tools/update_regression.d;
 * ordinary tests never rewrite a checked-in expectation.
 */
public void updateRegression()
{
    auto fixture = loadFixture();
    const directory = buildPath(tempDir(), "texflux-regression-update-" ~ thisProcessID.to!string);
    mkdirRecurse(directory);
    scope (exit)
        rmdirRecurse(directory);

    auto output = appender!string();
    output.put(dumpJson(jsonObject(
            member("record", "header"), member("format", "texflux-regression"),
            member("version", 1L), member("source_commit", expectedSourceCommit),
            member("case_count", cast(long) expectedCases),
            member("outcome_count", cast(long) expectedOutcomes),
            member("workspace_token", "${WORKSPACE}"),
            member("producer_version_token", "${TEXFLUX_VERSION}"))));
    foreach (index, current; fixture.cases)
        output.put(dumpJson(caseRecord(current, index, directory)));
    assert(fixture.cases.length == expectedCases && fixture.outcomes == expectedOutcomes);

    const temporary = fixturePath ~ ".tmp." ~ thisProcessID.to!string;
    write(temporary, output.data);
    scope (failure)
        if (exists(temporary))
            remove(temporary);
    rename(temporary, fixturePath);
}

private string expectedText(JSONValue object, string name)
{
    return textField(object, name);
}

private string context(string text, size_t offset)
{
    const start = offset > 200 ? offset - 200 : 0;
    const end = text.length < offset + 200 ? text.length : offset + 200;
    return text[start .. end];
}

private void compareText(string label, string expected, string actual)
{
    if (expected == actual)
        return;
    size_t offset;
    while (offset < expected.length && offset < actual.length
            && expected[offset] == actual[offset])
        ++offset;
    assert(false, format("%s differs at byte %d\nexpected: %s\nactual: %s",
            label, offset, quote(context(expected, offset)), quote(context(actual, offset))));
}

private void compareOutcome(FixtureCase current, JSONValue expected, ActualOutcome actual,
        string workspace)
{
    const check = textField(expected, "check");
    const expectedStatus = cast(int) integerField(expected, "status");
    assert(expectedStatus == actual.status,
            format("%s [%s] status expected %d, got %d", current.name, check,
                expectedStatus, actual.status));
    compareText(current.name ~ " [" ~ check ~ "] stdout",
            expectedText(expected, "stdout"), normalizeWorkspace(actual.stdout, workspace,
                checkNames.canFind(check) && (check.startsWith("ast") || check == "check-json"
                    || check == "check-stdin")));
    compareText(current.name ~ " [" ~ check ~ "] stderr",
            expectedText(expected, "stderr"), normalizeWorkspace(actual.stderr, workspace));

    auto expectedFiles = field(expected, "files");
    string[] names;
    foreach (value; expectedFiles.arrayNoRef)
        names ~= textField(value, "path");
    names.sort();
    foreach (value; expectedFiles.arrayNoRef)
    {
        const name = textField(value, "path");
        const content = field(value, "text");
        const found = name in actual.files;
        const shouldExist = !content.isNull;
        assert((found !is null && (*found).exists) == shouldExist,
                current.name ~ " [" ~ check ~ "] file presence differs for " ~ name);
        if (shouldExist)
        {
            const expectedValue = content.str;
            const jsonLike = name.endsWith(".tfxmap");
            compareText(current.name ~ " [" ~ check ~ "] " ~ name, expectedValue,
                    normalizeWorkspace((*found).text, workspace, jsonLike,
                        name.endsWith(".synctex")));
        }
    }
}

/// Every frozen case and outcome agrees with the v1 contract.
unittest
{
    auto fixture = loadFixture();
    auto workspaceRoot = buildPath(tempDir(),
            "texflux-regression-" ~ thisProcessID.to!string);
    scope (exit) rmdirRecurse(workspaceRoot);
    size_t compared;
    foreach (caseIndex, current; fixture.cases)
    {
        foreach (expected; current.outcomes.arrayNoRef)
        {
            const check = textField(expected, "check");
            const directory = buildPath(workspaceRoot,
                    format("%04d-%s", caseIndex, check));
            compareOutcome(current, expected, runOutcome(current, check, directory), directory);
            ++compared;
        }
    }
    assert(compared == expectedOutcomes,
            format("compared %d outcomes instead of %d", compared, expectedOutcomes));
}
