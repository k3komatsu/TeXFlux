/** A small, defensive ZIP boundary for Bundle files. */
module texflux.archive;

import std.algorithm : canFind;
import std.datetime.systime : DosFileTime;
import std.zip : ArchiveMember, CompressionMethod, ZipArchive, ZipException;
import std.string : endsWith, indexOf, startsWith;

import texflux.errors : BundleError;
import texflux.text : decodeUtf8, UnicodeDecodeError;

/// Resource limits applied before any member is expanded or written.
struct BundleLimits
{
    ulong maxArchiveBytes = 512UL * 1024 * 1024;
    ulong maxManifestBytes = 1UL * 1024 * 1024;
    ulong maxEntries = 4096;
    ulong maxEntryBytes = 256UL * 1024 * 1024;
    ulong maxExpandedBytes = 1UL * 1024 * 1024 * 1024;
    ulong maxNestedDepth = 16;
}

struct ArchiveEntry
{
    string name;
    immutable(ubyte)[] data;
}

private BundleError archiveError(string message)
{
    import texflux.source : SourcePosition, SourceSpan;

    return new BundleError("B007", message,
            SourceSpan("<bundle>", SourcePosition(1, 1), SourcePosition(1, 1)));
}

BundleError bundleLimitError(string message)
{
    import texflux.source : SourcePosition, SourceSpan;

    return new BundleError("B008", message,
            SourceSpan("<bundle>", SourcePosition(1, 1), SourcePosition(1, 1)));
}

private ushort u16(const(ubyte)[] bytes, size_t at)
{
    return cast(ushort) (bytes[at] | (cast(uint) bytes[at + 1] << 8));
}

private uint u32(const(ubyte)[] bytes, size_t at)
{
    return cast(uint) bytes[at]
        | (cast(uint) bytes[at + 1] << 8)
        | (cast(uint) bytes[at + 2] << 16)
        | (cast(uint) bytes[at + 3] << 24);
}

private struct ArchiveMemberInfo
{
    string name;
    size_t dataStart;
    uint compressed;
    uint expanded;
    uint checksum;
    ushort method;
}

private bool unsafeName(string name)
{
    if (name.length == 0 || name.canFind('\0') || name.canFind('\\')
            || name[0] == '/' || name.startsWith("//") || name.endsWith("/"))
        return true;
    if (name.length >= 2 && name[1] == ':')
        return true;
    size_t start;
    while (start < name.length)
    {
        const at = name[start .. $].indexOf('/');
        const stop = at < 0 ? name.length : start + at;
        if (stop == start || name[start .. stop] == "." || name[start .. stop] == "..")
            return true;
        start = stop + 1;
    }
    return false;
}

private size_t endOfCentralDirectory(const(ubyte)[] bytes)
{
    if (bytes.length < 22)
        return size_t.max;
    const first = bytes.length > 22 + 0xffff ? bytes.length - 22 - 0xffff : 0;
    for (size_t at = bytes.length - 22; at >= first; --at)
    {
        if (u32(bytes, at) == 0x06054b50)
            return at;
        if (at == 0)
            break;
    }
    return size_t.max;
}

/** Read and fully expand a validated archive. */
ArchiveEntry[] readArchive(const(ubyte)[] bytes, BundleLimits limits = BundleLimits.init)
{
    if (bytes.length > limits.maxArchiveBytes)
        throw bundleLimitError("Bundle archive exceeds the archive byte limit");

    const eocd = endOfCentralDirectory(bytes);
    if (eocd == size_t.max || eocd + 22 > bytes.length)
        throw archiveError("malformed ZIP end of central directory");
    const entries = u16(bytes, eocd + 10);
    const directorySize = u32(bytes, eocd + 12);
    const directoryOffset = u32(bytes, eocd + 16);
    const commentLength = u16(bytes, eocd + 20);
    const disk = u16(bytes, eocd + 4);
    const directoryDisk = u16(bytes, eocd + 6);
    const entriesOnDisk = u16(bytes, eocd + 8);
    if (eocd + 22 + commentLength != bytes.length
            || disk != 0 || directoryDisk != 0 || entriesOnDisk != entries
            || entries == 0xffff || directorySize == 0xffffffff
            || directoryOffset == 0xffffffff)
        throw archiveError("ZIP64, multi-disk, and malformed ZIP archives are not supported");
    if (entries > limits.maxEntries
            || cast(ulong) directoryOffset + directorySize > eocd)
        throw bundleLimitError("Bundle archive exceeds its entry or directory limit");

    string[] names;
    ArchiveMemberInfo[] members;
    size_t at = directoryOffset;
    ulong total;
    foreach (_; 0 .. entries)
    {
        if (at + 46 > eocd || u32(bytes, at) != 0x02014b50)
            throw archiveError("malformed ZIP central directory");
        const madeBy = u16(bytes, at + 4);
        const flags = u16(bytes, at + 8);
        const method = u16(bytes, at + 10);
        const checksum = u32(bytes, at + 16);
        const compressed = u32(bytes, at + 20);
        const expanded = u32(bytes, at + 24);
        const nameLength = u16(bytes, at + 28);
        const extraLength = u16(bytes, at + 30);
        const memberCommentLength = u16(bytes, at + 32);
        const external = u32(bytes, at + 38);
        const localOffset = u32(bytes, at + 42);
        const fieldsEnd = at + 46 + nameLength + extraLength + memberCommentLength;
        if (fieldsEnd > eocd)
            throw archiveError("malformed ZIP central directory fields");
        const name = cast(string) bytes[at + 46 .. at + 46 + nameLength];
        try
            decodeUtf8(cast(immutable(ubyte)[]) bytes[at + 46 .. at + 46 + nameLength]);
        catch (UnicodeDecodeError error)
            throw archiveError("ZIP member name is not valid UTF-8: " ~ error.msg);
        if (unsafeName(name) || names.canFind(name))
            throw archiveError("ZIP contains a duplicate or unsafe member name");
        names ~= name;
        if ((flags & 1) != 0 || (method != 0 && method != 8))
            throw archiveError("ZIP member is encrypted or uses an unsupported compression method");
        if (expanded > limits.maxEntryBytes
                || total + expanded > limits.maxExpandedBytes)
            throw bundleLimitError("Bundle archive exceeds its expanded byte limit");

        // Unix mode bits identify symlinks and devices even when the name is
        // otherwise harmless.  DOS entries are regular files by convention.
        if ((madeBy >> 8) == 3)
        {
            const mode = external >> 16;
            if ((mode & 0xf000) != 0 && (mode & 0xf000) != 0x8000)
                throw archiveError("ZIP member is not a regular file");
        }
        else if ((madeBy >> 8) == 0 && (external & 0x10) != 0)
            throw archiveError("ZIP member is a directory");

        const local = cast(ulong) localOffset;
        if (local + 30 > directoryOffset || local + 30 > bytes.length
                || u32(bytes, localOffset) != 0x04034b50)
            throw archiveError("ZIP member has an invalid local header");
        const localFlags = u16(bytes, localOffset + 6);
        const localMethod = u16(bytes, localOffset + 8);
        const localChecksum = u32(bytes, localOffset + 14);
        const localCompressed = u32(bytes, localOffset + 18);
        const localExpanded = u32(bytes, localOffset + 22);
        const localNameLength = u16(bytes, localOffset + 26);
        const localExtraLength = u16(bytes, localOffset + 28);
        const localFieldsEnd = local + 30 + localNameLength + localExtraLength;
        if (localFieldsEnd > directoryOffset || localFieldsEnd > bytes.length)
            throw archiveError("ZIP member local header fields are outside the archive");
        const localName = cast(string) bytes[localOffset + 30 ..
                localOffset + 30 + localNameLength];
        if (localName != name || localFlags != flags || localMethod != method
                || (localFlags & 1) != 0 || (localMethod != 0 && localMethod != 8))
            throw archiveError("ZIP local and central member headers do not match");
        // A data descriptor may leave local size/CRC fields empty.  If it is
        // not used, std.zip's max(local, central) behaviour must not be able
        // to inflate a declaration beyond the scanner's limits.
        if ((flags & 8) == 0)
        {
            if (localChecksum != checksum || localCompressed != compressed
                    || localExpanded != expanded)
                throw archiveError("ZIP local and central member sizes do not match");
        }
        else if ((localChecksum != 0 && localChecksum != checksum)
                || (localCompressed != 0 && localCompressed != compressed)
                || (localExpanded != 0 && localExpanded != expanded))
            throw archiveError("ZIP data descriptor member sizes do not match");
        const dataStart = localFieldsEnd;
        if (dataStart + compressed > directoryOffset || dataStart + compressed > bytes.length)
            throw archiveError("ZIP member data is outside the archive");

        members ~= ArchiveMemberInfo(name, dataStart, compressed, expanded, checksum, method);
        total += expanded;
        at = fieldsEnd;
    }
    if (at != directoryOffset + directorySize)
        throw archiveError("malformed ZIP central directory size");

    ArchiveEntry[] result;
    foreach (member; members)
    {
        const(ubyte)[] expanded;
        const compressed = bytes[member.dataStart .. member.dataStart + member.compressed];
        if (member.method == 0)
            expanded = compressed;
        else
            expanded = inflateRaw(compressed, member.expanded, limits, member.name);
        import std.zlib : crc32;

        uint checksum;
        () @trusted { checksum = crc32(0, cast(void[]) expanded); }();
        if (expanded.length != member.expanded)
            throw archiveError("ZIP expanded size mismatch for member '" ~ member.name ~ "'");
        if (checksum != member.checksum)
            throw archiveError("ZIP CRC mismatch for member '" ~ member.name ~ "'");
        result ~= ArchiveEntry(member.name, cast(immutable(ubyte)[]) expanded.idup);
    }
    return result;
}

private ubyte[] inflateRaw(const(ubyte)[] compressed, uint expected,
        BundleLimits limits, string name)
{
    import etc.c.zlib : inflate, inflateEnd, inflateInit2, z_stream,
        Z_BUF_ERROR, Z_OK, Z_NO_FLUSH, Z_STREAM_END;

    z_stream stream;
    const status = inflateInit2(&stream, -15);
    if (status != Z_OK)
        throw archiveError("cannot initialize DEFLATE member '" ~ name ~ "'");
    scope (exit)
        inflateEnd(&stream);

    stream.next_in = compressed.ptr;
    stream.avail_in = cast(uint) compressed.length;
    ubyte[] result;
    ubyte[64 * 1024] buffer;
    bool finished;
    while (!finished)
    {
        const beforeInput = stream.avail_in;
        stream.next_out = buffer.ptr;
        stream.avail_out = cast(uint) buffer.length;
        const current = inflate(&stream, Z_NO_FLUSH);
        const produced = buffer.length - stream.avail_out;
        if (cast(ulong) result.length + produced > expected
                || cast(ulong) result.length + produced > limits.maxEntryBytes)
            throw bundleLimitError("ZIP member '" ~ name ~ "' expands beyond its declared limit");
        result ~= buffer[0 .. produced];
        if (current == Z_STREAM_END)
        {
            finished = true;
            if (stream.avail_in != 0)
                throw archiveError("ZIP member '" ~ name ~ "' has trailing compressed data");
        }
        else if (current != Z_OK && current != Z_BUF_ERROR)
            throw archiveError("cannot expand ZIP member '" ~ name ~ "'");
        else if (produced == 0 && beforeInput == stream.avail_in)
            throw archiveError("cannot expand ZIP member '" ~ name ~ "'");
    }
    return result;
}

/** Build a deterministic STORE-only archive. */
immutable(ubyte)[] writeArchive(const(ArchiveEntry)[] entries)
{
    auto archive = new ZipArchive;
    foreach (index, entry; entries)
    {
        auto member = new ArchiveMember;
        member.name = entry.name;
        member.expandedData = cast(ubyte[]) entry.data.dup;
        member.compressionMethod = CompressionMethod.none;
        member.time = DosFileTime.init;
        member.index = cast(uint) index;
        archive.addMember(member);
    }
    try
    {
        auto built = cast(ubyte[]) archive.build();
        canonicalizeArchiveMetadata(built);
        return cast(immutable(ubyte)[]) built;
    }
    catch (ZipException error)
        throw archiveError("cannot build ZIP archive: " ~ error.msg);
}

private void canonicalizeArchiveMetadata(ref ubyte[] bytes)
{
    const eocd = endOfCentralDirectory(bytes);
    if (eocd == size_t.max)
        throw archiveError("cannot canonicalize ZIP archive metadata");
    const entries = u16(bytes, eocd + 10);
    size_t at = u32(bytes, eocd + 16);
    foreach (_; 0 .. entries)
    {
        if (at + 46 > eocd || u32(bytes, at) != 0x02014b50)
            throw archiveError("cannot canonicalize ZIP central directory");
        // Phobos uses the host OS in "version made by" and in the external
        // mode bits.  Normalize both so the writer is byte-stable on POSIX
        // and Windows alike, and advertises a regular read-only file.
        bytes[at + 4] = 0x14;
        bytes[at + 5] = 0x03;
        foreach (offset; 12 .. 14)
            bytes[at + offset] = 0;
        bytes[at + 14] = 0x21;
        bytes[at + 15] = 0;
        bytes[at + 38] = 0x00;
        bytes[at + 39] = 0x00;
        bytes[at + 40] = 0x24;
        bytes[at + 41] = 0x81;
        const local = u32(bytes, at + 42);
        if (cast(ulong) local + 14 > at)
            throw archiveError("cannot canonicalize ZIP local header");
        bytes[local + 10] = 0;
        bytes[local + 11] = 0;
        bytes[local + 12] = 0x21;
        bytes[local + 13] = 0;
        const next = at + 46 + u16(bytes, at + 28) + u16(bytes, at + 30)
            + u16(bytes, at + 32);
        if (next > eocd)
            throw archiveError("cannot canonicalize ZIP central directory fields");
        at = next;
    }
}
