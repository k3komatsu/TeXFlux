/**
 * The `texflux` command.
 *
 * Everything the command does lives in texflux.cli, which takes its streams as
 * arguments so the tests can drive it in memory. This file exists to connect
 * that to the real process and to nothing else.
 */
module texflux.app;

import std.array : appender;
import std.stdio : stdin, stdout, stderr;

import texflux.cli : CommandStreams, run;
import texflux.lsp.server : runLsp = run;
import texflux.lsp.transport : LspStreams;

int main(string[] args)
{
    CommandStreams streams;
    streams.write = (const(ubyte)[] bytes) { stdout.rawWrite(bytes); };
    streams.writeError = (const(ubyte)[] bytes) { stderr.rawWrite(bytes); };
    streams.readInput = () {
        auto data = appender!(immutable(ubyte)[])();
        foreach (ubyte[] chunk; stdin.byChunk(64 * 1024))
            data.put(chunk);
        return data.data;
    };
    scope (exit)
    {
        stdout.flush();
        stderr.flush();
    }

    if (args.length > 1 && args[1] == "lsp")
    {
        if (args.length > 2)
        {
            if (args.length == 3 && (args[2] == "-h" || args[2] == "--help"))
            {
                stdout.write("usage: texflux lsp\n");
                return 0;
            }
            stderr.write("texflux: error: lsp does not accept these arguments\n");
            return 2;
        }
        version (Windows)
        {
            import core.stdc.stdio : _O_BINARY, _setmode;

            _setmode(stdout.fileno, _O_BINARY);
        }
        LspStreams lspStreams;
        lspStreams.read = (ubyte[] buffer) { return readStdin(buffer); };
        lspStreams.write = (const(ubyte)[] bytes) {
            stdout.rawWrite(bytes);
            stdout.flush();
        };
        lspStreams.log = (string text) { stderr.rawWrite(cast(const(ubyte)[]) text); };
        return runLsp(lspStreams);
    }
    return run(args[1 .. $], streams);
}

/// Read one available pipe chunk without asking libc to fill the whole buffer.
private size_t readStdin(ubyte[] buffer)
{
    if (buffer.length == 0)
        return 0;
    version (Posix)
    {
        import core.stdc.errno : EINTR, errno;
        import core.sys.posix.unistd : read;

        auto count = read(stdin.fileno, buffer.ptr, buffer.length);
        while (count < 0 && errno == EINTR)
            count = read(stdin.fileno, buffer.ptr, buffer.length);
        return count > 0 ? cast(size_t) count : 0;
    }
    else version (Windows)
    {
        import core.sys.windows.winbase : ReadFile;

        uint count;
        if (!ReadFile(stdin.windowsHandle, buffer.ptr, cast(uint) buffer.length, &count, null))
            return 0;
        return count;
    }
    else
        return stdin.rawRead(buffer).length;
}
