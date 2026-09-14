# TeXFlux Diagnostic API 実装設計

## ステータス

- 種別: 実装設計書（詳細設計）。**実装済み**（`feature/diagnostics`）。
- 位置づけ: 本書が確定仕様である。規範定義は
  `texflux_tex_first_dsl_v1_spec.md` §16、利用者向け説明は `README.md` と
  `doc/dsl.md` §19 に反映済み（反映先の一覧は §5）。
- §2 のコード表は `tests/test_diagnostic_codes.py` がソースと突き合わせる。
  診断を足すときは、表・件数・上限値をソースと同時に更新すること。
- 前提コミット: `41b9b9f`（2026-09-14）。
- 対象: `src/texflux/` の v1 実装。
- 本書の目的は、これだけを読めば実装者が判断を一切追加せずに実装できることである。

## 背景と決定

TeXFlux のエラーは現在、`TeXFluxError(message, span)` の例外として送出され、CLI が
`file:line:col: parse error: message` の 1 行を stderr に印字して終了コード 1 を返すだけである。
レンダリング警告 `RenderWarning` は `compile_with_map(...).rendered.warnings` にしか現れない。
VSCode 拡張や Language Server は、位置・重大度・コード・関連位置を**構造化された形**で、
しかも**例外ではなく戻り値**として受け取る必要があり、未保存バッファも扱わねばならない。

本計画は、次の 3 層を追加する。

1. **Python API** `texflux.diagnose(...) -> DiagnosticReport`（例外を投げず、読んだ全ソースと診断を返す）
2. **CLI** `texflux check`（stdin 対応、`--format json`、終了コード 0/1/2）
3. **JSON 形式** `texflux-diagnostics` v1（外部 AST と同じ規約、JSON Schema 付き）

併せて、全 132 箇所の診断に**安定した診断コード**（`P004` など）を付与し、メッセージに
埋め込まれていた副位置（"already defined at …", "imported from …"）を**構造化された関連位置**
としても持たせる。既存のメッセージ文字列は 1 文字も変えない。

利用者の決定（確認済み）:

| 論点 | 決定 |
| --- | --- |
| 関連位置情報 | 構造化する（`TeXFluxError.related`、8 箇所） |
| 診断コード | **今回付与する**（全 raise 箇所、`P/V/D/E/M/W` + 3 桁） |
| 未保存バッファの overlay | Python API に含める（`CompilationSession(reader=)`） |
| エラー件数 | fail-fast のまま。エラー最大 1 件 + 警告 N 件 |

本書は API 署名・JSON の全メンバー・全コード表・変更する関数・テスト一覧・検証手順まで
確定させてある。実装しながら本書を最新に保つこと。

---

## 0. 全体像

```text
                 diagnose(source, filename=, flags=, source_bytes=, overlays=)
                                  │
      CompilationSession(reader=overlay_reader)  ── load() が reader 経由で読む
                                  │  compile_root()      ← TeXFluxError なら捕捉
                                  │  render_with_provenance()   ← warnings
                                  ▼
      DiagnosticReport(sources=session.loaded(), diagnostics=(Diagnostic, ...))
             │                         │
   serialize_diagnostics()        report.by_file()   → LSP publishDiagnostics
             │
   texflux check INPUT --format json   (stdout, exit 0/1/2)
```

不変条件:

- `diagnose()` は `TeXFluxError` を**決して**送出しない。`FlagError`・`InternalError`・
  `RecursionError` は文書の問題ではないので**そのまま伝播**する（既存 `compile_*` と同じ分類）。
- 診断は「エラーちょうど 1 件（警告 0 件）」または「エラー 0 件・警告 0 件以上」のどちらか。
  レンダリングはコンパイル成功後にしか走らないため、これは構造上の帰結である。
- `report.sources` は常に root を先頭に含む。**root のパースが失敗しても含む**（§3.6 の `_register` 修正）。
- 位置は既存どおり 1 始まり行・1 始まり列（Unicode code point）・半開区間。JSON もそのまま出す。

---

## 1. 使用方法（利用者向け仕様）

### 1.1 Python API

新モジュール `src/texflux/diagnostics.py`。`texflux` パッケージから再公開する。

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
    kind: str                     # "parse" | "validation" | "directive" | "macro" | "module" | "render"
    code: str                     # "P004", "W001" など（§2）
    message: str                  # 既存の例外・警告メッセージそのもの
    span: SourceSpan
    related: tuple[RelatedLocation, ...] = ()

    @property
    def label(self) -> str:       # "parse error" / "warning"
    def line(self) -> str:        # "file:l:c: parse error: message [P004]"
    def __str__(self) -> str:     # == line()
    @classmethod
    def from_error(cls, error: TeXFluxError) -> Diagnostic
    @classmethod
    def from_warning(cls, warning: RenderWarning) -> Diagnostic

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

`diagnose` の振る舞いで固定しておく細部:

- `ok` は「`severity == ERROR` の診断が無い」と同値。レンダリングはコンパイル成功後にしか走らないので、
  1 つの報告がエラーと警告を同時に持つことはない。
- `sources` / `by_file()` に載るのは**読めて UTF-8 として復号できたファイル**。存在しない・読めない・
  UTF-8 でない import 先は `load()` が M028 を import 行の span で報告し、そのファイル自体は表に載らない。
  パースに失敗したファイルは（読めているので）載る。
- import 先が root を `!import` で参照し返す場合、`filename` が実際のパスと同じファイルに正規化されるときだけ
  セッションのキャッシュに当たり循環として検出される。`filename="<string>"` / `<stdin>` のままだと
  ディスク上の root が別モジュールとして読まれる（既存 `compile_text` と同じ挙動）。LSP は実パスを渡すこと。
- パスの同一視は既存 `normalized_path`（`realpath` + `normcase`）に従う。macOS の APFS は大文字小文字を
  区別しないが `normcase` は恒等なので、綴りの違う 2 つの参照は 2 つのソースになる（既知の制限、`handoff.md` §A）。

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
| `ValueError` | `filename` に NUL など（既存挙動） | 通知 |

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
| 0 | エラー診断なし（警告は 0 のまま） |
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
| `diagnostics` | コンパイラが生成した順。エラーは最大 1 件で、エラーがあれば警告は無い |
| `severity` | `"error"` / `"warning"`（閉じた列挙。追加は version bump） |
| `kind` | 小文字英字。現在 `parse` `validation` `directive` `macro` `module` `render`。consumer は未知の値も受け入れる（表示用） |
| `code` | `^[A-Z][0-9]{3}$`。§2 の表。一度公開したコードは再利用・改番しない |
| `message` | 例外/警告のメッセージそのもの（コード・位置プレフィックスを含まない） |
| `span` | 外部 AST と同一: `source` は source ID、`start`/`end` は 1 始まり行・1 始まり列（code point）、半開 `[start, end)`。`end` は別行でもよい |
| `related` | 常に存在する配列（空でもよい）。各要素は `message` と `span` |

Serialization は外部 AST と同一: UTF-8、BOM なし、`ensure_ascii=False`、compact は
`separators=(",", ":")`、末尾 LF 1 個、`--pretty` は `indent=2`。同一入力に対しバイト単位で決定的。

Versioning 規約（外部 AST §15 と同じ）: consumer は未知の object member を無視しなければならない。
member 追加は v1 内、意味変更・必須 member の削除・位置意味の変更・`severity` 値の追加は version bump。

機械可読定義: `schemas/texflux-diagnostics-v1.schema.json`（§3.11）。

### 1.4 テキスト形式

1 診断 = 1 行 + 関連位置ごとに 1 行。

```text
{file}:{line}:{column}: {label}: {message} [{code}]
  {file}:{line}:{column}: note: {related.message}
```

- `label` はエラーなら `"{kind} error"`（`parse error` 等、既存どおり）、警告なら `warning`。
- `[{code}]` の追加により **`compile` の stderr も同じ形になる**（`TeXFluxError.diagnostic()` /
  `RenderWarning.diagnostic()` が単一の `diagnostic_line()` を使うため）。`compile` は `note:` 行を出さない
  （メッセージが既に位置を含む）。`check` の text 形式だけが `note:` 行を出す。
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

- 形式: 種別 1 文字 + 3 桁。`P` parse、`V` validation、`D` directive、`E` macro expansion、`M` module、`W` warning。
  `kind` との対応: P→parse、V→validation、D→directive、E→macro、M→module、W→render。
- **1 生成箇所 = 1 コード**。同じメッセージでも生成箇所が違えば別コード（例: P005/P007）。
  1 箇所が f-string で複数の文言を出す場合は 1 コード（例: P016、E004、M005）。
- 番号は初回付与時にファイル名アルファベット順→行順で振った。**以後は改番しない**。新規箇所は
  その種別の末尾番号 + 1。削除されたコードは欠番のまま残す。
- 既存の例外を別の例外に作り直す箇所は 5 つあり、扱いを次のとおり固定する。
  - **内側のコードを引き継ぐ（新コードなし）**: `macros._Expander._reject`（macros.py:470、`reject_markers` の E004 を frame 付きに）、
    `interpolate.interpolate` の `text_value` 包み（interpolate.py:107、E001〜E003 を hole の span に）、
    `CompilationSession._imported`（modules.py:770）と `_ImportResolver._expand`（modules.py:636）の `error.chained(...)`。
    E001〜E004 は**この転送経由でしか出力に現れない**が、自分のコードで現れる。
  - **新コードを振る**: parser.py:1062 の `raise ParseError("explicit sequence entries require one balanced group", error.span) from None`
    はメッセージを置き換える別の条件なので P036。`scan_group` の P003〜P009 はヘッダー走査経路から従来どおり現れる。
- M022（modules.py:499）は `load_standard_macros` が `InternalError` に変換するため出力に到達しない。表には載せ、到達不能と注記する。
- テスト（§4.2）が「全生成箇所に `code=` がある」「リテラルが一意」「本表と一致」を機械的に検査する。

下表の行番号は 2026-09-14 時点（HEAD `41b9b9f`）の位置で、実装時の探索補助。同定は「ファイル・関数・メッセージ」で行う。

### 2.2 P — `ParseError`（`parser.py`、37 件）

| コード | 行 | 関数 | メッセージ |
| --- | --- | --- | --- |
| P001 | 115 | `_raw_regions` | `'!END_RAW_MODE' has no matching '!BEGIN_RAW_MODE'` |
| P002 | 124 | `_raw_regions` | `'!BEGIN_RAW_MODE' is not closed by '!END_RAW_MODE'` |
| P003 | 157 | `scan_group` | `unclosed overlay group` |
| P004 | 172 | `scan_group` | `unclosed required group` |
| P005 | 186 | `scan_group`（`[` 走査中） | `mismatched group delimiter` |
| P006 | 195 | `scan_group` | `unclosed optional group` |
| P007 | 210 | `scan_group`（`(` 走査中） | `mismatched group delimiter` |
| P008 | 219 | `scan_group` | `unclosed binding list` |
| P009 | 222 | `scan_group` | `invalid group opener` |
| P010 | 279 | `HeaderScanner.scan` | `empty structural header` |
| P011 | 311 | `HeaderScanner` | `unexpected token in structural header` |
| P012 | 333 | `HeaderScanner` | `trailing token after suite marker` |
| P013 | 343 | `HeaderScanner` | `unexpected token in structural header` |
| P014 | 352 | `HeaderScanner` | `stack separator requires surrounding spaces` |
| P015 | 383 | `HeaderScanner` | `missing structural segment` |
| P016 | 394 | `HeaderScanner` | `structural header must start with '\', '@', or '!'` / `each stack segment must start with '\', '@', or '!'` |
| P017 | 431 | `HeaderScanner` | `invalid structural name` |
| P018 | 449 | `HeaderScanner` | `invalid structural name` |
| P019 | 451 | `HeaderScanner` | `'!BEGIN_RAW_MODE' must stand alone on its own line`（END も同文） |
| P020 | 474 | `HeaderScanner` | `a special's '(...)' list must follow its groups` |
| P021 | 479 | `HeaderScanner` | `a special accepts at most one '(...)' list` |
| P022 | 483 | `HeaderScanner` | `unexpected token after structural name or group` |
| P023 | 560 | `_Parser._reject_tab` | `tab characters are not allowed` |
| P024 | 720 | `_Parser`（`!|`） | `'!|' must be followed by one space or end the line` |
| P025 | 753 | `_Parser._indent_error` | `invalid structural indentation` |
| P026 | 820 | `_Parser` | `environment directives require a suite marker ':' or '::'` |
| P027 | 827 | `_Parser` | `indented lines require a suite marker ':' or '::'` |
| P028 | 844 | `_Parser` | `stack separator needs a following segment` |
| P029 | 850 | `_Parser` | `stack continuation requires the next line at the same indentation` |
| P030 | 896 | `_Parser` | `sequence suite entries require four-space indentation` |
| P031 | 909 | `_Parser` | `sequence suites require at least one '-' or '+' value entry` |
| P032 | 935 | `_Parser` | `sequence entries must start at suite indentation` |
| P033 | 941 | `_Parser` | `sequence suites require '-' or '+' value entries` |
| P034 | 1036 | `_Parser._sequence_entry` | `explicit sequence entries require one '{...}', '[...]', or '<...>' group` |
| P035 | 1051 | `_Parser._sequence_entry` | `explicit sequence entries require opaque raw text` |
| P036 | 1062 | `_Parser._sequence_entry` | `explicit sequence entries require one balanced group` |
| P037 | 1067 | `_Parser._sequence_entry` | `explicit sequence entries require exactly one group` |

旧文書ラベル対応: R01→P002、R02→P001、R03→P019、R04→P025、R05→P023、L01→P024、L02→P017/P018（`!|` を `>>` セグメントに置いたとき到達する側。実装時に確認）、L03→P023。

### 2.3 V — `ValidationError`（43 件）

| コード | ファイル:行 | メッセージ |
| --- | --- | --- |
| V001 | flags.py:95 | `!{when|unless} modifier must be '[and]' or '[or]', got '[…]'` |
| V002 | flags.py:114 | `unknown build flag '…'; {declared_flags_hint}` |
| V003 | flags.py:119 | `build flag '…' is listed twice` |
| V004 | flags.py:136 | `!{when|unless} requires at least one '{flag}' group` |
| V005 | flags.py:142 | `!{when|unless} requires an '[and]' or '[or]' modifier to combine N flags` |
| V006 | flags.py:167 | `!flag must be a top-level declaration and cannot be a '>>' segment` |
| V007 | flags.py:181 | `!flag does not accept a suite` |
| V008 | flags.py:183 | `!flag requires a name group and an 'on' or 'off' default group` |
| V009 | flags.py:190 | `invalid build flag name '…'` |
| V010 | flags.py:195 | `build flag '…' is already declared at {loc}` **related: "first declared here" → `declared[name]`** |
| V011 | flags.py:203 | `!flag default must be 'on' or 'off', got '…'` |
| V012 | flags.py:250 | `!flag is only valid at the top level` |
| V013 | macros.py:135 | `invalid macro parameter name '…'` |
| V014 | macros.py:140 | `duplicate macro parameter '…'` |
| V015 | macros.py:142 | `a rest parameter must be the last macro parameter` |
| V016 | macros.py:159 | `!defmacro requires a ':' template suite` |
| V017 | macros.py:164 | `!defmacro requires a macro name group` |
| V018 | macros.py:169 | `invalid macro name '…'` |
| V019 | macros.py:171 | `'!…' is reserved by TeXFlux` |
| V020 | macros.py:173 | `'!…' is a built-in special and cannot be redefined` |
| V021 | macros.py:181 | `macro '!…' conflicts with a TeXFlux standard flow macro` |
| V022 | macros.py:187 | `macro '!…' is already defined at {loc}` **related: "first defined here" → `defined[name].span`** |
| V023 | macros.py:195 | `!defmacro is only valid at the top level`（テンプレート内） |
| V024 | macros.py:202 | `!{import|macroimport} is not allowed inside a macro template` |
| V025 | macros.py:232 | `!defmacro must be a top-level ':' definition and cannot be a '>>' segment` |
| V026 | macros.py:242 | `!each requires a ':' template suite of its own` |
| V027 | macros.py:389 | `!defmacro is only valid at the top level`（収集後の残存） |
| V028 | macros.py:520 | `{label} requires exactly {count} required group(s)` |
| V029 | macros.py:552 | `!param does not accept a suite` |
| V030 | macros.py:574 | `!each requires a ':' template suite` |
| V031 | macros.py:577 | `invalid !each item name '…'` |
| V032 | macros.py:624 | `!{name} requires a ':' suite or a '>>' payload` |
| V033 | macros.py:629 | `!{name} does not accept a '::' sequence suite` |
| V034 | normalize.py:58 | `stack requires at least two segments` |
| V035 | normalize.py:146 | `sequence entries are only valid inside a '::' suite` |
| V036 | normalize.py:224 | `anonymous containers do not accept '+' sequence entries` |
| V037 | normalize.py:279 | `environment sequence suites require a body value` |
| V038 | normalize.py:284 | `environment sequence suites require the final '-' entry to be the body` |
| V039 | normalize.py:303 | `container values require a suite or a closed stack payload` |
| V040 | normalize.py:336 | `literal brace containers require one raw header group` |
| V041 | normalize.py:343 | `transparent containers do not accept header groups` |
| V042 | syntax.py:100 | `{label} must be a required '{...}' group`（`demand_text`、多数の構文が共用） |
| V043 | syntax.py:145 | `sequence suites require '-' or '+' value entries` |

旧文書ラベル対応: R06→V019。

### 2.4 D — `DirectiveError`（1 件）

| コード | ファイル:行 | メッセージ |
| --- | --- | --- |
| D001 | normalize.py:359 | `unknown special directive '!…'` |

### 2.5 E — `MacroExpansionError`（19 件 + 包み直し 2 箇所）

| コード | ファイル:行 | メッセージ |
| --- | --- | --- |
| E001 | interpolate.py:27 (`text_value`) | `'…' is a rest parameter; use !each to access its values` |
| E002 | interpolate.py:32 (`text_value`) | `unknown macro parameter '…'` |
| E003 | interpolate.py:35 (`text_value`) | `macro parameter '…' is not a text value; use !param for structural values` |
| E004 | interpolate.py:49 (`reject_markers`) | `interpolation is not allowed in {where}`（`where` は 4 通り。旧 T10〜T13） |
| E005 | interpolate.py:93 | `unterminated !text{...}` |
| E006 | interpolate.py:100 | `invalid !text parameter name '…'` |
| E007 | interpolate.py:102 | `!text is only valid inside a macro template` |
| — | interpolate.py:107 | **包み直し**: `_error(error.message, span, lookup, code=error.code)`（E001〜E003 を引き継ぐ） |
| E008 | interpolate.py:112 | `!param cannot be used inside a text field; use !text for text interpolation` |
| E009 | macros.py:396 | `!text is only valid inside a textual field; use !param for an AST position` |
| — | macros.py:470 (`_reject`) | **包み直し**: `_error(error.message, error.span, frame, code=error.code)`（E004 を引き継ぐ） |
| E010 | macros.py:539 | `!{param|each} is only valid inside a macro template` |
| E011 | macros.py:556 | `'…' is a rest parameter; use !each to expand it` |
| E012 | macros.py:562 | `unknown macro parameter '…'`（`!param`） |
| E013 | macros.py:583 | `'…' is not a rest parameter; !each needs one` |
| E014 | macros.py:589 | `unknown macro parameter '…'`（`!each`） |
| E015 | macros.py:595 | `!each item '…' shadows a bound macro parameter` |
| E016 | macros.py:653 | `recursive macro expansion detected: {chain}` |
| E017 | macros.py:680 | `macro calls accept required '{...}' values only` |
| E018 | macros.py:699 | `macro calls do not accept '+' sequence entries` |
| E019 | macros.py:723 | `'!…' expects {signature}, got N` |

`_error(..., frame)` で frame があるものは全て message に `; while expanding '…' called at {loc}` が付き、
**related: "called here" → `frame.call_span`** が付く（§3.4）。

旧文書ラベル対応: T01→E009、T02→E007、T03→E002、T04→E001、T05→E003、T06→E006、T07→E005、T08→E008、T10〜T13→E004。

### 2.6 M — `ModuleError`（30 件 + 包み直し 1 箇所）

| コード | ファイル:行 | メッセージ |
| --- | --- | --- |
| M001 | modules.py:155 | `module path must not be empty` |
| M002 | modules.py:160 | `module path must not contain a NUL character` |
| M003 | modules.py:162 | `module paths use '/' separators` |
| M004 | modules.py:164 | `module paths must be relative to the importing file` |
| M005 | modules.py:172 | `!{import|macroimport} requires a '{.tfx|.tfxm}' module; got '…'` |
| M006 | modules.py:225 | `!import binding list must be inline text` |
| M007 | modules.py:227 | `!import binding list is empty; omit '(...)' instead` |
| M008 | modules.py:247 | `!import bindings are written 'flag=on', 'flag=off' or 'flag=$callerFlag'; got '…'` |
| M009 | modules.py:286 | `imported module '…' does not declare build flag '…'; {hint}` |
| M010 | modules.py:292 | `build flag '…' is bound twice; first bound at {loc}` **related: "first bound here" → `bound[binding.name]`** |
| M011 | modules.py:298 | `unknown build flag '…' in this module; {hint}` |
| M012 | modules.py:331 | `!macroimport must be a top-level declaration and cannot be a '>>' segment` |
| M013 | modules.py:350 | `'!…' is not allowed in a .tfxm macro module` |
| M014 | modules.py:364 | `a .tfxm macro module may contain only !defmacro, !macroimport, comment lines and blank lines` |
| M015 | modules.py:382 | `!macroimport does not accept a suite` |
| M016 | modules.py:388 | `!macroimport does not accept a '(...)' list` |
| M017 | modules.py:393 | `!macroimport requires one '{path}' group` |
| M018 | modules.py:396 | `!macroimport path must be a required '{...}' group` |
| M019 | modules.py:405 | `macro module '…' is already imported at {loc}` **related: "first imported here" → `seen[path]`** |
| M020 | modules.py:444 | `!macroimport is only valid at the top level` |
| M021 | modules.py:466 | `macro '!…' is already available here, defined at {loc}` **related: "defined here" → `environment[name].span`** |
| M022 | modules.py:499 | `the bundled standard macro module imports no other module`（**到達不能**: `load_standard_macros` が `InternalError` に変換する） |
| M023 | modules.py:570 | `!import produces content: it does not accept a suite and cannot wrap a '>>' payload` |
| M024 | modules.py:583 | `!import requires exactly one '{path}' group`（2 個目の path 群） |
| M025 | modules.py:589 | `!import does not accept '[...]' or '<...>' groups` |
| M026 | modules.py:594 | `!import requires exactly one '{path}' group`（path 群なし） |
| M027 | modules.py:619 | `content import cycle: {chain}` |
| — | modules.py:636 | **包み直し**: `error.chained(f"{error.message}; imported from {loc}", RelatedLocation("imported from here", node.span))` |
| — | modules.py:770 | **包み直し**: `_imported()` の `return type(error)(message, error.span)` → `error.chained(message, *related)`（related が空なら元のオブジェクトを返す） |
| M028 | modules.py:738 | `cannot read module '…': {reason}` |
| M029 | modules.py:866 | `'!…' is not defined in {file} and is not available through its own !macroimport` |
| M030 | normalize.py:385 | `'!…' requires module compilation; use texflux.compile_with_map or the texflux CLI` |

`CompilationSession._imported()`（modules.py:746）は message に `; imported from {loc}` を積む各段で
**related: "imported from here" → `site`** を同じ条件（`error.span.file != site.file`）で積み、
`error.chained(message, *related)` を返す。

旧文書ラベル対応（`doc/module-system.md` §13）: M01→M001、M01'→M002、M02→M003、M03→M004、M04/M05→M005、M06→M028、M07→M023、M08→M024/M026、M09→M025、M10→M007、M11→M008、M12→M009、M13→M010、M14→M011、M15→M027、M16→M015、M17→M017、M18→M016、M19→M018、M20→M019、M21→M020、M22→M012、M23→M014、M24→M013、M25→M021、M26→M029、M27→M030。新規行: M006、M022。

### 2.7 W — `RenderWarning`（2 件）

| コード | ファイル:行 | メッセージ |
| --- | --- | --- |
| W001 | render.py:318 (`_close_hugged`) | `value ends with a comment line, so what follows it stays on its own line` |
| W002 | render.py:325 (`_close_hugged`) | `value ends with a line containing '%', so the closing brace and whatever follows are commented out` |

---

## 3. 実装詳細（ファイル別）

### 3.1 `src/texflux/errors.py`

```python
from typing import ClassVar, Self
from dataclasses import dataclass
from .ast import SourceSpan


@dataclass(frozen=True, slots=True)
class RelatedLocation:
    """A second place a diagnostic points at, such as the first definition."""

    message: str
    span: SourceSpan


def diagnostic_line(span: SourceSpan, label: str, message: str, code: str) -> str:
    """The one-line form every diagnostic prints: ``file:line:col: label: message [CODE]``."""

    return f"{span.location}: {label}: {message} [{code}]"


class TeXFluxError(Exception):
    #: The category a diagnostic reports; subclasses override it.
    kind: ClassVar[str] = "texflux"

    def __init__(
        self,
        message: str,
        span: SourceSpan,
        *,
        code: str,
        related: tuple[RelatedLocation, ...] = (),
    ):
        self.message = message
        self.span = span
        self.code = code
        self.related = related
        super().__init__(message)

    @property
    def error_kind(self) -> str:           # 既存 API を保つ: "parse error" など
        return f"{self.kind} error"

    def diagnostic(self) -> str:
        return diagnostic_line(self.span, self.error_kind, self.message, self.code)

    def __str__(self) -> str:
        return self.diagnostic()

    def chained(self, message: str, *related: RelatedLocation) -> Self:
        """The same error with a longer message and more related locations.

        Code, span and existing related locations are kept, so an import
        chain can be appended without losing what the callee reported.
        """

        return type(self)(message, self.span, code=self.code, related=self.related + related)


class ParseError(TeXFluxError):           kind = "parse"
class ValidationError(TeXFluxError):      kind = "validation"
class DirectiveError(TeXFluxError):       kind = "directive"
class MacroExpansionError(TeXFluxError):  kind = "macro"
class ModuleError(TeXFluxError):          kind = "module"   # docstring は現状維持
```

- `code` は**必須のキーワード引数**。省略した生成箇所は実行時 `TypeError` になり、テストで即座に露見する。
- `InternalError` / `FlagError` は変更しない（span も code も持たない）。
- `__all__` に `RelatedLocation`, `diagnostic_line` を追加。

### 3.2 `src/texflux/render.py`

```python
from .errors import diagnostic_line

@dataclass(frozen=True, slots=True)
class RenderWarning:
    kind: ClassVar[str] = "render"
    message: str
    span: SourceSpan
    code: str

    def diagnostic(self) -> str:
        return diagnostic_line(self.span, "warning", self.message, self.code)

class MappedEmitter:
    def warn(self, message: str, span: SourceSpan, *, code: str) -> None:
        self._warnings.append(RenderWarning(message, span, code))
```

`_close_hugged` の 2 呼び出しに `code="W001"` / `code="W002"`。それ以外の render.py は無変更。
（`ClassVar` は dataclass のフィールドにならないので `slots=True` と共存する。）

### 3.3 `src/texflux/parser.py`

- `HeaderScanner._error(self, message, offset=0, *, code) -> ParseError`:
  `return ParseError(message, self._span(offset, end), code=code)`。呼び出し 13 箇所に `code="P0xx"` を追加。
- `_Parser._indent_error`: `code="P025"`。
- モジュール関数 `scan_group` / `_raw_regions` 内の直接 `raise ParseError(...)` と `_Parser` 内の直接 raise:
  §2.2 のとおり `code=` を追加。
- parser.py:1062 の `raise ParseError("explicit sequence entries require one balanced group", error.span) from None`
  はメッセージを**置き換える**ので新コード P036（`chained` ではない）。

### 3.4 `src/texflux/macros.py`

```python
def _error(
    message: str,
    span: SourceSpan,
    frame: _Frame | None,
    *,
    code: str,
) -> MacroExpansionError:
    """Build a diagnostic that names the expansion chain when inside one."""

    if frame is None:
        return MacroExpansionError(message, span, code=code)
    return MacroExpansionError(
        f"{message}; {frame.where()}",
        span,
        code=code,
        related=(RelatedLocation("called here", frame.call_span),),
    )
```

- `_error(...)` 呼び出し全 11 箇所（§2.5）に `code=`。`_reject`（470）は `code=error.code`。
- 直接の `ValidationError(...)` 21 箇所、`MacroExpansionError(...)`（539）に `code=`。
- macros.py:186-190（V022）: `related=(RelatedLocation("first defined here", defined[name].span),)`。
  `previous = defined[name].span.location` はそのまま使う（メッセージ不変）。
- `_Frame.where()` は無変更。

### 3.5 `src/texflux/interpolate.py`

- `text_value` の 3 箇所、`reject_markers` に `code=`。
- `interpolate()` 内の `_error(...)` 4 箇所に `code=`、`text_value` の包み直し（107）は `code=error.code`。

### 3.6 `src/texflux/modules.py`

読み込みフック:

```python
from collections.abc import Callable

#: Reads one module's bytes given the display spelling ``load`` was asked for.
SourceReader: TypeAlias = Callable[[str], bytes]


def read_source(display: str) -> bytes:
    """The default reader: the file at ``display``, resolved against the cwd."""

    with open(os.path.abspath(display), "rb") as stream:
        return stream.read()


class CompilationSession:
    def __init__(
        self,
        registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
        *,
        reader: SourceReader | None = None,
    ):
        ...
        self._reader = read_source if reader is None else reader
```

`load()`（716-742）: `with open(...)` を `data = self._reader(display)` に置き換える。例外の握り方
（`OSError, UnicodeError, ValueError` → `ModuleError(..., code="M028")`）は不変。

reader の契約（`doc/diagnostics.md` にも書く）:

- 引数は `load()` が受け取った **display 綴りそのもの**（import を書いたファイルからの相対で解決済み、
  root が相対なら相対のまま）。既定の `read_source` はそれを `os.path.abspath` して開く。
- 戻り値は生バイト列。復号（UTF-8）は従来どおり `load()` が行い、失敗は M028 になる。
- 「無い」は **`OSError`（`FileNotFoundError` など）で知らせる**。`OSError` / `UnicodeError` / `ValueError`
  以外の例外は span 無しで伝播する（reader は呼び出し側のコードなので握らない）。`overlay_reader` は
  overlay に無ければ `read_source` に委ねるので、この契約を自動的に満たす。
- キャッシュ（`_sources`）は `normalized_path(display)` で引く。reader は初回ロード時にだけ呼ばれる。
- `compile_root` は `load()` を通らないので、reader は root について呼ばれない（overlay の root 無視はこの帰結）。

`_register()` の順序変更:

```python
def _register(self, display, kind, data, text) -> ModuleSource:
    # Record the read before parsing: a diagnostic report lists every file
    # the compilation read, including one whose parse is what failed.
    self._loaded.append(LoadedSource(display, os.path.abspath(display), data))
    source = ModuleSource(normalized_path(display), display, kind, data, parse(text, display))
    self._sources[source.path] = source
    return source
```

成功経路の `loaded()` は従来と同一（外部 AST・ソースマップの出力は変わらない）。

関連位置と包み直し:

- 292（M010）: `related=(RelatedLocation("first bound here", bound[binding.name]),)`
- 405（M019）: `related=(RelatedLocation("first imported here", seen[path]),)`
- 466（M021）: `related=(RelatedLocation("defined here", environment[name].span),)`
- 636（`_ImportResolver._expand`）:

  ```python
  raise error.chained(
      f"{error.message}; imported from {node.span.location}",
      RelatedLocation("imported from here", node.span),
  ) from error
  ```

- `_imported()`（746-770）: ループ内で `message` を伸ばす同じ分岐で
  `related.append(RelatedLocation("imported from here", site))`。
  末尾は `return error if not related else error.chained(message, *related)`。
  呼び出し側の `if chained is error: raise` / `raise chained from error` は無変更
  （`test_a_chained_error_keeps_its_real_root_cause` が `__cause__` を検査する）。
- 残る 28 箇所の `ModuleError(...)` に `code=`。
- `__all__` に `SourceReader`, `read_source` を追加。

### 3.7 `src/texflux/flags.py` / `normalize.py` / `syntax.py`

- flags.py: 12 箇所に `code=`。195（V010）に `related=(RelatedLocation("first declared here", declared[name]),)`。
- normalize.py: `ValidationError` 8 箇所、`DirectiveError`（D001）、`_module_guard` の `ModuleError`（M030）に `code=`。
- syntax.py: 2 箇所に `code=`。

### 3.8 `src/texflux/diagnostics.py`（新規）

```python
"""Structured diagnostics for editors and language servers.

``diagnose`` runs the same compilation ``compile_with_map`` does, but returns
every file it read and every diagnostic it produced instead of raising the
first error. Nothing here interprets a document: a ``Diagnostic`` is one
``TeXFluxError`` or one ``RenderWarning`` with the same message and span.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os

from . import __version__
from .ast import SourceSpan
from .errors import RelatedLocation, TeXFluxError, diagnostic_line
from .flags import Flags
from .modules import CompilationSession, SourceReader, read_source
from .paths import normalized_path
from .render import LoadedSource, RenderWarning, render_with_provenance


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    kind: str
    code: str
    message: str
    span: SourceSpan
    related: tuple[RelatedLocation, ...] = ()

    @property
    def label(self) -> str:
        return "warning" if self.severity is Severity.WARNING else f"{self.kind} error"

    def line(self) -> str:
        return diagnostic_line(self.span, self.label, self.message, self.code)

    def __str__(self) -> str:
        return self.line()

    @classmethod
    def from_error(cls, error: TeXFluxError) -> "Diagnostic":
        return cls(Severity.ERROR, error.kind, error.code, error.message, error.span, error.related)

    @classmethod
    def from_warning(cls, warning: RenderWarning) -> "Diagnostic":
        return cls(Severity.WARNING, warning.kind, warning.code, warning.message, warning.span)


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    sources: tuple[LoadedSource, ...]
    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("a diagnostic report needs at least the root source")

    @property
    def root(self) -> LoadedSource:
        return self.sources[0]

    @property
    def ok(self) -> bool:
        return all(d.severity is not Severity.ERROR for d in self.diagnostics)

    def by_file(self) -> dict[str, tuple[Diagnostic, ...]]:
        grouped: dict[str, list[Diagnostic]] = {source.file: [] for source in self.sources}
        for diagnostic in self.diagnostics:
            grouped.setdefault(diagnostic.span.file, []).append(diagnostic)
        return {file: tuple(items) for file, items in grouped.items()}


def overlay_reader(overlays: Mapping[str, str | bytes]) -> SourceReader:
    """A reader that serves unsaved editor buffers before touching the disk."""

    table = {
        normalized_path(path): text.encode("utf-8") if isinstance(text, str) else text
        for path, text in overlays.items()
    }

    def read(display: str) -> bytes:
        data = table.get(normalized_path(display))
        return read_source(display) if data is None else data

    return read


def diagnose(
    source: str,
    *,
    filename: str = "<string>",
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
    overlays: Mapping[str, str | bytes] | None = None,
) -> DiagnosticReport:
    """Report every diagnostic one compilation produces, without raising.

    ``FlagError``, ``InternalError`` and ``RecursionError`` still propagate:
    none of them describes a place in the document.
    """

    session = CompilationSession(reader=overlay_reader(overlays) if overlays else None)
    data = source.encode("utf-8") if source_bytes is None else source_bytes
    try:
        document = session.compile_root(source, filename=filename, data=data, flags=flags)
    except TeXFluxError as error:
        return DiagnosticReport(session.loaded(), (Diagnostic.from_error(error),))
    rendered = render_with_provenance(document)
    return DiagnosticReport(
        session.loaded(),
        tuple(Diagnostic.from_warning(warning) for warning in rendered.warnings),
    )


def serialize_diagnostics(report: DiagnosticReport, *, pretty: bool = False) -> str:
    """Serialize ``texflux-diagnostics`` v1 as deterministic JSON with one final LF."""

    ids = {source.file: index for index, source in enumerate(report.sources)}
    if len(ids) != len(report.sources):
        raise ValueError("diagnostic report sources must have unique file names")

    def span(value: SourceSpan) -> dict:
        if value.file not in ids:
            raise ValueError(f"diagnostic span names a file that was not loaded: {value.file}")
        return {
            "source": ids[value.file],
            "start": {"line": value.start.line, "column": value.start.column},
            "end": {"line": value.end.line, "column": value.end.column},
        }

    payload = {
        "format": "texflux-diagnostics",
        "version": 1,
        "producer": {"name": "texflux", "version": __version__},
        "root": 0,
        "sources": [
            {"id": index, "file": source.file.replace(os.sep, "/"),
             "sha256": hashlib.sha256(source.data).hexdigest()}
            for index, source in enumerate(report.sources)
        ],
        "diagnostics": [
            {
                "severity": diagnostic.severity,
                "kind": diagnostic.kind,
                "code": diagnostic.code,
                "message": diagnostic.message,
                "span": span(diagnostic.span),
                "related": [
                    {"message": related.message, "span": span(related.span)}
                    for related in diagnostic.related
                ],
            }
            for diagnostic in report.diagnostics
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":")) + "\n"


__all__ = ["Diagnostic", "DiagnosticReport", "Severity", "diagnose",
           "overlay_reader", "serialize_diagnostics"]
```

`diagnose` は `source_comments` を取らない（常に `False` でレンダリングする。警告の有無は変わらない）。

span の `file` が `sources` に無い場合、`serialize_diagnostics` は外部 AST（`external_ast.py:41-42`）と同じく
`ValueError` を投げ、CLI は exit 2 になる。到達し得るのは、マクロテンプレート内部で起きる展開エラー
（macros.py:396・520〜723、interpolate.py:93〜116 はテンプレート側ノードの span を使う）の定義ファイルが
同梱 prelude（`texflux:prelude`）である場合だけで、現在の prelude（テンプレートは `!param{宣言済み}` のみ、
arity は呼び出しノードで検査）では発生しない。AGENTS.md が prelude のパスを span / sources に出すことを
禁じているので、黙って `sources` に足すのではなく defect として大きく失敗させる。

### 3.9 `src/texflux/__init__.py`

`from .errors import RelatedLocation`、`from .diagnostics import Diagnostic, DiagnosticReport, Severity, diagnose, serialize_diagnostics`、
`from .modules import SourceReader, read_source` を追加し、`__all__` をアルファベット順に更新。
`diagnostics.py` は `from . import __version__` を使うので、`__init__.py` では **`__version__` 定義の後**、
`external_ast` と同じ位置に import する。

### 3.10 `src/texflux/cli.py`

```python
from . import compile_ast, compile_with_map, diagnose, serialize_ast, serialize_diagnostics

def _parser():
    ...
    check_parser = commands.add_parser("check", help="report diagnostics without writing output")
    check_parser.add_argument("input", metavar="INPUT", help="a .tfx file, or '-' for standard input")
    check_parser.add_argument("--format", choices=("text", "json"), default="text")
    check_parser.add_argument("--pretty", action="store_true")
    check_parser.add_argument("--stdin-filename", metavar="PATH")
    for frontend in (compile_parser, ast_parser):
        frontend.add_argument("input", metavar="INPUT")
        frontend.add_argument("-o", "--output", required=True, metavar="OUTPUT")
    for frontend in (compile_parser, ast_parser, check_parser):
        frontend.add_argument("--flag", dest="flags", action="append", default=[], metavar="NAME[=on|off]")
    ...

def _fail(message: str, status: int = 1) -> int:
    print(f"texflux: {message}", file=sys.stderr)
    return status

def _write_stdout(text: str) -> None:
    """Write bytes to bypass platform encoding and newline translation."""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        sys.stdout.write(text)
```

（`_ast` の stdout 書き出しも `_write_stdout` に置き換える。挙動は同一。）

```python
def _check(args: argparse.Namespace) -> int:
    """Report one document's diagnostics on stdout; write nothing else."""

    if args.pretty and args.format != "json":
        return _fail("--pretty requires --format json", 2)
    if args.input == "-":
        filename = args.stdin_filename if args.stdin_filename is not None else "<stdin>"
        if args.stdin_filename is not None and Path(filename).suffix != ".tfx":
            return _fail("--stdin-filename must have a .tfx extension", 2)
    else:
        if args.stdin_filename is not None:
            return _fail("--stdin-filename requires INPUT '-'", 2)
        if Path(args.input).suffix != ".tfx":
            return _fail("input must have a .tfx extension", 2)
        filename = args.input
    try:
        source_bytes = sys.stdin.buffer.read() if args.input == "-" else Path(filename).read_bytes()
        report = diagnose(
            source_bytes.decode("utf-8"),
            filename=filename,
            flags=_flags(args.flags),
            source_bytes=source_bytes,
        )
        if args.format == "json":
            _write_stdout(serialize_diagnostics(report, pretty=args.pretty))
        else:
            lines: list[str] = []
            for diagnostic in report.diagnostics:
                lines.append(diagnostic.line())
                lines.extend(f"  {r.span.location}: note: {r.message}" for r in diagnostic.related)
            if lines:
                _write_stdout("\n".join(lines) + "\n")
    except (FlagError, InternalError, ValueError, OSError) as error:
        return _fail(str(error), 2)
    except RecursionError:
        return _fail(f"{filename}: input nests too deeply to compile", 2)
    return 0 if report.ok else 1
```

`main()` の `match` に `case ("check", _): return _check(args)` を追加。
`_compile` / `_ast` の終了コードは変更しない（既存テストが 1 を固定している）。
`compile` の stderr は `error.diagnostic()` の変更により自動的に ` [P004]` 付きになる。

### 3.11 `schemas/texflux-diagnostics-v1.schema.json`（新規）

外部 AST スキーマと同じ Draft 2020-12。未知 member は許容（`additionalProperties` を書かない）。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "TeXFlux Diagnostics v1",
  "description": "Diagnostics emitted by `texflux check --format json` and texflux.serialize_diagnostics. Unknown object members are allowed. `kind` and the code's letter are open sets; `severity` is closed.",
  "$comment": "Source IDs must match load-order indices, source files must be unique, every span.source must resolve, and span.start must not follow span.end. These cross-value invariants require consumer checks beyond JSON Schema.",
  "type": "object",
  "required": ["format", "version", "producer", "root", "sources", "diagnostics"],
  "properties": {
    "format": {"const": "texflux-diagnostics"},
    "version": {"type": "integer", "const": 1},
    "producer": {"type": "object", "required": ["name", "version"],
                 "properties": {"name": {"const": "texflux"}, "version": {"type": "string"}}},
    "root": {"type": "integer", "const": 0},
    "sources": {"type": "array", "minItems": 1,
                "prefixItems": [{"allOf": [{"$ref": "#/$defs/source"}, {"properties": {"id": {"const": 0}}}]}],
                "items": {"$ref": "#/$defs/source"}},
    "diagnostics": {"type": "array", "items": {"$ref": "#/$defs/diagnostic"}}
  },
  "$defs": {
    "source": {"type": "object", "required": ["id", "file", "sha256"],
               "properties": {"id": {"type": "integer", "minimum": 0}, "file": {"type": "string"},
                              "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
    "position": {"type": "object", "required": ["line", "column"],
                 "properties": {"line": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1}}},
    "span": {"type": "object", "required": ["source", "start", "end"],
             "properties": {"source": {"type": "integer", "minimum": 0},
                            "start": {"$ref": "#/$defs/position"}, "end": {"$ref": "#/$defs/position"}}},
    "related": {"type": "object", "required": ["message", "span"],
                "properties": {"message": {"type": "string"}, "span": {"$ref": "#/$defs/span"}}},
    "diagnostic": {"type": "object",
                   "required": ["severity", "kind", "code", "message", "span", "related"],
                   "properties": {"severity": {"enum": ["error", "warning"]},
                                  "kind": {"type": "string", "pattern": "^[a-z]+$"},
                                  "code": {"type": "string", "pattern": "^[A-Z][0-9]{3}$"},
                                  "message": {"type": "string"},
                                  "span": {"$ref": "#/$defs/span"},
                                  "related": {"type": "array", "items": {"$ref": "#/$defs/related"}}}}
  }
}
```

`$defs/source`・`position`・`span` は既存 `schemas/texflux-ast-v1.schema.json` の同名定義を
**そのまま複製**する（`sha256` の `minLength`/`maxLength`/`pattern` と各 `description` を含む）。

---

## 4. テスト計画（テストファースト）

### 4.1 `tests/test_diagnostics.py`（新規）

`TempDirTestCase`（`tests/support.py`）を使う。

**ErrorModelTests**
- `ParseError("m", span, code="P001").error_kind == "parse error"`、`.kind == "parse"`、
  `.diagnostic() == "f.tfx:1:2: parse error: m [P001]"`、`str(e)` も同じ。
- 5 サブクラスの `kind` が `parse/validation/directive/macro/module`。
- `code` を省くと `TypeError`。
- `chained("m; more", RelatedLocation("x", span2))` が同じ型・同じ `code`・同じ `span`、`related` が連結される。
- `RenderWarning("w", span, "W001").diagnostic() == "f.tfx:1:2: warning: w [W001]"`、`.kind == "render"`。

**DiagnoseTests**
- 正常文書 → `diagnostics == ()`、`ok`、`sources` は root 1 件、`root.file == filename`、`by_file() == {filename: ()}`。
- root のパースエラー（`@frame{x}:\n    @foo{bad\n`）→ 1 件、`severity ERROR`、`kind "parse"`、`code "P004"`、
  `message/span` が `compile_text` で得た例外と一致、`sources` に root が**ある**、`ok` は False。
- 警告（`\\foo::\n    - a % trailing\n`）→ 1 件 `WARNING`、`kind "render"`、`code "W002"`、`ok` は True。
- import 先のエラー（`main.tfx → mid.tfx → broken.tfx`、`test_modules.DiagnosticChainTests` と同じ配置）:
  `span.file` が broken、`sources` の `file` 列が `[main, mid, broken]`（読み込み順）、
  `related` が 2 件、順序は内側から（mid の import 行、main の import 行）、message は "imported from here"。
- `.tfxm` の自己完結性違反（`impure.tfxm`）→ `related` に `!macroimport` 行（"imported from here"）。
- `!flag{d}{on}` 二重宣言 → `code "V010"`、`related[0].message == "first declared here"`、
  `related[0].span == 1 個目の !flag 行の span`。
- `!defmacro` 二重定義 → `V022` / "first defined here"。マクロ展開エラー（arity）→ `E019` /
  "called here" が呼び出し行を指す。`!import{child.tfx}(d=on,d=off)` → `M010` / "first bound here"。
  `!macroimport` 二重 → `M019` / "first imported here"。取り込み名衝突 → `M021` / "defined here"。
- `by_file()` が全ソースをキーに持ち、診断の無いファイルは `()`、診断のあるファイルにその診断。
- `flags={"nosuch": True}` → `FlagError` が伝播。`flags={"d": "on"}` → `FlagError`。
- `overlays`: ディスクの `a.tfxm` を壊しておき、overlays に正しい内容を渡すと成功する／逆にディスクが正しく
  overlays が壊れているとエラーになる（overlay が優先）。キーを相対・`..` 入り・絶対で渡しても同一視される。
  存在しないファイルを overlays だけで供給できる。root のパスを overlays に入れても `source` 引数が使われる。
  `str` 値の `sha256` が UTF-8 バイト列のハッシュになる。
- `source_bytes` 省略時の `sources[0].data == source.encode("utf-8")`。

**SerializeTests**
- 正常・エラー・警告・import 連鎖の 4 ケースで `json.loads` 後の構造を検証: `format`、`version`、`root == 0`、
  `sources[i].id == i`、`sha256` が実バイトのハッシュ、`diagnostics[0].span.source` が正しい ID、
  `related[].span.source` が import 元の ID。
- 決定性: 2 回呼んで同一文字列。末尾が `"\n"` ちょうど 1 個。compact に空白が無い。`pretty=True` で
  `json.loads` が等しい。日本語のファイル名・メッセージが `\u` エスケープされない。
- `related` が空でも `[]` として存在する。
- span の file が sources に無い `DiagnosticReport` を手で作ると `ValueError`。`sources=()` は `ValueError`。

### 4.2 `tests/test_diagnostic_codes.py`（新規、stdlib `ast` のみ）

`src/texflux/*.py` を `ast.parse` して:

- 対象の呼び出し = 関数名が `ParseError` `ValidationError` `DirectiveError` `MacroExpansionError` `ModuleError`
  `RenderWarning` `_error` `warn` のいずれかである**全 `Call`**（`raise` の有無、`self.` の有無を問わない）。
  各呼び出しは `code=` キーワードを持ち、その値は次の 3 形のどれか:
  `ast.Constant`（str リテラル）、`ast.Attribute` で属性名 `code`（`error.code` の転送）、
  `ast.Name` で名前 `code`（ヘルパー本体 parser.py:275 `_error`、macros.py:339 `_error`、render.py:175 `warn`
  の引数素通し）。`_indent_error` はリテラルを自分で持つのでヘルパーとして扱わない。
- 全 `code=` リテラルが `^[PVDEMW][0-9]{3}$` に一致し、ソース全体で**一意**。
- リテラル集合が本書のコード表と**集合として一致**。表の行は `| P001 | <場所> | ... |`
  の形で、正規表現 `^\| ([PVDEMW][0-9]{3}) \| ([^|]*) \| (.*) \|$` で抽出する。
- **各コードが、それを持つ生成箇所の行に載っていること**。第 2 列のファイル名
  （`P` 表は行番号だけなので `parser.py`）が生成箇所のファイルと一致し、生成箇所の
  メッセージ literal の冒頭 5 語がその行に現れることを検査する。集合の一致だけでは
  2 つのコードが入れ替わっても通ってしまうため、機械的な採番で最も起こりやすい
  取り違えはこちらで塞ぐ。比較前に `{...}` とリテラル中の `…` は同じ穴として除去する。
- 件数の固定: P 37、V 43、D 1、E 19、M 30、W 2。
- **採番上限の固定**: 各文字の最大番号（P 37、V 43、D 1、E 19、M 30、W 2）。
  削除された診断の番号は欠番として残す規則なので、連番の密度ではなく上限を固定する。
  新規追加は上限 +1 を取り、この値と表を同時に更新する。

### 4.3 `tests/test_diagnostics_schema.py`（新規）

`tests/test_ast_schema.py` を鏡写しにする（`jsonschema` 不在なら skip）。
正常・エラー・警告・import 連鎖の payload を検証し、`format` 違い、`version` 2、`severity: "hint"`、
`code: "p1"`、`span.start.line: 0`、`related` 欠落、`sources: []` を reject することを確認。
未知 member を全 object に足しても valid。

### 4.4 `tests/test_cli.py` への追加（`CliCheckTests`）

- 正常: exit 0、stdout 空、stderr 空。
- エラー: exit 1、stdout 1 行 `f"{path}:2:9: parse error: unclosed required group [P004]\n"`、stderr 空。
- 警告: exit 0、stdout に `warning: ... [W002]`。
- 関連位置: import 連鎖で `note:` 行が 2 行、`"  "` インデント、順序が内側から。
- `--format json`: `json.loads(stdout)["format"] == "texflux-diagnostics"`、`diagnostics[0]["code"]`、exit 1。
  `--pretty` でインデントあり。`--pretty` 単独は exit 2。
- stdin: `sys.stdin` を `io.TextIOWrapper(io.BytesIO(...))` に差し替え、`check -` が読む。
  `--stdin-filename` が span の file と `sources[0].file` に出る。相対 import が `--stdin-filename` の
  ディレクトリ基準で解決される。`--stdin-filename x.txt` は exit 2。ファイル入力に `--stdin-filename` は exit 2。
- 非 `.tfx`、存在しないファイル、UTF-8 でないバイト列、`--flag nosuch` → exit 2、stdout 空、stderr `texflux:`。
- `compile` の既存テスト `test_compile_failure_does_not_change_existing_output` に
  `assertIn("[P0", stderr)`（コードが付くこと）を 1 行追加。

### 4.5 既存テストの更新

- `tests/test_interpolation.py:104` の期待文字列の末尾に ` [E0xx]` を付ける。9 ケースの順に
  E002（`nope`）、E006・E006・E006（不正な名前 3 件）、E005（unterminated）、E008・E008（`!param{`）、
  E009・E009（AST 位置の `!text`）。`cases` タプルに 4 列目としてコードを持たせる。
- 他は `assertRaisesRegex` の部分一致・`assertIn` なので変更不要（`$` アンカーは無いことを確認済み。
  `tests/test_prelude.py:296-300` は `InternalError` の行を `:` で分割して先頭 3 要素だけ比較するので、
  末尾に付くコードの影響を受けない）。
- `tests/test_modules.py::DiagnosticChainTests` に `related` の件数・順序（**内側から外側**: 診断の
  ファイルに最も近い import 行が先）・message の assert を追加。
  `test_a_chained_error_keeps_its_real_root_cause`（`__cause__` が元の `OSError`）は、`_imported()` が
  related を積まなかったとき**同じオブジェクトを返す**短絡を保つことで引き続き通る。

### 4.6 回帰

- `python3 -m unittest discover` 全件（現在 392、skip 3）。
- `examples/*.tfx` 6 件の再コンパイルが `.tex` とバイト一致（renderer 本体は無変更）。
- 外部 AST / `.tfxmap` の golden・既存テストが無変更で通る（`_register` の順序変更は成功経路に影響しない）。

---

## 5. ドキュメント更新

| 文書 | 変更 |
| --- | --- |
| `doc/diagnostics.md` | **本書**。実装に合わせて最新に保つ。ステータス節の「未実装」を実装済みに改め、§2 のコード表は `tests/test_diagnostic_codes.py` が読むので、診断を足すたびに表と件数を同時に更新する |
| `schemas/texflux-diagnostics-v1.schema.json`（新規） | §3.11 |
| `README.md` | 「外部ASTのJSON出力」の直後に「診断の JSON 出力（`texflux check`）」節: 用途（エディタ連携）、コマンド 3 例、終了コード表、`diagnose` の Python 例、`doc/diagnostics.md` へのリンク。「仕様書・ドキュメント」リストに追加。クイックスタート §3 の後に `texflux check slides.tfx` を 1 行紹介 |
| `doc/dsl.md` §19 | 冒頭に「全診断は `[コード]` 付きで出力される。コード体系・JSON 形式・`texflux check` は `doc/diagnostics.md`」の段落と、行形式 `file:line:column: kind: message [CODE]` を追記 |
| `texflux_tex_first_dsl_v1_spec.md` §16 | 段落追加（英語）: "Every diagnostic carries a stable code (`P004`) and zero or more related locations; the CLI prints `file:line:column: kind: message [CODE]`. `texflux check` and `texflux.diagnose` expose the same diagnostics as the `texflux-diagnostics` v1 format, whose members are defined in doc/diagnostics.md. Codes are never renumbered or reused." §12.8 末尾に "Each import site the message names is also carried as a related location." |
| `doc/module-system.md` §13 | `#` 列の値を新コードに**置換**（旧 M01〜M27 と同じ文字 `M` で桁だけ違うため併記しない）、M006・M022 の行を追加、§10.1 と §13 冒頭の `error_kind = "module error"` を `kind = "module"`（`error_kind` はプロパティ）に。旧→新の対応表は `doc/diagnostics.md` §4 に置く |
| `doc/raw-mode.md` §4・§8 | R/L ラベル列の右に「コード」列を**追加**（R/L は歴史的 ID として残す） |
| `doc/string-interpolation.md` §11 | T 列の右に「コード」列を**追加**（T は歴史的 ID として残す） |
| `handoff.md` | 冒頭表に「Diagnostic API」行、§H として要約（決定・参照先・検証コマンド） |
| `AGENTS.md`（gitignored、ローカル） | Source-of-truth に「全 `TeXFluxError` / `RenderWarning` は必須の `code` を持つ。1 生成箇所 1 コード、改番禁止。メッセージ中の `<loc>` は `related` にも入れる。`diagnose()` は `TeXFluxError` を送出しない」。Review checklist に「新しい raise に code があり `doc/diagnostics.md` の表と件数を更新したか」「`compile` と `check` が同じ行形式を出すか」 |

---

## 6. 実装手順

ブランチ `feature/diagnostics` を作成して作業する（`main` には commit しない）。各段で focused test → 全 suite。

1. **errors.py / render.py**（§3.1, §3.2）+ `ErrorModelTests`。この時点で全 suite は `TypeError` で大量に落ちる（`code` 必須）。
2. **コード付与**（§3.3〜§3.7、全 132 箇所）+ `tests/test_diagnostic_codes.py`（表との一致テストは `doc/diagnostics.md` を書く 9. まで skip か、表を先に書く）。`test_interpolation.py:104` の期待値更新。全 suite green。commit。
3. **関連位置**（V010, V022, M010, M019, M021, `_expand`, `_imported`, `_error`）+ 対応テスト。commit。
4. **modules.py の reader と `_register`**（§3.6）+ overlay / sources テスト。commit。
5. **diagnostics.py + `__init__.py`**（§3.8, §3.9）+ `DiagnoseTests` / `SerializeTests`。commit。
6. **cli.py `check`**（§3.10）+ `CliCheckTests`。commit。
7. **schema + schema テスト**（§3.11, §4.3）。commit。
8. **examples 6 件バイト比較**、`python3 -W error::ResourceWarning -m unittest discover`。
9. **ドキュメント**（§5）。`doc/diagnostics.md` のコード表 ↔ ソースの一致テストが green になることを確認。commit。
10. README・dsl.md など日本語文書は事実が固まった後に `ja-doc-polish` を使ってよい（任意）。

---

## 7. 検証

```bash
python3 -W error::ResourceWarning -m unittest discover
PYTHONPATH=src python3 -m unittest tests.test_diagnostics tests.test_diagnostic_codes tests.test_cli -v
for f in basic structured stacked-items macros modules content; do
  PYTHONPATH=src python3 -m texflux compile examples/$f.tfx -o /tmp/$f.tex && cmp examples/$f.tex /tmp/$f.tex
done
# 手動確認
printf '@frame{x}:\n    @foo{bad\n' > /tmp/bad.tfx
PYTHONPATH=src python3 -m texflux check /tmp/bad.tfx; echo "exit=$?"           # 1 行 + [P004]、exit 1
PYTHONPATH=src python3 -m texflux check /tmp/bad.tfx --format json --pretty      # JSON、exit 1
printf '@frame{x}:\n    @foo{bad\n' | PYTHONPATH=src python3 -m texflux check - --stdin-filename /tmp/bad.tfx --format json
PYTHONPATH=src python3 -m texflux check examples/modules.tfx; echo "exit=$?"     # 何も出ず exit 0
PYTHONPATH=src python3 -m texflux check examples/basic.tfx --flag nosuch; echo "exit=$?"   # stderr, exit 2
pip install jsonschema && PYTHONPATH=src python3 -m unittest tests.test_diagnostics_schema
```

---

## 8. 非対象（v1 で意図的に入れないもの）

- **複数エラーの収集 / エラー回復**。fail-fast のまま。将来入れる場合も本形式は変わらない（`diagnostics` が長くなるだけ）。
- **`information` / `hint` 重大度**、**`codeDescription` の URL**、**quick fix（code action）**。
- **CLI での import 先 overlay**（`--overlay` 等）。Python API のみ。
- **複数 INPUT**。1 呼び出し 1 文書。ワークスペース全体はシェル / LSP 側でループする。
- **`compile` への `--format json`**。診断は `check` で取る。
- **LSP サーバー本体**（pygls 等は runtime dependency になるので別プロジェクト）。
- **`compile_ast` / `compile_with_map` の戻り値変更**。エラー時のソース一覧が要るなら `diagnose` を使う。
- **`error_kind` の削除**。プロパティとして残す。
- **`__version__` の bump**。外部 AST 追加時にも上げていないので本計画では触らない（利用者判断）。
