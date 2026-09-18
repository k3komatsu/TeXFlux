/** Explicitly regenerate the checked-in D-only regression corpus. */
module update_regression;

import std.stdio : stderr;

import texflux_tests.regression : updateRegression;

int main(string[] args)
{
    if (args.length != 2 || args[1] != "--accept-current")
    {
        stderr.write("usage: update-regression --accept-current\n");
        return 2;
    }
    try
    {
        updateRegression();
        return 0;
    }
    catch (Exception error)
    {
        stderr.write("update-regression: " ~ error.msg ~ "\n");
        return 1;
    }
}
