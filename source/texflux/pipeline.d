/**
 * The order the passes run in.
 *
 * Every pass has a reason to be where it is, and the order is as much a part
 * of the language as the syntax:
 *
 *  - the form checks run before `>>` is resolved, because each asks where a
 *    construct was written and desugaring moves it;
 *  - flags are collected before any conditional is read, so a conditional may
 *    precede the declaration it names;
 *  - macros are collected before any call is expanded, so a call may precede
 *    the definition it names;
 *  - conditionals resolve inside expansion, and a dropped payload is never
 *    expanded, so an author can disable content that no longer compiles;
 *  - normalization comes last, which is what keeps the renderer free of every
 *    construct above.
 *
 * This is the low-level, import-free pipeline: its macro environment is
 * exactly what the document defines, so a document calling a standard flow
 * macro fails here as an unknown special. Full compilation goes through a
 * session, which seeds the bundled prelude and resolves modules.
 */
module texflux.pipeline;

import texflux.ast : Document;
import texflux.canonical : builtinDirectives, canonicalize, DirectiveRegistry;
import texflux.desugar : desugar;
import texflux.flags : collectFlags, Flags, validateFlagForms;
import texflux.macros : collectMacros, expandMacros, validateMacroForms;

/**
 * Turn a syntax tree into a canonical one, expanding macros and specials.
 *
 * `flags` overrides the defaults the document's own declarations give. An
 * override naming no declaration is an error, so a typo cannot quietly build
 * the other version of the document.
 */
Document normalize(Document document, DirectiveRegistry registry = builtinDirectives(),
        Flags flags = Flags.init)
{
    validateMacroForms(document);
    validateFlagForms(document);

    document = desugar(document);
    auto collected = collectFlags(document, flags);
    auto macros = collectMacros(collected.document, registry.keys);
    auto expanded = expandMacros(macros.document, macros.macros, collected.flags);
    return canonicalize(expanded, registry);
}
