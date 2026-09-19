/** The small, session-owned ledger used to make a Bundle closure. */
module texflux.trace;

import texflux.ast : Document;
import texflux.flags : Flags;
import texflux.source : SourceSpan;

struct TraceSource
{
    string path;
    string display;
    string sourceKind;
    immutable(ubyte)[] data;
}

struct TraceAsset
{
    string path;
    string display;
    immutable(ubyte)[] data;
}

struct TraceBundle
{
    string path;
    string display;
    immutable(ubyte)[] data;
}

struct TraceDependency
{
    string from;
    string kind;
    string path;
    string target;
    string selector;
    SourceSpan span;
}

/** Shared active-stack state for nested Bundle recompilation. */
final class BundleResolutionState
{
    struct Active
    {
        string digest;
        SourceSpan span;
    }

    Active[] active;

    size_t depth() const
    {
        return active.length;
    }

    bool find(string digest, out SourceSpan first)
    {
        foreach (entry; active)
            if (entry.digest == digest)
            {
                first = entry.span;
                return true;
            }
        return false;
    }

    void enter(string digest, SourceSpan span)
    {
        active ~= Active(digest, span);
    }

    void leave(string digest)
    {
        assert(active.length != 0 && active[$ - 1].digest == digest);
        active = active[0 .. $ - 1];
    }
}

/**
 * First-use ordered inputs of one root compilation.
 *
 * Source and asset records are deduplicated by their canonical physical path;
 * dependency records are intentionally not deduplicated because each authored
 * edge is part of the manifest.
 */
struct CompilationTrace
{
    string root;
    string rootDisplay;
    TraceSource[] sources;
    TraceAsset[] assets;
    TraceBundle[] bundles;
    TraceDependency[] dependencies;
    Flags flags;
    Document document;

    void source(string path, string display, immutable(ubyte)[] data)
    {
        foreach (entry; sources)
            if (entry.path == path)
                return;
        sources ~= TraceSource(path, display,
                display.endsWith(".tfxm") ? "macro" : "content", data.idup);
    }

    void asset(string path, string display, immutable(ubyte)[] data)
    {
        foreach (entry; assets)
            if (entry.path == path)
                return;
        assets ~= TraceAsset(path, display, data.idup);
    }

    void bundle(string path, string display, immutable(ubyte)[] data)
    {
        foreach (entry; bundles)
            if (entry.path == path)
                return;
        bundles ~= TraceBundle(path, display, data.idup);
    }

    void dependency(string from, string kind, string path, string target,
            SourceSpan span, string selector = null)
    {
        dependencies ~= TraceDependency(from, kind, path, target, selector, span);
    }
}

private bool endsWith(string text, string suffix) @safe pure nothrow
{
    return text.length >= suffix.length && text[$ - suffix.length .. $] == suffix;
}
