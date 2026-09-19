/**
 * The deliberately small resource boundary used by `!asset`.
 *
 * Asset markers are found while an opaque text field is assembled.  This
 * module owns only the path contract and the callback data; the compilation
 * session decides whether the resolved path is a host file or a Bundle cache
 * file.
 */
module texflux.assets;

import std.algorithm : canFind;
import std.file : isFile;
import std.path : buildNormalizedPath, dirName, isAbsolute;
import std.string : indexOf, startsWith;

import texflux.errors : BundleError;
import texflux.source : SourceSpan;

/// The information available when one marker is expanded.
struct AssetReference
{
    string path;
    SourceSpan span;
    SourceSpan useSpan;
    string ownerFile;
    bool template_;
}

/// Return the TeX path to write for one validated asset.
alias AssetResolver = string delegate(AssetReference reference);

private BundleError invalidAssetPath(SourceSpan span, string message)
{
    return new BundleError("B002", message, span);
}

private BundleError unreadableAsset(SourceSpan span, string message)
{
    return new BundleError("B003", message, span);
}

/** Validate the post-interpolation spelling of an asset path. */
void validateAssetPath(string path, SourceSpan span)
{
    if (path.length == 0)
        throw invalidAssetPath(span, "asset path must not be empty");
    if (path.canFind('\0'))
        throw invalidAssetPath(span, "asset path must not contain a NUL character");
    if (path.canFind('\\'))
        throw invalidAssetPath(span, "asset paths use '/' separators");
    if (path.isAbsolute || path.startsWith("//")
            || (path.length >= 2 && path[1] == ':'))
        throw invalidAssetPath(span, "asset path must be relative to the source module");

    size_t start;
    while (start <= path.length)
    {
        const end = path[start .. $].indexOf('/');
        const stop = end < 0 ? path.length : start + end;
        if (stop == start)
            throw invalidAssetPath(span, "asset path contains an empty component");
        start = stop + 1;
        if (end < 0)
            break;
    }
}

/** Resolve and validate one ordinary filesystem asset. */
string resolveFilesystemAsset(AssetReference reference)
{
    import std.file : getAttributes, read;
    import texflux.paths : absoluteNormalized;
    import texflux.text : quoted;

    validateAssetPath(reference.path, reference.span);
    const path = buildNormalizedPath(dirName(absoluteNormalized(reference.ownerFile)),
            reference.path);
    try
    {
        if (!path.isFile)
            throw unreadableAsset(reference.span,
                    "asset is not a regular file: " ~ quoted(path));
        // Reading here makes an unreadable file fail at the marker, not later
        // while a Bundle is being written.
        read(path);
    }
    catch (BundleError error)
    {
        throw error;
    }
    catch (Exception error)
    {
        throw unreadableAsset(reference.span,
                "cannot read asset '" ~ reference.path ~ "': " ~ error.msg);
    }
    return path;
}
