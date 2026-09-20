module texflux.lsp.transport;

import std.conv : to;
import std.json : JSONValue, parseJSON;
import std.string : indexOf, split, strip, toLower;

import texflux.json : JsonValue;
import texflux.lsp.protocol : packet;

struct LspStreams
{
    size_t delegate(ubyte[]) read;
    void delegate(const(ubyte)[]) write;
    void delegate(string) log;
}

enum PacketStatus
{
    message,
    eof,
    framingError,
    invalidJson,
}

struct Packet
{
    PacketStatus status;
    JSONValue value;
    string error;
}

struct LspTransport
{
    LspStreams streams;
    private ubyte[] pending;

    Packet readPacket()
    {
        while (true)
        {
            auto end = delimiter(pending);
            if (end != size_t.max)
            {
                auto header = cast(string) pending[0 .. end];
                size_t length;
                bool found;
                foreach (line; header.split("\r\n"))
                {
                    if (line.length == 0)
                        continue;
                    auto colon = line.indexOf(':');
                    if (colon < 0)
                        return Packet(PacketStatus.framingError, JSONValue.init,
                                "malformed LSP header");
                    auto name = line[0 .. colon].strip.toLower;
                    auto value = line[colon + 1 .. $].strip;
                    if (name == "content-length")
                    {
                        if (found)
                            return Packet(PacketStatus.framingError, JSONValue.init,
                                    "duplicate Content-Length");
                        try
                            length = value.to!size_t;
                        catch (Exception)
                            return Packet(PacketStatus.framingError, JSONValue.init,
                                    "invalid Content-Length");
                        found = true;
                    }
                }
                if (!found)
                    return Packet(PacketStatus.framingError, JSONValue.init,
                            "missing Content-Length");
                enum headerBytes = 4;
                enum maxBody = 16 * 1024 * 1024;
                if (length > maxBody)
                    return Packet(PacketStatus.framingError, JSONValue.init,
                            "LSP message is too large");
                const total = end + headerBytes + length;
                while (pending.length < total)
                    if (!readMore())
                        return Packet(PacketStatus.framingError, JSONValue.init,
                                "unexpected EOF in LSP message");
                auto body = cast(string) pending[end + headerBytes .. total];
                pending = pending[total .. $].dup;
                try
                    return Packet(PacketStatus.message, parseJSON(body), null);
                catch (Exception error)
                    return Packet(PacketStatus.invalidJson, JSONValue.init, error.msg);
            }
            if (!readMore())
                return pending.length == 0
                    ? Packet(PacketStatus.eof, JSONValue.init, null)
                    : Packet(PacketStatus.framingError, JSONValue.init,
                        "unexpected EOF in LSP header");
            if (pending.length > 8192)
                return Packet(PacketStatus.framingError, JSONValue.init,
                        "LSP header is too large");
        }
    }

    void write(JsonValue value)
    {
        streams.write(cast(const(ubyte)[]) packet(value));
    }

    private bool readMore()
    {
        ubyte[4096] chunk;
        const count = streams.read(chunk[]);
        if (count == 0)
            return false;
        pending ~= chunk[0 .. count];
        return true;
    }
}

private size_t delimiter(const(ubyte)[] bytes)
{
    if (bytes.length < 4)
        return size_t.max;
    for (size_t index; index + 3 < bytes.length; ++index)
        if (bytes[index] == '\r' && bytes[index + 1] == '\n'
                && bytes[index + 2] == '\r' && bytes[index + 3] == '\n')
            return index;
    return size_t.max;
}
