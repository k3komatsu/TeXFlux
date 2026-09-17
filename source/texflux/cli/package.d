/**
 * The `texflux` command.
 *
 * The streams are arguments rather than globals, so a test can run a whole
 * command in memory and read back exactly the bytes a terminal would see.
 * Output is written as bytes throughout: a generated document and a published
 * report are UTF-8 with line feeds whatever the console is set to, because
 * both are compared byte for byte by the things that read them.
 *
 * A status says which kind of thing went wrong. Zero is success. One is a
 * document that does not compile, and the diagnostic that says why. Two is
 * everything else: a command line that cannot be read, a file that cannot be
 * looked at, an argument that contradicts another.
 */
module texflux.cli;

import std.array : join;
import std.conv : to;
import std.file : FileException, read, write;
import std.path : extension;
import std.typecons : nullable;

import texflux;
import texflux.cli.args;
import texflux.diagnostics : diagnose, serializeDiagnostics;
import texflux.external_ast : serializeAst;
import texflux.remap : RemapError, remapSyncTeXFile;
import texflux.synctex : SyncTeXError;
import texflux.errors : CompilerDefect, NestingError;
import texflux.flags : flagValue;
import texflux.paths : openFailure, samePath;
import texflux.text : decodeUtf8, stripWhitespace, UnicodeDecodeError;

/// Where a command reads its input and writes its two output streams.
struct CommandStreams
{
    void delegate(const(ubyte)[]) write;
    void delegate(const(ubyte)[]) writeError;
    immutable(ubyte)[] delegate() readInput;

    void writeLine(string text)
    {
        write(cast(const(ubyte)[])(text ~ "\n"));
    }

    void writeErrorLine(string text)
    {
        writeError(cast(const(ubyte)[])(text ~ "\n"));
    }
}

private enum usage = "usage: texflux [-h] {compile,ast,check,synctex} ...";

/// Run one command and return the status the process should exit with.
int run(string[] args, CommandStreams streams)
{
    try
    {
        if (args.length == 0)
            throw new UsageError("the following arguments are required: command");
        if (args[0] == "-h" || args[0] == "--help")
        {
            streams.writeLine(usage);
            return 0;
        }

        switch (args[0])
        {
        case "compile":
            return compileCommand(args[1 .. $], streams);
        case "ast":
            return astCommand(args[1 .. $], streams);
        case "check":
            return checkCommand(args[1 .. $], streams);
        case "synctex":
            return synctexCommand(args[1 .. $], streams);
        default:
            throw new UsageError("argument command: invalid choice: '" ~ args[0]
                    ~ "' (choose from 'compile', 'ast', 'check', 'synctex')");
        }
    }
    catch (UsageError error)
    {
        streams.writeErrorLine(usage);
        streams.writeErrorLine("texflux: error: " ~ error.msg);
        return 2;
    }
}

/// The options every command that compiles a document shares.
private enum flagOption = Option("flags", ["--flag"], true, true);

private enum compileOptions = [
    Option("sourceComments", ["--source-comments"], false),
    Option("output", ["-o", "--output"], true, false, true),
    flagOption,
    Option("help", ["-h", "--help"], false),
];

/**
 * Compile one document, writing the TeX and its map side by side.
 *
 * Both files are written only once the whole compilation has succeeded, so a
 * failed build leaves whatever was there before it untouched.
 */
private int compileCommand(string[] words, CommandStreams streams)
{
    auto arguments = parse(compileOptions, words);
    if (arguments.has("help"))
    {
        streams.writeLine("usage: texflux compile [-h] [--source-comments] -o OUTPUT"
                ~ " [--flag NAME[=on|off]] INPUT");
        return 0;
    }
    const input = arguments.onePositional("INPUT").pathSpelling;
    const output = arguments.value("output").pathSpelling;
    if (auto status = rejectPaths(input, output, streams))
        return status;

    return guarded(input, streams, 1, {
        const mapPath = output ~ ".tfxmap";
        const data = cast(immutable(ubyte)[]) read(input);
        auto result = compileWithMap(decodeUtf8(data), input,
                arguments.has("sourceComments"), readFlags(arguments), data);
        const rendered = cast(const(ubyte)[]) result.text;
        const map = serializeSourceMap(result, output, mapPath, rendered);
        write(output, rendered);
        write(mapPath, cast(const(ubyte)[]) map);
        return 0;
    });
}

private enum astOptions = [
    Option("pretty", ["--pretty"], false),
    Option("output", ["-o", "--output"], true, false, true),
    flagOption,
    Option("help", ["-h", "--help"], false),
];

/**
 * Export the tree the renderer would have been given.
 *
 * No TeX and no map: a consumer of this reads meaning rather than layout, and
 * writing files it did not ask for would only get in its way.
 */
private int astCommand(string[] words, CommandStreams streams)
{
    auto arguments = parse(astOptions, words);
    if (arguments.has("help"))
    {
        streams.writeLine("usage: texflux ast [-h] [--pretty] -o OUTPUT"
                ~ " [--flag NAME[=on|off]] INPUT");
        return 0;
    }
    const input = arguments.onePositional("INPUT").pathSpelling;
    const output = arguments.value("output");
    const toStandardOutput = output == "-";
    if (auto status = rejectPaths(input, toStandardOutput ? null : output, streams))
        return status;

    return guarded(input, streams, 1, {
        const data = cast(immutable(ubyte)[]) read(input);
        auto result = compileAst(decodeUtf8(data), input, readFlags(arguments), data);
        const text = serializeAst(result, arguments.has("pretty"));
        if (toStandardOutput)
            streams.write(cast(const(ubyte)[]) text);
        else
            write(output.pathSpelling, cast(const(ubyte)[]) text);
        return 0;
    });
}

private enum checkOptions = [
    Option("format", ["--format"], true, false, false, ["text", "json"]),
    Option("pretty", ["--pretty"], false),
    Option("stdinFilename", ["--stdin-filename"], true),
    flagOption,
    Option("help", ["-h", "--help"], false),
];

/**
 * Report one document's diagnostics, writing nothing else.
 *
 * Status 1 means the document has an error, so it cannot also mean that the
 * document could not be looked at. That is 2, which is what a command line
 * that cannot be read returns as well.
 */
private int checkCommand(string[] words, CommandStreams streams)
{
    auto arguments = parse(checkOptions, words);
    if (arguments.has("help"))
    {
        streams.writeLine("usage: texflux check [-h] [--format {text,json}] [--pretty]"
                ~ " [--stdin-filename PATH] [--flag NAME[=on|off]] INPUT");
        return 0;
    }
    const input = arguments.onePositional("INPUT");
    const asJson = arguments.value("format", "text") == "json";
    const fromStandardInput = input == "-";
    const named = arguments.value("stdinFilename");

    if (arguments.has("pretty") && !asJson)
        return fail(streams, "--pretty requires --format json", 2);
    // Unlike compile and ast, check names the document by the spelling it was
    // given; only the extension test and the read see the tidied one.
    string filename;
    if (!fromStandardInput)
    {
        if (named !is null)
            return fail(streams, "--stdin-filename requires INPUT '-'", 2);
        if (input.pathSpelling.extension != ".tfx")
            return fail(streams, "input must have a .tfx extension", 2);
        filename = input;
    }
    else if (named is null)
        // Imports then resolve against the working directory, which is the
        // best a caller that named no path can be given.
        filename = "<stdin>";
    else if (named.pathSpelling.extension != ".tfx")
        return fail(streams, "--stdin-filename must have a .tfx extension", 2);
    else
        filename = named;

    return guarded(filename, streams, 2, {
        const data = fromStandardInput
            ? streams.readInput() : cast(immutable(ubyte)[]) read(filename.pathSpelling);
        auto report = diagnose(decodeUtf8(data), filename, readFlags(arguments), data);
        if (asJson)
            streams.write(cast(const(ubyte)[]) serializeDiagnostics(report,
                    arguments.has("pretty")));
        else
        {
            string[] lines;
            foreach (diagnostic; report.diagnostics)
            {
                lines ~= diagnostic.line;
                foreach (related; diagnostic.related)
                    lines ~= "  " ~ related.span.location ~ ": note: " ~ related.message;
            }
            if (lines.length != 0)
                streams.write(cast(const(ubyte)[])(lines.join("\n") ~ "\n"));
        }
        return report.ok ? 0 : 1;
    });
}

private enum remapOptions = [
    Option("maps", ["--map"], true, true, true),
    Option("output", ["--output"], true),
    Option("help", ["-h", "--help"], false),
];

/// The SyncTeX subcommands, of which there is one.
private int synctexCommand(string[] words, CommandStreams streams)
{
    if (words.length == 0)
        throw new UsageError("the following arguments are required: synctex_command");
    if (words[0] == "-h" || words[0] == "--help")
    {
        streams.writeLine("usage: texflux synctex [-h] {remap} ...");
        return 0;
    }
    if (words[0] != "remap")
        throw new UsageError("argument synctex_command: invalid choice: '" ~ words[0]
                ~ "' (choose from 'remap')");

    auto arguments = parse(remapOptions, words[1 .. $]);
    if (arguments.has("help"))
    {
        streams.writeLine("usage: texflux synctex remap [-h] --map MAP [--output OUTPUT] SYNCTEX");
        return 0;
    }
    const synctex = arguments.onePositional("SYNCTEX");

    // Remapping rewrites a file the engine wrote, so a failure here is never a
    // document's fault and never gets a document's exit code.
    try
    {
        remapSyncTeXFile(synctex, arguments.values("maps").dup, arguments.value("output"));
        return 0;
    }
    catch (RemapError error)
        return fail(streams, error.msg);
    catch (SyncTeXError error)
        return fail(streams, error.msg);
    catch (FileException error)
        return fail(streams, openFailure(error, error.msg.pathOf));
}

/**
 * Run one command, turning anything it throws into a status.
 *
 * A diagnostic is printed as itself, because an editor matches that line. A
 * failure of the environment is printed behind the command's own name, because
 * it is the command speaking rather than the document.
 */
private int guarded(string filename, CommandStreams streams, int failure, int delegate() body_)
{
    try
        return body_();
    catch (TeXFluxError error)
    {
        // The diagnostic line alone, with no prefix: an editor matches it, and
        // a related location belongs to the structured report that `check`
        // produces rather than to a build's error output.
        streams.writeErrorLine(error.diagnostic);
        return 1;
    }
    catch (NestingError _)
        return fail(streams, filename ~ ": input nests too deeply to compile", failure);
    catch (UnicodeDecodeError error)
        return fail(streams, error.msg, failure);
    catch (FileException error)
        return fail(streams, openFailure(error, error.msg.pathOf), failure);
    catch (FlagError error)
        return fail(streams, error.msg, failure);
    catch (InternalError error)
        return fail(streams, error.msg, failure);
    catch (ValueError error)
        return fail(streams, error.msg, failure);
    catch (CompilerDefect error)
        return fail(streams, error.msg, failure);
}

/// The one positional word a command takes, named as its usage names it.
private string onePositional(Arguments arguments, string label)
{
    if (arguments.positional.length == 1)
        return arguments.positional[0];
    throw new UsageError(arguments.positional.length == 0
            ? "the following arguments are required: " ~ label
            : "unrecognized arguments: " ~ arguments.positional[1 .. $].join(" "));
}

private int fail(CommandStreams streams, string message, int status = 1)
{
    streams.writeErrorLine("texflux: " ~ message);
    return status;
}

/// The path a file error names, which it puts in front of its own reason.
private string pathOf(string message)
{
    import std.string : lastIndexOf;

    // The reason never holds ": ", so the last one is the one that ends the
    // path, whatever the path itself holds.
    const at = message.lastIndexOf(": ");
    return at < 0 ? message : message[0 .. at];
}

/**
 * The spelling `pathlib.Path` gives a path, which is how the reference names
 * a file: no `.` segment, no repeated or trailing separator, and `..` left as
 * written.
 */
private string pathSpelling(string path)
{
    import std.algorithm : filter, splitter, startsWith;

    const root = path.startsWith("//") && !path.startsWith("///") ? "//"
        : path.startsWith("/") ? "/" : "";
    const rest = path.splitter('/').filter!(part => part.length != 0 && part != ".").join("/");
    return root.length == 0 && rest.length == 0 ? "." : root ~ rest;
}

///
unittest
{
    assert(pathSpelling("./x.tfx") == "x.tfx");
    assert(pathSpelling("sub//x.tfx/") == "sub/x.tfx");
    assert(pathSpelling("/a/./b/../c") == "/a/b/../c", "'..' is left as written");
    assert(pathSpelling("//x.tfx") == "//x.tfx" && pathSpelling("///x.tfx") == "/x.tfx");
    assert(pathSpelling(".") == "." && pathSpelling("") == "." && pathSpelling("/") == "/");
}

/**
 * Refuse a pair of paths no compilation should be asked for.
 *
 * The extension is what says a file is a document, and writing the output over
 * the input would destroy the only copy of it.
 */
private int rejectPaths(string input, string output, CommandStreams streams)
{
    if (input.extension != ".tfx")
        return fail(streams, "input must have a .tfx extension");
    if (output !is null && samePath(input, output))
        return fail(streams, "input and output must be different paths");
    return 0;
}

/**
 * Read the build-flag overrides one command line gives.
 *
 * A flag is written as its name alone, which turns it on, or as the name and
 * one of the two spellings a declaration uses. Nothing else is accepted: an
 * override that looked like it worked and did not would build the wrong
 * document silently.
 */
private Flags readFlags(Arguments arguments)
{
    import std.string : indexOf;

    Flags flags;
    foreach (argument; arguments.values("flags"))
    {
        const equals = argument.indexOf('=');
        const name = (equals < 0 ? argument : argument[0 .. equals]).stripWhitespace;
        // A name alone turns its flag on.
        auto value = equals < 0 ? true.nullable
            : flagValue(argument[equals + 1 .. $].stripWhitespace);
        if (value.isNull)
            throw new FlagError("build flag must be NAME, NAME=on or NAME=off; got '"
                    ~ argument ~ "'");
        if (name in flags)
            throw new FlagError("build flag '" ~ name ~ "' is set more than once");
        flags[name] = value.get;
    }
    return flags;
}
