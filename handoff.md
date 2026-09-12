# 引き継ぎメモ — モジュールシステム実装

最終更新: 2026-09-12 / ブランチ `feature/module-system`（`main` から 18 コミット）

## 1. 現在の状態

**実装は完了しており、全テストが通っている。**

```bash
python3 -W error::ResourceWarning -m unittest discover   # 273 tests, OK
```

- `main` には未マージ。作業ブランチは `feature/module-system`。
- 未コミットの変更なし。未追跡は `texflux_module_system_design.md`（ユーザーが置いた
  出発点の方針メモ。設計書の元ネタなので触っていない）のみ。
- `AGENTS.md` は `.gitignore` 済みのため、**v1 境界とレビューチェックリストへの追記は
  ローカルのみ**で、コミットされていない。別マシンで作業を続ける場合は再度加筆が要る。

### 検証済みのこと

| 項目 | 結果 |
| --- | --- |
| ユニット/ゴールデン | 273 tests green（`ResourceWarning` をエラー扱いでも green） |
| ゴールデン 20 件 | すべて再現（バイト一致） |
| `examples/` 6 件 | すべて再現（バイト一致） |
| 単一ファイル文書の `.tfxmap` | モジュール導入前と `sources`・全 `mappings` がバイト一致 |
| 実 LaTeX 往復 | `pdflatex -synctex=1` → `texflux synctex remap` → native `synctex edit` で、**取り込まれた行が取り込み元モジュールに解決する**（`!import` 行ではない）ことを確認済み。`tests/test_e2e.py` に固定してある |

## 2. 何を作ったか

規範は次の 2 つ。実装で迷ったら**必ずこちらを先に読む**こと。

- `doc/module-system.md` — 詳細設計書（日本語, 1650 行）。M01〜M27 の診断表を含む。
- `texflux_tex_first_dsl_v1_spec.md` §12 — 規範仕様（英語）。§13 以降は 1 つずつ繰り下げた。

利用者向けは `doc/dsl.md` 14 章、`README.md` のキラー機能 5、`examples/modules.tfx`。

### 追加した構文（言語への追加はこれ 1 つだけ）

```text
!macroimport{style-v2.tfxm}
!import{quiz.tfx}
!import{quiz.tfx}(answers=on, memo=$handout)
```

`(...)` 束縛リストは **`!` セグメントの末尾に最大 1 個**だけ走査する。
`(` は `GROUP_OPENERS` に入れていないので、`\command` / `@env` / 生 TeX の挙動と
**診断文言**は一切変わっていない。`!name(` は従来必ず ParseError だったため、
意味が変わる既存プログラムは存在しない。

### 主なファイル

| ファイル | 役割 |
| --- | --- |
| `src/texflux/modules.py` | **新規**。`CompilationSession` が本体。ロード/パースキャッシュ、マクロ環境の 2 フェーズ構築、`!import` の解決、フラグ束縛 |
| `src/texflux/macros.py` | `MacroDefinition.module` を追加。語彙的スコープ、`(module, name)` での再帰検出 |
| `src/texflux/normalize.py` | `canonicalize()` を切り出し。`!import`/`!macroimport` のガードハンドラ |
| `src/texflux/source_map.py` | 複数ソース対応。ルートは常に id 0 |
| `src/texflux/parser.py`, `ast.py`, `syntax.py` | `(...)` の走査 |
| `src/texflux/remap.py` | **無変更**（もともと複数ソース対応済みだった） |

### 押さえておくべき不変条件

1. `!import` の解決は**条件分岐の解決後**（パイプライン手順 10）。
   捨てられたペイロード内の import はファイルを開かない。これは意図的で、
   「壊れた依存を `!when` で無効化してビルドできる」保証の根拠。
2. 一方 `!macroimport` はトップレベル限定の検査が**条件分岐より前**に走る。
   `AGENTS.md` / 仕様 §11.5 の「捨てられたペイロードに届く規則」は
   **3 件から 4 件に増やした**。増やした理由は設計書 §6.4 に書いてある。
   5 件目を足すときは同じ判断をやり直すこと。
3. マクロ環境は `own ∪ ⋃ own(直接 import 先)`。**推移的に入らない**ので再帰不要で、
   `.tfxm` の循環が自然に安全になる。ここを「便利だから」推移的にすると
   非推移性・語彙的スコープ・キャッシュ健全性が同時に壊れる。
4. 取り込まれた正準 AST は**呼び出し先の span を保持**する。
   呼び出し側の `!import` 行に付け替えてはならない（SyncTeX 逆引きの生命線）。
5. `GroupKind.BINDING` の `Argument` はレンダラに到達しない。
   到達したら `render._emit_group` が TypeError で落ちる。

## 3. レビューの経緯

別エージェントによるレビューを **4 周**実施し、**指摘は全て解消済み**。

| 指摘 | 対応 |
| --- | --- |
| `\vspace{1em}(x):` が「特殊は `(...)` を 1 個しか取れない」と誤報 | `binding is not None` でガード（`af3ebd4`） |
| `_Expander._env` が裸の `KeyError` を投げる | 不変条件違反として `TypeError` に（`af3ebd4`） |
| 設計書 §6.4 と §7.5.2 の自己矛盾 | ドキュメント側を修正（`af3ebd4`） |
| callee のパースエラーが import 連鎖を 1 段取りこぼす | `load` を `try` の中へ（`69a50ea`） |
| NUL を含むパスで span なしの `ValueError` が漏れる | `ValueError` も捕捉（`69a50ea`） |
| 束縛の正規表現がフラグ名パターンを二重定義 | `FLAG_NAME_PATTERN` を共有（`3cedf64`） |
| NUL 対処が片手落ち（`!macroimport` 側が素通り） | `resolve_module_path` で弾く（`956d718`） |
| `.tfxm` のエラーが `!macroimport` を書いたファイルを挙げない（範囲外指摘） | 設計書 §8.5.1 を新設した上で実装（`c83dbe0`） |
| 連鎖が自ファイル段で打ち切られ、入れ子の読めないモジュールが文脈を全部失う | 木を根まで辿る方式に（`34b607b`） |
| `raise error from error` で `__cause__` が自己参照し、真の原因が消える | 抑制時は素の `raise`（`34b607b`） |
| 設計書が「ソース順で最初」と過剰主張、§7.4 の疑似コードが §8.5.1 と矛盾 | 幅優先＝最も浅い段、と修正（`34b607b`） |

`c83dbe0` + `34b607b` は、コンテンツ import の連鎖と対称に、マクロモジュール由来の
エラーに `!macroimport` の位置を足す。各モジュールが記録する site は 1 つだけなので
記録の集合は**木**であり、エラーはその木を根まで辿って全段を並べる。
自ファイル段は読み飛ばすが**打ち切らない**——ここが 4 周目の指摘点で、
打ち切ると入れ子の壊れた `!macroimport` が文脈を完全に失う。
複数箇所から取り込まれている場合は**幅優先で最も浅い段**が記録される。
そのため `_load_macro_modules` の作業キューは FIFO でなければならない（§8.5.1、§7.4）。

3 周目のレビューは 6 項目中 5 項目を確認済みとし、6 項目めで上記の NUL の取りこぼしを
指摘した。確認の根拠として、非特殊ヘッダー 413,710 件の差分検査が `main` と
**バイト一致**（`(` の導入が `\command` / `@env` / 生 TeX の挙動にも診断文言にも
影響していないこと）、および循環診断が `873041f` と文字単位で一致することが示されている。

## 4. 次にやること（優先度順）

### A. `main` へのマージ前に必要

**なし。** 実装・ドキュメント・テストは揃っている。
残りはマージの判断のみ。

### A'. 判断済み（2026-09-12、ユーザー確認済み）

| 項目 | 判断 |
| --- | --- |
| `AGENTS.md` の加筆 | **ローカル保持のまま**。`.gitignore` から外さない。別マシンや clone し直しでは失われるので、その際は再度加筆が要る |
| 日本語ドキュメントの推敲（`/ja-doc-polish`） | **今はかけない**。内容が固まってからで良い。かける際は事実の再検証を全件やり直すこと（Gemini は「ちょうど」「のみ」を黙って落とす） |
| §B の見送り項目 | **今回は着手しない**。必要になった時点で |

### B. 設計上、意図的に見送った項目

`doc/module-system.md` §16 に列挙してある。特に実運用で欲しくなりそうなのは:

- **依存関係の出力コマンド**（`texflux compile --deps` 相当）。
  Makefile で `.tfx` / `.tfxm` の依存を正しく書くには要る。
  セッションは `session.loaded()` で全ソースを持っているので、実装は軽い。
  「ただし条件分岐で依存グラフが変わる」問題をどう扱うかは要設計。
- **`.tfxm` の陳腐化検出**。`.tfxm` はフラグメントを生まないため
  `.tfxmap` の `sources` に載らない。今はビルドシステムの責務としている。
- **`ModuleInstance` のキャッシュ**。現状はパース結果のみキャッシュで、
  同じモジュールを同じフラグで 2 回 import すると 2 回コンパイルする。
  `(path, frozenset(flags.items()))` をキーにすれば安全に効かせられるが、
  v1 では単純さを優先した。

- **ルートの `filename` に NUL** が入っていると `compile_with_map` は `ValueError` を
  返す（文書中のパスではなく呼び出し側の引数なので、span がない `FlagError` と同じ扱い、
  という判断。CLI は `ValueError` を捕捉するのでクラッシュはしない）。
  API の利用者にもっと親切にするなら別途検討。

### C. 既知だが対処不要と判断したもの

- FIFO 化により `CompilationSession.loaded()` / `CompilationResult.sources` に並ぶ
  `.tfxm` の順序が変わった（逆 DFS → 幅優先）。`.tfxm` はフラグメントを生まず、
  `_source_ids` はフラグメント初出順で採番するため、**`.tfxmap` はバイト不変**。
  4 周目レビューが約 470 ケースの差分検査で確認済み。

- `_check_self_contained` は `_build_environments` のたびに
  ロード済み全マクロモジュールを走査する（O(コンテンツモジュール数 × マクロモジュール数)）。
  レビューで実測して 40×40×20 マクロで 0.09 秒。純粋かつ冪等なので害はない。
  実測で問題になったら「検査済み集合」を持てばよい。
- 大文字小文字を区別しないボリュームでは `paths.normalized_path` の `normcase` により
  `b.tfx` と `B.tfx` が同一視される。`paths.py` は本ブランチで未変更。

## 5. 再開時の最短手順

```bash
git switch feature/module-system
python3 -W error::ResourceWarning -m unittest discover      # 273 OK であること
PYTHONPATH=src python3 -m texflux compile examples/modules.tfx -o /tmp/m.tex
diff -u examples/modules.tex /tmp/m.tex                      # 差分なしであること
```

手で触って確かめたいときは `examples/modules.tfx` と `examples/modules/` が
一番早い。マクロライブラリの新旧併存・リテラル束縛・`$` 転送・
開かれない条件付き import が 1 ファイルに揃っている。
