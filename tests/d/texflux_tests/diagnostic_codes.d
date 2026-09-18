/**
 * Keep diagnostic construction sites, the published table, and prose links
 * in sync without relying on a second implementation.
 */
module texflux_tests.diagnostic_codes;

import std.algorithm : canFind, sort;
import std.conv : to;
import std.file : dirEntries, exists, getcwd, isDir, readText, SpanMode;
import std.format : format;
import std.path : baseName, buildPath, relativePath;
import std.string : endsWith, indexOf, replace, split, startsWith, strip;

private enum root = ".";
private enum design = "doc/diagnostics.md";
private enum codePattern = "PVDEM";
private immutable string[] builders = [
    "ParseError", "ValidationError", "DirectiveError", "MacroExpansionError", "ModuleError",
    "fail", "expansionError",
];
private immutable int[string] highWater = ["P": 37, "V": 43, "D": 1, "E": 19, "M": 29];
private immutable int[string] expectedCounts = ["P": 37, "V": 43, "D": 1, "E": 19, "M": 29];

struct Site
{
    string file;
    size_t line;
    string name;
    string code;
    string message;
}

private bool isBoundary(char c)
{
    return !((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
            || (c >= '0' && c <= '9') || c == '_');
}

private bool isWordAt(string text, size_t at, string word)
{
    if (at + word.length > text.length || text[at .. at + word.length] != word)
        return false;
    return (at == 0 || isBoundary(text[at - 1]))
        && (at + word.length == text.length || isBoundary(text[at + word.length]));
}

private size_t skipString(string text, size_t at)
{
    const quote = text[at];
    ++at;
    while (at < text.length)
    {
        if (quote == '"' && text[at] == '\\')
            at += 2;
        else if (text[at++] == quote)
            return at;
    }
    return at;
}

private string withoutUnittests(string input)
{
    auto text = input.dup;
    size_t search;
    while (search < text.length)
    {
        auto at = text.indexOf("unittest", search);
        if (at < 0)
            break;
        if ((at != 0 && !isBoundary(text[at - 1]))
                || (at + 8 < text.length && !isBoundary(text[at + 8])))
        {
            search = at + 8;
            continue;
        }
        auto open = at + 8;
        while (open < text.length && (text[open] == ' ' || text[open] == '\t'
                || text[open] == '\r' || text[open] == '\n'))
            ++open;
        if (open >= text.length || text[open] != '{')
        {
            search = at + 8;
            continue;
        }
        size_t depth;
        size_t end = open;
        while (end < text.length)
        {
            if (text[end] == '"' || text[end] == '`')
            {
                end = skipString(cast(string) text, end);
                continue;
            }
            if (text[end] == '{')
                ++depth;
            else if (text[end] == '}' && --depth == 0)
            {
                ++end;
                break;
            }
            ++end;
        }
        foreach (ref char c; text[at .. end])
            if (c != '\n')
                c = ' ';
        search = end;
    }
    return text.idup;
}

private string[] arguments(string text, size_t opening)
{
    string[] result;
    size_t start = opening + 1;
    size_t at = start;
    size_t depth = 1;
    while (at < text.length)
    {
        if (text[at] == '"' || text[at] == '`')
        {
            at = skipString(text, at);
            continue;
        }
        if (text[at] == '(' || text[at] == '[' || text[at] == '{')
            ++depth;
        else if (text[at] == ')' || text[at] == ']' || text[at] == '}')
        {
            --depth;
            if (depth == 0)
            {
                result ~= text[start .. at];
                return result;
            }
        }
        else if (text[at] == ',' && depth == 1)
        {
            result ~= text[start .. at];
            start = at + 1;
        }
        ++at;
    }
    return result;
}

private string literalText(string expression)
{
    string result;
    size_t at;
    while (at < expression.length)
    {
        if (expression[at] != '"' && expression[at] != '`')
        {
            ++at;
            continue;
        }
        const quote = expression[at];
        const start = at;
        at = skipString(expression, start);
        auto part = expression[start + 1 .. at - 1];
        if (quote == '"')
            part = part.replace(`\"`, `"`).replace(`\\`, `\`);
        result ~= part;
    }
    return result;
}

private void collectD(string path, ref string[] files)
{
    foreach (entry; dirEntries(path, SpanMode.shallow))
    {
        if (entry.isDir)
            collectD(entry.name, files);
        else if (entry.name.endsWith(".d"))
            files ~= entry.name;
    }
}

private Site[] sites()
{
    string[] files;
    collectD(buildPath(root, "source", "texflux"), files);
    collectD(buildPath(root, "tests", "d"), files);
    files.sort();
    Site[] result;
    foreach (path; files)
    {
        auto text = withoutUnittests(readText(path));
        foreach (name; builders)
        {
            size_t search;
            while (search < text.length)
            {
                auto at = text.indexOf(name, search);
                if (at < 0)
                    break;
                if (!isWordAt(text, at, name))
                {
                    search = at + name.length;
                    continue;
                }
                auto before = at;
                while (before > 0 && (text[before - 1] == ' ' || text[before - 1] == '\t'
                        || text[before - 1] == '\r' || text[before - 1] == '\n'))
                    --before;
                const isClass = name != "fail" && name != "expansionError";
                const prefix = isClass ? "new" : "throw";
                if (before < prefix.length || text[before - prefix.length .. before] != prefix
                        || (before > prefix.length && !isBoundary(text[before - prefix.length - 1])))
                {
                    search = at + name.length;
                    continue;
                }
                auto opening = at + name.length;
                while (opening < text.length && (text[opening] == ' ' || text[opening] == '\t'))
                    ++opening;
                if (opening >= text.length || text[opening] != '(')
                {
                    search = at + name.length;
                    continue;
                }
                auto args = arguments(text, opening);
                Site site;
                site.file = relativePath(path, root).replace("\\", "/");
                site.line = 1;
                foreach (char c; text[0 .. before])
                    if (c == '\n')
                        ++site.line;
                site.name = name;
                if (args.length != 0)
                {
                    auto first = args[0].strip;
                    if (first.length >= 2 && first[0] == '"' && first[$ - 1] == '"')
                        site.code = first[1 .. $ - 1];
                    else if (first == "code" || first == "error.code")
                        site.code = first;
                }
                if (args.length > 1)
                    site.message = literalText(args[1]);
                result ~= site;
                search = opening + 1;
            }
        }
    }
    return result;
}

private string skeleton(string text)
{
    auto value = text.replace("`", " ").replace("|", " ").replace("…", " ");
    string result;
    bool pendingSpace;
    bool hole;
    foreach (char c; value)
    {
        if (hole)
        {
            if (c == '}' || c == '>')
                hole = false;
            continue;
        }
        if (c == '{' || c == '<')
        {
            hole = true;
            pendingSpace = true;
            continue;
        }
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                || (c >= '0' && c <= '9'))
        {
            if (pendingSpace && result.length != 0)
                result ~= ' ';
            result ~= c >= 'A' && c <= 'Z' ? cast(char) (c + ('a' - 'A')) : c;
            pendingSpace = false;
        }
        else
            pendingSpace = true;
    }
    return result;
}

private string[string] publishedRows()
{
    string[string] rows;
    foreach (line; readText(design).split("\n"))
    {
        auto parts = line.split("|");
        if (parts.length < 4 || parts[0].strip != "")
            continue;
        auto code = parts[1].strip;
        if (code.length == 4 && codePattern.canFind(code[0])
                && code[1] >= '0' && code[1] <= '9'
                && code[2] >= '0' && code[2] <= '9'
                && code[3] >= '0' && code[3] <= '9')
        {
            string row = parts[2];
            foreach (part; parts[3 .. $])
                row ~= "|" ~ part;
            rows[code] = row.strip;
        }
    }
    return rows;
}

private string[] citedCodes()
{
    string[] documents;
    foreach (entry; dirEntries(buildPath(root, "doc"), SpanMode.depth))
        if (!entry.isDir && entry.name.endsWith(".md"))
            documents ~= entry.name;
    foreach (entry; dirEntries(root, SpanMode.shallow))
        if (!entry.isDir && entry.name.endsWith(".md") && baseName(entry.name) != "AGENTS.md")
            documents ~= entry.name;
    documents.sort();
    string[] result;
    foreach (path; documents)
    {
        auto text = readText(path);
        foreach (line; text.split("\n"))
        {
            size_t at;
            while (at + 4 <= line.length)
            {
                if (codePattern.canFind(line[at]) && line[at + 1] >= '0'
                        && line[at + 1] <= '9' && line[at + 2] >= '0'
                        && line[at + 2] <= '9' && line[at + 3] >= '0'
                        && line[at + 3] <= '9'
                        && (at == 0 || isBoundary(line[at - 1]))
                        && (at + 4 == line.length || isBoundary(line[at + 4])))
                    result ~= path ~ ":" ~ line[at .. at + 4];
                ++at;
            }
        }
    }
    return result;
}

private Site[] owned()
{
    Site[] result;
    foreach (site; sites())
        if (site.code.length == 4 && codePattern.canFind(site.code[0])
                && site.code[1] >= '0' && site.code[1] <= '9'
                && site.code[2] >= '0' && site.code[2] <= '9'
                && site.code[3] >= '0' && site.code[3] <= '9')
            result ~= site;
    return result;
}

private void assertCodes()
{
    auto allSites = sites();
    foreach (site; allSites)
    {
        assert(site.code.length != 0,
                format("%s:%d: %s() has no literal or forwarded code", site.file, site.line,
                    site.name));
        if (site.code == "code" || site.code == "error.code")
            continue;
        assert(site.code.length == 4 && codePattern.canFind(site.code[0])
                && site.code[1] >= '0' && site.code[1] <= '9'
                && site.code[2] >= '0' && site.code[2] <= '9'
                && site.code[3] >= '0' && site.code[3] <= '9',
                format("invalid diagnostic code %s at %s:%d", site.code, site.file, site.line));
    }
    auto all = owned();
    string[string] first;
    int[string] counts;
    foreach (site; all)
    {
        assert(site.code.length == 4 && codePattern.canFind(site.code[0])
                && site.code[1] >= '0' && site.code[1] <= '9'
                && site.code[2] >= '0' && site.code[2] <= '9'
                && site.code[3] >= '0' && site.code[3] <= '9',
                format("invalid diagnostic code %s at %s:%d", site.code, site.file, site.line));
        assert(site.code !in first,
                format("diagnostic code %s is used at %s:%d and %s", site.code,
                    first.get(site.code, "?"), site.file, site.line));
        first[site.code] = format("%s:%d", site.file, site.line);
        ++counts[site.code[0 .. 1]];
    }
    assert(counts.length == expectedCounts.length);
    foreach (letter, expected; expectedCounts)
    {
        assert(letter.length == 1 && letter in highWater && letter in counts);
        int maxNumber;
        foreach (site; all)
            if (site.code[0 .. 1] == letter)
                maxNumber = maxNumber > cast(int) to!int(site.code[1 .. $])
                    ? maxNumber : cast(int) to!int(site.code[1 .. $]);
        assert(counts[letter] == expected,
                format("expected %d %s codes, found %d", expected, letter, counts[letter]));
        assert(maxNumber == highWater[letter],
                format("expected %s high-water %s%03d, found %s%03d", letter,
                    letter, highWater[letter], letter, maxNumber));
    }
    auto rows = publishedRows();
    assert(rows.length == first.length,
            format("source has %d codes but diagnostics table has %d", first.length, rows.length));
    foreach (code; first.keys)
        assert(code in rows, "diagnostic code missing from published table: " ~ code);
    foreach (code; rows.keys)
        assert(code in first, "published code has no construction site: " ~ code);
    foreach (code, row; rows)
    {
        int occurrences;
        foreach (line; readText(design).split("\n"))
            if (line.startsWith("| " ~ code ~ " |"))
                ++occurrences;
        assert(occurrences == 1, "published code has duplicate rows: " ~ code);
        foreach (site; all)
            if (site.code == code && site.message.length != 0)
            {
                auto words = skeleton(site.message).split(" ");
                string opening;
                foreach (word; words[0 .. words.length < 5 ? words.length : 5])
                    if (word.length != 0)
                        opening ~= (opening.length ? " " : "") ~ word;
                assert(row.startsWith(baseName(site.file) ~ " `"),
                        format("%s file is absent from table", code));
                if (opening.length != 0)
                    assert(skeleton(row).indexOf(opening) >= 0,
                            format("%s message skeleton is absent from table", code));
                break;
            }
    }
    foreach (citation; citedCodes())
    {
        const code = citation[$ - 4 .. $];
        assert(code in rows, "document cites unpublished code " ~ citation);
    }
}

unittest
{
    assertCodes();
}
