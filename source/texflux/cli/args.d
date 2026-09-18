/**
 * Reading the v1 command line grammar.
 *
 * Commands accept stable invocations so a build script remains portable. That
 * means more than the
 * option names: a long option may be given as `--flag=value` or as two words,
 * a short one may have its value attached, a long name may be abbreviated as
 * far as it stays unambiguous, and `--` ends the options.
 *
 * What the two need not share is how they word a usage error. Only the status
 * matters there, and it is 2.
 */
module texflux.cli.args;

import std.algorithm : canFind, filter, map, startsWith;
import std.array : array, join;
import std.exception : basicExceptionCtors, enforce;
import std.string : indexOf;

/// A command line that cannot be read, which the caller reports as status 2.
class UsageError : Exception
{
    mixin basicExceptionCtors;
}

/// One option a command accepts.
struct Option
{
    /// The name results are looked up by, which is the long spelling's stem.
    string name;
    /// Every way it may be written, long and short.
    string[] spellings;
    bool takesValue;
    /// Whether writing it more than once collects the values.
    bool repeatable;
    bool required;
    /// The values it accepts, when it accepts only some.
    string[] choices;
}

/// What one command line said.
struct Arguments
{
    string[] positional;
    private string[][string] given;

    /// Whether an option was written at all.
    bool has(string name) const
    {
        return (name in given) !is null;
    }

    /// The last value given for an option, or a default when it was not.
    string value(string name, string fallback = null) const
    {
        auto found = name in given;
        return found is null ? fallback : (*found)[$ - 1];
    }

    /// Every value given for a repeatable option, in order.
    const(string)[] values(string name) const
    {
        auto found = name in given;
        return found is null ? null : *found;
    }
}

/**
 * Read one command's arguments.
 *
 * `parse` is given the options that command accepts and the words after its
 * name; what it returns is the words that were not options, in order.
 */
Arguments parse(const Option[] options, string[] words)
{
    Arguments result;
    bool optionsEnded = false;

    for (size_t index = 0; index < words.length; ++index)
    {
        const word = words[index];
        if (optionsEnded || word == "-" || !word.startsWith("-"))
        {
            result.positional ~= word;
            continue;
        }
        if (word == "--")
        {
            optionsEnded = true;
            continue;
        }

        string spelling = word;
        string attached = null;
        bool hasAttached = false;
        if (word.startsWith("--"))
        {
            const equals = word.indexOf('=');
            if (equals >= 0)
            {
                spelling = word[0 .. equals];
                attached = word[equals + 1 .. $];
                hasAttached = true;
            }
        }
        else if (word.length > 2)
        {
            spelling = word[0 .. 2];
            attached = word[2 .. $];
            hasAttached = true;
        }

        const option = find(options, spelling);
        if (!option.takesValue)
        {
            enforce!UsageError(!hasAttached, "argument " ~ spelling ~ ": ignored explicit argument");
            result.record(option, "");
            // argparse acts on -h the moment it reads it, so nothing after it
            // is read and no required option is missed.
            if (option.name == "help")
                return result;
            continue;
        }

        string given;
        if (hasAttached)
            given = attached;
        else
        {
            enforce!UsageError(index + 1 < words.length,
                    "argument " ~ spelling ~ ": expected one argument");
            given = words[++index];
        }
        enforce!UsageError(option.choices.length == 0 || option.choices.canFind(given),
                "argument " ~ spelling ~ ": invalid choice: '" ~ given ~ "' (choose from "
                ~ option.choices.map!(c => "'" ~ c ~ "'").join(", ") ~ ")");
        result.record(option, given);
    }

    foreach (option; options)
        if (option.required && !result.has(option.name))
            throw new UsageError("the following arguments are required: "
                    ~ option.spellings[$ - 1]);
    return result;
}

private void record(ref Arguments result, const Option option, string value)
{
    if (!option.repeatable && result.has(option.name))
        // argparse lets a later value replace an earlier one, and so does this.
        result.given[option.name] = [value];
    else
        result.given[option.name] ~= value;
}

/**
 * The option one spelling names.
 *
 * A long spelling may be abbreviated as far as it names exactly one option,
 * which is what argparse allows and therefore what a script may already rely
 * on.
 */
private const(Option) find(const Option[] options, string spelling)
{
    foreach (option; options)
        if (option.spellings.canFind(spelling))
            return option;

    if (spelling.startsWith("--"))
    {
        auto matches = options.filter!(option => option.spellings
                .canFind!(candidate => candidate.startsWith("--")
                    && candidate.startsWith(spelling))).array;
        enforce!UsageError(matches.length <= 1, "ambiguous option: " ~ spelling);
        if (matches.length == 1)
            return matches[0];
    }
    throw new UsageError("unrecognized arguments: " ~ spelling);
}

///
unittest
{
    static immutable Option[] options = [
        Option("output", ["-o", "--output"], true, false, true),
        Option("sourceComments", ["--source-comments"], false),
        Option("flags", ["--flag"], true, true),
        Option("help", ["-h", "--help"], false),
    ];

    auto plain = parse(options, ["in.tfx", "-o", "out.tex"]);
    assert(plain.positional == ["in.tfx"]);
    assert(plain.value("output") == "out.tex");
    assert(!plain.has("sourceComments"));

    auto attached = parse(options, ["-oout.tex", "--output=other.tex", "in.tfx"]);
    assert(attached.value("output") == "other.tex", "a later value replaces an earlier one");

    auto repeated = parse(options, ["in.tfx", "-o", "out.tex", "--flag", "a", "--flag=b"]);
    assert(repeated.values("flags") == ["a", "b"]);

    auto abbreviated = parse(options, ["in.tfx", "-o", "out.tex", "--source"]);
    assert(abbreviated.has("sourceComments"), "an unambiguous abbreviation is accepted");

    auto ended = parse(options, ["-o", "out.tex", "--", "-weird.tfx"]);
    assert(ended.positional == ["-weird.tfx"]);

    assert(parse(options, ["in.tfx", "--help", "--bogus"]).has("help"),
            "help ends the reading before a missing option or a bad word is seen");
}

/// A missing required option, an unknown one and a bad choice are all usage errors.
unittest
{
    import std.exception : assertThrown;

    static immutable Option[] options = [
        Option("output", ["-o", "--output"], true, false, true),
        Option("format", ["--format"], true, false, false, ["text", "json"]),
    ];

    assertThrown!UsageError(parse(options, ["in.tfx"]));
    assertThrown!UsageError(parse(options, ["-o", "out.tex", "--nope"]));
    assertThrown!UsageError(parse(options, ["-o", "out.tex", "--format", "xml"]));
    assertThrown!UsageError(parse(options, ["-o"]));
}
