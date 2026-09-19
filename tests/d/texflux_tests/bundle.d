module texflux_tests.bundle;

import std.file : mkdirRecurse, read, readText, rmdirRecurse, tempDir, write;
import std.array : join;
import std.algorithm : canFind;
import std.conv : to;
import std.path : buildPath;
import std.process : thisProcessID;
import std.string : endsWith, replace, startsWith;
import std.zlib : crc32;
import std.zip : ArchiveMember, CompressionMethod, ZipArchive;

import texflux;
import texflux.archive : ArchiveEntry, readArchive;
import texflux.bundle : resolveBundleFrame;
import texflux.canonical : builtinDirectives;
import texflux.errors : BundleError;
import texflux.interchange : digest;
import texflux.render : renderWithProvenance;
import texflux.text : decodeUtf8;
import texflux.trace : BundleResolutionState;

private string fixture(string name)
{
    return buildPath(tempDir(), "texflux-bundle-" ~ thisProcessID.to!string ~ "-" ~ name);
}

private void assertBundleCode(void delegate() action, string expected)
{
    bool caught;
    try
        action();
    catch (BundleError error)
    {
        caught = true;
        assert(error.code == expected, error.diagnostic);
    }
    assert(caught, "expected BundleError " ~ expected);
}

private struct RawZipEntry
{
    string name;
    immutable(ubyte)[] data;
    ushort flags;
    ushort method;
    ushort localFlags;
    ushort localMethod;
    bool localFlagsSet;
    bool localMethodSet;
    bool checksumSet;
    uint checksum;
    ushort madeBy = 0x0314;
    uint external = 0x81a40000;
}

private void put16(ref ubyte[] output, uint value)
{
    output ~= cast(ubyte) value;
    output ~= cast(ubyte) (value >> 8);
}

private void put32(ref ubyte[] output, ulong value)
{
    output ~= cast(ubyte) value;
    output ~= cast(ubyte) (value >> 8);
    output ~= cast(ubyte) (value >> 16);
    output ~= cast(ubyte) (value >> 24);
}

private uint get32(const(ubyte)[] bytes, size_t at)
{
    return cast(uint) bytes[at]
        | (cast(uint) bytes[at + 1] << 8)
        | (cast(uint) bytes[at + 2] << 16)
        | (cast(uint) bytes[at + 3] << 24);
}

private void overwrite32(ref ubyte[] bytes, size_t at, uint value)
{
    bytes[at] = cast(ubyte) value;
    bytes[at + 1] = cast(ubyte) (value >> 8);
    bytes[at + 2] = cast(ubyte) (value >> 16);
    bytes[at + 3] = cast(ubyte) (value >> 24);
}

private uint rawChecksum(RawZipEntry entry)
{
    if (entry.checksumSet)
        return entry.checksum;
    uint result;
    () @trusted { result = crc32(0, cast(void[]) entry.data); }();
    return result;
}

private immutable(ubyte)[] rawZip(RawZipEntry[] entries)
{
    ubyte[] output;
    uint[] offsets;
    foreach (entry; entries)
    {
        const name = cast(const(ubyte)[]) entry.name;
        const flags = entry.flags;
        const method = entry.method;
        const localFlags = entry.localFlagsSet ? entry.localFlags : flags;
        const localMethod = entry.localMethodSet ? entry.localMethod : method;
        const checksum = rawChecksum(entry);
        offsets ~= cast(uint) output.length;
        put32(output, 0x04034b50);
        put16(output, 20);
        put16(output, localFlags);
        put16(output, localMethod);
        put16(output, 0);
        put16(output, 0);
        put32(output, checksum);
        put32(output, entry.data.length);
        put32(output, entry.data.length);
        put16(output, cast(uint) name.length);
        put16(output, 0);
        output ~= name;
        output ~= entry.data;
    }
    const directoryOffset = cast(uint) output.length;
    foreach (index, entry; entries)
    {
        const name = cast(const(ubyte)[]) entry.name;
        const checksum = rawChecksum(entry);
        put32(output, 0x02014b50);
        put16(output, entry.madeBy);
        put16(output, 20);
        put16(output, entry.flags);
        put16(output, entry.method);
        put16(output, 0);
        put16(output, 0);
        put32(output, checksum);
        put32(output, entry.data.length);
        put32(output, entry.data.length);
        put16(output, cast(uint) name.length);
        put16(output, 0);
        put16(output, 0);
        put16(output, 0);
        put16(output, 0);
        put32(output, entry.external);
        put32(output, offsets[index]);
        output ~= name;
    }
    const directorySize = cast(uint) output.length - directoryOffset;
    put32(output, 0x06054b50);
    put16(output, 0);
    put16(output, 0);
    put16(output, cast(uint) entries.length);
    put16(output, cast(uint) entries.length);
    put32(output, directorySize);
    put32(output, directoryOffset);
    put16(output, 0);
    return cast(immutable(ubyte)[]) output;
}

private immutable(ubyte)[] rewriteManifest(const(ubyte)[] archive, string manifest,
        RawZipEntry[] additions = null)
{
    RawZipEntry[] entries;
    foreach (entry; readArchive(archive))
        entries ~= RawZipEntry(entry.name,
                entry.name == "manifest.json"
                    ? cast(immutable(ubyte)[]) manifest.dup : entry.data);
    entries ~= additions;
    return rawZip(entries);
}

private size_t findEocd(ref ubyte[] bytes)
{
    for (size_t at = bytes.length - 22; ; --at)
    {
        if (bytes[at] == 0x50 && bytes[at + 1] == 0x4b
                && bytes[at + 2] == 0x05 && bytes[at + 3] == 0x06)
            return at;
        if (at == 0)
            break;
    }
    assert(false, "test archive has no EOCD");
    return 0;
}

unittest
{
    const root = fixture("asset");
    mkdirRecurse(buildPath(root, "figures"));
    scope (exit) rmdirRecurse(root);

    const asset = buildPath(root, "figures", "system.pdf");
    const input = buildPath(root, "deck.tfx");
    write(asset, "not really a PDF\n");
    write(input, "!defmacro{figure}{name}::\n"
            ~ "    \\includegraphics{!asset{figures/!text{name}.pdf}}\n"
            ~ "!figure{system}\n");

    assert(compileText(readText(input), input) == "\\includegraphics{figures/system.pdf}\n");
    assert(compileText("\\includegraphics{!!asset{figures/system.pdf}}\n", input)
            == "\\includegraphics{!asset{figures/system.pdf}}\n");
    assert(compileText("!| \\includegraphics{!asset{missing.pdf}}\n", input)
            == "\\includegraphics{!asset{missing.pdf}}\n");
    assertBundleCode({ compileText("\\includegraphics{!asset{/absolute.pdf}}\n", input); },
            "B002");
    assertBundleCode({ compileText("\\includegraphics{!asset{figures}}\n", input); }, "B003");
    assertBundleCode({ compileText("\\includegraphics{!asset{fig!macroimport{x}}}\n", input); },
            "B001");
    auto first = buildBundle(input);
    auto second = buildBundle(input);
    assert(first.archive == second.archive, "Bundle output must be deterministic");

    auto manifest = readBundleManifest(first.archive);
    assert(manifest.files.length == 2);
    assert(manifest.files[0].kind == "source");
    assert(manifest.files[1].kind == "asset");
    assert(manifest.dependencies.length == 1);
    assert(manifest.dependencies[0].kind == "asset");
    assert(manifest.dependencies[0].path == "figures/system.pdf");
    assert(manifest.fragments.length == 0);

    auto index = readBundleIndex(first.archive);
    assert(index.fragments.length == 0);
}

unittest
{
    const root = fixture("import");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);

    const child = buildPath(root, "child.tfx");
    const childBundle = buildPath(root, "child.tfxb");
    const parent = buildPath(root, "parent.tfx");
    const badParent = buildPath(root, "bad-parent.tfx");
    const cache = buildPath(root, "cache");
    write(buildPath(root, "asset.txt"), "asset payload\n");
    write(child, "@frame{Child}::\n"
            ~ "    \\includegraphics{!asset{asset.txt}}\n");
    write(parent, "!bundleimport{child.tfxb}{frame:1}\n");
    write(badParent, "!bundleimport{child.tfxb}{frame:2}\n");

    write(childBundle, buildBundle(child).archive);

    auto session = new texflux.CompilationSession(builtinDirectives(), null, null, null,
            BundleLimits.init, cache);
    auto data = cast(immutable(ubyte)[]) read(parent);
    auto document = session.compileRoot(decodeUtf8(data), parent, data);
    auto rendered = renderWithProvenance(document);
    assert(rendered.text.startsWith("\\begin{frame}{Child}\n\\includegraphics{"));
    assert(rendered.text.canFind("payload/asset/000000.bin"));
    assert(rendered.text.endsWith("}\n\\end{frame}\n"));

    bool hasLogical;
    bool hasPhysical;
    string[] loadedNames;
    foreach (source; session.loaded)
    {
        loadedNames ~= source.file;
        hasLogical |= source.file.endsWith("!/child.tfx");
        hasPhysical |= source.file.endsWith("!/child.tfx")
            && source.path.replace("\\", "/").endsWith("/payload/source/000000.tfx")
            && source.data == cast(immutable(ubyte)[]) read(child);
    }
    assert(hasLogical, "Bundle source spans use a logical tfxb identity: "
            ~ loadedNames.join(" | "));
    assert(hasPhysical, "Bundle source maps retain the materialized physical path");

    const style = buildPath(root, "style.tfxm");
    const part = buildPath(root, "part.tfx");
    const main = buildPath(root, "main.tfx");
    const mainBundle = buildPath(root, "main.tfxb");
    const moduleParent = buildPath(root, "module-parent.tfx");
    write(style, "!defmacro{body}::\n    macro body\n");
    write(part, "!macroimport{style.tfxm}\n"
            ~ "@frame{Imported}::\n"
            ~ "    !body\n");
    write(main, "!import{part.tfx}\n");
    write(moduleParent, "!bundleimport{main.tfxb}{frame:1}\n");
    auto builtMain = buildBundle(main);
    write(mainBundle, builtMain.archive);
    auto moduleData = cast(immutable(ubyte)[]) read(moduleParent);
    auto moduleSession = new texflux.CompilationSession(builtinDirectives(), null, null, null,
            BundleLimits.init, cache);
    auto moduleDocument = moduleSession.compileRoot(decodeUtf8(moduleData), moduleParent,
            moduleData);
    assert(renderWithProvenance(moduleDocument).text
            == "\\begin{frame}{Imported}\nmacro body\n\\end{frame}\n");
    auto moduleManifest = readBundleManifest(builtMain.archive);
    assert(moduleManifest.files.length == 3);
    assert(moduleManifest.dependencies.length == 2);
    assert(moduleManifest.fragments[0].source == "source:1");

    const outer = buildPath(root, "outer.tfx");
    const outerBundle = buildPath(root, "outer.tfxb");
    const outerParent = buildPath(root, "outer-parent.tfx");
    write(outer, "!bundleimport{child.tfxb}{frame:1}\n");
    write(outerParent, "!bundleimport{outer.tfxb}{frame:1}\n");
    BundleBuildOptions outerOptions;
    outerOptions.cacheRoot = cache;
    auto builtOuter = buildBundle(outer, outerOptions);
    write(outerBundle, builtOuter.archive);
    auto outerManifest = readBundleManifest(builtOuter.archive);
    assert(outerManifest.fragments.length == 1);
    assert(outerManifest.fragments[0].source == "bundle:0");
    auto outerData = cast(immutable(ubyte)[]) read(outerParent);
    auto outerSession = new texflux.CompilationSession(builtinDirectives(), null, null, null,
            BundleLimits.init, cache);
    auto outerDocument = outerSession.compileRoot(decodeUtf8(outerData), outerParent, outerData);
    assert(renderWithProvenance(outerDocument).text.startsWith("\\begin{frame}{Child}\n"));

    assertBundleCode({
        auto bad = new texflux.CompilationSession(builtinDirectives(), null, null, null,
                BundleLimits.init, cache);
        auto badData = cast(immutable(ubyte)[]) read(badParent);
        bad.compileRoot(decodeUtf8(badData), badParent, badData);
    }, "B012");
}

unittest
{
    const root = fixture("frame-contract");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const child = buildPath(root, "child.tfx");
    const childBundle = buildPath(root, "child.tfxb");
    write(child, "@frame{Child}::\n    child\n@frame{Second}::\n    second\n");
    write(childBundle, buildBundle(child).archive);

    const repeated = buildPath(root, "repeated.tfx");
    write(repeated, "!bundleimport{child.tfxb}{frame:1}\n"
            ~ "!bundleimport{child.tfxb}{frame:2}\n"
            ~ "!bundleimport{child.tfxb}{frame:1}\n");
    auto repeatedData = cast(immutable(ubyte)[]) read(repeated);
    auto repeatedSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "repeated-cache"));
    auto repeatedDocument = repeatedSession.compileRoot(decodeUtf8(repeatedData), repeated,
            repeatedData);
    const repeatedText = renderWithProvenance(repeatedDocument).text;
    assert(repeatedText == "\\begin{frame}{Child}\nchild\n\\end{frame}\n"
            ~ "\\begin{frame}{Second}\nsecond\n\\end{frame}\n"
            ~ "\\begin{frame}{Child}\nchild\n\\end{frame}\n");

    foreach (selector; ["frame:0", "frame:01", "frame:", "frame:1/2"])
    {
        const safeSelector = selector.replace(":", "-").replace("/", "-");
        const bad = buildPath(root, "bad-" ~ safeSelector ~ ".tfx");
        write(bad, "!bundleimport{child.tfxb}{" ~ selector ~ "}\n");
        auto badData = cast(immutable(ubyte)[]) read(bad);
        auto badSession = new texflux.CompilationSession(builtinDirectives(), null, null,
                null, BundleLimits.init, buildPath(root, "bad-cache-" ~ safeSelector));
        assertBundleCode({ badSession.compileRoot(decodeUtf8(badData), bad, badData); }, "B012");
    }
}

unittest
{
    const root = fixture("frame-rules");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "deck.tfx");
    write(input, "!flag{draft}{off}\n"
            ~ "!defmacro{make}::\n"
            ~ "    @frame{Generated}::\n"
            ~ "        generated\n"
            ~ "!make\n"
            ~ "@frame::\n"
            ~ "    @frame{Nested}::\n"
            ~ "        nested\n"
            ~ "!when{draft}::\n"
            ~ "    @frame{Dropped}::\n"
            ~ "        dropped\n");
    auto off = buildBundle(input);
    assert(off.manifest.flags["draft"] == false);
    assert(off.manifest.fragments.length == 2);
    assert(off.manifest.fragments[0].title.get == "Generated");
    assert(off.manifest.fragments[1].title.isNull);
    auto onOptions = BundleBuildOptions.init;
    onOptions.flags["draft"] = true;
    auto on = buildBundle(input, onOptions);
    assert(on.manifest.flags["draft"] == true);
    assert(on.manifest.fragments.length == 3);

    const onBundle = buildPath(root, "on.tfxb");
    write(onBundle, on.archive);
    const parent = buildPath(root, "parent.tfx");
    write(parent, "!bundleimport{on.tfxb}{frame:3}\n");
    auto parentData = cast(immutable(ubyte)[]) read(parent);
    auto parentSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "parent-cache"));
    auto parentDocument = parentSession.compileRoot(decodeUtf8(parentData), parent, parentData);
    assert(renderWithProvenance(parentDocument).text
            == "\\begin{frame}{Dropped}\ndropped\n\\end{frame}\n");

    const dropped = buildPath(root, "dropped.tfx");
    write(dropped, "!flag{draft}{off}\n!when{draft}::\n    !import{missing.tfx}\n");
    auto droppedBundle = buildBundle(dropped);
    assert(droppedBundle.manifest.files.length == 1);
}

unittest
{
    const root = fixture("source-and-asset");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const child = buildPath(root, "child.tfx");
    const input = buildPath(root, "main.tfx");
    write(child, "\\item child\n");
    write(input, "!import{child.tfx}\n\\includegraphics{!asset{child.tfx}}\n");

    auto result = buildBundle(input);
    auto manifest = readBundleManifest(result.archive);
    assert(manifest.files.length == 3);
    assert(manifest.dependencies.length == 2);
    foreach (dependency; manifest.dependencies)
        if (dependency.kind == "asset")
            assert(dependency.target == "asset:0");
        else
            assert(dependency.kind == "import" && dependency.target == "source:1");
}

unittest
{
    const root = fixture("limits");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "deck.tfx");
    write(input, "@frame{One}::\n    body\n");
    auto archive = buildBundle(input).archive;
    auto limits = BundleLimits.init;
    limits.maxArchiveBytes = archive.length - 1;
    assertBundleCode({ readBundleManifest(archive, limits); }, "B008");
    limits = BundleLimits.init;
    limits.maxEntries = 0;
    assertBundleCode({ readBundleManifest(archive, limits); }, "B008");
    limits = BundleLimits.init;
    limits.maxEntryBytes = 0;
    assertBundleCode({ readBundleManifest(archive, limits); }, "B008");
    limits = BundleLimits.init;
    limits.maxExpandedBytes = 0;
    assertBundleCode({ readBundleManifest(archive, limits); }, "B008");
    limits = BundleLimits.init;
    limits.maxManifestBytes = 1;
    assertBundleCode({ readBundleManifest(archive, limits); }, "B008");
}

unittest
{
    // These archives are deliberately assembled from bytes rather than from
    // std.zip so each hostile ZIP shape remains a regression test.
    const payload = cast(immutable(ubyte)[]) "payload".dup;
    const invalidName = cast(string) [cast(ubyte) 0xff];
    assertBundleCode({ readArchive(rawZip([RawZipEntry(invalidName, payload)])); }, "B007");
    foreach (name; ["../evil", "/evil", "C:/evil", "a//b", "a\\b"])
        assertBundleCode({ readArchive(rawZip([RawZipEntry(name, payload)])); }, "B007");
    assertBundleCode({
        readArchive(rawZip([RawZipEntry("same", payload), RawZipEntry("same", payload)]));
    }, "B007");

    auto symlink = RawZipEntry("link", payload);
    symlink.external = 0xa1ff0000;
    assertBundleCode({ readArchive(rawZip([symlink])); }, "B007");
    auto encrypted = RawZipEntry("encrypted", payload);
    encrypted.flags = 1;
    assertBundleCode({ readArchive(rawZip([encrypted])); }, "B007");
    auto localMismatch = RawZipEntry("mismatch", payload);
    localMismatch.localMethod = 8;
    localMismatch.localMethodSet = true;
    assertBundleCode({ readArchive(rawZip([localMismatch])); }, "B007");
    auto crcMismatch = RawZipEntry("crc", payload);
    crcMismatch.checksum = 0;
    crcMismatch.checksumSet = true;
    assertBundleCode({ readArchive(rawZip([crcMismatch])); }, "B007");

    auto zip64 = cast(ubyte[]) rawZip([RawZipEntry("zip64", payload)]).dup;
    auto zip64Eocd = findEocd(zip64);
    zip64[zip64Eocd + 8] = 0xff;
    zip64[zip64Eocd + 9] = 0xff;
    assertBundleCode({ readArchive(cast(immutable(ubyte)[]) zip64); }, "B007");
    auto multidisk = cast(ubyte[]) rawZip([RawZipEntry("disk", payload)]).dup;
    auto multidiskEocd = findEocd(multidisk);
    multidisk[multidiskEocd + 4] = 1;
    assertBundleCode({ readArchive(cast(immutable(ubyte)[]) multidisk); }, "B007");

    auto deflated = new ZipArchive;
    auto deflatedMember = new ArchiveMember;
    deflatedMember.name = "payload/deflated";
    deflatedMember.expandedData = cast(ubyte[]) "deflate payload".dup;
    deflatedMember.compressionMethod = CompressionMethod.deflate;
    deflatedMember.index = 0;
    deflated.addMember(deflatedMember);
    auto deflatedBytes = cast(immutable(ubyte)[]) cast(ubyte[]) deflated.build();
    auto expanded = readArchive(deflatedBytes);
    assert(expanded.length == 1 && expanded[0].data == "deflate payload");
    auto bomb = cast(ubyte[]) deflatedBytes.dup;
    const bombEocd = findEocd(bomb);
    const bombCentral = get32(bomb, bombEocd + 16);
    const bombLocal = get32(bomb, bombCentral + 42);
    overwrite32(bomb, bombCentral + 24, 1);
    overwrite32(bomb, bombLocal + 22, 1);
    assertBundleCode({ readArchive(cast(immutable(ubyte)[]) bomb); }, "B008");
}

unittest
{
    const root = fixture("manifest-rejections");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "deck.tfx");
    write(input, "@frame{One}::\n    body\n");
    auto built = buildBundle(input);
    auto archive = built.archive;
    const span = SourceSpan("parent.tfx", SourcePosition(1, 1), SourcePosition(1, 2));
    auto entries = readArchive(archive);
    string manifestText;
    foreach (entry; entries)
        if (entry.name == "manifest.json")
            manifestText = cast(string) entry.data;

    assertBundleCode({ readBundleManifest(rewriteManifest(archive, "{")); }, "B009");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive, cast(string) [cast(ubyte) 0xff]));
    }, "B009");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive,
                manifestText.replace("\"version\":1", "\"version\":2")));
    }, "B010");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive,
                manifestText.replace(built.manifest.files[0].archivePath,
                    "payload/source/missing.tfx")));
    }, "B011");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive, manifestText,
                [RawZipEntry("payload/extra.bin", cast(immutable(ubyte)[]) "x".dup)]));
    }, "B011");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive,
                manifestText.replace(built.manifest.files[0].sha256,
                    "0000000000000000000000000000000000000000000000000000000000000000")));
    }, "B011");
    RawZipEntry[] withoutManifest;
    foreach (entry; entries)
        if (entry.name != "manifest.json")
            withoutManifest ~= RawZipEntry(entry.name, entry.data);
    assertBundleCode({ readBundleManifest(rawZip(withoutManifest)); }, "B011");

    auto unreachable = manifestText;
    const extraFile = ",{\"id\":\"asset:99\",\"kind\":\"asset\","
        ~ "\"archivePath\":\"payload/asset/999999.bin\","
        ~ "\"logicalPath\":\"_external/99/unused.bin\",\"size\":1,"
        ~ "\"sha256\":\"2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881\"}";
    unreachable = unreachable.replace("],\"dependencies\"",
            extraFile ~ "],\"dependencies\"");
    assertBundleCode({
        readBundleManifest(rewriteManifest(archive, unreachable,
                [RawZipEntry("payload/asset/999999.bin", cast(immutable(ubyte)[]) "x".dup)]));
    }, "B014");

    assertBundleCode({
        readBundleManifest(rewriteManifest(archive,
                manifestText.replace("\"source\":\"source:0\"",
                    "\"source\":\"source:99\"")));
    }, "B013");

    auto invalidRootMutable = new ubyte[built.manifest.files[0].size];
    invalidRootMutable[] = 0xff;
    const invalidRoot = cast(immutable(ubyte)[]) invalidRootMutable.idup;
    auto invalidRootManifest = manifestText.replace(built.manifest.files[0].sha256,
            digest(invalidRoot));
    RawZipEntry[] invalidRootEntries;
    foreach (entry; entries)
        invalidRootEntries ~= RawZipEntry(entry.name,
                entry.name == built.manifest.files[0].archivePath ? invalidRoot :
                    entry.name == "manifest.json"
                        ? cast(immutable(ubyte)[]) invalidRootManifest.dup : entry.data);
    const invalidRootPath = buildPath(root, "invalid-root.tfxb");
    write(invalidRootPath, rawZip(invalidRootEntries));
    auto invalidRootSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "invalid-root-cache"));
    assertBundleCode({ resolveBundleFrame(invalidRootPath, "frame:1", span, invalidRootSession); },
            "B019");

    const badFlagsPath = buildPath(root, "bad-flags.tfxb");
    write(badFlagsPath, rewriteManifest(archive,
            manifestText.replace("\"flags\":{}", "\"flags\":{\"ghost\":true}")));
    auto badFlagsSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "bad-flags-cache"));
    assertBundleCode({ resolveBundleFrame(badFlagsPath, "frame:1", span, badFlagsSession); },
            "B020");

    const ambiguousRoot = buildPath(root, "ambiguous");
    mkdirRecurse(ambiguousRoot);
    write(buildPath(ambiguousRoot, "a.txt"), "a");
    write(buildPath(ambiguousRoot, "b.txt"), "b");
    const ambiguousInput = buildPath(ambiguousRoot, "deck.tfx");
    write(ambiguousInput, "\\includegraphics{!asset{a.txt}}\n"
            ~ "\\includegraphics{!asset{b.txt}}\n");
    auto ambiguous = buildBundle(ambiguousInput);
    string ambiguousManifest;
    foreach (entry; readArchive(ambiguous.archive))
        if (entry.name == "manifest.json")
            ambiguousManifest = cast(string) entry.data;
    const ambiguousNeedle = "\"kind\":\"asset\",\"path\":\"b.txt\",\"target\":\"asset:1\"";
    assert(ambiguousManifest.canFind(ambiguousNeedle));
    ambiguousManifest = ambiguousManifest.replace(ambiguousNeedle,
            "\"kind\":\"asset\",\"path\":\"a.txt\",\"target\":\"asset:1\"");
    assertBundleCode({ readBundleManifest(rewriteManifest(ambiguous.archive,
            ambiguousManifest)); }, "B014");
}

unittest
{
    const root = fixture("diagnostic-sites");
    mkdirRecurse(root);
    scope (exit) rmdirRecurse(root);
    const input = buildPath(root, "deck.tfx");
    const bundlePath = buildPath(root, "deck.tfxb");
    write(input, "@frame{One}::\n    body\n");
    auto built = buildBundle(input);
    write(bundlePath, built.archive);
    const span = SourceSpan("parent.tfx", SourcePosition(1, 1), SourcePosition(1, 2));

    assertBundleCode({ readBundleManifest(buildPath(root, "missing.tfxb")); }, "B006");
    assertBundleCode({ buildBundle(buildPath(root, "not-a-source.txt")); }, "B017");
    assertBundleCode({ compileText("!bundleimport{x.tfxb}\n", input); }, "B004");
    assertBundleCode({ compileText("!bundleimport{x.txt}{frame:1}\n", input); }, "B005");

    string manifestText;
    foreach (entry; readArchive(built.archive))
        if (entry.name == "manifest.json")
            manifestText = cast(string) entry.data;
    const mismatchPath = buildPath(root, "mismatch.tfxb");
    write(mismatchPath, rewriteManifest(built.archive,
            manifestText.replace("\"title\":\"One\"", "\"title\":\"Other\"")));
    auto mismatchSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "mismatch-cache"));
    assertBundleCode({ resolveBundleFrame(mismatchPath, "frame:1", span, mismatchSession); },
            "B021");

    auto cycleState = new BundleResolutionState;
    auto firstImport = SourceSpan("first.tfx", SourcePosition(4, 3),
            SourcePosition(4, 20));
    cycleState.enter(digest(built.archive), firstImport);
    auto cycleSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, buildPath(root, "cycle-cache"), cycleState);
    bool cycleCaught;
    try
        resolveBundleFrame(bundlePath, "frame:1", span, cycleSession);
    catch (BundleError error)
    {
        cycleCaught = true;
        assert(error.code == "B015");
        assert(error.related.length == 1 && error.related[0].span == firstImport);
    }
    assert(cycleCaught, "expected BundleError B015");

    auto shallowLimits = BundleLimits.init;
    shallowLimits.maxNestedDepth = 0;
    auto shallowSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, shallowLimits, buildPath(root, "shallow-cache"));
    assertBundleCode({ resolveBundleFrame(bundlePath, "frame:1", span, shallowSession); },
            "B008");

    const occupiedCache = buildPath(root, "occupied-cache");
    mkdirRecurse(occupiedCache);
    const occupied = buildPath(occupiedCache, digest(built.archive));
    write(occupied, "not a cache directory");
    auto outputSession = new texflux.CompilationSession(builtinDirectives(), null, null,
            null, BundleLimits.init, occupiedCache);
    assertBundleCode({ resolveBundleFrame(bundlePath, "frame:1", span, outputSession); },
            "B018");
    assertBundleCode({
        writeBundleAtomic(buildPath(root, "missing-output-directory", "out.tfxb"), built.archive);
    }, "B016");
}
