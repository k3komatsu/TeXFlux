# TeXFlux Diagnostic API 設計

## ステータス

- 種別: 設計書。実装済み（`src/texflux/diagnostics.py`、`texflux check`、
  `schemas/texflux-diagnostics-v1.schema.json`）。
- 位置づけ: 本書が Diagnostic API の確定仕様である。規範定義は
  `texflux_tex_first_dsl_v1_spec.md` §16、利用者向け説明は `README.md` と
  `doc/dsl.md` §19。
- §2 のコード表は `tests/test_diagnostic_codes.py` がソースと突き合わせる。
  診断を足すときは、表・件数・上限値をソースと同時に更新すること。

## 背景と決定

TeXFlux のエラーは `TeXFluxError(message, span, code=)` の例外として送出され、CLI が
`file:line:col: parse error: message [P004]` の 1 行を stderr に印字して終了コード 1 を返す。
VSCode 拡張や Language Server は、位置・重大度・コード・関連位置を**構造化された形**で、
しかも**例外ではなく戻り値**として受け取る必要があり、未保存バッファも扱わねばならない。

そのために次の 3 層がある。

1. **Python API** `texflux.diagnose(...) -> DiagnosticReport`（例外を投げず、読んだ全ソースと診断を返す）
2. **CLI** `texflux check`（stdin 対応、`--format json`、終了コード 0/1/2）
3. **JSON 形式** `texflux-diagnostics` v1（外部 AST と同じ規約、JSON Schema 付き）

全診断は**安定した診断コード**（`P004` など）を持ち、メッセージに埋め込まれた副位置
（"already defined at …", "imported from …"）は**構造化された関連位置**としても持つ。

決定事項:

| 論点 | 決定 |
| --- | --- |
| 関連位置情報 | 構造化する（`TeXFluxError.related`） |
| 診断コード | 全 raise 箇所に付与する（`P/V/D/E/M` + 3 桁）。v1 以降は改番・再利用しない |
| 未保存バッファの overlay | Python API に含める（`CompilationSession(reader=)`）。CLI は root の stdin のみ |
| エラー件数 | fail-fast のまま。エラー最大 1 件。`severity: "warning"` は予約値で、生成箇所は無い |

---

## 0. 全体像

```text
                 diagnose(source, filename=, flags=, source_bytes=, overlays=)
                                  │
      CompilationSession(reader=overlay_reader)  ── load() が reader 経由で読む
                                  │  compile_root()      ← TeXFluxError なら捕捉
                                  ▼
      DiagnosticReport(sources=session.loaded(), diagnostics=(Diagnostic, ...))
             │                         │
   serialize_diagnostics()        report.by_file()   → LSP publishDiagnostics
             │
   texflux check INPUT --format json   (stdout, exit 0/1/2)
```

不変条件:

- `diagnose()` は `TeXFluxError` を**決して**送出しない。`FlagError`・`InternalError`・
  `RecursionError` は文書の問題ではないので**そのまま伝播**する（`compile_*` と同じ分類）。
- 診断は 0 件か、エラーちょうど 1 件。`severity: "warning"` は形式上の予約値で、v1 のコンパイラは
  生成しない。`diagnose` はレンダリングを行わない: レンダラは診断を生まないので、コンパイルが通れば描ける。
- `report.sources` は常に root を先頭に含む。**root のパースが失敗しても含む**
  （`CompilationSession._register` はパースより前にファイルを記録する）。
- 位置は 1 始まり行・1 始まり列（Unicode code point）・半開区間。JSON もそのまま出す。

---

## 1. 使用方法（利用者向け仕様）

### 1.1 Python API

`src/texflux/diagnostics.py` で定義し、`texflux` パッケージから再公開する。

```python
from texflux import (
    Diagnostic, DiagnosticReport, RelatedLocation, Severity,
    diagnose, serialize_diagnostics,
)

class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"

@dataclass(frozen=True, slots=True)
class RelatedLocation:            # errors.py で定義、ここから再公開
    message: str                  # 例: "first defined here"
    span: SourceSpan

@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    kind: str                     # "parse" | "validation" | "directive" | "macro" | "module"
    code: str                     # "P004", "M012" など（§2）
    message: str                  # 例外メッセージそのもの
    span: SourceSpan
    related: tuple[RelatedLocation, ...] = ()

    @property
    def label(self) -> str:       # "{kind} error"。予約値 WARNING なら "warning"
    def line(self) -> str:        # "file:l:c: parse error: message [P004]"
    def __str__(self) -> str:     # == line()
    @classmethod
    def from_error(cls, error: TeXFluxError) -> Diagnostic

@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    sources: tuple[LoadedSource, ...]      # root が先頭、以降はセッションの読み込み順。空にならない
    diagnostics: tuple[Diagnostic, ...]    # コンパイラが生成した順

    @property
    def root(self) -> LoadedSource         # sources[0]
    @property
    def ok(self) -> bool                   # severity ERROR が 1 件も無い
    def by_file(self) -> dict[str, tuple[Diagnostic, ...]]
        # 読んだ全ファイル（display 綴り）→ そのファイルの診断。診断が無いファイルは ()。
        # LSP が「以前エラーがあったが直ったファイル」の診断を空で publish して消すために使う。

def diagnose(
    source: str,
    *,
    filename: str = "<string>",
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
    overlays: Mapping[str, str | bytes] | None = None,
) -> DiagnosticReport

def serialize_diagnostics(report: DiagnosticReport, *, pretty: bool = False) -> str
```

引数の意味は `compile_with_map` と同じ。追加の `overlays` は
「ファイルパス → 未保存の内容」で、`!import` / `!macroimport` がそのパスを読むときに
ディスクの代わりに使われる。キーは `texflux.paths.normalized_path` で正規化して比較する
（相対/絶対・`..`・シンボリックリンクの違いを吸収する。大文字小文字は吸収しない。下記参照）。
`str` の値は UTF-8 に符号化して `sha256` に使う。**root 自身は `source` 引数で渡す**ので、
overlays に root のパスがあっても無視される。

`diagnose` の振る舞いで固定している細部:

- `ok` は「`severity == ERROR` の診断が無い」と同値。v1 では診断が 1 件も無いことと同じである。
- `sources` / `by_file()` に載るのは**読めて UTF-8 として復号できたファイル**。存在しない・読めない・
  UTF-8 でない import 先は `load()` が M027 を import 行の span で報告し、そのファイル自体は表に載らない。
  パースに失敗したファイルは（読めているので）載る。
- import 先が root を `!import` で参照し返す場合、`filename` が実際のパスと同じファイルに正規化されるときだけ
  セッションのキャッシュに当たり循環として検出される。`filename="<string>"` / `<stdin>` のままだと
  ディスク上の root が別モジュールとして読まれる（`compile_text` と同じ挙動）。LSP は実パスを渡すこと。
- パスの同一視は `normalized_path`（`realpath` + `normcase`）に従う。macOS の APFS は大文字小文字を
  区別しないが `normcase` は恒等なので、綴りの違う 2 つの参照は 2 つのソースになる（既知の制限）。

使用例（Python 製 LSP サーバー、pygls 等は別プロジェクト）:

```python
import texflux

def check(root_path: str, open_documents: dict[str, str]) -> None:
    report = texflux.diagnose(
        open_documents[root_path],
        filename=root_path,                 # 絶対パスを渡す。import はこの位置からの相対で解決される
        overlays=open_documents,            # 開いている全バッファ。root は無視される
    )
    for file, diagnostics in report.by_file().items():
        publish(uri_for(file), [to_lsp(d) for d in diagnostics])   # 空リストで消える
```

伝播する例外（文書ではなく呼び出し側・環境の問題）:

| 例外 | 原因 | LSP 側の扱い |
| --- | --- | --- |
| `FlagError` | `flags` が宣言に無い名前・bool 以外の値 | 設定エラーとして通知 |
| `InternalError` | 同梱 prelude が壊れている（インストール不良） | 通知 |
| `RecursionError` | 入れ子が深すぎる | 通知（span が無い） |
| `ValueError` | `filename` に NUL など | 通知 |

### 1.2 CLI `texflux check`

```text
texflux check INPUT [--format {text,json}] [--pretty] [--flag NAME[=on|off]]... [--stdin-filename PATH]
```

| 引数 | 意味 |
| --- | --- |
| `INPUT` | `.tfx` ファイル、または `-`（標準入力）。ファイルは `.tfx` 拡張子必須（`compile` と同じ。`compile_root` はどんなファイルもコンテンツモジュールとして扱うため、`.tfxm` を直接 check すると純粋性検査が走らず誤った結果になる） |
| `--stdin-filename PATH` | `INPUT` が `-` のとき、この文書の表示名。span の `file`、import の基準ディレクトリ、`sources[0].file` に使う。`.tfx` 拡張子必須。省略時は `<stdin>`（import はカレントディレクトリ基準）。`INPUT` がファイルのときに指定すると終了コード 2 |
| `--format text` | 既定。§1.4 の行を stdout へ。診断が無ければ何も出さない |
| `--format json` | §1.3 の JSON 1 文書を stdout へ（compact、末尾 LF 1 個） |
| `--pretty` | JSON をインデント 2 で出す。`--format json` 以外と組むと終了コード 2 |
| `--flag` | `compile` と同じ構文・同じエラー |

`-o` は無い。出力は常に stdout、ツール自体のメッセージだけが stderr。TeX も `.tfxmap` も書かない。

終了コード:

| コード | 意味 |
| --- | --- |
| 0 | 診断なし |
| 1 | エラー診断が 1 件ある（stdout に診断） |
| 2 | 報告を作れなかった: 使用法エラー、入力が読めない、UTF-8 でない、`FlagError`、`InternalError`、`RecursionError`、内部 `ValueError`。stderr に `texflux: ...`、stdout には何も出さない |

例:

```bash
$ texflux check slides.tfx
slides.tfx:12:5: parse error: unclosed required group [P004]
$ echo $?
1

$ texflux check main.tfx
main.tfx:3:1: module error: macro '!m' is already available here, defined at a.tfxm:2:1 [M021]
  a.tfxm:2:1: note: defined here

$ texflux check - --stdin-filename /abs/slides.tfx --format json < buffer.txt
{"format":"texflux-diagnostics","version":1,...}

$ texflux check slides.tfx --flag drfat; echo $?
texflux: unknown build flag 'drfat'; declared flags are: draft
2
```

LSP からは **絶対パス**を `INPUT` / `--stdin-filename` に渡すこと。import 先の `file` は
それを書いたファイルからの相対で解決されるので、root が絶対なら全ソースが絶対になり、URI 化が一意になる。

### 1.3 JSON 形式 `texflux-diagnostics` v1

```json
{
  "format": "texflux-diagnostics",
  "version": 1,
  "producer": {"name": "texflux", "version": "0.1.0"},
  "root": 0,
  "sources": [
    {"id": 0, "file": "/w/main.tfx", "sha256": "…64 hex…"},
    {"id": 1, "file": "/w/a.tfxm",   "sha256": "…"}
  ],
  "diagnostics": [
    {
      "severity": "error",
      "kind": "module",
      "code": "M021",
      "message": "macro '!m' is already available here, defined at /w/a.tfxm:2:1",
      "span": {"source": 0, "start": {"line": 3, "column": 1}, "end": {"line": 3, "column": 21}},
      "related": [
        {"message": "defined here",
         "span": {"source": 1, "start": {"line": 2, "column": 1}, "end": {"line": 2, "column": 15}}}
      ]
    }
  ]
}
```

| メンバー | 規定 |
| --- | --- |
| `format` | 常に `"texflux-diagnostics"` |
| `version` | 形式の major version。整数 `1`。パッケージ版とは独立 |
| `producer` | `{"name": "texflux", "version": __version__}`。形式判定に使ってはならない |
| `root` | root の source ID。常に `0` |
| `sources` | 外部 AST と同じ表。`id` は読み込み順、`file` は診断と同じ display 綴りを `/` 区切りに正規化、`sha256` は読んだバイト列（stdin / overlay ならその内容）の小文字 hex。**パースに失敗したファイルも含む**。`.tfxm` も含む。落ちた条件分岐で読まなかったファイルは含まない |
| `diagnostics` | コンパイラが生成した順。fail-fast なので最大 1 件 |
| `severity` | `"error"` / `"warning"`（閉じた列挙。追加は version bump）。`"warning"` は予約値で、v1 の producer は `"error"` だけを出す。consumer は両方を扱うこと |
| `kind` | 小文字英字。現在 `parse` `validation` `directive` `macro` `module`。consumer は未知の値も受け入れる（表示用） |
| `code` | `^[A-Z][0-9]{3}$`。§2 の表。一度公開したコードは再利用・改番しない |
| `message` | 例外のメッセージそのもの（コード・位置プレフィックスを含まない） |
| `span` | 外部 AST と同一: `source` は source ID、`start`/`end` は 1 始まり行・1 始まり列（code point）、半開 `[start, end)`。`end` は別行でもよい |
| `related` | 常に存在する配列（空でもよい）。各要素は `message` と `span` |

Serialization は外部 AST と同一（`src/texflux/interchange.py` を共有する）: UTF-8、BOM なし、
`ensure_ascii=False`、compact は `separators=(",", ":")`、末尾 LF 1 個、`--pretty` は `indent=2`。
同一入力に対しバイト単位で決定的。

Versioning 規約（外部 AST と同じ）: consumer は未知の object member を無視しなければならない。
member 追加は v1 内、意味変更・必須 member の削除・位置意味の変更・`severity` 値の追加は version bump。

機械可読定義: `schemas/texflux-diagnostics-v1.schema.json`。

### 1.4 テキスト形式

1 診断 = 1 行 + 関連位置ごとに 1 行。

```text
{file}:{line}:{column}: {label}: {message} [{code}]
  {file}:{line}:{column}: note: {related.message}
```

- `label` はエラーなら `"{kind} error"`（`parse error` 等）、予約値 `warning` なら `warning`。
- **`compile` の stderr も同じ形**である（`TeXFluxError.diagnostic()` と `Diagnostic.line()` が
  単一の `diagnostic_line()` を使うため）。`compile` は `note:` 行を出さない（メッセージが既に位置を含む）。
  `check` の text 形式だけが `note:` 行を出す。
- VSCode problem matcher 用正規表現（参考）:
  `^(.+?):(\d+):(\d+): (\w+ error|warning): (.*?) \[([A-Z]\d{3})\]$`

### 1.5 LSP / VSCode へのマッピング

| TeXFlux | LSP `Diagnostic` |
| --- | --- |
| `sources[span.source].file` | `uri`（絶対パスなら `file://`、相対なら CLI の cwd 基準で解決） |
| `span.start.line` (1-based) | `range.start.line = line - 1` |
| `span.start.column` (1-based, code point) | `range.start.character` = 行テキストの先頭 `column-1` code point を **クライアントの positionEncoding** に換算した長さ（UTF-16 なら `Array.from(lineText).slice(0, column-1).join("").length`、UTF-32 なら `column-1`、UTF-8 ならそのバイト長） |
| `span.end` | 同じ換算。半開区間はそのまま LSP の exclusive end に対応 |
| `severity: error/warning` | `DiagnosticSeverity.Error (1)` / `Warning (2)` |
| `code` | `Diagnostic.code` |
| `kind` | 表示用（`message` の前置きや `tags` には使わない） |
| `message` | `Diagnostic.message` |
| 定数 | `Diagnostic.source = "texflux"` |
| `related[]` | `relatedInformation[]`（`location.uri` は `sources[related.span.source].file` から） |

注意点:

- **空幅の span は実在する**（`!` だけの行 → `1:2`〜`1:2`、空文書の document span → `1:1`〜`1:1`）。
  複数行にまたがる span もある（suite・document）。クライアントは `start == end` をそのまま渡してよい
  （VSCode は 1 文字に広げて表示する）。
- 行の切り方は LSP と同じ: CR / CRLF は LF に正規化され、`\f` や U+2028 は改行ではない。
- `by_file()` が返すのは**今回読んだファイル**だけ。`!when` で落ちた import や削除された import は現れないので、
  クライアントは前回 publish したファイル集合と差分を取り、消えたファイルには空配列を publish すること。
- import 先のファイルに診断が出た場合、`span.source != 0`。`by_file()` / `sources` で全ファイルを
  publish すること（診断が無いファイルには空配列を publish して以前の診断を消す）。
- 未保存バッファ: TypeScript から CLI を呼ぶ場合、root は stdin で渡せる。import 先の未保存内容は
  CLI では渡せない（Python API の `overlays` のみ）。

TypeScript（VSCode 拡張、LSP なし）の最小例:

```ts
const proc = spawnSync("texflux",
  ["check", "-", "--format", "json", "--stdin-filename", document.uri.fsPath],
  { input: document.getText(), encoding: "utf8" });
if (proc.status === 2) { window.showErrorMessage(proc.stderr.trim()); return; }
const report = JSON.parse(proc.stdout) as Report;
const files = report.sources.map(s => s.file);
const perFile = new Map<string, Diagnostic[]>(files.map(f => [f, []]));
for (const d of report.diagnostics) {
  const file = files[d.span.source];
  const diag = new Diagnostic(toRange(d.span, file), d.message,
    d.severity === "error" ? DiagnosticSeverity.Error : DiagnosticSeverity.Warning);
  diag.code = d.code; diag.source = "texflux";
  diag.relatedInformation = d.related.map(r => new DiagnosticRelatedInformation(
    new Location(Uri.file(files[r.span.source]), toRange(r.span, files[r.span.source])), r.message));
  perFile.get(file)!.push(diag);
}
for (const [file, diags] of perFile) collection.set(Uri.file(file), diags);

function toPosition(p: {line: number; column: number}, lineText: string): Position {
  // TeXFlux: 1 始まり・code point。VSCode: 0 始まり・UTF-16 code unit。
  return new Position(p.line - 1, Array.from(lineText).slice(0, p.column - 1).join("").length);
}
```

---

## 2. 診断コード体系と全コード表

### 2.1 規則

- 形式: 種別 1 文字 + 3 桁。`P` parse、`V` validation、`D` directive、`E` macro expansion、`M` module。
  `kind` との対応: P→parse、V→validation、D→directive、E→macro、M→module。
- **1 生成箇所 = 1 コード**。同じメッセージでも生成箇所が違えば別コード（例: P005/P007）。
  1 箇所が f-string で複数の文言を出す場合は 1 コード（例: P016、E004、M005）。
- 番号は付与順。v1 リリース前に一度だけ欠番を圧縮した（到達不能だった当時の P018 と M022 を削除し、
  それより上の番号を 1 つずつ前に詰めた）。**v1 以降は改番しない**。新規箇所はその種別の末尾番号 + 1。
  削除されたコードは欠番のまま残す。
- 既存の例外を別の例外に作り直す箇所は 5 つあり、扱いを次のとおり固定する。
  - **内側のコードを引き継ぐ（新コードなし）**: `macros._Expander._reject`（`reject_markers` の E004 を frame 付きに）、
    `interpolate.interpolate` の `text_value` 包み（E001〜E003 を hole の span に）、
    `CompilationSession._importing` と `_ImportResolver._expand` の `error.chained(...)`。
    E001〜E004 は**この転送経由でしか出力に現れない**が、自分のコードで現れる。
  - **新コードを振る**: `_Parser._explicit_sequence_entry` の
    `raise ParseError("explicit sequence entries require one balanced group", error.span) from None`
    はメッセージを置き換える別の条件なので P035。`scan_group` の P003〜P009 はヘッダー走査経路から従来どおり現れる。
- `tests/test_diagnostic_codes.py` が「全生成箇所に `code=` がある」「リテラルが一意」「本表と一致」
  「各コードが生成箇所のファイルの行に載っている」を機械的に検査する。

下表の生成箇所は「ファイル 関数（またはクラス）」で示す。同定は「ファイル・関数・メッセージ」で行う。

### 2.2 P — `ParseError`（`parser.py`、37 件）

| コード | 生成箇所 | メッセージ |
| --- | --- | --- |
| P001 | parser.py `_scan_raw_regions` | `'!END_RAW_MODE' has no matching '!BEGIN_RAW_MODE'` |
| P002 | parser.py `_scan_raw_regions` | `'!BEGIN_RAW_MODE' is not closed by '!END_RAW_MODE'` |
| P003 | parser.py `scan_group` | `unclosed overlay group` |
| P004 | parser.py `scan_group` | `unclosed required group` |
| P005 | parser.py `scan_group` | `mismatched group delimiter` |
| P006 | parser.py `scan_group` | `unclosed optional group` |
| P007 | parser.py `scan_group` | `mismatched group delimiter` |
| P008 | parser.py `scan_group` | `unclosed binding list` |
| P009 | parser.py `scan_group` | `invalid group opener` |
| P010 | parser.py `HeaderScanner` | `empty structural header` |
| P011 | parser.py `HeaderScanner` | `unexpected token in structural header; write '!\| ' in front of a line that has to stay raw TeX` |
| P012 | parser.py `HeaderScanner` | `trailing token after suite marker` |
| P013 | parser.py `HeaderScanner` | `unexpected ':' in a structural header; the suite markers are '::' and ':::'` |
| P014 | parser.py `HeaderScanner` | `stack separator requires surrounding spaces` |
| P015 | parser.py `HeaderScanner` | `missing structural segment` |
| P016 | parser.py `HeaderScanner` | `structural header must start with '\', '@', or '!'` / `each stack segment must start with '\', '@', or '!'` |
| P017 | parser.py `HeaderScanner` | `invalid structural name` |
| P018 | parser.py `HeaderScanner` | `'!BEGIN_RAW_MODE' must stand alone on its own line`（END も同文） |
| P019 | parser.py `HeaderScanner` | `a special's '(...)' list must follow its groups` |
| P020 | parser.py `HeaderScanner` | `a special accepts at most one '(...)' list` |
| P021 | parser.py `HeaderScanner` | `unexpected token after structural name or group` |
| P022 | parser.py `_Parser` | `tab characters are not allowed; keep one after '!\| ' or inside a raw-mode region` |
| P023 | parser.py `_Parser` | `'!|' must be followed by one space or end the line` |
| P024 | parser.py `_Parser` | `invalid structural indentation; a structural line sits at its suite base, and a literal '@' or '!' line is written '@@' or '!!'` |
| P025 | parser.py `_Parser` | `environment directives require a suite marker '::' or ':::'; a literal '@' line is written '@@'` |
| P026 | parser.py `_Parser` | `indented lines require a suite marker '::' or ':::'` |
| P027 | parser.py `_Parser` | `stack separator needs a following segment` |
| P028 | parser.py `_Parser` | `stack continuation requires the next line at the same indentation` |
| P029 | parser.py `_Parser` | `sequence suite entries require four-space indentation` |
| P030 | parser.py `_Parser` | `sequence suites require at least one '-' or '+' value entry` |
| P031 | parser.py `_Parser` | `sequence entries must start at suite indentation` |
| P032 | parser.py `_Parser` | `sequence suites require '-' or '+' value entries` |
| P033 | parser.py `_Parser` | `explicit sequence entries require one '{...}', '[...]', or '<...>' group` |
| P034 | parser.py `_Parser` | `explicit sequence entries require opaque raw text` |
| P035 | parser.py `_Parser` | `explicit sequence entries require one balanced group` |
| P036 | parser.py `_Parser` | `explicit sequence entries require exactly one group` |
| P037 | parser.py `_Parser` | `command block suites require an indented body` |

### 2.3 V — `ValidationError`（43 件）

| コード | 生成箇所 | メッセージ |
| --- | --- | --- |
| V001 | flags.py `_combinator` | `!{when|unless} modifier must be '[and]' or '[or]', got '[…]'` |
| V002 | flags.py `_flag_names` | `unknown build flag '…'; {declared_flags_hint}` |
| V003 | flags.py `_flag_names` | `build flag '…' is listed twice` |
| V004 | flags.py `evaluate_conditional` | `!{when|unless} requires at least one '{flag}' group` |
| V005 | flags.py `evaluate_conditional` | `!{when|unless} requires an '[and]' or '[or]' modifier to combine {count} flags` |
| V006 | flags.py `validate_flag_forms` | `!flag must be a top-level declaration and cannot be a '>>' segment` |
| V007 | flags.py `_declaration` | `!flag does not accept a suite` |
| V008 | flags.py `_declaration` | `!flag requires a name group and an 'on' or 'off' default group` |
| V009 | flags.py `_declaration` | `invalid build flag name '…'` |
| V010 | flags.py `_declaration` | `build flag '…' is already declared at {loc}` **related: "first declared here" → `declared[name]`** |
| V011 | flags.py `_declaration` | `!flag default must be 'on' or 'off', got '…'` |
| V012 | flags.py `collect_flags` | `!flag is only valid at the top level` |
| V013 | macros.py `_parameters` | `invalid macro parameter name '…'` |
| V014 | macros.py `_parameters` | `duplicate macro parameter '…'` |
| V015 | macros.py `_parameters` | `a rest parameter must be the last macro parameter` |
| V016 | macros.py `_definition` | `!defmacro requires a '::' template suite` |
| V017 | macros.py `_definition` | `!defmacro requires a macro name group` |
| V018 | macros.py `_definition` | `invalid macro name '…'` |
| V019 | macros.py `_definition` | `'!…' is reserved by TeXFlux` |
| V020 | macros.py `_definition` | `'!…' is a built-in special and cannot be redefined` |
| V021 | macros.py `_definition` | `macro '!…' conflicts with a TeXFlux standard flow macro` |
| V022 | macros.py `_definition` | `macro '!…' is already defined at {loc}` **related: "first defined here" → `defined[name].span`** |
| V023 | macros.py `_definition` | `!defmacro is only valid at the top level`（テンプレート内） |
| V024 | macros.py `_definition` | `!{import|macroimport} is not allowed inside a macro template` |
| V025 | macros.py `validate_macro_forms` | `!defmacro must be a top-level '::' definition and cannot be a '>>' segment` |
| V026 | macros.py `validate_macro_forms` | `!each requires a '::' template suite of its own` |
| V027 | macros.py `_Expander` | `!defmacro is only valid at the top level`（収集後の残存） |
| V028 | macros.py `_Expander` | `{label} requires exactly {count} required group(s)` |
| V029 | macros.py `_Expander` | `!param does not accept a suite` |
| V030 | macros.py `_Expander` | `!each requires a '::' template suite` |
| V031 | macros.py `_Expander` | `invalid !each item name '…'` |
| V032 | macros.py `_Expander` | `!{name} requires a '::' suite or a '>>' payload` |
| V033 | macros.py `_Expander` | `!{name} does not accept a ':::' sequence suite` |
| V034 | normalize.py `_desugar_stack` | `stack requires at least two segments` |
| V035 | normalize.py `_normalize_node` | `sequence entries are only valid inside a ':::' suite` |
| V036 | normalize.py `_sequence_body` | `anonymous containers do not accept '+' sequence entries` |
| V037 | normalize.py `_normalize_environment` | `environment sequence suites require a body value` |
| V038 | normalize.py `_normalize_environment` | `environment sequence suites require the final '-' entry to be the body` |
| V039 | normalize.py `_normalize_invocation` | `container values require a suite or a closed stack payload` |
| V040 | normalize.py `_normalize_invocation` | `literal brace containers require one raw header group` |
| V041 | normalize.py `_normalize_invocation` | `transparent containers do not accept header groups` |
| V042 | syntax.py `demand_text` | `{label} must be a required '{...}' group`（`demand_text`、多数の構文が共用） |
| V043 | syntax.py `sequence_entries` | `sequence suites require '-' or '+' value entries` |

### 2.4 D — `DirectiveError`（1 件）

| コード | 生成箇所 | メッセージ |
| --- | --- | --- |
| D001 | normalize.py `_normalize_special` | `unknown special directive '!…'; a literal '!' line is written '!!', and a run of them goes in a raw-mode region` |

### 2.5 E — `MacroExpansionError`（19 件 + 包み直し 2 箇所）

| コード | 生成箇所 | メッセージ |
| --- | --- | --- |
| E001 | interpolate.py `text_value` | `'…' is a rest parameter; use !each to access its values` |
| E002 | interpolate.py `text_value` | `unknown macro parameter '…'` |
| E003 | interpolate.py `text_value` | `macro parameter '…' is not a text value; use !param for structural values` |
| E004 | interpolate.py `reject_markers` | `interpolation is not allowed in {where}`（`where` は 4 通り） |
| E005 | interpolate.py `interpolate` | `unterminated !text{...}` |
| E006 | interpolate.py `interpolate` | `invalid !text parameter name '…'` |
| E007 | interpolate.py `interpolate` | `!text is only valid inside a macro template` |
| E008 | interpolate.py `interpolate` | `!param cannot be used inside a text field; use !text for text interpolation` |
| E009 | macros.py `_Expander` | `!text is only valid inside a textual field; use !param for an AST position` |
| E010 | macros.py `_Expander` | `!{param|each} is only valid inside a macro template` |
| E011 | macros.py `_Expander` | `'…' is a rest parameter; use !each to expand it` |
| E012 | macros.py `_Expander` | `unknown macro parameter '…'`（`!param`） |
| E013 | macros.py `_Expander` | `'…' is not a rest parameter; !each needs one` |
| E014 | macros.py `_Expander` | `unknown macro parameter '…'`（`!each`） |
| E015 | macros.py `_Expander` | `!each item '…' shadows a bound macro parameter` |
| E016 | macros.py `_Expander` | `recursive macro expansion detected: {chain}` |
| E017 | macros.py `_Expander` | `macro calls accept required '{...}' values only` |
| E018 | macros.py `_Expander` | `macro calls do not accept '+' sequence entries` |
| E019 | macros.py `_Expander` | `'!…' expects {signature}, got N` |

包み直し（新コードなし）: `interpolate.interpolate` は `text_value` の E001〜E003 を
`_error(error.message, span, lookup, code=error.code)` で hole の span に付け替え、
`macros._Expander._reject` は `reject_markers` の E004 を
`_error(error.message, error.span, frame, code=error.code)` で frame 付きにする。

`_error(..., frame)` で frame があるものは全て message に `; while expanding '…' called at {loc}` が付き、
**related: "called here" → `frame.call_span`** が付く。

### 2.6 M — `ModuleError`（29 件 + 包み直し 2 箇所）

| コード | 生成箇所 | メッセージ |
| --- | --- | --- |
| M001 | modules.py `resolve_module_path` | `module path must not be empty` |
| M002 | modules.py `resolve_module_path` | `module path must not contain a NUL character` |
| M003 | modules.py `resolve_module_path` | `module paths use '/' separators` |
| M004 | modules.py `resolve_module_path` | `module paths must be relative to the importing file` |
| M005 | modules.py `resolve_module_path` | `!{import|macroimport} requires a '{.tfx|.tfxm}' module; got '…'` |
| M006 | modules.py `parse_bindings` | `!import binding list must be inline text` |
| M007 | modules.py `parse_bindings` | `!import binding list is empty; omit '(...)' instead` |
| M008 | modules.py `parse_bindings` | `!import bindings are written 'flag=on', 'flag=off' or 'flag=$callerFlag'; got '…'` |
| M009 | modules.py `bind_import_flags` | `imported module '…' does not declare build flag '…'; {hint}` |
| M010 | modules.py `bind_import_flags` | `build flag '…' is bound twice; first bound at {loc}` **related: "first bound here" → `bound[binding.name]`** |
| M011 | modules.py `bind_import_flags` | `unknown build flag '…' in this module; {hint}` |
| M012 | modules.py `validate_macroimport_forms` | `!macroimport must be a top-level declaration and cannot be a '>>' segment` |
| M013 | modules.py `validate_macro_module_purity` | `'!…' is not allowed in a .tfxm macro module` |
| M014 | modules.py `validate_macro_module_purity` | `a .tfxm macro module may contain only !defmacro, !macroimport, comment lines and blank lines` |
| M015 | modules.py `_macro_import` | `!macroimport does not accept a suite` |
| M016 | modules.py `_macro_import` | `!macroimport does not accept a '(...)' list` |
| M017 | modules.py `_macro_import` | `!macroimport requires one '{path}' group` |
| M018 | modules.py `_macro_import` | `!macroimport path must be a required '{...}' group` |
| M019 | modules.py `_macro_import` | `macro module '…' is already imported at {loc}` **related: "first imported here" → `seen[path]`** |
| M020 | modules.py `resolve_macro_imports` | `!macroimport is only valid at the top level` |
| M021 | modules.py `merge_imports` | `macro '!…' is already available here, defined at {loc}` **related: "defined here" → `environment[name].span`** |
| M022 | modules.py `_ImportResolver` | `!import produces content: it does not accept a suite and cannot wrap a '>>' payload` |
| M023 | modules.py `_ImportResolver` | `!import requires exactly one '{path}' group`（2 個目の path 群） |
| M024 | modules.py `_ImportResolver` | `!import does not accept '[...]' or '<...>' groups` |
| M025 | modules.py `_ImportResolver` | `!import requires exactly one '{path}' group`（path 群なし） |
| M026 | modules.py `_ImportResolver` | `content import cycle: {chain}` |
| M027 | modules.py `CompilationSession` | `cannot read module '…': {reason}` |
| M028 | modules.py `CompilationSession` | `'!…' is not defined in {file} and is not available through its own !macroimport` |
| M029 | normalize.py `_module_guard` | `'!…' requires module compilation; use texflux.compile_with_map or the texflux CLI` |

包み直し（新コードなし）:

- `_ImportResolver._expand`: 呼び出し先で起きた `TeXFluxError` を
  `error.chained(f"{error.message}; imported from {loc}", RelatedLocation("imported from here", node.span))`
  で包む。束縛リストのエラーはこの行に書かれており span が既にここを指すので、包まない。
- `CompilationSession._importing`: マクロモジュールで起きたエラーについて、message に
  `; imported from {loc}` を積む各段で **related: "imported from here" → `site`** を同じ条件
  （`error.span.file != site.file`）で積み、`error.chained(message, *related)` を送出する
  （related が空なら元の例外をそのまま再送出する）。

---

## 3. 実装の構成

| ファイル | 役割 |
| --- | --- |
| `src/texflux/errors.py` | `RelatedLocation`、`diagnostic_line()`、`TeXFluxError(message, span, *, code, related=())`。`code` は必須キーワード引数で、省略した生成箇所は実行時 `TypeError` になる。`chained(message, *related)` は同じ型・コード・span で message と related を伸ばす。`InternalError` / `FlagError` は span も code も持たない |
| `src/texflux/modules.py` | `SourceReader` フック（下記の契約）。`CompilationSession._register` はパースより前に `LoadedSource` を記録する。`_importing` が関連位置付きで import 連鎖を包む |
| `src/texflux/diagnostics.py` | `Severity`、`Diagnostic`、`DiagnosticReport`、`overlay_reader`、`diagnose`、`serialize_diagnostics` |
| `src/texflux/interchange.py` | 外部 AST と共有する JSON のヘッダー・span・出力規約 |
| `src/texflux/cli.py` | `texflux check`。`_run` が例外を終了コードに写す（`check` は環境の失敗を 2 にする） |
| `schemas/texflux-diagnostics-v1.schema.json` | Draft 2020-12。未知 member は許容。`$defs/source`・`position`・`span` は外部 AST スキーマと同じ定義 |
| `tests/test_diagnostics.py` | エラーモデル、`diagnose`、`serialize_diagnostics` |
| `tests/test_diagnostic_codes.py` | 全生成箇所の `code=`、一意性、§2 の表との一致（重複行なし）、件数と上限値、他文書が引用するコードの実在 |
| `tests/test_diagnostics_schema.py` | JSON Schema 検証（dev extra の `jsonschema` が無ければ skip） |
| `tests/test_cli.py` `CliCheckTests` | CLI の終了コード・出力・stdin |

`SourceReader` の契約:

- 引数は `load()` が受け取った **display 綴りそのもの**（import を書いたファイルからの相対で解決済み、
  root が相対なら相対のまま）。既定の `read_source` はそれを `os.path.abspath` して開く。
- 戻り値は生バイト列。復号（UTF-8）は `load()` が行い、失敗は M027 になる。
- 「無い」は **`OSError`（`FileNotFoundError` など）で知らせる**。`OSError` / `UnicodeError` / `ValueError`
  以外の例外は span 無しで伝播する（reader は呼び出し側のコードなので握らない）。`overlay_reader` は
  overlay に無ければ `read_source` に委ねるので、この契約を自動的に満たす。
- キャッシュ（`_sources`）は `normalized_path(display)` で引く。reader は初回ロード時にだけ呼ばれる。
- `compile_root` は `load()` を通らないので、reader は root について呼ばれない（overlay の root 無視はこの帰結）。

`diagnose` はレンダリングを行わない。レンダラが投げるのは AST 不変条件違反の `TypeError` だけで、診断になるものは無い。
span の `file` が `sources` に無い場合、`serialize_diagnostics` は外部 AST と同じく `ValueError` を投げ、
CLI は exit 2 になる。到達し得るのは、マクロテンプレート内部で起きる展開エラーの定義ファイルが
同梱 prelude（`texflux:prelude`）である場合だけで、現在の prelude（テンプレートは `!param{宣言済み}` のみ、
arity は呼び出しノードで検査）では発生しない。`AGENTS.md` が prelude のパスを span / sources に出すことを
禁じているので、黙って `sources` に足すのではなく defect として大きく失敗させる。

---

## 4. 検証

```bash
python3 -W error::ResourceWarning -m unittest discover
python3 -m pip install -e '.[dev]' && python3 -m unittest tests.test_diagnostics_schema tests.test_ast_schema
```

---

## 5. 非対象（v1 で意図的に入れないもの）

- **複数エラーの収集 / エラー回復**。fail-fast のまま。将来入れる場合も本形式は変わらない（`diagnostics` が長くなるだけ）。
- **`information` / `hint` 重大度**、**`codeDescription` の URL**、**quick fix（code action）**。
- **CLI での import 先 overlay**（`--overlay` 等）。Python API のみ。
- **複数 INPUT**。1 呼び出し 1 文書。ワークスペース全体はシェル / LSP 側でループする。
- **`compile` への `--format json`**。診断は `check` で取る。
- **LSP サーバー本体**（pygls 等は runtime dependency になるので別プロジェクト）。
- **`compile_ast` / `compile_with_map` の戻り値変更**。エラー時のソース一覧が要るなら `diagnose` を使う。
