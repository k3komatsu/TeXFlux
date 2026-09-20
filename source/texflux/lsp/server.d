module texflux.lsp.server;

import std.algorithm : canFind, sort;
import std.json : JSONType, JSONValue;
import std.path : extension;

import texflux.json : JsonValue, jsonArray, jsonNull, jsonObject, member;
import texflux : texfluxVersion;

import texflux.lsp.protocol;
import texflux.lsp.text : LspPosition, LspRange, PositionEncoding, uriToPath;
import texflux.lsp.transport : LspStreams, LspTransport, PacketStatus;
import texflux.lsp.workspace : PublishedDiagnostic, PublishedFile, RelatedDiagnostic,
    Workspace;

enum long serverNotInitializedCode = -32002;
enum long serverShuttingDownCode = -32001;
enum long serverAlreadyInitializedCode = -32000;

/// Run one single-threaded LSP connection until exit or EOF.
int run(LspStreams streams)
{
    auto server = new LspServer(streams);
    return server.run();
}

private final class LspServer
{
    LspTransport transport;
    Workspace workspace;
    bool initialized;
    bool shuttingDown;
    string[] publishedUris;

    this(LspStreams streams)
    {
        transport.streams = streams;
    }

    int run()
    {
        while (true)
        {
            auto packet = transport.readPacket();
            final switch (packet.status)
            {
            case PacketStatus.eof:
                return shuttingDown ? 0 : 1;
            case PacketStatus.framingError:
                log(packet.error);
                return 1;
            case PacketStatus.invalidJson:
                transport.write(errorResponse(JSONValue.init, parseErrorCode, packet.error));
                continue;
            case PacketStatus.message:
                if (!dispatch(packet.value))
                    return shuttingDown ? 0 : 1;
                break;
            }
        }
    }

    private bool dispatch(JSONValue value)
    {
        auto message = inspect(value);
        if (!message.valid)
        {
            transport.write(errorResponse(JSONValue.init, invalidRequestCode,
                    "invalid JSON-RPC request"));
            return true;
        }

        try
        {
        if (message.method == "exit")
        {
            return false;
        }
        if (message.method == "initialize")
            return initialize(message);
        if (!initialized)
        {
            if (message.hasId)
                transport.write(errorResponse(message.id, serverNotInitializedCode,
                        "server is not initialized"));
            return true;
        }
        if (shuttingDown)
        {
            if (message.hasId)
                transport.write(errorResponse(message.id, serverShuttingDownCode,
                        "server is shutting down"));
            return true;
        }
        if (message.method == "initialized")
            return true;
        if (message.method == "shutdown")
        {
            if (!message.hasId)
                return true;
            shuttingDown = true;
            transport.write(response(message.id, jsonNull()));
            return true;
        }

        if (message.method == "textDocument/didOpen")
            return documentOpen(message);
        if (message.method == "textDocument/didChange")
            return documentChange(message);
        if (message.method == "textDocument/didSave")
            return documentSave(message);
        if (message.method == "textDocument/didClose")
            return documentClose(message);
        if (message.method == "workspace/didChangeWatchedFiles")
            return watchedFiles(message);

        if (message.hasId)
            transport.write(errorResponse(message.id, methodNotFoundCode,
                    "method not found: " ~ message.method));
        return true;
        }
        catch (Exception error)
        {
            if (message.hasId)
                transport.write(errorResponse(message.id, internalErrorCode,
                        "internal error: " ~ error.msg));
            else
                log("internal error: " ~ error.msg);
            return true;
        }
    }

    private bool initialize(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        if (initialized)
        {
            transport.write(errorResponse(message.id, serverAlreadyInitializedCode,
                    "server is already initialized"));
            return true;
        }
        auto encoding = requestedEncoding(message.hasParams ? message.params : JSONValue.init);
        workspace = new Workspace(encoding);
        initialized = true;
        auto capabilities = jsonObject(
                member("positionEncoding", cast(string) encoding),
                member("textDocumentSync", jsonObject(
                    member("openClose", true),
                    member("change", 1L),
                    member("save", jsonObject(member("includeText", false))))));
        auto result = jsonObject(member("capabilities", capabilities),
                member("serverInfo", jsonObject(member("name", "texflux"),
                    member("version", texfluxVersion))));
        transport.write(response(message.id, result));
        return true;
    }

    private bool documentOpen(RpcMessage message)
    {
        string uri;
        string text;
        long documentVersion;
        auto document = message.hasParams ? field(message.params, "textDocument") : JSONValue.init;
        if (!stringField(document, "uri", uri)
                || !stringField(document, "text", text)
                || !integerField(document, "version", documentVersion))
            return invalidNotification(message, "didOpen requires uri, version and text");

        auto path = uriToPath(uri);
        if (path.isNull)
            return invalidNotification(message, "only file URIs are supported");
        auto affected = workspace.affected(path.get);
        string storedPath;
        string error;
        if (!workspace.put(uri, documentVersion, text, storedPath, error))
            return invalidNotification(message, error);
        appendMissing(affected, workspace.affected(storedPath));
        analyze(affected);
        publishDiagnostics();
        return true;
    }

    private bool documentChange(RpcMessage message)
    {
        string uri;
        long documentVersion;
        auto params = message.hasParams ? message.params : JSONValue.init;
        auto document = field(params, "textDocument");
        auto changes = field(params, "contentChanges");
        if (!stringField(document, "uri", uri)
                || !integerField(document, "version", documentVersion)
                || changes.type != JSONType.array || changes.array.length != 1)
            return invalidNotification(message, "didChange requires one full content change");
        auto change = changes.array[0];
        string text;
        if (!stringField(change, "text", text) || hasField(change, "range"))
            return invalidNotification(message, "didChange requires a range-free full change");
        auto path = uriToPath(uri);
        if (path.isNull)
            return invalidNotification(message, "only file URIs are supported");
        auto affected = workspace.affected(path.get);
        string storedPath;
        string error;
        if (!workspace.put(uri, documentVersion, text, storedPath, error))
            return invalidNotification(message, error);
        appendMissing(affected, workspace.affected(storedPath));
        analyze(affected);
        publishDiagnostics();
        return true;
    }

    private bool documentSave(RpcMessage message)
    {
        string uri;
        auto document = message.hasParams ? field(message.params, "textDocument") : JSONValue.init;
        if (!stringField(document, "uri", uri))
            return invalidNotification(message, "didSave requires uri");
        auto path = uriToPath(uri);
        if (path.isNull)
            return invalidNotification(message, "only file URIs are supported");
        auto affected = workspace.affected(path.get);
        workspace.touch(path.get);
        appendMissing(affected, workspace.affected(path.get));
        analyze(affected);
        publishDiagnostics();
        return true;
    }

    private bool documentClose(RpcMessage message)
    {
        string uri;
        auto document = message.hasParams ? field(message.params, "textDocument") : JSONValue.init;
        if (!stringField(document, "uri", uri))
            return invalidNotification(message, "didClose requires uri");
        auto path = uriToPath(uri);
        if (path.isNull)
            return invalidNotification(message, "only file URIs are supported");
        auto affected = workspace.affected(path.get);
        workspace.close(path.get);
        analyze(affected);
        publishDiagnostics();
        return true;
    }

    private bool watchedFiles(RpcMessage message)
    {
        auto params = message.hasParams ? message.params : JSONValue.init;
        auto changes = field(params, "changes");
        if (changes.type != JSONType.array)
            return invalidNotification(message, "didChangeWatchedFiles requires changes");

        string[] affected;
        foreach (change; changes.array)
        {
            string uri;
            if (!stringField(change, "uri", uri))
            {
                log("watched file change without uri");
                continue;
            }
            auto path = uriToPath(uri);
            if (path.isNull)
                continue;
            const suffix = extension(path.get);
            if (suffix != ".tfx" && suffix != ".tfxm" && suffix != ".tfxb")
                continue;
            appendMissing(affected, workspace.affected(path.get));
        }
        analyze(affected);
        if (affected.length != 0)
            publishDiagnostics();
        return true;
    }

    private void analyze(string[] paths)
    {
        foreach (path; paths)
        {
            if (!workspace.contains(path))
                continue;
            try
                workspace.commit(path, workspace.analyzeRoot(path));
            catch (Exception error)
            {
                workspace.invalidate(path, error.msg);
                log("analysis failed for " ~ path ~ ": " ~ error.msg);
            }
        }
    }

    private void publishDiagnostics()
    {
        auto files = workspace.diagnostics();
        string[string] current;
        foreach (file; files)
            current[file.uri] = file.uri;
        foreach (uri; publishedUris)
            if (uri !in current)
                files ~= PublishedFile(uri);
        files.sort!((left, right) => left.uri < right.uri);
        foreach (file; files)
        {
            auto params = jsonObject(member("uri", file.uri),
                    member("diagnostics", diagnosticArray(file.diagnostics)));
            if (file.hasVersion)
                params = jsonObject(member("uri", file.uri),
                        member("version", cast(long) file.documentVersion),
                        member("diagnostics", diagnosticArray(file.diagnostics)));
            transport.write(notification("textDocument/publishDiagnostics", params));
        }
        publishedUris = current.keys;
    }

    private bool invalidNotification(RpcMessage message, string text)
    {
        if (message.hasId)
            transport.write(errorResponse(message.id, invalidParamsCode, text));
        else
            log(text);
        return true;
    }

    private void log(string text)
    {
        if (transport.streams.log !is null)
            transport.streams.log(text ~ "\n");
    }
}

private PositionEncoding requestedEncoding(JSONValue params)
{
    auto capabilities = field(params, "capabilities");
    auto general = field(capabilities, "general");
    auto encodings = field(general, "positionEncodings");
    if (encodings.type == JSONType.array)
        foreach (encoding; encodings.array)
            if (encoding.type == JSONType.string)
            {
                if (encoding.str == "utf-8")
                    return PositionEncoding.utf8;
                if (encoding.str == "utf-16")
                    return PositionEncoding.utf16;
                if (encoding.str == "utf-32")
                    return PositionEncoding.utf32;
            }
    return PositionEncoding.utf16;
}

private JsonValue diagnosticArray(const PublishedDiagnostic[] diagnostics)
{
    JsonValue[] result;
    foreach (diagnostic; diagnostics)
    {
        JsonValue[] related;
        foreach (location; diagnostic.related)
            related ~= jsonObject(
                    member("location", jsonObject(member("uri", location.uri),
                        member("range", rangeJson(location.range)))),
                    member("message", location.message));
        result ~= jsonObject(
                member("severity", cast(long) diagnostic.severity),
                member("code", diagnostic.code),
                member("source", "texflux"),
                member("message", diagnostic.message),
                member("range", rangeJson(diagnostic.range)),
                member("relatedInformation", jsonArray(related)));
    }
    return jsonArray(result);
}

private JsonValue rangeJson(LspRange range)
{
    return jsonObject(member("start", positionJson(range.start)),
            member("end", positionJson(range.end)));
}

private JsonValue positionJson(LspPosition position)
{
    return jsonObject(member("line", position.line), member("character", position.character));
}

private void appendMissing(ref string[] destination, const string[] additions)
{
    foreach (addition; additions)
        if (!destination.canFind(addition))
            destination ~= addition;
}
