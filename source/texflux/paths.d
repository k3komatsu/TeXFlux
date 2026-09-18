/**
 * When two spellings name the same file.
 *
 * A module is cached, and a content import cycle is detected, by identity
 * rather than by spelling, so `./part.tfx` and `part.tfx` have to agree. What
 * they must not do is agree with a genuinely different file, which is why this
 * resolves symbolic links rather than merely tidying a path.
 *
 * Case is folded only where the platform folds it, which is Windows. On a
 * case-insensitive macOS volume two spellings that differ in case therefore
 * still compare as two files. That is a known and accepted difference from
 * what the filesystem itself would say.
 */
module texflux.paths;

import std.file : exists, isSymlink, readLink;
import std.path : absolutePath, buildNormalizedPath, buildPath, dirName, pathSplitter;
import std.typecons : Nullable, nullable, Tuple, tuple;

/**
 * One path made absolute, with its relative segments folded away.
 *
 * This is what a diagnostic quotes when the operating system refused to open
 * something, and what the source map stores, so `a/../b.tfx` has to read as
 * `b.tfx` rather than as the longer spelling that names the same file.
 */
string absoluteNormalized(string path)
{
    return buildNormalizedPath(absolutePath(path));
}

/// One comparable spelling of a path.
string normalizedPath(string path)
{
    return foldCase(resolveLinks(absoluteNormalized(path)));
}

/**
 * Whether two paths name the same file.
 *
 * A path that does not exist yet still has to answer, which is what the
 * command line asks when it refuses to write its output over its input.
 */
bool samePath(string first, string second)
{
    // Two names of one existing file -- hard links included -- are the same
    // file however they are spelled.
    const identity = fileIdentity(first);
    if (!identity.isNull && identity == fileIdentity(second))
        return true;
    return normalizedPath(first) == normalizedPath(second);
}

///
unittest
{
    assert(samePath("a/b.tfx", "a/../a/b.tfx"));
    assert(!samePath("a/b.tfx", "a/c.tfx"));
}

/// The device and inode of an existing file, or nothing for a path that has none.
private Nullable!(Tuple!(ulong, ulong)) fileIdentity(string path)
{
    version (Posix)
    {
        import core.sys.posix.sys.stat : stat, stat_t;
        import std.string : toStringz;

        stat_t info;
        if (stat(path.toStringz, &info) == 0)
            return tuple(cast(ulong) info.st_dev, cast(ulong) info.st_ino).nullable;
    }
    return typeof(return).init;
}

/// A hard link is the same file under a name no spelling rule could relate.
version (Posix) unittest
{
    import core.sys.posix.unistd : link;
    import std.file : remove, tempDir, write;
    import std.path : buildPath;
    import std.string : toStringz;

    const original = buildPath(tempDir, "texflux-samepath-original.tfx");
    const alias_ = buildPath(tempDir, "texflux-samepath-alias.tfx");
    write(original, "x\n");
    scope (exit)
    {
        remove(original);
        if (exists(alias_))
            remove(alias_);
    }
    assert(link(original.toStringz, alias_.toStringz) == 0);
    assert(samePath(original, alias_));
    assert(!samePath(original, buildPath(tempDir, "texflux-samepath-missing.tfx")));
}

/**
 * Resolve the symbolic links along a path, leaving what does not exist alone.
 *
 * A path is often asked about before it exists -- an output file, a module
 * that turns out to be missing -- so a component that cannot be resolved is
 * kept as written rather than making the whole answer unavailable.
 */
private string resolveLinks(string path)
{
    enum maximumHops = 40;

    string resolved;
    foreach (component; pathSplitter(path))
    {
        auto next = buildPath(resolved, component);
        size_t hops = 0;
        while (hops < maximumHops && exists(next) && isSymlink(next))
        {
            next = buildNormalizedPath(absolutePath(readLink(next), dirName(next)));
            ++hops;
        }
        resolved = next;
    }
    return resolved;
}

/// Fold a path's case only where the platform itself folds it.
private string foldCase(string path)
{
    version (Windows)
    {
        import std.uni : toLower;

        return path.toLower;
    }
    else
        return path;
}

/**
 * How the operating system's refusal to open a path reads in a diagnostic.
 *
 * The v1 contract quotes the same thing: the error number, what that
 * number means, and the path that was being opened. The path is quoted rather
 * than bare, because one holding a space or a quote has to stay readable.
 */
string openFailure(Exception error, string path)
{
    import core.stdc.errno : ENOENT;
    import core.stdc.string : strerror;
    import std.conv : to;
    import std.file : FileException;
    import std.string : fromStringz;

    import texflux.text : quoted;

    auto fileError = cast(FileException) error;
    const code = fileError is null ? ENOENT : cast(int) fileError.errno;
    return "[Errno " ~ code.to!string ~ "] " ~ strerror(code).fromStringz.idup
        ~ ": " ~ quoted(path);
}
