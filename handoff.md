# 作業メモ

最終更新: 2026-09-14。

## 現状

v1 の言語機能はすべて実装・検証済みで、`main` に統合されている。規範定義は
`texflux_tex_first_dsl_v1_spec.md`、利用者向け説明は `README.md` と `doc/dsl.md`、
設計判断は `doc/` の各設計書にある。

| 機能 | 実装 | 設計書 / 規範 |
| --- | --- | --- |
| パーサ・正規化・レンダラ | `parser.py` `normalize.py` `render.py` | spec §1〜§8, §13〜§15 / `doc/dsl.md` |
| 行単位 raw mode（`!BEGIN_RAW_MODE` / `!END_RAW_MODE`）と raw escape（`!\| `） | `parser.py` `syntax.py` | `doc/raw-mode.md` / spec §2 / `doc/dsl.md` §3 |
| ソースマクロと標準フロー制御（同梱 `prelude.tfxm`） | `macros.py` `modules.py` | spec §9, §10, §12.9 / `doc/dsl.md` §11, §12, §14.10 |
| 文字列 interpolation（`!text`） | `interpolate.py` `macros.py` | `doc/string-interpolation.md` / spec §10.6 / `doc/dsl.md` §12.7 |
| ビルドフラグ（`!flag` / `!when` / `!unless`） | `flags.py` | spec §11 / `doc/dsl.md` §13 |
| モジュールシステム（`!import` / `!macroimport`） | `modules.py` | `doc/module-system.md` / spec §12 / `doc/dsl.md` §14 |
| ソースマップと SyncTeX リマップ | `source_map.py` `remap.py` `synctex.py` | spec §15 / `doc/dsl.md` §18 |
| 外部 AST（`texflux ast`） | `external_ast.py` `interchange.py` | `doc/external-ast.md` / `schemas/texflux-ast-v1.schema.json` |
| Diagnostic API（`texflux check` / `diagnose`） | `diagnostics.py` `interchange.py` `cli.py` | `doc/diagnostics.md` / `schemas/texflux-diagnostics-v1.schema.json` |

## 検証

Python 3.11 以上が必要である（`.venv` が 3.9 のままなら作り直す）。

```bash
python3 -W error::ResourceWarning -m unittest discover
for f in basic structured stacked-items macros modules content; do
  PYTHONPATH=src python3 -m texflux compile examples/$f.tfx -o /tmp/$f.tex && cmp examples/$f.tex /tmp/$f.tex
done
```

JSON Schema の検証テストは開発用の `jsonschema` を入れた環境でのみ走り、未導入なら skip する。
LaTeX 連携のテストは `pdflatex` / `synctex` を検出して skip する。

## 意図的に見送った項目

- **依存関係の出力**（`texflux compile --deps` 相当）。`session.loaded()` が全ソースを持っているので
  実装は軽いが、条件分岐で依存グラフが変わる点をどう扱うかは要設計（`doc/module-system.md` §14）。
- **`.tfxm` の陳腐化検出**。`.tfxm` はフラグメントを生まないため `.tfxmap` の `sources` に載らない。
  ビルドシステムの責務としている。
- **モジュールインスタンスのキャッシュ**。同じモジュールを同じフラグで 2 回 import すると 2 回
  コンパイルする。`(path, frozenset(flags.items()))` をキーにすれば安全に効かせられる。
- **複数エラーの収集**、**LSP サーバー本体**、**CLI からの import 先 overlay**（`doc/diagnostics.md` §5）。

## 承知の上で放置している残件

- 大文字小文字を区別しないボリュームでは、`paths.normalized_path` の `normcase` が macOS では恒等なので
  `b.tfx` と `B.tfx` が 2 つのソースになる。
- ルートの `filename` に NUL が入ると `compile_with_map` は span のない `ValueError` を返す
  （文書中のパスではなく呼び出し側の引数なので、`FlagError` と同じ扱い）。CLI は捕捉する。

## 運用メモ

- `AGENTS.md` は `.gitignore` 済みでローカルにしか無い。clone し直すか別マシンへ移ると失われる。
- 日本語文書は事実が固まった後に `ja-doc-polish` スキルで整えてよい（任意）。
