module texflux.lsp.protocol;

import std.conv : to;
import std.json : JSONType, JSONValue;

import texflux.json : JsonValue, dumpJson, jsonNull, jsonObject, jsonOf, member;

struct RpcMessage
{
    JSONValue value;
    string method;
    JSONValue id;
    JSONValue params;
    bool hasId;
    bool hasParams;
    bool valid;
}

RpcMessage inspect(JSONValue value)
{
    RpcMessage result;
    result.value = value;
    if (value.type != JSONType.object)
        return result;
    auto jsonRpcVersion = field(value, "jsonrpc");
    auto method = field(value, "method");
    if (jsonRpcVersion.type != JSONType.string || jsonRpcVersion.str != "2.0"
            || method.type != JSONType.string)
        return result;
    result.method = method.str;
    result.hasId = hasField(value, "id");
    if (result.hasId)
        result.id = field(value, "id");
    result.hasParams = hasField(value, "params");
    if (result.hasParams)
        result.params = field(value, "params");
    result.valid = true;
    return result;
}

bool hasField(JSONValue object, string name)
{
    return object.type == JSONType.object && (name in object.objectNoRef) !is null;
}

JSONValue field(JSONValue object, string name)
{
    if (object.type != JSONType.object)
        return JSONValue.init;
    auto found = name in object.objectNoRef;
    return found is null ? JSONValue.init : *found;
}

bool stringField(JSONValue object, string name, out string result)
{
    auto value = field(object, name);
    if (value.type != JSONType.string)
        return false;
    result = value.str;
    return true;
}

bool integerField(JSONValue object, string name, out long result)
{
    auto value = field(object, name);
    switch (value.type)
    {
    case JSONType.integer:
        result = value.integer;
        return true;
    case JSONType.uinteger:
        result = cast(long) value.uinteger;
        return true;
    default:
        return false;
    }
}

JsonValue jsonId(JSONValue id)
{
    switch (id.type)
    {
    case JSONType.null_:
        return jsonNull();
    case JSONType.string:
        return memberValue(id.str);
    case JSONType.integer:
        return memberValue(id.integer);
    case JSONType.uinteger:
        return memberValue(cast(long) id.uinteger);
    default:
        return jsonNull();
    }
}

private JsonValue memberValue(string value)
{
        return jsonOf(value);
}

private JsonValue memberValue(long value)
{
        return jsonOf(value);
}

JsonValue response(JSONValue id, JsonValue result)
{
    return jsonObject(member("jsonrpc", "2.0"), member("id", jsonId(id)),
            member("result", result));
}

JsonValue errorResponse(JSONValue id, long code, string message)
{
    return jsonObject(member("jsonrpc", "2.0"), member("id", jsonId(id)),
            member("error", jsonObject(member("code", code), member("message", message))));
}

JsonValue notification(string method, JsonValue params)
{
    return jsonObject(member("jsonrpc", "2.0"), member("method", method),
            member("params", params));
}

string packet(JsonValue value)
{
    auto body = dumpJson(value);
    return "Content-Length: " ~ body.length.to!string ~ "\r\n\r\n" ~ body;
}

enum long parseErrorCode = -32700;
enum long invalidRequestCode = -32600;
enum long methodNotFoundCode = -32601;
enum long invalidParamsCode = -32602;
enum long internalErrorCode = -32603;
