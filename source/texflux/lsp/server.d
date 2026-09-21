module texflux.lsp.server;

import std.algorithm : canFind, sort;
import std.json : JSONType, JSONValue;
import std.file : exists;
import std.path : extension;
import std.string : indexOf, lastIndexOf, startsWith, strip;
import std.utf : decode;

import texflux.json : JsonMember, JsonValue, jsonArray, jsonNull, jsonObject, jsonOf,
    member;
import texflux : texfluxVersion;
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
    bool prepareRenameSupport;
    bool diagnosticRelatedInformationSupport;
    bool diagnosticVersionSupport;
    bool hoverMarkdownSupport;
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
            transport.write(errorResponse(message.hasId ? message.id : JSONValue.init,
                    invalidRequestCode,
                    "invalid JSON-RPC request"));
            return true;
        }

        try
        {
        if (notificationMethod(message.method) && message.hasId)
        {
            transport.write(errorResponse(message.id, invalidRequestCode,
                    "notification method must not have an id"));
            return true;
        }
        if (requestMethod(message.method) && !message.hasId)
        {
            log("request method received as notification: " ~ message.method);
            return true;
        }
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
                transport.write(errorResponse(message.id, invalidRequestCode,
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
        if (message.method == "textDocument/prepareRename")
            return prepareRename(message);
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
            transport.write(errorResponse(message.id, invalidRequestCode,
                    "server is already initialized"));
            return true;
        }
        auto encoding = requestedEncoding(message.hasParams ? message.params : JSONValue.init);
        prepareRenameSupport = clientSupports(message, "rename", "prepareSupport");
        diagnosticRelatedInformationSupport = clientSupports(message, "publishDiagnostics",
                "relatedInformation");
        diagnosticVersionSupport = clientSupports(message, "publishDiagnostics", "versionSupport");
        hoverMarkdownSupport = clientSupportsMarkdown(message);
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
                    jsonArray([jsonOf("!")] )))),
                member("hoverProvider", true),
                member("definitionProvider", true),
                member("referencesProvider", true),
                member("renameProvider", prepareRenameSupport
                    ? jsonObject(member("prepareProvider", true)) : jsonOf(true)),
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
                workspace.invalidate(path);
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
                    member("diagnostics", diagnosticArray(file.diagnostics,
                        diagnosticRelatedInformationSupport)));
            if (file.hasVersion && diagnosticVersionSupport)
                params = jsonObject(member("uri", file.uri),
                        member("version", cast(long) file.documentVersion),
                        member("diagnostics", diagnosticArray(file.diagnostics,
                            diagnosticRelatedInformationSupport)));
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
        if (!completionContextAllowed(document, cast(size_t) position.line, prefix))
        {
            transport.write(response(message.id, jsonObject(member("isIncomplete", false),
                member("items", jsonArray([])))));
            return true;
        }
        const whenIndex = conditionalIndex(prefix, "!when");
        const unlessIndex = conditionalIndex(prefix, "!unless");
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
        else if (bang >= 0)
        {
            const partial = prefix[bang .. $];
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
        string plainValue;
        final switch (occurrence.kind)
        {
        case FeatureOccurrenceKind.macro_:
            value = "**macro** `!" ~ occurrence.name ~ "`";
            plainValue = "macro !" ~ occurrence.name;
            break;
        case FeatureOccurrenceKind.flag_:
            value = "**build flag** `" ~ occurrence.name ~ "`";
            plainValue = "build flag " ~ occurrence.name;
            break;
        case FeatureOccurrenceKind.modulePath:
            value = "**module** `" ~ occurrence.name ~ "`";
            plainValue = "module " ~ occurrence.name;
            break;
        }
        if (occurrence.kind != FeatureOccurrenceKind.modulePath
                && hasLocalDeclaration(document, *occurrence))
        {
            value ~= "\n\nDefined in `" ~ document.uri ~ "`";
            plainValue ~= "\n\nDefined in " ~ document.uri;
        }
        auto contents = hoverMarkdownSupport
            ? jsonObject(member("kind", "markdown"), member("value", value))
            : jsonObject(member("kind", "plaintext"), member("value", plainValue));
        auto result = jsonObject(
            member("contents", contents),
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
            auto target = workspace.dependencyTarget(document.canonicalPath,
                    occurrence.moduleKind, occurrence.span);
            if (target.length != 0)
            {
                auto targetDocument = workspace.document(target);
                if (targetDocument !is null || exists(target))
                    locations ~= locationJson(targetDocument is null ? pathToUri(target)
                        : targetDocument.uri,
                        LspRange(LspPosition(0, 0), LspPosition(0, 0)));
            }
        }
        else
        {
            auto declaration = localDeclaration(document, *occurrence);
            if (declaration !is null)
                locations ~= locationJson(document.uri,
                    document.lines.toLsp(declaration.span, workspace.encoding));
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
        if (document.kind == ModuleKind.macro_
                || occurrence.kind == FeatureOccurrenceKind.flag_)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        if (occurrence.kind == FeatureOccurrenceKind.macro_
                && (!isMacroName(newName) || isReservedMacroName(newName)))
            return invalidNotification(message, "newName is not valid for this symbol");

        foreach (candidate; document.features.occurrences)
            if (candidate.declaration && candidate.kind == occurrence.kind
                    && candidate.name == newName && candidate.name != occurrence.name)
            {
                transport.write(errorResponse(message.id, invalidParamsCode,
                        "newName conflicts with an existing declaration"));
                return true;
            }

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

    private bool prepareRename(RpcMessage message)
    {
        if (!message.hasId)
            return true;
        string uri;
        auto document = openDocument(workspace,
                message.hasParams ? message.params : JSONValue.init, uri);
        if (document is null)
            return invalidNotification(message, "prepareRename requires an open file URI");
        LspPosition position;
        if (!positionValue(field(message.params, "position"), position))
            return invalidNotification(message, "prepareRename requires a position");
        auto occurrence = occurrenceAt(document, position, workspace.encoding);
        if (occurrence is null || occurrence.kind != FeatureOccurrenceKind.macro_
                || document.kind == ModuleKind.macro_
                || localDeclaration(document, *occurrence) is null)
        {
            transport.write(response(message.id, jsonNull()));
            return true;
        }
        transport.write(response(message.id, jsonObject(
                member("range", rangeJson(document.lines.toLsp(occurrence.span,
                    workspace.encoding))), member("placeholder", occurrence.name))));
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
    return localDeclaration(document, occurrence) !is null;
}

private FeatureOccurrence* localDeclaration(OpenDocument* document,
        FeatureOccurrence occurrence)
{
    foreach (ref candidate; document.features.occurrences)
        if (candidate.declaration && candidate.kind == occurrence.kind
                && candidate.name == occurrence.name)
            return &candidate;
    return null;
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

private bool requestMethod(string method) @safe pure nothrow
{
    switch (method)
    {
    case "initialize":
    case "shutdown":
    case "textDocument/documentSymbol":
    case "textDocument/foldingRange":
    case "textDocument/completion":
    case "textDocument/hover":
    case "textDocument/definition":
    case "textDocument/semanticTokens/full":
    case "textDocument/references":
    case "textDocument/rename":
    case "textDocument/prepareRename":
    case "textDocument/codeAction":
        return true;
    default:
        return false;
    }
}

private bool notificationMethod(string method) @safe pure nothrow
{
    switch (method)
    {
    case "initialized":
    case "exit":
    case "textDocument/didOpen":
    case "textDocument/didChange":
    case "textDocument/didSave":
    case "textDocument/didClose":
    case "workspace/didChangeWatchedFiles":
        return true;
    default:
        return false;
    }
}

private bool clientSupports(RpcMessage message, string feature, string capability)
{
    auto textDocument = field(field(message.params, "capabilities"), "textDocument");
    auto value = field(field(textDocument, feature), capability);
    return value.type == JSONType.true_;
}

private bool clientSupportsMarkdown(RpcMessage message)
{
    auto textDocument = field(field(message.params, "capabilities"), "textDocument");
    auto formats = field(field(textDocument, "hover"), "contentFormat");
    if (formats.type != JSONType.array)
        return false;
    foreach (format; formats.array)
        if (format.type == JSONType.string && format.str == "markdown")
            return true;
    return false;
}

private bool completionContextAllowed(OpenDocument* document, size_t lineNumber,
        string prefix)
{
    auto trimmed = prefix.strip;
    if (hasTeXComment(prefix) || trimmed.startsWith("!!")
            || trimmed.startsWith("!|"))
        return false;

    const bang = prefix.lastIndexOf('!');
    if (bang >= 0 && prefix.lastIndexOf('\\') > bang)
        return false;
    if (bang > 0 && prefix[bang - 1] == '!')
        return false;
    if (prefix.lastIndexOf("!|") >= 0)
        return false;

    bool raw;
    foreach (index; 0 .. lineNumber)
    {
        auto line = document.lines.line(index).strip;
        if (line == "!BEGIN_RAW_MODE")
            raw = true;
        else if (line == "!END_RAW_MODE")
            raw = false;
    }
    return !raw;
}

private bool hasTeXComment(string text)
{
    bool escaped;
    foreach (character; text)
    {
        if (character == '\\')
        {
            escaped = !escaped;
            continue;
        }
        if (character == '%' && !escaped)
            return true;
        escaped = false;
    }
    return false;
}

private long conditionalIndex(string prefix, string name)
{
    const index = cast(long) prefix.lastIndexOf(name);
    if (index < 0)
        return -1;
    auto rest = prefix[cast(size_t) index + name.length .. $];
    if (rest.startsWith("{"))
        return index;
    if (!rest.startsWith("["))
        return -1;
    const close = rest.indexOf(']');
    if (close < 0 || close + 1 >= rest.length || rest[close + 1] != '{')
        return -1;
    return index;
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

private JsonValue diagnosticArray(const PublishedDiagnostic[] diagnostics,
        bool includeRelated)
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
        JsonMember[] fields = [
                member("severity", cast(long) diagnostic.severity),
                member("code", diagnostic.code),
                member("source", "texflux"),
                member("message", diagnostic.message),
                member("range", rangeJson(diagnostic.range))];
        if (includeRelated)
            fields ~= member("relatedInformation", jsonArray(related));
        result ~= jsonObject(fields);
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
