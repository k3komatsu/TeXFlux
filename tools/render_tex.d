/**
 * Compile one document through the import-free pipeline and print the TeX.
 *
 * Like tools/dump_syntax.d this is a comparison instrument rather than part of
 * the product: it runs the pipeline that needs no compilation session, so that
 * the two implementations can be diffed on rendered output before the module
 * system exists on this side. tests/conformance/render_tex.py prints the same
 * thing from the other implementation.
 *
 * A document that calls a standard flow macro fails here, in both
 * implementations alike, because the session is what seeds those.
 */
module tools.render_tex;

import std.file : readText;
import std.stdio : stderr, stdout;

import texflux.errors : FlagError, NestingError, TeXFluxError;
import texflux.parser : parse;
import texflux.pipeline : normalize;
import texflux.render : render;

int main(string[] args)
{
    if (args.length < 2 || args.length > 3)
    {
        stderr.writeln("usage: render-tex FILE [--source-comments]");
        return 2;
    }
    const sourceComments = args.length == 3 && args[2] == "--source-comments";

    string output;
    try
        output = render(normalize(parse(readText(args[1]), args[1])), sourceComments);
    catch (TeXFluxError error)
        output = "error " ~ error.code ~ " " ~ error.span.location ~ " " ~ error.message ~ "\n";
    catch (FlagError error)
        output = "flag error " ~ error.msg ~ "\n";
    catch (NestingError error)
        output = "nesting error\n";
    stdout.rawWrite(cast(const(ubyte)[]) output);
    return 0;
}
