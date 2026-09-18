/**
 * Contract tests that do not fit in the frozen command/output corpus.
 *
 * These assertions exercise the public D API and the few observable
 * invariants that a JSONL snapshot cannot express: source registration,
 * overlays, failure atomicity, provenance, and the boundary between syntax
 * and canonical AST.
 */
module texflux_tests.contracts;

import std.algorithm : canFind;
import std.exception : assertThrown, collectException;
import std.file : chdir, exists, getcwd, mkdirRecurse, read, readText, rmdirRecurse, tempDir,
    write;
import std.json : JSONType, parseJSON;
import std.path : buildPath;
import std.string : endsWith;

import texflux : AstCompilationResult, CompilationResult, Flags, compileAst, compileText,
    compileWithMap, texfluxVersion;
import texflux.ast : ParsedInvocation;
import texflux.cli : CommandStreams, run;
import texflux.diagnostics : diagnose, serializeDiagnostics;
import texflux.errors : CompilerDefect, TeXFluxError, ValueError;
import texflux.external_ast : serializeAst;
import texflux.parser : parse;
import texflux.remap : remapSyncTeX;
import texflux.source : LoadedSource, SourcePosition, SourceSpan;
import texflux.sourcemap : serializeSourceMap;
import texflux.synctex : parseSyncTeX, serializeSyncTeX;

private struct CliResult
{
    int status;
    string stdout;
    string stderr;
}

private CliResult invoke(string[] args, immutable(ubyte)[] input = null)
{
    CliResult result;
    CommandStreams streams;
    streams.write = (const(ubyte)[] data) { result.stdout ~= cast(string) data; };
    streams.writeError = (const(ubyte)[] data) { result.stderr ~= cast(string) data; };
    streams.readInput = () { return input; };
    result.status = run(args, streams);
    return result;
}

private string tempRoot(string name)
{
    auto path = buildPath(tempDir(), "texflux-contract-" ~ name);
    mkdirRecurse(path);
    return path;
}

unittest
{
    assert(texfluxVersion == "0.2.0");
    auto ast = parseJSON(serializeAst(compileAst("", "version.tfx")));
    assert(ast["producer"]["version"].str == texfluxVersion);
    auto diagnostics = parseJSON(serializeDiagnostics(diagnose("", "version.tfx")));
    assert(diagnostics["producer"]["version"].str == texfluxVersion);
}

unittest
{
    auto result = compileAst("@frame{title}::\n    \\item one\n", "deck.tfx");
    const encoded = serializeAst(result);
    assert(encoded.canFind(`"type":"invocation"`));
    assert(!encoded.canFind(`"type":"special"`));
    assert(!encoded.canFind(`"type":"stack"`));
    assert(result.sources.length == 1 && result.sources[0].file == "deck.tfx");

    auto mapped = compileWithMap("日本\n", "unicode.tfx");
    assert(mapped.rendered.fragments.length != 0);
    assert(mapped.rendered.fragments[$ - 1].generated.end == SourcePosition(2, 1));
    assert(mapped.text == "日本\n");
}

unittest
{
    const root = tempRoot("overlay");
    scope (exit) rmdirRecurse(root);
    const main = buildPath(root, "main.tfx");
    const child = buildPath(root, "child.tfx");
    write(main, "!import{child.tfx}\n");

    const(ubyte)[][string] overlays;
    overlays[child] = cast(const(ubyte)[]) "\\item overlay\n";
    auto report = diagnose(readText(main), main, Flags.init,
            cast(immutable(ubyte)[]) readText(main), overlays);
    assert(report.ok);
    assert(report.sources.length == 2);
    auto grouped = report.byFile;
    assert(main in grouped && child in grouped);
    assert(grouped[main].length == 0 && grouped[child].length == 0);
    assert(report.root.file == main);
}

unittest
{
    auto report = diagnose("@frame\n", "broken.tfx");
    assert(!report.ok && report.diagnostics.length == 1);
    assert(report.sources.length == 1, "the root is listed even when parsing fails");
    assert(report.diagnostics[0].line.canFind("parse error:")
            && report.diagnostics[0].code[0] == 'P');
    auto json = parseJSON(serializeDiagnostics(report));
    assert(json["diagnostics"].array.length == 1);
}

unittest
{
    enum source = "!flag{draft}{off}\n!when{draft}::\n    \\marginpar{note}\n";
    assert(compileText(source, "flags.tfx") == "\n");
    Flags flags;
    flags["draft"] = true;
    assert(compileText(source, "flags.tfx", false, flags) == "\\marginpar{note}\n");
}

unittest
{
    auto result = compileWithMap("\\item one\n", "map.tfx");
    const first = serializeSourceMap(result, "out.tex", "out.tex.tfxmap");
    const second = serializeSourceMap(result, "out.tex", "out.tex.tfxmap");
    assert(first == second && first.endsWith("\n"));
    assert(first.canFind(`"format":"texflux-source-map"`));
    assert(first.canFind(`"version":"` ~ texfluxVersion ~ `"`));
    assertThrown!ValueError(serializeSourceMap(result, "out.tex", "out.tex.tfxmap",
            cast(immutable(ubyte)[]) "different\n"));
}

unittest
{
    const root = tempRoot("cli");
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "input.tfx");
    const output = buildPath(root, "output.tex");
    write(input, "\\item one\n");
    auto compiled = invoke(["compile", input, "-o", output]);
    assert(compiled.status == 0 && compiled.stdout.length == 0 && compiled.stderr.length == 0);
    assert(readText(output) == "\\item one\n");
    assert(exists(output ~ ".tfxmap"));

    auto ast = invoke(["ast", input, "-o", "-"]);
    assert(ast.status == 0 && ast.stdout.canFind(`"format":"texflux-ast"`));
    assert(!exists(input ~ ".tfxmap"));
    auto clean = invoke(["check", input]);
    assert(clean.status == 0 && clean.stdout == "" && clean.stderr == "");
    auto usage = invoke(["compile", input]);
    assert(usage.status == 2 && usage.stdout == "" && usage.stderr.canFind("usage:"));
}

unittest
{
    const root = tempRoot("same-path");
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "input.tfx");
    const original = "\\item must survive\n";
    write(input, original);

    auto absolute = invoke(["compile", input, "-o", input]);
    assert(absolute.status == 1);
    assert(absolute.stderr.canFind("input and output must be different paths"));
    assert(readText(input) == original);

    const oldDirectory = getcwd();
    scope (exit) chdir(oldDirectory);
    chdir(root);
    auto dotted = invoke(["compile", "./input.tfx", "-o", "./input.tfx"]);
    assert(dotted.status == 1);
    assert(dotted.stderr.canFind("input and output must be different paths"));
    assert(readText(input) == original);
}

unittest
{
    const root = tempRoot("atomic");
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "broken.tfx");
    const output = buildPath(root, "output.tex");
    write(input, "!unknown\n");
    write(output, "previous\n");
    auto failed = invoke(["compile", input, "-o", output]);
    assert(failed.status == 1);
    assert(readText(output) == "previous\n");
    assert(!exists(output ~ ".tfxmap"));
}

unittest
{
    auto data = cast(immutable(ubyte)[]) read("tests/fixtures/minimal.synctex");
    auto parsed = parseSyncTeX(data);
    auto rewritten = serializeSyncTeX(parsed);
    assert(rewritten.length != 0 && parseSyncTeX(rewritten).lines.length == parsed.lines.length);
    assertThrown!Exception(remapSyncTeX(data, ["missing.tfxmap"]));
}

unittest
{
    const span = SourceSpan("syntax.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    auto document = parse("\\foo::\n    body\n", "syntax.tfx");
    auto bad = AstCompilationResult(document, [LoadedSource("syntax.tfx", "syntax.tfx",
            cast(immutable(ubyte)[]) "\\foo::\n    body\n")]);
    assertThrown!CompilerDefect(serializeAst(bad));
    assert(span.location == "syntax.tfx:1:1");
}
