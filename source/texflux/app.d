/**
 * The `texflux` command.
 *
 * Everything the command does lives in texflux.cli, which takes its streams as
 * arguments so the tests can drive it in memory. This file exists to connect
 * that to the real process and to nothing else.
 */
module texflux.app;

import std.stdio : stdin, stdout, stderr;

import texflux.cli : CommandStreams, run;

int main(string[] args)
{
    CommandStreams streams;
    streams.write = (const(ubyte)[] bytes) { stdout.rawWrite(bytes); };
    streams.writeError = (const(ubyte)[] bytes) { stderr.rawWrite(bytes); };
    streams.readInput = () {
        immutable(ubyte)[] data;
        foreach (ubyte[] chunk; stdin.byChunk(64 * 1024))
            data ~= chunk.idup;
        return data;
    };
    scope (exit)
    {
        stdout.flush();
        stderr.flush();
    }
    return run(args[1 .. $], streams);
}
