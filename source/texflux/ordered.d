/**
 * A map that remembers the order things were put into it.
 *
 * Compilation stops at the first error, so which error that is has to be the
 * one a reader would call first. Two of the tables the module system builds
 * are walked rather than only looked up in -- the macros a module makes
 * visible, and the modules a closure loaded -- and walking a plain associative
 * array would report a different collision each time the hash happened to
 * differ. This keeps the order the names were added in, which is definition
 * order for macros and load order for modules.
 */
module texflux.ordered;

/// A string-keyed map whose iteration follows insertion.
struct OrderedMap(V)
{
    private V[string] values;
    private string[] order;

    /**
     * The pairs of an associative array, whose keys are taken in sorted order
     * because the array itself has none.
     */
    this(V[string] pairs)
    {
        import std.algorithm : sort;

        foreach (key; pairs.keys.sort)
            this[key] = pairs[key];
    }

    /// Add a value, or replace one already there without moving it.
    void opIndexAssign(V value, string key)
    {
        if (key !in values)
            order ~= key;
        values[key] = value;
    }

    /// The value under a key, or null when there is none.
    inout(V)* opBinaryRight(string op : "in")(string key) inout
    {
        return key in values;
    }

    /// ditto
    ref inout(V) opIndex(string key) inout
    {
        return values[key];
    }

    /// How many keys the map holds.
    size_t length() const
    {
        return order.length;
    }

    /// Every key, in the order it was added.
    const(string)[] keys() const
    {
        return order;
    }

    /// An independent copy, in the same order.
    OrderedMap dup()
    {
        OrderedMap copy;
        copy.values = values.dup;
        copy.order = order.dup;
        return copy;
    }

    /// Walk the map in that same order.
    int opApply(scope int delegate(string, ref V) body_)
    {
        foreach (key; order)
        {
            const result = body_(key, values[key]);
            if (result)
                return result;
        }
        return 0;
    }
}

///
unittest
{
    OrderedMap!int counts;
    counts["zebra"] = 1;
    counts["apple"] = 2;
    counts["zebra"] = 3;

    assert(counts.keys == ["zebra", "apple"], "replacing a value keeps its place");
    assert(counts["zebra"] == 3);
    assert(counts.length == 2);
    assert(("apple" in counts) !is null && ("moose" in counts) is null);

    string[] walked;
    foreach (key, ref value; counts)
        walked ~= key;
    assert(walked == ["zebra", "apple"]);

    auto copy = counts.dup;
    copy["moose"] = 4;
    assert(counts.length == 2 && copy.keys == ["zebra", "apple", "moose"],
            "a copy grows on its own");

    assert(OrderedMap!int(["b": 2, "a": 1]).keys == ["a", "b"],
            "an associative array's pairs arrive in one order, always");
}
