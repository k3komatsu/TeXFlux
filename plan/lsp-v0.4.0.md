# TeXFlux LSP v0.4.0 実装計画

## 1. 目的と今回の範囲

TeXFlux の既存 D compiler core を再利用し、単一バイナリの
'texflux lsp' として Language Server Protocol を提供する。

依存方向は次に固定する。

~~~text
stdio / JSON-RPC
    ↓
texflux.lsp
    ↓
texflux.analysis
    ↓
CompilationSession / compiler core
~~~

compiler core は URI、LSP Position、document version、CompletionItem 等を
知らない。外部 LSP framework、別言語 sidecar、毎回の 'texflux check'
subprocess は導入しない。Phobos と既存 JSON writer だけで実装し、
runtime dependency と配布物を増やさない。

v0.4.0 の実装範囲は次に限定する。

- stdio transport、Content-Length framing、minimal JSON-RPC
- 'initialize' / 'initialized' / 'shutdown' / 'exit'
- 'textDocument/didOpen' / 'didChange' / 'didSave' / 'didClose'
- Full document synchronization
- '.tfx' と '.tfxm' の直接解析
- open document overlay
- 'publishDiagnostics'、related information、stale diagnostics clear
- UTF-8 / UTF-16 / UTF-32 position encoding
- root→dependency と reverse dependency による粗粒度 invalidation
- 単一スレッド event loop

次は後続フェーズへ延期する。

- Document Symbols、Folding Range
- Completion、Hover、Go to Definition
- Semantic Tokens、References、Rename、Code Action
- parser recovery、incremental sync、background analysis
- unsaved '.tfxb' / asset bytes overlay
- TeX/LaTeX package semantics
- VSCode extension 本体

## 2. 現状から確定する制約

- 'app.d' は stdin を EOF まで一括読み込みし、'cli.run' を一度だけ呼ぶ。
  LSP だけはこの経路を使わず長寿命 loop へ分岐する。
- 'diagnose()' は新しい 'CompilationSession' を作り、構造化された
  'DiagnosticReport' を返す。現在は fail-fast で最大一件の診断である。
- overlay は '.tfx' / '.tfxm' の module reader にだけ適用され、root text は
  'compileRoot' へ直接渡す必要がある。
- 'SourcePosition' は 1-based Unicode code point、'SourceSpan' は half-open。
  parser は CRLF/CR を LF として扱うが、'LoadedSource.data' は元 bytes を保持する。
- 'CompilationTrace' は caller-owned pointer を session constructor に渡せる。
  通常の 'compileAst' / 'diagnose' は trace を返さない。
- 'compileRoot' は root を常に content module として扱う。'.tfxm' の purity、
  macroimport closure、self-contained 検査は既存の macro-module helper を
  session-level API へ接続する必要がある。
- syntax AST は symbols/folding に使える span/name/suite 情報を持つが、strict
  parse failure 時の partial AST は存在しない。macro/flag の lexical resolution
  情報は pass 中の private state であり、一般 index として返されない。
- bundle/asset は通常 filesystem/archive reader であり、既存 diagnose overlay
  では未保存 bundle/asset bytes を差し替えられない。
- 'normalizedPath' を module cache/overlay identity とし、display spelling は
  diagnostics/source span 用に残す。macOS case-insensitive volume の既知制限は
  LSP で修正しない。

## 3. Analysis API

新規 'source/texflux/analysis.d' を editor-independent な公開 module とする。
LSP 型や URI はここへ入れない。

~~~d
struct AnalysisRequest
{
    string source;
    string filename;
    ModuleKind kind;
    Flags flags;
    immutable(ubyte)[] sourceBytes;
    const(ubyte)[][string] overlays;
}

struct AnalysisResult
{
    ModuleKind kind;
    string rootPath;
    DiagnosticReport report;
    TraceDependency[] dependencies;
    bool complete;
}

AnalysisResult analyze(AnalysisRequest request);
~~~

契約:

- LSP は絶対 path を 'filename' に渡す。
- root は 'source'/'sourceBytes' を直接 compile し、root overlay は無視する。
- overlays は canonical path→bytes として import reader に渡す。
- 'ModuleKind.content' は既存 'compileRoot' を使う。
- 'ModuleKind.macro' は新規 'CompilationSession.validateMacroRoot' を使う。
- 'TeXFluxError' は 'DiagnosticReport' に変換する。'InternalError'、不正引数、
  壊れた prelude 等は server-level error として扱う。
- 'complete' は compiler error なしで完了した場合だけ true。
- 'dependencies' は成功時の全 trace、失敗時の観測済み partial trace を返す。

既存 'diagnose()' と session 構築・overlay・例外変換を重複させないため、
'diagnostics.d' に package-visible な共通 root diagnose helper を置く。
既存 'diagnose()' の public behavior と JSON output は変更しない。

### .tfxm root validation

'CompilationSession' に次を追加する。

~~~d
void validateMacroRoot(
    string text,
    string filename,
    immutable(ubyte)[] data
);
~~~

既存処理を次の順序で再利用する。

~~~text
register root
→ validate macro forms/import forms/purity
→ desugar
→ collect own macros/imports
→ load macroimport closure through SourceReader/overlays
→ build lexical environments
→ check self-contained rules
~~~

root '.tfxm' へ flags は適用しない。LSP は 'Flags.init' を渡し、非空 flags
は API misuse として拒否する。

### Trace と invalidation

module import の attempted edge は read 前に記録する。これにより、存在しない
import や parse failure でも修正対象が reverse graph に残る。成功時は旧 edge を
置換し、失敗時は旧成功 edge と今回観測 edge の union を保持する。

bundle/asset の成功 edge は既存 trace を利用する。missing asset の完全な
attempted-edge 化や unsaved binary overlay は v0.4.0 の範囲外とする。

## 4. LSP runtime

初期ファイル構成:

~~~text
source/texflux/analysis.d
source/texflux/lsp/transport.d
source/texflux/lsp/protocol.d
source/texflux/lsp/text.d
source/texflux/lsp/workspace.d
source/texflux/lsp/server.d
tests/d/texflux_tests/lsp.d
~~~

'app.d' は argv の command が 'lsp' の場合だけ streaming server を起動する。
通常 CLI の EOF 一括入力と 'CommandStreams' は維持する。

### Transport

- header は ASCII、body は UTF-8。
- 'Content-Length' は body の byte 数。
- header は 8 KiB、body は 16 MiB を上限とする。
- partial read、複数 packet、途中 EOF を処理する。
- framing が保たれた invalid JSON は parse error response 後に継続する。
- Content-Length 欠落・不正・途中 EOF は再同期不能として終了する。
- stdout は protocol bytes のみ、ログは stderr のみ。

### Lifecycle と同期

- initialize 前は initialize/exit 以外を拒否する。
- initialize は一度だけ受け付ける。
- shutdown は 'null' を返し、以後 request を拒否する。
- shutdown 後の exit は status 0、それ以外の exit/EOF は status 1。
- unknown request は MethodNotFound、unknown notification は無視する。
- notification の内部例外はログへ、request の内部例外は InternalError response へ変換する。
- client の 'general.positionEncodings' の提示順から UTF-8/16/32 を選び、未提示時は UTF-16。
- 'textDocumentSync.change' は Full、save は 'includeText=false' とする。

## 5. URI、position、workspace

'lsp/text.d' に URI/path と位置変換を集約する。

~~~d
struct LineIndex
{
    // 元の UTF-8 text と行開始 byte offset を所有
}

struct PositionMapper
{
    PositionEncoding encoding;

    LspPosition toLsp(SourcePosition position, const LineIndex index);
    Nullable!SourcePosition fromLsp(
        LspPosition position,
        const LineIndex index
    );
}
~~~

- MVP は 'file:' URI のみを受理する。
- URI decode 後に既存 'normalizedPath' を通して canonical key を作る。
- open document の URI は publish 用に保持する。
- CRLF/CR/LF の行境界を統一する。
- UTF-8 code point または UTF-16 surrogate の途中の position は invalid とする。
- compiler の 1-based code-point 座標と LSP の 0-based code-unit 座標を feature ごとに
  個別実装しない。

~~~d
struct OpenDocument
{
    string uri;
    string displayPath;
    string canonicalPath;
    int version;
    string text;
    immutable(ubyte)[] bytes;
    LineIndex lines;
    ModuleKind kind;
}

struct WorkspaceSnapshot
{
    ulong revision;
    OpenDocument[] documents;
}
~~~

Workspace は canonical path を key として open documents、root state、reverse
dependency graph を所有する。解析は全 open document の immutable snapshot を
overlays として使う。session は解析間で共有せず、single-threaded event loop
で同期実行する。

'didOpen'/'didChange'/'didSave'/'didClose' のたびに revision を進める。Full Sync
の 'didChange' は range なしの単一 change のみ受理し、version の逆行は無視する。
結果 commit 前に revision/version を検査し、将来の非同期化でも stale result を
publish しない。

## 6. Diagnostics と dependency graph

診断は root ごとに保持する。

~~~text
rootDiagnostics[root][targetUri] = diagnostics
~~~

新しい解析結果では、旧 source 集合と新 source 集合の union を publish 対象とする。
今回存在しなくなった URI へ 'diagnostics: []' を送る。複数 root が同じ imported
file を診断する場合は URI ごとに統合し、完全一致する item を重複除去する。

変換:

- error/warning → LSP severity 1/2
- TeXFlux code → Diagnostic.code
- 'source = \"texflux\"'
- related locations → relatedInformation
- open file には snapshot version、disk-only file には version なし

逆依存 graph は 'TraceDependency.path/target' を canonicalize して管理する。
open dependency の変更は reverse dependents と自身の root だけを再解析する。
watch notification は '.tfx'、'.tfxm'、'.tfxb' を対象にし、未知 path は無視する。

## 7. 後続フェーズ

### Phase 4: Symbols/Folding

strict 'parse()' が成功した場合だけ syntax Document を cache する。
'@environment'、top-level '!defmacro'、'!flag' を symbols とし、suite nesting
を階層にする。複数行 suite/block/sequence を folding 対象にする。parse failure
時は空結果とし、partial AST は作らない。

### Phase 5: Completion

compiler の実際の lexical resolution 中に、macro/flag declaration、reference、
definition span、visible symbol set を editor-independent trace metadata として
記録する。current-line の軽量 context scanner で special/macro、flags、'.tfx'、
'.tfxm'、'.tfxb' と selector を候補化する。

### Phase 6: Hover/Definition

記録済み declaration/reference と BundleIndex cache を使い、macro、flag、import、
macroimport、bundleimport を解決する。stale semantic index は返さない。

### Phase 7以降

parser recovery、semantic tokens、references、rename、code action、incremental
sync、background analysis は、利用要求または性能測定が出た機能だけ追加する。
strict parser の behavior は変更しない。

## 8. 実装順序と完了条件

1. この文書を保存し、既存 'diagnose()' parity を確認する。
2. analysis API と '.tfxm' root validation を実装する。
3. trace attempted edge と partial result test を追加する。
4. transport/protocol/lifecycle を実装する。
5. workspace、URI、LineIndex、Full Sync を実装する。
6. diagnostics publish、aggregation、stale clear、reverse invalidation を接続する。
7. subprocess smoke test で 'texflux lsp' の stdout 汚染がないことを確認する。
8. 全既存 D test/build configuration を通す。
9. Luna Primary Engineer のレビューを実行し、具体的な findings を修正する。
10. 修正 scope が public protocol/core boundary に及ぶ場合は同一 review session の
    re-review を行う。

必要な検証:

- Content-Length の partial/multiple/invalid packet
- initialize/shutdown/exit lifecycle
- ASCII、日本語、emoji、combining character、CRLF/CR/LF の UTF-8/16/32 mapping
- unsaved root/import、version handling、didClose fallback
- shared dependency、removed import、macroimport edit、stale diagnostics clear
- '.tfxm' purity/closure、root parse failure、missing import
- 既存 compile/check/AST/JSON/regression output の非回帰

最終的に以下を実行する。

~~~bash
source ~/dlang/ldc-1.43.0/activate
dub test
dub build
dub build -c library
dub build --build=release
dub build -c update-regression
~~~

'tests/regression/v1.jsonl' は意図的な output change がない限り変更しない。
release/tag/Homebrew は implementation PR の後段で扱い、別 version tag の再利用や
force update は行わない。

## 9. 実装状況

2026-09-20 時点で Phase 0--3 を実装した。

- 'texflux lsp' の stdio JSON-RPC transport と lifecycle を追加した。
- 'texflux.analysis' と '.tfxm' root validation を追加した。
- open-document overlay、Full Sync、UTF-8/16/32 の位置変換を追加した。
- diagnostics の publish、related information、stale clear、root の逆依存再解析を追加した。
- partial packet、複数 packet、失敗時の dependency union、bundleimport edge、
  version 逆行拒否、subprocess lifecycle smoke をテストした。
- `didChangeWatchedFiles`、予期しない解析失敗の internal diagnostic、Windows/Posix
  の pipe 境界を実装した。
- symbols/completion/hover/definition 等は本計画どおり v0.4.0 の対象外である。
