# 作業メモ

最終更新: 2026-09-16。

## 現状

v1 の言語機能はすべて実装・検証済みで、`main` に統合されている。規範定義は
`texflux_tex_first_dsl_v1_spec.md`、利用者向け説明は `README.md` と `doc/dsl.md`、
設計判断は `doc/` の各設計書にある。

実装は 2 つある。Python 実装が参照実装で、D 実装は同じ入力に対して同じバイト列を出す。

| 機能 | Python | D | 設計書 / 規範 |
| --- | --- | --- | --- |
| パーサ・正規化・レンダラ | `parser.py` `normalize.py` `render.py` | `parser.d` `scanner.d` `desugar.d` `canonical.d` `render.d` | spec §1〜§8, §13〜§15 / `doc/dsl.md` |
| 行単位 raw mode と raw escape | `parser.py` `syntax.py` | `parser.d` `syntax.d` | `doc/raw-mode.md` / spec §2 |
| suite marker `::` / `:::` | `parser.py` | `scanner.d` | spec §3 / `doc/dsl.md` §4 |
| 生成中括弧の配置 | `render.py` | `render.d` | spec §14 / `doc/dsl.md` §15 |
| ソースマクロと標準フロー制御 | `macros.py` `modules.py` | `macros.d` `modules.d` | spec §9, §10, §12.9 |
| 文字列 interpolation（`!text`） | `interpolate.py` | `interpolate.d` | `doc/string-interpolation.md` / spec §10.6 |
| ビルドフラグ | `flags.py` | `flags.d` | spec §11 / `doc/dsl.md` §13 |
| モジュールシステム | `modules.py` | `modules.d` | `doc/module-system.md` / spec §12 |
| ソースマップと SyncTeX リマップ | `source_map.py` `remap.py` `synctex.py` | `sourcemap.d` `remap.d` `synctex.d` | spec §15 / `doc/dsl.md` §18 |
| 外部 AST | `external_ast.py` `interchange.py` | `external_ast.d` `interchange.d` | `doc/external-ast.md` |
| Diagnostic API | `diagnostics.py` `cli.py` | `diagnostics.d` `cli/` | `doc/diagnostics.md` |

## 検証

Python 3.11 以上が必要である（`.venv` が 3.9 のままなら作り直す）。
D は LDC 1.43 以上。`source ~/dlang/ldc-1.43.0/activate` で入る。

```bash
python3 -W error::ResourceWarning -m unittest discover   # Python 486 件（D ソースの診断コード検査を含む）
dub test                                                  # D の unittest（ゴールデン照合を含む）
dub build && dub build -c dump-syntax && dub build -c render-tex
python3 tests/conformance/run.py                          # 905 ケース × 15 チェック = 9101 比較
```

`tests/conformance/run.py` が両実装の一致を保証する主検証である。コーパスはリポジトリ内の全ドキュメントと
Python テストスイート中の全ソース断片（不正な文書はすべてここにある）。比較するのは終了コード、標準出力、
標準エラー、生成ファイル。`--check` でチェックを、`--only` でケースを絞れる。`--jobs` で並列度を変えられる。

JSON Schema の検証テストは開発用の `jsonschema`（dev extra `.[dev]`）を入れた環境でのみ走り、未導入なら skip する。
LaTeX 連携のテストは `pdflatex` / `synctex` を検出して skip する。

## D 実装のメモ

- **ビルド構成**。`dub.sdl` に 5 つ。`application`（`bin/texflux`）、`library`、`unittest`、
  そして比較用の `dump-syntax` と `render-tex`。後者 2 つは製品ではなく、構文木と（セッション非依存の）
  生成 TeX を両実装で同じ書式に印字して差分を取るための道具である。
- **prelude の共有**。`dub.sdl` の `stringImportPaths "src/texflux"` で、Python 実装が package resource
  として読むのと同一の `prelude.tfxm` をコンパイル時に埋め込む。単一の正。
- **列はコードポイント**。パーサとヘッダスキャナは物理行を `dstring` に復号して走査する。オフセットが
  そのまま列になるので、日本語の見出しが後続の列をずらすことがない。
- **ブロック参照はポインタ**。`Block` を値で持つ struct を `SumType` の要素にすると解析が循環するため、
  ノードが持つブロックはすべて `Block*`。
- **順序保持マップ**。マクロの可視表とモジュール閉包は「引くだけ」ではなく「なめる」ので、
  どの衝突を先に報告するかが反復順に依存する。`ordered.d` の `OrderedMap` を使う。連想配列では
  実行ごとに違うエラーが出る。
- **`@safe` は AST を触る関数には付かない**。Phobos の `SumType.opAssign` が `@system` で、
  `SumType!(int, string)` でもそうなので、それを含む struct はすべて伝染する。ノードを持たない
  モジュール（`text.d` `json.d` `source.d` `errors.d`）は `@safe`。`errors.d` の `Descent` だけは `@trusted` で、
  渡されたカウンタが自身より長生きすることを呼び出し側が保証する。
- **`std.json` は書き出しに使えない**。キーを辞書順にソートし、インデントが 4 で、`/` をエスケープし、
  DEL を大文字 hex で書く。4 点とも公開フォーマットと食い違う。読み込みには使っている（`remap.d`）。
- **`std.uni.isWhite` は直接使わない**。U+001C〜U+001F で CPython の `str.isspace` と食い違う。`text.d` の
  `isWhitespace` がその 4 文字を足したものを唯一の正とし、全コードポイントの照合テストが差分を固定している。
- **再帰の深さ**。D はスタックオーバーフローを捕捉できないので、木を歩く各パスが `errors.d` の `Descent` で深さを数え、
  `maximumNestingDepth` を超えたら `NestingError` を投げる。CLI は Python の `RecursionError` と
  同じ文言を出す。閾値そのものは一致対象外。

## 意図的に見送った項目

- **依存関係の出力**（`texflux compile --deps` 相当）。`session.loaded()` が全ソースを持っているので
  実装は軽いが、条件分岐で依存グラフが変わる点をどう扱うかは要設計（`doc/module-system.md` §14）。
- **`.tfxm` の陳腐化検出**。`.tfxm` はフラグメントを生まないため `.tfxmap` の `sources` に載らない。
  ビルドシステムの責務としている。
- **モジュールインスタンスのキャッシュ**。同じモジュールを同じフラグで 2 回 import すると 2 回
  コンパイルする。`(path, flags)` をキーにすれば安全に効かせられる。
- **複数エラーの収集**、**LSP サーバー本体**、**CLI からの import 先 overlay**（`doc/diagnostics.md` §5）。
- **D 実装の公開ライブラリ化**。`configuration "library"` はあるが dub パッケージとしては未公開。

## 承知の上で放置している残件

- 大文字小文字を区別しないボリュームでは、パス正規化の `normcase` が macOS では恒等なので
  `b.tfx` と `B.tfx` が 2 つのソースになる（両実装とも）。
- ルートの `filename` に NUL が入ると `compile_with_map` は span のない `ValueError` を返す
  （文書中のパスではなく呼び出し側の引数なので、`FlagError` と同じ扱い）。CLI は捕捉する。
- D では `Flags` が `OrderedMap!bool` なので、Python API にある「bool でない値を渡した」という
  `FlagError` が型的に到達不能。CLI 経由では両者とも同じ文を出すので一致対象外。
- `--help` の本文と引数エラーの文言は一致対象外。終了コードは一致させている。
- 壊れた JSON の `.tfxmap` を読んだときの文言は、`std.json` と Python の `json` で異なる
  （`cannot read source map ...: ` の後ろ）。読めないファイルと UTF-8 でないファイルは一致する。
- SyncTeX のリンク（タグ・行・列）と座標の数値は D では 32 bit に収まらないと `invalid link tag: b'...'` の形で
  拒否する（Python は任意精度）。バージョン、カウント、アンカー、シート／フォームのタグは 64 bit まで受け付ける。
- LDC 1.43 が `SumType` の `toHash` について警告を 1 つ出す。原因箇所を安価に特定できず、動作にも
  影響しないため放置している。

## 運用メモ

- `AGENTS.md` は `.gitignore` 済みでローカルにしか無い。clone し直すか別マシンへ移ると失われる。
- 日本語文書は事実が固まった後に `ja-doc-polish` スキルで整えてよい（任意）。
