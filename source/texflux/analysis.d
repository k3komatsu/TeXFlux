/**
 * Editor-independent analysis over the existing compiler session.
 *
 * This module deliberately contains no URI, LSP position, document version or
 * JSON-RPC type. Those belong to texflux.lsp.
 */
module texflux.analysis;

import texflux.diagnostics : DiagnosticReport, diagnoseModule;
import texflux.flags : Flags;
import texflux.modules : ModuleKind;
import texflux.paths : normalizedPath;
import texflux.trace : CompilationTrace, TraceDependency;

/// One source snapshot to analyze.
struct AnalysisRequest
{
    string source;
    string filename;
    ModuleKind kind = ModuleKind.content;
    Flags flags;
    immutable(ubyte)[] sourceBytes;
    const(ubyte)[][string] overlays;
}

/// The compiler-facing result consumed by editor adapters.
struct AnalysisResult
{
    ModuleKind kind;
    string rootPath;
    DiagnosticReport report;
    TraceDependency[] dependencies;
    bool complete;
}

/// Analyze one root using the same module and diagnostic rules as the compiler.
AnalysisResult analyze(AnalysisRequest request)
{
    CompilationTrace trace;
    trace.collectAssets = false;
    auto report = diagnoseModule(request.kind, request.source, request.filename,
            request.flags, request.sourceBytes, request.overlays, &trace);
    return AnalysisResult(request.kind, normalizedPath(request.filename), report,
            trace.dependencies.dup, report.ok);
}
