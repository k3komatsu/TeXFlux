/** Contract tests for the editor-independent analysis and the LSP boundary. */
module texflux_tests.lsp;

import std.algorithm : canFind;
import std.conv : to;
import std.json : JSONType, JSONValue, parseJSON;
import std.file : exists, getcwd;
import std.path : buildPath;
import std.process : pipeProcess, Redirect, wait;
import std.stdio : File;
import std.string : indexOf, split, strip;

import texflux : AnalysisRequest, analyze;
import texflux.flags : Flags;
import texflux.lsp.server : run;
import texflux.lsp.features : FeatureOccurrenceKind, buildFeatureIndex;
import texflux.lsp.text : LspPosition, LineIndex, PositionEncoding, pathToUri,
    uriToPath;
import texflux.lsp.transport : LspStreams, LspTransport, PacketStatus;
import texflux.lsp.workspace : Workspace;
import texflux.modules : ModuleKind;
import texflux.paths : normalizedPath;
import texflux.source : SourcePosition;

private final class MemoryConnection
{
    private ubyte[] input;
    private size_t offset;
    ubyte[] output;
    string logText;
    size_t chunkSize = 2;

    this(string text)
    {
        input = cast(ubyte[]) text.dup;
    }

    size_t read(ubyte[] buffer)
    {
        if (offset == input.length)
            return 0;
        const count = (input.length - offset) < chunkSize
            ? input.length - offset : chunkSize;
        buffer[0 .. count] = input[offset .. offset + count];
        offset += count;
        return count;
    }

    void write(const(ubyte)[] bytes)
    {
        output ~= cast(ubyte[]) bytes.dup;
    }

    void log(string text)
    {
        logText ~= text;
    }
}

private string frame(string body)
{
    return "Content-Length: " ~ body.length.to!string ~ "\r\n\r\n" ~ body;
}

private JSONValue[] frames(const(ubyte)[] bytes)
{
    const text = cast(string) bytes;
    JSONValue[] result;
    size_t offset;
    while (offset < text.length)
    {
        const relativeHeaderEnd = text[offset .. $].indexOf("\r\n\r\n");
        assert(relativeHeaderEnd >= 0, "truncated output header");
        const headerEnd = offset + relativeHeaderEnd;
        size_t length;
        bool found;
        foreach (line; text[offset .. headerEnd].split("\r\n"))
        {
            const colon = line.indexOf(':');
            if (colon < 0)
                continue;
            if (line[0 .. colon].strip == "Content-Length")
            {
                length = line[colon + 1 .. $].strip.to!size_t;
                found = true;
            }
        }
        assert(found);
        const bodyStart = headerEnd + 4;
        assert(bodyStart + length <= text.length, "truncated output body");
        result ~= parseJSON(text[bodyStart .. bodyStart + length]);
        offset = bodyStart + length;
    }
    return result;
}

private JSONValue field(JSONValue value, string name)
{
    auto found = name in value.objectNoRef;
    return found is null ? JSONValue.init : *found;
}

private LspStreams streams(MemoryConnection connection)
{
    LspStreams result;
    result.read = &connection.read;
    result.write = &connection.write;
    result.log = &connection.log;
    return result;
}

private string readProcessFrame(File output)
{
    string bytes;
    size_t headerEnd;
    while (true)
    {
        ubyte[1] one;
        auto read = output.rawRead(one[]);
        assert(read.length == 1, "LSP subprocess ended before its response");
        bytes ~= cast(char) read[0];
        const relative = bytes.indexOf("\r\n\r\n");
        if (relative < 0)
            continue;
        headerEnd = relative;
        break;
    }
    size_t length;
    foreach (line; bytes[0 .. headerEnd].split("\r\n"))
    {
        const colon = line.indexOf(':');
        if (colon >= 0 && line[0 .. colon].strip == "Content-Length")
            length = line[colon + 1 .. $].strip.to!size_t;
    }
    const bodyStart = headerEnd + 4;
    while (bytes.length < bodyStart + length)
    {
        ubyte[1] one;
        auto read = output.rawRead(one[]);
        assert(read.length == 1, "LSP subprocess body was truncated");
        bytes ~= cast(char) read[0];
    }
    return bytes[bodyStart .. bodyStart + length].idup;
}

private void writeProcessFrame(ref File input, string body)
{
    input.rawWrite(cast(const(ubyte)[]) frame(body));
    input.flush();
}

unittest
{
    const source =
        "!defmacro{wrap}{body}::\n" ~
        "    !param{body}\n" ~
        "!flag{draft}{off}\n" ~
        "@frame::\n" ~
        "    !wrap{hello}\n" ~
        "    !when{draft} >> \\note{draft}\n";
    auto index = buildFeatureIndex(source, "/tmp/features.tfx");
    assert(index.complete);
    bool macroDeclaration;
    bool flagDeclaration;
    bool macroCall;
    foreach (occurrence; index.occurrences)
    {
        macroDeclaration |= occurrence.kind == FeatureOccurrenceKind.macro_
            && occurrence.name == "wrap" && occurrence.declaration;
        flagDeclaration |= occurrence.kind == FeatureOccurrenceKind.flag_
            && occurrence.name == "draft" && occurrence.declaration;
        macroCall |= occurrence.kind == FeatureOccurrenceKind.macro_
            && occurrence.name == "wrap" && !occurrence.declaration;
    }
    assert(macroDeclaration && flagDeclaration && macroCall);
    assert(index.symbols.length == 3, "macro, flag and environment symbols");
    assert(index.folds.length != 0);
    assert(index.tokens.length >= 6);

    auto incomplete = buildFeatureIndex("!defmacro{wrap\n", "/tmp/incomplete.tfx");
    assert(!incomplete.complete);
    assert(incomplete.symbols.length == 0);
    assert(incomplete.folds.length == 0);
    assert(incomplete.tokens.length == 0);
}

unittest
{
    auto packetInput = frame("{") ~ frame(
        `{"jsonrpc":"2.0","method":"initialized","params":{}}`);
    auto connection = new MemoryConnection(packetInput);
    LspTransport transport;
    transport.streams = streams(connection);
    assert(transport.readPacket().status == PacketStatus.invalidJson);
    auto next = transport.readPacket();
    assert(next.status == PacketStatus.message);
    assert(next.value["method"].str == "initialized");
}

unittest
{
    const uri = pathToUri("/tmp/features.tfx");
    auto input = frame(
        `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"initialized","params":{}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":"`
            ~ uri ~ `","version":1,"text":"!defmacro{wrap}{body}::\n    !param{body}\n!flag{draft}{off}\n@frame::\n    !wrap{hello}\n    !when{draft} >> \\note{draft}\n"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":2,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"`
            ~ uri ~ `"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":3,"method":"textDocument/foldingRange","params":{"textDocument":{"uri":"`
            ~ uri ~ `"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":4,"method":"textDocument/completion","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":7}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":5,"method":"textDocument/hover","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":6}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":6,"method":"textDocument/definition","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":6}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":7,"method":"textDocument/semanticTokens/full","params":{"textDocument":{"uri":"`
            ~ uri ~ `"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":8,"method":"textDocument/references","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":6},"context":{"includeDeclaration":true}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":9,"method":"textDocument/rename","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":6},"newName":"shell"}}`)
        ~ frame(`{"jsonrpc":"2.0","id":10,"method":"textDocument/codeAction","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"range":{"start":{"line":0,"character":0},"end":{"line":0,"character":1}},"context":{"diagnostics":[]}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":11,"method":"textDocument/rename","params":{"textDocument":{"uri":"`
            ~ uri ~ `"},"position":{"line":4,"character":6},"newName":"text"}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"textDocument/didChange","params":{"textDocument":{"uri":"`
            ~ uri ~ `","version":2},"contentChanges":[{"range":{"start":{"line":0,"character":10},"end":{"line":0,"character":14}},"text":"shell"},{"range":{"start":{"line":0,"character":17},"end":{"line":0,"character":21}},"text":"arg"},{"range":{"start":{"line":4,"character":5},"end":{"line":4,"character":9}},"text":"shell"}]}}`)
        ~ frame(`{"jsonrpc":"2.0","id":12,"method":"textDocument/documentSymbol","params":{"textDocument":{"uri":"`
            ~ uri ~ `"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":13,"method":"shutdown","params":null}`)
        ~ frame(`{"jsonrpc":"2.0","method":"exit"}`);
    auto connection = new MemoryConnection(input);
    assert(run(streams(connection)) == 0);
    auto messages = frames(connection.output);
    JSONValue[string] responses;
    foreach (message; messages)
    {
        auto id = field(message, "id");
        if (id.type == JSONType.integer)
            responses[to!string(id.integer)] = message;
    }
    assert(responses["2"]["result"].array.length == 3);
    assert(responses["2"]["result"].array[0]["name"].str == "!wrap");
    assert(responses["2"]["result"].array[0]["location"]["range"]["start"]["line"].integer == 0);
    assert(responses["2"]["result"].array[0]["location"]["range"]["end"]["line"].integer == 1);
    assert(responses["3"]["result"].array.length != 0);
    assert(responses["3"]["result"].array[0]["startLine"].integer == 0);
    assert(responses["3"]["result"].array[0]["endLine"].integer == 1);
    assert(responses["4"]["result"]["items"].array.length == 1);
    assert(responses["4"]["result"]["items"].array[0]["label"].str == "!wrap");
    assert(responses["4"]["result"]["items"].array[0]["textEdit"]["range"]["start"]["character"].integer == 4);
    assert(responses["4"]["result"]["items"].array[0]["textEdit"]["range"]["end"]["character"].integer == 7);
    assert(responses["5"]["result"]["contents"]["value"].str.indexOf("wrap") >= 0);
    assert(responses["5"]["result"]["range"]["start"]["character"].integer == 5);
    assert(responses["5"]["result"]["range"]["end"]["character"].integer == 9);
    assert(responses["6"]["result"].array.length == 1);
    assert(responses["6"]["result"].array[0]["range"]["start"]["line"].integer == 0);
    assert(responses["6"]["result"].array[0]["range"]["start"]["character"].integer == 10);
    assert(responses["7"]["result"]["data"].array.length != 0);
    assert(responses["7"]["result"]["data"].array[0].integer == 0);
    assert(responses["7"]["result"]["data"].array[1].integer == 1);
    assert(responses["8"]["result"].array.length == 2);
    assert(responses["9"]["result"]["changes"].objectNoRef.length == 1);
    auto renameEdits = field(responses["9"]["result"]["changes"], uri);
    assert(renameEdits.array.length == 2);
    assert(responses["10"]["result"].array.length == 0);
    assert(responses["11"]["error"]["code"].integer == -32602);
    assert(responses["12"]["result"].array[0]["name"].str == "!shell");
    assert(responses["13"]["result"].type == JSONType.null_);
}

unittest
{
    auto macroResult = analyze(AnalysisRequest(
            "!defmacro{m}{body}::\n    !param{body}\n",
            "/tmp/analysis-root.tfxm", ModuleKind.macro_, Flags.init, null, null));
    assert(macroResult.complete);
    assert(macroResult.report.diagnostics.length == 0);

    auto impure = analyze(AnalysisRequest(
            "@frame::\n    body\n", "/tmp/analysis-impure.tfxm", ModuleKind.macro_,
            Flags.init, null, null));
    assert(!impure.complete);
    assert(impure.report.diagnostics.length == 1);

    auto missingMacro = analyze(AnalysisRequest(
            "!macroimport{missing.tfxm}\n", "/tmp/analysis-missing.tfxm",
            ModuleKind.macro_, Flags.init, null, null));
    assert(!missingMacro.complete);
    assert(missingMacro.dependencies.length == 1);
    assert(missingMacro.dependencies[0].kind == "macroimport");

    auto missing = analyze(AnalysisRequest(
            "!import{missing.tfx}\n", "/tmp/analysis-root.tfx", ModuleKind.content,
            Flags.init, null, null));
    assert(!missing.complete);
    assert(missing.report.diagnostics.length == 1);
    assert(missing.dependencies.length == 1);
    assert(missing.dependencies[0].kind == "import");
    assert(missing.dependencies[0].target == normalizedPath("/tmp/missing.tfx"));

    auto missingBundle = analyze(AnalysisRequest(
            "!bundleimport{missing.tfxb}{frame:1}\n", "/tmp/analysis-bundle.tfx",
            ModuleKind.content, Flags.init, null, null));
    assert(!missingBundle.complete);
    assert(missingBundle.dependencies.length == 1);
    assert(missingBundle.dependencies[0].kind == "bundleimport");
}

unittest
{
    const rootUri = pathToUri("/tmp/lsp-root.tfx");
    const partUri = pathToUri("/tmp/lsp-part.tfx");
    auto workspace = new Workspace(PositionEncoding.utf16);
    string rootPath;
    string error;
    assert(workspace.put(rootUri, 1, "!import{lsp-part.tfx}\n", rootPath, error));
    string partPath;
    assert(workspace.put(partUri, 1, "@frame::\n    body\n", partPath, error));
    workspace.commit(rootPath, workspace.analyzeRoot(rootPath));
    auto affected = workspace.affected(partPath);
    assert(affected.length == 2);
    assert(affected[0] == normalizedPath(partPath) || affected[1] == normalizedPath(partPath));
    assert(affected[0] == normalizedPath(rootPath) || affected[1] == normalizedPath(rootPath));

    assert(workspace.put(rootUri, 2, "@broken\n", rootPath, error));
    workspace.commit(rootPath, workspace.analyzeRoot(rootPath));
    auto retained = workspace.affected(partPath);
    assert(retained.canFind(normalizedPath(rootPath)));

    assert(workspace.put(rootUri, 3, "!import{lsp-part.tfx}\n", rootPath, error));
    workspace.commit(rootPath, workspace.analyzeRoot(rootPath));

    assert(!workspace.put(partUri, 0, "@frame::\n    changed\n", partPath, error));
    assert(workspace.document(partPath).documentVersion == 1);
    assert(workspace.put(partUri, 2, "@broken\n", partPath, error));
    workspace.commit(rootPath, workspace.analyzeRoot(rootPath));
    auto diagnostics = workspace.diagnostics();
    bool foundPart;
    foreach (file; diagnostics)
        if (file.uri == partUri)
        {
            foundPart = true;
            assert(file.diagnostics.length == 1);
            assert(file.diagnostics[0].related.length == 1);
        }
    assert(foundPart);

    string ignoredPath;
    assert(!workspace.put(pathToUri("/tmp/not-a-texflux.txt"), 1, "text\n",
            ignoredPath, error));

    auto internalWorkspace = new Workspace(PositionEncoding.utf16);
    string internalPath;
    assert(internalWorkspace.put(pathToUri("/tmp/internal.tfx"), 1,
            "@frame::\n    body\n", internalPath, error));
    internalWorkspace.invalidate(internalPath, "boom");
    auto internalFiles = internalWorkspace.diagnostics();
    assert(internalFiles.length == 1);
    assert(internalFiles[0].diagnostics.length == 1);
    assert(internalFiles[0].diagnostics[0].code == "internal");
    internalWorkspace.commit(internalPath, internalWorkspace.analyzeRoot(internalPath));
    assert(internalWorkspace.diagnostics()[0].diagnostics.length == 0);
}

unittest
{
    auto source =
        frame(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{"general":{"positionEncodings":["utf-32"]}}}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"initialized","params":{}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"textDocument/didOpen","params":{"textDocument":{"uri":"file:///tmp/server.tfx","languageId":"texflux","version":1,"text":"@foo\n"}}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"textDocument/didChange","params":{"textDocument":{"uri":"file:///tmp/server.tfx","version":2},"contentChanges":[{"text":"@foo::\n    body\n"}]}}`)
        ~ frame(`{"jsonrpc":"2.0","method":"textDocument/didClose","params":{"textDocument":{"uri":"file:///tmp/server.tfx"}}}`)
        ~ frame(`{"jsonrpc":"2.0","id":2,"method":"shutdown","params":null}`)
        ~ frame(`{"jsonrpc":"2.0","method":"exit"}`);
    auto connection = new MemoryConnection(source);
    assert(run(streams(connection)) == 0);
    auto messages = frames(connection.output);
    JSONValue[] publications;
    bool shutdownResponse;
    foreach (message; messages)
    {
        if (message.type != JSONType.object)
            continue;
        auto method = field(message, "method");
        if (method.type == JSONType.string && method.str == "textDocument/publishDiagnostics")
            publications ~= message;
        auto id = field(message, "id");
        if (id.type == JSONType.integer && id.integer == 2)
            shutdownResponse = field(message, "result").type == JSONType.null_;
    }
    assert(publications.length == 3);
    assert(publications[0]["params"]["diagnostics"].array.length == 1);
    assert(publications[1]["params"]["diagnostics"].array.length == 0);
    assert(publications[2]["params"]["diagnostics"].array.length == 0);
    assert(shutdownResponse);
}

unittest
{
    auto index = LineIndex("a\r\n日本語😀\rnext\n");
    assert(index.lineCount == 4);
    assert(index.fromLsp(LspPosition(1, 5), PositionEncoding.utf16).get
            == SourcePosition(2, 5));
    assert(index.fromLsp(LspPosition(1, 4), PositionEncoding.utf16).isNull);
    assert(index.fromLsp(LspPosition(1, 13), PositionEncoding.utf8).get
            == SourcePosition(2, 5));
    assert(index.fromLsp(LspPosition(1, 10), PositionEncoding.utf8).isNull);
    version (Windows)
        assert(uriToPath(pathToUri("C:\\tmp\\日本語.tfx")).get == "c:\\tmp\\日本語.tfx");
    else
        assert(uriToPath(pathToUri("/tmp/日本語.tfx")).get == "/tmp/日本語.tfx");
}

unittest
{
    auto binary = buildPath(getcwd(), "bin", "texflux");
    version (Windows)
        binary ~= ".exe";
    if (!exists(binary))
        return;

    auto pipes = pipeProcess([binary, "lsp"], Redirect.stdin | Redirect.stdout
            | Redirect.stderr);
    auto input = pipes.stdin;
    auto output = pipes.stdout;
    bool waited;
    scope (exit)
    {
        if (input.isOpen)
            input.close();
        if (!waited)
            wait(pipes.pid);
    }
    writeProcessFrame(input,
            `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`);
    auto initialize = parseJSON(readProcessFrame(output));
    assert(initialize["id"].integer == 1);
    assert(initialize["result"]["capabilities"].type == JSONType.object);

    writeProcessFrame(input,
            `{"jsonrpc":"2.0","id":2,"method":"shutdown","params":null}`);
    writeProcessFrame(input, `{"jsonrpc":"2.0","method":"exit"}`);
    auto shutdown = parseJSON(readProcessFrame(output));
    assert(shutdown["id"].integer == 2);
    input.close();
    assert(wait(pipes.pid) == 0);
    waited = true;
}
