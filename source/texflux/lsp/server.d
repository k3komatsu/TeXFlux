module texflux.lsp.server;

import std.algorithm : canFind, sort;
import std.json : JSONType, JSONValue;
import std.file : exists;
import std.path : buildNormalizedPath, dirName, extension;
import std.string : indexOf, lastIndexOf, startsWith;
import std.utf : decode;

import texflux.json : JsonMember, JsonValue, jsonArray, jsonNull, jsonObject, jsonOf,
    member;
import texflux : texfluxVersion;
import texflux.flags : conditionalNames, isFlagName;
import texflux.macros : isMacroName;
import texflux.modules : ModuleKind;

import texflux.lsp.features : FeatureOccurrence, FeatureOccurrenceKind, FeatureToken,
    builtinSpecialNames, isReservedMacroName;
import texflux.lsp.protocol;
import texflux.lsp.text : LspPosition, LspRange, PositionEncoding, pathToUri, uriToPath;
import texflux.lsp.transport : LspStreams, LspTransport, PacketStatus;
import texflux.lsp.workspace : OpenDocument, PublishedDiagnostic, PublishedFile,
    RelatedDiagnostic, TextChange, Workspace;
import texflux.source : SourcePosition;

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
        if (message.method == "textDocument/documentSymbol")
            return documentSymbols(message);
        if (message.method == "textDocument/foldingRange")
            return foldingRanges(message);
        if (message.method == "textDocument/completion")
            return completion(message);
        if (message.method == "textDocument/hover")
            return hover(message);
        if (message.method == "textDocument/definition")
            return definition(message);
        if (message.method == "textDocument/semanticTokens/full")
            return semanticTokens(message);
        if (message.method == "textDocument/references")
            return references(message);
        if (message.method == "textDocument/rename")
            return rename(message);
        if (message.method == "textDocument/codeAction")
            return codeAction(message);
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
        auto semantic = jsonObject(
                member("legend", jsonObject(
                    member("tokenTypes", tokenTypes()), member("tokenModifiers", jsonArray([])))),
                member("full", true));
        auto capabilitiesWithFeatures = jsonObject(
                member("positionEncoding", cast(string) encoding),
                member("textDocumentSync", jsonObject(
                    member("openClose", true),
                    member("change", 2L),
                    member("save", jsonObject(member("includeText", false))))),
                member("documentSymbolProvider", true),
                member("foldingRangeProvider", true),
                member("completionProvider", jsonObject(member("triggerCharacters",
                    jsonArray([jsonOf("!"), jsonOf("@")] )))),
                member("hoverProvider", true),
                member("definitionProvider", true),
                member("referencesProvider", true),
                member("renameProvider", true),
                member("semanticTokensProvider", semantic));
        auto result = jsonObject(member("capabilities", capabilitiesWithFeatures),
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
                || changes.type != JSONType.array)
            return invalidNotification(message, "didChange requires contentChanges");
        if (changes.array.length == 0)
            return true;
        TextChange[] edits;
        foreach (change; changes.array)
        {
            string text;
            if (!stringField(change, "text", text))
                return invalidNotification(message, "didChange requires a text field");
            TextChange edit;
            edit.text = text;
            if (hasField(change, "range"))
            {
                if (!rangeField(change, "range", edit.range))
                    return invalidNotification(message, "didChange range is invalid");
                edit.hasRange = true;
            }
            edits ~= edit;
        }
        auto path = uriToPath(uri);
        if (path.isNull)
            return invalidNotification(message, "only file URIs are supported");
        auto affected = workspace.affected(path.get);
        string storedPath;
        string error;
        if (!workspace.applyChanges(uri, documentVersion, edits, storedPath, error))
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

    private bool documentSymbols(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "documentSymbol requires an open file URI");

        JsonValue[] symbols;
        if (document.features.complete)
            foreach (symbol; document.features.symbols)
            {
                JsonMember[] fields = [
                    member("name", symbol.name),
                    member("kind", symbol.kind),
                    member("location", locationJson(uri,
                        document.lines.toLsp(symbol.span, workspace.encoding)))
                ];
                if (symbol.container.length != 0)
                    fields ~= member("containerName", symbol.container);
                symbols ~= jsonObject(fields);
            }
        transport.write(response(message.id, jsonArray(symbols)));
        return true;
    }

    private bool foldingRanges(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "foldingRange requires an open file URI");

        JsonValue[] ranges;
        if (document.features.complete)
            foreach (fold; document.features.folds)
            {
                auto range = document.lines.toLsp(fold.span, workspace.encoding);
                if (range.start.line == range.end.line)
                    continue;
                JsonMember[] fields = [
                    member("startLine", range.start.line),
                    member("endLine", range.end.line)
                ];
                if (fold.kind.length != 0)
                    fields ~= member("kind", fold.kind);
                ranges ~= jsonObject(fields);
            }
        transport.write(response(message.id, jsonArray(ranges)));
        return true;
    }

    private bool completion(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "completion requires an open file URI");
        LspPosition position;
        if (!positionValue(field(message.params, "position"), position))
            return invalidNotification(message, "completion requires a position");
        auto sourcePosition = document.lines.fromLsp(position, workspace.encoding);
        if (sourcePosition.isNull)
            return invalidNotification(message, "completion position is invalid");
        const endPosition = document.lines.toLsp(sourcePosition.get, workspace.encoding);

        const prefix = linePrefix(document.lines.line(cast(size_t) position.line),
            cast(size_t) (sourcePosition.get.column <= 1
                ? 0 : sourcePosition.get.column - 1));
        const whenIndex = prefix.lastIndexOf("!when{");
        const unlessIndex = prefix.lastIndexOf("!unless{");
        const flagIndex = whenIndex > unlessIndex ? whenIndex : unlessIndex;
        const open = prefix.lastIndexOf('{');
        const close = prefix.lastIndexOf('}');
        const inFlagGroup = flagIndex >= 0 && open > flagIndex && close < open
            && prefix[flagIndex .. $].indexOf(">>") < 0;
        const bang = prefix.lastIndexOf('!');
        LspRange editRange;
        if (inFlagGroup)
        {
            const startColumn = cast(int) codePointCount(prefix[0 .. open]) + 2;
            editRange.start = document.lines.toLsp(
                SourcePosition(cast(int) position.line + 1, startColumn),
                workspace.encoding);
            editRange.end = endPosition;
        }
        else if (bang >= 0)
        {
            const startColumn = cast(int) codePointCount(prefix[0 .. bang]) + 1;
            editRange.start = document.lines.toLsp(
                SourcePosition(cast(int) position.line + 1, startColumn),
                workspace.encoding);
            editRange.end = endPosition;
        }
        else
            editRange = LspRange(endPosition, endPosition);
        JsonValue[] items;
        string[] labels;
        if (inFlagGroup)
        {
            const partial = prefix[open + 1 .. $];
            foreach (occurrence; document.features.occurrences)
                if (occurrence.kind == FeatureOccurrenceKind.flag_ && occurrence.declaration)
                    addCompletion(items, labels, occurrence.name, 6L, partial, editRange);
        }
        else
        {
            const partial = bang >= 0 ? prefix[bang .. $] : "";
            foreach (special; builtinSpecialNames)
                addCompletion(items, labels, "!" ~ special, 14L, partial, editRange);
            foreach (occurrence; document.features.occurrences)
                if (occurrence.kind == FeatureOccurrenceKind.macro_ && occurrence.declaration)
                    addCompletion(items, labels, "!" ~ occurrence.name, 3L, partial, editRange);
        }
        transport.write(response(message.id, jsonObject(member("isIncomplete", false),
            member("items", jsonArray(items)))));
        return true;
    }

    private bool hover(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "hover requires an open file URI");
        LspPosition position;
        if (!positionValue(field(message.params, "position"), position))
            return invalidNotification(message, "hover requires a position");
        auto occurrence = occurrenceAt(document, position, workspace.encoding);
        if (occurrence is null)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        string value;
        final switch (occurrence.kind)
        {
        case FeatureOccurrenceKind.macro_:
            value = "**macro** `!" ~ occurrence.name ~ "`";
            break;
        case FeatureOccurrenceKind.flag_:
            value = "**build flag** `" ~ occurrence.name ~ "`";
            break;
        case FeatureOccurrenceKind.modulePath:
            value = "**module** `" ~ occurrence.name ~ "`";
            break;
        }
        if (occurrence.kind != FeatureOccurrenceKind.modulePath
                && hasLocalDeclaration(document, *occurrence))
        {
            value ~= "\n\nDefined in `" ~ document.uri ~ "`";
        }
        auto result = jsonObject(
            member("contents", jsonObject(member("kind", "markdown"),
                member("value", value))),
            member("range", rangeJson(document.lines.toLsp(occurrence.span,
                workspace.encoding))));
        transport.write(response(message.id, result));
        return true;
    }

    private bool definition(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "definition requires an open file URI");
        LspPosition position;
        if (!positionValue(field(message.params, "position"), position))
            return invalidNotification(message, "definition requires a position");
        auto occurrence = occurrenceAt(document, position, workspace.encoding);
        if (occurrence is null)
        {
            transport.write(response(message.id, jsonArray([])));
            return true;
        }

        JsonValue[] locations;
        if (occurrence.kind == FeatureOccurrenceKind.modulePath)
        {
            const target = buildNormalizedPath(dirName(document.displayPath), occurrence.name);
            auto targetDocument = workspace.document(target);
            if (targetDocument !is null || exists(target))
                locations ~= locationJson(targetDocument is null ? pathToUri(target)
                    : targetDocument.uri, LspRange(LspPosition(0, 0), LspPosition(0, 0)));
        }
        else
        {
            foreach (path; sortedDocumentPaths(workspace))
            {
                auto candidate = &workspace.documents[path];
                foreach (declaration; candidate.features.occurrences)
                    if (declaration.declaration && declaration.kind == occurrence.kind
                            && declaration.name == occurrence.name)
                        locations ~= locationJson(candidate.uri,
                            candidate.lines.toLsp(declaration.span, workspace.encoding));
            }
        }
        transport.write(response(message.id, jsonArray(locations)));
        return true;
    }

    private bool semanticTokens(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "semanticTokens requires an open file URI");

        FeatureToken[] tokens = document.features.complete
            ? document.features.tokens.dup : null;
        tokens.sort!((left, right) => left.span.start < right.span.start);
        JsonValue[] data;
        long previousLine;
        long previousStart;
        bool havePrevious;
        foreach (token; tokens)
        {
            auto range = document.lines.toLsp(token.span, workspace.encoding);
            if (range.start.line != range.end.line)
                continue;
            const deltaLine = range.start.line - (havePrevious ? previousLine : 0);
            const deltaStart = !havePrevious || deltaLine != 0
                ? range.start.character : range.start.character - previousStart;
            const length = range.end.character - range.start.character;
            if (length <= 0)
                continue;
            data ~= jsonOf(deltaLine);
            data ~= jsonOf(deltaStart);
            data ~= jsonOf(length);
            data ~= jsonOf(token.tokenType);
            data ~= jsonOf(token.modifiers);
            previousLine = range.start.line;
            previousStart = range.start.character;
            havePrevious = true;
        }
        transport.write(response(message.id, jsonObject(member("data", jsonArray(data)))));
        return true;
    }

    private bool references(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "references requires an open file URI");
        LspPosition position;
        if (!positionValue(field(message.params, "position"), position))
            return invalidNotification(message, "references requires a position");
        auto occurrence = occurrenceAt(document, position, workspace.encoding);
        if (occurrence is null)
        {
            transport.write(response(message.id, jsonArray([])));
            return true;
        }
        if (occurrence.kind == FeatureOccurrenceKind.modulePath
                || !hasLocalDeclaration(document, *occurrence))
        {
            transport.write(response(message.id, jsonArray([])));
            return true;
        }
        bool includeDeclaration;
        auto context = field(message.params, "context");
        if (hasField(context, "includeDeclaration")
                && !booleanField(context, "includeDeclaration", includeDeclaration))
            return invalidNotification(message, "references includeDeclaration is invalid");

        JsonValue[] locations;
        foreach (item; document.features.occurrences)
            if (item.kind == occurrence.kind && item.name == occurrence.name
                    && (includeDeclaration || !item.declaration))
                locations ~= locationJson(document.uri,
                    document.lines.toLsp(item.span, workspace.encoding));
        transport.write(response(message.id, jsonArray(locations)));
        return true;
    }

    private bool rename(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "rename requires an open file URI");
        LspPosition position;
        string newName;
        if (!positionValue(field(message.params, "position"), position)
                || !stringField(message.params, "newName", newName))
            return invalidNotification(message, "rename requires position and newName");
        auto occurrence = occurrenceAt(document, position, workspace.encoding);
        if (occurrence is null || occurrence.kind == FeatureOccurrenceKind.modulePath)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        if (!hasLocalDeclaration(document, *occurrence))
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        if (document.kind == ModuleKind.macro_)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        if ((occurrence.kind == FeatureOccurrenceKind.macro_
                    && (!isMacroName(newName) || isReservedMacroName(newName)))
                || (occurrence.kind == FeatureOccurrenceKind.flag_
                    && (!isFlagName(newName) || conditionalNames.canFind(newName))))
            return invalidNotification(message, "newName is not valid for this symbol");

        JsonMember[] changes;
        JsonValue[] edits;
        foreach (item; document.features.occurrences)
            if (item.kind == occurrence.kind && item.name == occurrence.name)
                edits ~= jsonObject(member("range", rangeJson(
                    document.lines.toLsp(item.span, workspace.encoding))),
                    member("newText", newName));
        if (edits.length != 0)
            changes ~= member(document.uri, jsonArray(edits));
        if (changes.length == 0)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        transport.write(response(message.id,
            jsonObject(member("changes", jsonObject(changes)))));
        return true;
    }

    private bool codeAction(RpcMessage message)
    {
        if (message.hasId)
            transport.write(response(message.id, jsonArray([])));
        return true;
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

private JsonValue tokenTypes()
{
    return jsonArray([
        jsonOf("namespace"), jsonOf("function"), jsonOf("keyword"),
        jsonOf("variable"), jsonOf("string"), jsonOf("macro"), jsonOf("flag")
    ]);
}

private OpenDocument* openDocument(Workspace workspace, JSONValue params, out string uri)
{
    auto document = field(params, "textDocument");
    if (!stringField(document, "uri", uri))
        return null;
    auto path = uriToPath(uri);
    if (path.isNull)
        return null;
    return workspace.document(path.get);
}

private bool positionValue(JSONValue value, out LspPosition result)
{
    long line;
    long character;
    if (!integerField(value, "line", line) || !integerField(value, "character", character))
        return false;
    result = LspPosition(line, character);
    return true;
}

private bool rangeField(JSONValue object, string name, out LspRange result)
{
    auto value = field(object, name);
    return value.type == JSONType.object
        && positionValue(field(value, "start"), result.start)
        && positionValue(field(value, "end"), result.end);
}

private bool booleanField(JSONValue object, string name, out bool result)
{
    auto value = field(object, name);
    if (value.type != JSONType.true_ && value.type != JSONType.false_)
        return false;
    result = value.type == JSONType.true_;
    return true;
}

private FeatureOccurrence* occurrenceAt(OpenDocument* document, LspPosition position,
        PositionEncoding encoding)
{
    auto sourcePosition = document.lines.fromLsp(position, encoding);
    if (sourcePosition.isNull)
        return null;
    foreach (ref occurrence; document.features.occurrences)
        if (sourcePosition.get >= occurrence.span.start
                && sourcePosition.get < occurrence.span.end)
            return &occurrence;
    return null;
}

private bool hasLocalDeclaration(OpenDocument* document, FeatureOccurrence occurrence)
{
    foreach (candidate; document.features.occurrences)
        if (candidate.declaration && candidate.kind == occurrence.kind
                && candidate.name == occurrence.name)
            return true;
    return false;
}

private string[] sortedDocumentPaths(Workspace workspace)
{
    auto result = workspace.documents.keys;
    result.sort;
    return result;
}

private string linePrefix(string line, size_t codePoints)
{
    size_t index;
    size_t points;
    while (index < line.length && points < codePoints)
    {
        decode(line, index);
        ++points;
    }
    return line[0 .. index].idup;
}

private size_t codePointCount(string text)
{
    size_t index;
    size_t result;
    while (index < text.length)
    {
        decode(text, index);
        ++result;
    }
    return result;
}

private void addCompletion(ref JsonValue[] items, ref string[] labels, string label,
        long kind, string prefix, LspRange editRange)
{
    if (prefix.length != 0 && !label.startsWith(prefix))
        return;
    if (labels.canFind(label))
        return;
    labels ~= label;
    items ~= jsonObject(member("label", label), member("kind", kind),
        member("textEdit", jsonObject(member("range", rangeJson(editRange)),
            member("newText", label))));
}

private JsonValue locationJson(string uri, LspRange range)
{
    return jsonObject(member("uri", uri), member("range", rangeJson(range)));
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
