/** Optional integration checks for tools outside the TeXFlux runtime. */
module texflux_tests.integration;

import std.algorithm : canFind;
import std.conv : to;
import std.file : exists, mkdirRecurse, readText, rmdirRecurse, tempDir, write;
import std.path : buildPath;
import std.process : Config, execute, thisProcessID;
import std.string : splitLines, startsWith;

import texflux : compileText;
import texflux.cli : CommandStreams, run;

private struct CliResult
{
    int status;
    string stdout;
    string stderr;
}

private bool available(string command)
{
    try
        return execute(["sh", "-c", "command -v " ~ command]).status == 0;
    catch (Exception)
        return false;
}

private string integrationRoot(string name)
{
    auto path = buildPath(tempDir(), "texflux-integration-" ~ thisProcessID.to!string ~ "-" ~ name);
    assert(!exists(path), "integration directory already exists: " ~ path);
    mkdirRecurse(path);
    return path;
}

private CliResult invoke(string[] args)
{
    CliResult result;
    CommandStreams streams;
    streams.write = (const(ubyte)[] data) { result.stdout ~= cast(string) data; };
    streams.writeError = (const(ubyte)[] data) { result.stderr ~= cast(string) data; };
    result.status = run(args, streams);
    return result;
}

private void runLatex(string directory, string texPath)
{
    auto result = execute(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
            "-synctex=1", "-output-directory", directory, texPath], null, Config.none,
            size_t.max, directory);
    assert(result.status == 0, "pdflatex failed:\n" ~ result.output);
}

private size_t lineOf(string text, string needle)
{
    size_t line = 1;
    foreach (value; text.splitLines())
    {
        if (value.canFind(needle))
            return line;
        ++line;
    }
    assert(false, "missing generated text: " ~ needle);
    return 0;
}

private string[] coordinates(string output)
{
    string[] result;
    foreach (key; ["Page", "x", "y"])
    {
        foreach (line; output.splitLines())
            if (line.startsWith(key ~ ":"))
            {
                result ~= line[key.length + 1 .. $];
                break;
            }
        const wanted = key == "Page" ? 1 : key == "x" ? 2 : 3;
        assert(result.length == wanted, "missing SyncTeX field " ~ key ~ ":\n" ~ output);
    }
    return result;
}

/** Generated TeX can be consumed by a normal LaTeX document. */
unittest
{
    if (!available("pdflatex"))
        return;
    const directory = integrationRoot("beamer");
    scope (exit) rmdirRecurse(directory);
    write(buildPath(directory, "content.tex"),
            compileText("@frame{Title}::\n    Generated body\n"));
    write(buildPath(directory, "main.tex"),
            "\\documentclass{beamer}\n\\begin{document}\n"
            ~ "\\input{content.tex}\n\\end{document}\n");
    runLatex(directory, buildPath(directory, "main.tex"));
    assert(exists(buildPath(directory, "main.pdf")));
}

/** TeX accepts the exact brace layout emitted for block and sequence values. */
unittest
{
    if (!available("pdflatex"))
        return;
    const directory = integrationRoot("layout");
    scope (exit) rmdirRecurse(directory);
    write(buildPath(directory, "content.tex"), compileText(
        "\\wrap::\n"
        ~ "    block value\n"
        ~ "\\pair:::\n"
        ~ "    - trailing % comment\n"
        ~ "    - tail\n"
        ~ "@{\\small}::\n"
        ~ "    inside a literal brace container\n"));
    write(buildPath(directory, "main.tex"),
        "\\documentclass{article}\n"
        ~ "\\newcommand\\wrap[1]{#1}\n"
        ~ "\\newcommand\\pair[2]{#1#2}\n"
        ~ "\\begin{document}\n\\input{content.tex}\n\\end{document}\n");
    runLatex(directory, buildPath(directory, "main.tex"));
    assert(exists(buildPath(directory, "main.pdf")));
}

/** Native SyncTeX forward and reverse queries survive source-map remapping. */
unittest
{
    if (!available("pdflatex") || !available("synctex"))
        return;
    const directory = integrationRoot("synctex");
    scope (exit) rmdirRecurse(directory);
    const sourcePath = buildPath(directory, "source.tfx");
    const generatedPath = buildPath(directory, "generated.tex");
    const pdfPath = buildPath(directory, "generated.pdf");
    const source = "\\documentclass{article}\n"
        ~ "\\begin{document}\n"
        ~ "@center::\n"
        ~ "    First block\n"
        ~ "@center::\n"
        ~ "    End-to-end marker\n"
        ~ "\\end{document}\n";
    write(sourcePath, source);
    auto compiled = invoke(["compile", sourcePath, "-o", generatedPath]);
    assert(compiled.status == 0, compiled.stderr);
    runLatex(directory, generatedPath);

    auto syncPath = buildPath(directory, "generated.synctex.gz");
    if (!exists(syncPath))
        syncPath = buildPath(directory, "generated.synctex");
    assert(exists(syncPath));
    const generatedLine = lineOf(readText(generatedPath), "End-to-end marker");
    auto before = execute(["synctex", "view", "-i", generatedLine.to!string ~ ":0:" ~ generatedPath,
            "-o", pdfPath], null, Config.none, size_t.max, directory);
    assert(before.status == 0, "synctex view failed:\n" ~ before.output);
    auto expected = coordinates(before.output);

    auto remapped = invoke(["synctex", "remap", syncPath, "--map", generatedPath ~ ".tfxmap"]);
    assert(remapped.status == 0, remapped.stderr);
    auto view = execute(["synctex", "view", "-i", "6:0:" ~ sourcePath, "-o", pdfPath],
            null, Config.none, size_t.max, directory);
    assert(view.status == 0, "remapped synctex view failed:\n" ~ view.output);
    assert(coordinates(view.output) == expected);

    auto edit = execute(["synctex", "edit", "-o", expected[0] ~ ":" ~ expected[1] ~ ":"
            ~ expected[2] ~ ":" ~ pdfPath], null, Config.none, size_t.max, directory);
    assert(edit.status == 0, "synctex edit failed:\n" ~ edit.output);
    assert(edit.output.canFind("Input:" ~ sourcePath));
    assert(edit.output.canFind("Line:6"));
}

/** Imported content keeps its own file in native SyncTeX queries. */
unittest
{
    if (!available("pdflatex") || !available("synctex"))
        return;
    const directory = integrationRoot("synctex-module");
    scope (exit) rmdirRecurse(directory);
    const sourcePath = buildPath(directory, "deck.tfx");
    const partPath = buildPath(directory, "part.tfx");
    const generatedPath = buildPath(directory, "generated.tex");
    const pdfPath = buildPath(directory, "generated.pdf");
    write(partPath, "@center::\n    Imported marker\n");
    write(sourcePath, "\\documentclass{article}\n\\begin{document}\n"
        ~ "@center::\n    Root marker\n!import{part.tfx}\n"
        ~ "\\end{document}\n");
    auto compiled = invoke(["compile", sourcePath, "-o", generatedPath]);
    assert(compiled.status == 0, compiled.stderr);
    runLatex(directory, generatedPath);

    auto syncPath = buildPath(directory, "generated.synctex.gz");
    if (!exists(syncPath))
        syncPath = buildPath(directory, "generated.synctex");
    assert(exists(syncPath));
    const generatedLine = lineOf(readText(generatedPath), "Imported marker");
    auto before = execute(["synctex", "view", "-i", generatedLine.to!string ~ ":0:" ~ generatedPath,
            "-o", pdfPath], null, Config.none, size_t.max, directory);
    assert(before.status == 0, "synctex view failed:\n" ~ before.output);
    auto expected = coordinates(before.output);

    auto remapped = invoke(["synctex", "remap", syncPath, "--map", generatedPath ~ ".tfxmap"]);
    assert(remapped.status == 0, remapped.stderr);
    auto view = execute(["synctex", "view", "-i", "2:0:" ~ partPath, "-o", pdfPath],
            null, Config.none, size_t.max, directory);
    assert(view.status == 0, "remapped module view failed:\n" ~ view.output);
    assert(coordinates(view.output) == expected);

    auto edit = execute(["synctex", "edit", "-o", expected[0] ~ ":" ~ expected[1] ~ ":"
            ~ expected[2] ~ ":" ~ pdfPath], null, Config.none, size_t.max, directory);
    assert(edit.status == 0, "synctex edit failed:\n" ~ edit.output);
    assert(edit.output.canFind("Input:" ~ partPath));
    assert(!edit.output.canFind("Input:" ~ sourcePath));
}
