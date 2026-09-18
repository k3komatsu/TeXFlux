# 作業メモ

最終更新: 2026-09-18。

## 現状

TeXFlux v0.2.0 は D/LDC 1.43 以上で動く D 単独実装である。規範定義は
`texflux_tex_first_dsl_v1_spec.md`、利用者向け説明は `README.md` と
`doc/dsl.md`、各設計判断は `doc/` の設計書にある。実行時依存は Phobos
だけで、製品・テスト・回帰fixture更新ツールは DUB からビルドする。

| 機能 | 実装 |
| --- | --- |
| パーサ・ヘッダ走査 | `source/texflux/parser.d`、`scanner.d` |
| 脱糖・正規化・レンダラ | `desugar.d`、`pipeline.d`、`render.d` |
| AST・canonical tree | `ast.d`、`canonical.d` |
| ソースマクロ・標準フロー制御 | `macros.d`、`modules.d` |
| 文字列 interpolation（`!text`） | `interpolate.d` |
| ビルドフラグ | `flags.d` |
| モジュールと prelude | `modules.d`、`source/texflux/prelude.tfxm` |
| ソースマップ・SyncTeX | `sourcemap.d`、`remap.d`、`synctex.d` |
| 外部 AST・JSON | `external_ast.d`、`interchange.d`、`json.d` |
| Diagnostic API | `diagnostics.d`、`errors.d` |
| CLI | `source/texflux/cli/` |
| 固定回帰 | `tests/regression/v1.jsonl`、`tests/d/texflux_tests/regression.d` |

公開 API は `texflux` の `compileText`、`compileWithMap`、`compileAst`、
`CompilationSession`、`Flags`、`serializeSourceMap` と、
`texflux.external_ast.serializeAst`、`texflux.diagnostics.diagnose` /
`serializeDiagnostics`、`DiagnosticReport.root` / `ok` / `byFile` である。AST を扱う内部 pass は安定 API として案内しない。

## 検証

ツールチェーンは次で有効にする。

```bash
source ~/dlang/ldc-1.43.0/activate
```

通常の変更では次を実行する。

```bash
dub test
dub build
dub build -c library
dub build --build=release
dub build -c update-regression
```

回帰確認は `dub test` が `tests/regression/v1.jsonl` を読み、905 cases / 15 checks /
9101 outcomes を比較する。意図した公開出力変更時だけ、明示的に次を使ってfixtureを更新する。

```bash
dub run -c update-regression -- --accept-current
```

LaTeX 連携は `tests/d/texflux_tests/integration.d` が `pdflatex` などを PATH から検出し、
無い環境では skip する。D の JSON schema、診断code、公開 API、不変条件の検査も
`dub test` に含まれる。

## D実装のメモ

- **prelude**。`modules.d` の `import("prelude.tfxm")` は `source/texflux` を
  string import path として compile-time に埋め込む。製品・library・unittest・更新ツールで同じ値を使う。
- **Unicode の列**。物理行と header は `dstring` で走査し、source span の列は Unicode code point の
  1 始まりである。`text.d` が whitespace、UTF-8 decode、quoting の v1 規則を一元化する。
- **順序保持**。`ordered.d` の `OrderedMap` は、マクロ環境やモジュール閉包を走査する順序を固定する。
  エラーの報告順を D の連想配列順に依存させない。
- **canonical 境界**。renderer に syntax-only の `ParsedInvocation`、`SpecialInvocation`,
  `SequenceEntry`、`Stack`、binding argument を渡さない。special expansion は AST-to-AST で行う。
- **診断code**。各 construction site の literal code は `tests/d/texflux_tests/diagnostic_codes.d` が
  `doc/diagnostics.md` の表と照合する。コードは P037/V043/D001/E019/M029 を上限とし、再利用しない。
- **JSON writer**。公開 JSON は `texflux.json` / `interchange.d` の writer を使う。
  `std.json` は入力読込だけに使い、member order や escape 規則を変えない。
- **再帰深度**。`errors.d` の `Descent` が各 recursive pass の深度を数え、
  `maximumNestingDepth` を超える入力は `NestingError` として扱う。
- **`@safe`**。AST を含む Phobos `SumType` 操作の `@system` 境界は無理に隠さず、
  AST を持たない低レベル module にだけ `@safe` を付ける。

## 意図的に見送った項目

依存グラフ専用の出力、モジュールinstance cache、複数エラー収集、LSP server本体、
CLIからのimport先overlay、registry公開、自動配布はこの版の対象外である。DUBの
`library` configuration はビルド検証するが、registryへの公開は行わない。

## 既知の制限

- 大文字小文字を区別しない volume では `foldCase` が恒等なため、綴りだけが異なる二つの path が
  別 source として扱われる。
- ルート filename に NUL がある場合は span の無い `ValueError` になる。CLI は tool failure として扱う。
- `Flags` は型付きの bool map なので、宣言にない名前や型違いの値はAPI境界で拒否される。
- `--help` と usage error の本文は公開byte contractの対象外である。
- 壊れた `.tfxmap` JSON の parser-specific な後置メッセージは JSON reader 実装に依存する。
- SyncTeX のリンク・座標は 32-bit 整数範囲に収まる値を受け付ける。範囲外の link tag は拒否する。
- M029 の固定メッセージには、回帰fixture互換のため旧API表記 `texflux.compile_with_map` が残る。
- LDC 1.43 が `SumType` の `toHash` について出す既知の warning は許容する。新しい warning は受け入れない。

## 運用メモ

- `AGENTS.md` は `.gitignore` 済みでローカルにのみ存在する。今回のD単独規約もローカルファイルへ反映する。
- 日本語文書の事実が固まった後は `ja-doc-polish` skill を任意で使えるが、技術的事実の判断は
  実装とテストを正とする。
