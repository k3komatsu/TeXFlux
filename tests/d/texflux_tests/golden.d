/**
 * The exact output every case in the repository is expected to produce.
 *
 * These are the same files the Python implementation is checked against, read
 * from the same place: a golden file is a statement about the language rather
 * than about either implementation, and the two would be worth much less if
 * each kept its own copy.
 *
 * The conformance harness compares the two implementations run for run, which
 * catches a difference. This catches a change: if both were to drift together,
 * only a stored expectation would notice.
 */
module texflux_tests.golden;

import std.algorithm : sort;
import std.file : dirEntries, exists, getcwd, isDir, readText, SpanMode;
import std.format : format;
import std.path : baseName, buildPath, relativePath;
import std.string : replace;

import texflux : compileText, Flags;

/**
 * Where the repository is, seen from wherever the tests were started.
 *
 * `dub test` runs from the package root, which is what every path here assumes.
 */
private string repository()
{
    return getcwd();
}

private string[] goldenCases()
{
    auto root = repository.buildPath("tests", "golden");
    string[] names;
    foreach (entry; dirEntries(root, SpanMode.shallow))
        if (entry.isDir && exists(entry.name.buildPath("input.tfx")))
            names ~= entry.name.baseName;
    names.sort();
    return names;
}

/**
 * The name a case is compiled under.
 *
 * An import resolves against the file that writes it, so a case that imports
 * needs its real path. The one case that prints its own filename needs the
 * repository-relative spelling instead, because that is what its golden file
 * records.
 */
private string filenameFor(string name)
{
    const path = repository.buildPath("tests", "golden", name, "input.tfx");
    if (name != "source-comments")
        return path;
    return relativePath(path, repository).replace("\\", "/");
}

/// Every case renders to exactly the bytes stored beside it.
unittest
{
    auto cases = goldenCases();
    assert(cases.length == 23, format("expected 23 golden cases, found %d: %s",
            cases.length, cases));

    foreach (name; cases)
    {
        const directory = repository.buildPath("tests", "golden", name);
        const source = readText(directory.buildPath("input.tfx"));
        const expected = readText(directory.buildPath("expected.tex"));
        const actual = compileText(source, filenameFor(name), name == "source-comments");
        assert(actual == expected, format("golden case '%s' rendered differently", name));
    }
}

/// A flag override builds the other version of the one case that has two.
unittest
{
    const directory = repository.buildPath("tests", "golden", "build-flags");
    const source = readText(directory.buildPath("input.tfx"));
    const expected = readText(directory.buildPath("expected-draft.tex"));
    const actual = compileText(source, "tests/golden/build-flags/input.tfx", false,
            Flags(["draft": true, "handout": false]));
    assert(actual == expected, "the draft build of build-flags rendered differently");
}

/// Every example in the documentation renders to the file published beside it.
unittest
{
    foreach (name; ["basic", "structured", "stacked-items", "macros", "modules", "content"])
    {
        const source = readText(repository.buildPath("examples", name ~ ".tfx"));
        const expected = readText(repository.buildPath("examples", name ~ ".tex"));
        // The multi-source examples import, so they need their real path; the
        // others are given the spelling the documentation tells a reader to use.
        const filename = name == "modules" || name == "content"
            ? repository.buildPath("examples", name ~ ".tfx") : "examples/" ~ name ~ ".tfx";
        assert(compileText(source, filename) == expected,
                format("example '%s' rendered differently", name));
    }
}
