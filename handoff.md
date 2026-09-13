# 作業メモ

最終更新: 2026-09-13。

| 主題 | 状態 |
| --- | --- |
| モジュールシステム | `b3e8e33` で `main` に統合済み（§A） |
| 文字列 interpolation（`!text`） | **実装・検証済み**（§B） |
| `!items` の削除 | `feature/remove-items` で実施済み（§C） |
| 行頭 `!!` raw escape | 実装済み（§D は導入前の設計メモ） |
| 行単位 raw mode（`!BEGIN_RAW_MODE` / `!END_RAW_MODE`） | **実装・検証済み**（§E） |

---

# §B 文字列 interpolation（`!text`） — 実装済み

`feature/string-interpolation` で実装した。利用者向け説明は `doc/dsl.md` §12.7、
規範定義は `texflux_tex_first_dsl_v1_spec.md` §10.6、詳細設計は
`doc/string-interpolation.md`。原案の未追跡ファイルは変更していない。

- テキストフィールドの `!text{name}` は、ちょうど1個の `RawTex` の値のみを補間する。
  `!param` は AST 位置専用。挿入文字列は再走査も再パースもしない。
- 補間は macro expander 内で遅延処理し、捨てた条件分岐の中には入らない。
  parser、flag 処理、import graph、lexical scope は変更していない。
- fragment の由来を nested macro と `.tfxm` 越しにも保持する。テンプレートの
  リテラルは呼び出し位置と `scaffold` role、値は元の span を保持する。
- `RenderRole` と remapper の rank に `scaffold` を同時追加した。列なし SyncTeX は
  値の行を優先する。異なる行の値を1行に複数差し込む場合の曖昧性は既存の制約として残る。
- `AGENTS.md` の禁止リストと checklist も更新済み（gitignored のためローカルのみ）。
- 設計書の疑似コードで欠けていた `text` / `value` の同時更新、マクロ章の参照先、
  golden に必要な inline group 例を訂正した。既存 lexical scope テスト1件の
  テキスト中 `!param` は `!text` に移行した。

検証:

```bash
python3 -W error::ResourceWarning -m unittest discover
```

補間・診断・drop・escape・fragment・module・列なし SyncTeX・golden を検証済み。
`examples/` 6件は再コンパイル結果と `.tex` がバイト単位で一致した。

以下の §D・§C は raw-line escape 導入前の設計判断の履歴であり、当時の
「未実装」「回避策なし」という記述は現在の仕様ではない。

---

# §D 行頭 `!!` raw escape — 次の作業

## 決定

行頭の `!!` を、`@@` と同じ規則の raw-line escape として追加する。`!!foo` は
`!foo` という生の TeX 行を出力する。

## なぜ要るか

`@` には `@@` があるが `!` には対応する escape が無い、という**元からある非対称**。
`!items` の raw suite が項目本文についてだけ穴を塞いでいたが、それを削除して
露出した。実測で今日失敗するもの:

| 入力 | 結果 |
| --- | --- |
| `@verbatim: \|` の本文行 `!important` | `DirectiveError` |
| `@lstlisting: \|` の `!! # ...` | `ParseError: invalid structural name` |
| 散文 `!重要` | `ParseError`（`!` の次は ASCII 英字でないと名前にならない） |
| シーケンス payload `- !bar` | `DirectiveError` |

## なぜ `!raw{...}` ではないか

1. **`!raw{...}` はインデントできない。** `!` 始まりの行はブロック基準位置ちょうど
   にしか書けない（`first in {"@", "!"} and line.indent != base` → indent error）。
   字下げされた listing 本文という最も必要な場面で使えない。`@@` の分岐は
   **インデント検査より前**にあり `rest[:extra]` で字下げを保存するので、`!!` は
   その性質を継承する。
2. 不均衡な `{` を書けない（`scan_group` が balance を要求する）。
3. 行単位で効く `!raw:` が欲しくなるが、それは parser が special 名をハードコード
   して suite を raw にする仕組み、すなわち §C で削除した `raw_suite` の復活である。

## 変更箇所（2 箇所だけ）

`src/texflux/parser.py` のみ。他のモジュールは変更しない。

1. `_block`（現 520 行付近）
   ```python
   if first == "@" and rest[extra : extra + 2] == "@@":
   ```
   → `if first in {"@", "!"} and rest[extra : extra + 2] == first * 2:`
   本体（`rest[:extra] + rest[extra + 1 :]`）は変更不要。

2. `_sequence_entry`（現 778 行付近）
   ```python
   if payload.startswith("@@"):
   ```
   → `if payload[:2] in {"@@", "!!"}:`
   直後の `elif payload[0] in "\\@!":` はそのまま残す。

## 決めておくべき細部

- `!!` 単独 → `!`、`!!!foo` → `!!foo`。`@@` と同じ doubling。
- `!!literal >>` は生の行なので行末 `>>` は継続にならない（`@@` と同じ。
  `tests/test_parser.py:48` の対応物）。
- 非 ASCII が続く場合（`!!重要` → `!重要`）も通る。これが散文のケースを救う。

## 文字列 interpolation 設計との衝突（必ず対応する）

`doc/string-interpolation.md` の **D9 は「行頭 `!!` の raw-line escape は導入しない」
と書いてある。この決定は覆る**ので D9 を書き換えること。あわせて §3.3 に、二層に
なることを明記する:

```text
!!text{x}   → parser が ! を1つ剥がす → RawTex "!text{x}" → 補間される
!!!text{x}  → "!!text{x}" → 走査器が escape → リテラル "!text{x}"
```

実際には踏まない（テンプレート行頭の `!text{` は設計上すでに T01 エラー）が、
記録しないと実装者が混乱する。

## 文書の更新先

`@@` が書かれている場所と対にする:

- `doc/dsl.md:55-58`（`@@` の説明）、`:378-381`（§8 の表）、`:521`、`:1072`
- `texflux_tex_first_dsl_v1_spec.md:66`、`:341`、`:420`、`:430-431`
- `AGENTS.md` の source-of-truth 規則（`@@` に触れている箇所）と review checklist
- `README.md` のチートシート注記

## テスト

`@@` のテストと対にする: `tests/test_parser.py:48`, `:128-129`,
`tests/test_compile.py:362`, `:383`。加えて §D の「なぜ要るか」表の 4 ケースが
`!!` で書けるようになることを固定する。golden は `tests/golden/source-comments`
が `@@` を含むだけなので、`!!` 専用の golden を足すかは AGENTS.md の
「既存ケースが覆う形を重ねない」規則で判断する。

---

# §E 行単位 raw mode（`!BEGIN_RAW_MODE` / `!END_RAW_MODE`） — 実装済み

行頭 `!!` は1行を救う escape であり、verbatim / lstlisting のような長い領域では
全行に `!!` または `@@` を付ける必要が残る。そのため、`!BEGIN_RAW_MODE` の次の物理行
から、同じインデントの `!END_RAW_MODE` の直前までを行単位の raw 領域として扱う機能を
追加した。

- マーカーは前後の半角スペースを除いて単独でなければならず、BEGIN はブロック基準位置に置く。
- 領域内はヘッダースキャン、`@@` / `!!` escape、dedent による終了、空行の巻き戻し、
  `!text{...}` 補間を行わない。タブも許可する。
- 本文は基準位置まで最大で先頭スペースを除去した `RawTex(verbatim=True)` として出力する。
  マーカーはノードを生まず、インデントの違う END は本文になる。
- 実装は `syntax.py` / `parser.py` / `ast.py` / `macros.py` に閉じ、正準 AST のノード種別、
  normalize、renderer、external AST、source map、flags、modules は変更しない。
- パーサがこの2つの特殊名を物理行層で決め打ちするのは意図的な例外であり、正準 AST・
  normalize・renderer に raw mode 固有の知識は追加しない。

詳細設計は `doc/raw-mode.md`、利用者向け説明は `doc/dsl.md` §3、規範定義は
`texflux_tex_first_dsl_v1_spec.md` §2。`tests/golden/raw-mode` を含む parser、compile、
macro、module、golden のテストと、全スイートを検証済み。

---

# §F suite suffix の移行（`:` / `::`）

旧記法のシーケンス suite は `:`、ブロック suite は `:|` / `: |` だった。
現在はシーケンスを `::`、ブロックを `:` で書く。旧ブロック記法はパースエラーに
なるが、旧シーケンス記法は `-` エントリーを1つのブロック引数として受け入れるため、
静かに出力が変わる。

| 旧入力 | 現在の解釈 | 移行 |
| --- | --- | --- |
| `\foo:` + `- A` / `- B` | 1つのブロック引数 | `\foo::` |
| `\foo:|` / `\foo: |` | パースエラー | `\foo:` |
| `\foo:`（エントリーなし） | 空のブロック suite | 意図を確認 |

---

# §C `!items` の削除

## 何をしたか

`!items` ミニ文法を言語から完全に削除した。箇条書きは通常の `@itemize` 環境と
生の `\item` 行で書く。

削除の理由は、`!items` が**表現力をほとんど増やしていなかった**こと。オーバーレイ、
ラベル、継続行、入れ子、複数行項目のすべてが `@itemize` + `\item` で既に書けており、
差はタイプ量（1 行あたり 6 文字）だけだった。その対価として:

- `parser.py` が special の**名前をハードコード**していた（`raw_suite` を決める
  `segments[-1].name == "items"`）。プレフィックスだけで分類するという中核原則の
  唯一の例外だった。
- 正準 AST が 4 種のうち 1 種（`Item`）を itemize 専用に使っていた。
- `normalize.py` の 325/870 行（37%）が `!items` 専用だった。
- `itemize` 決め打ちで、`enumerate` / `description` には使えなかった。
  つまり利用者は結局どちらの書き方も覚えさせられていた。

## 削除が安全だったことの根拠

変換後に再コンパイルして、`examples/` 6 ファイルと golden 4 件の出力が
**1 バイトも変わらなかった**。`examples/content.tex`（1370 行の実物のスライド）を
含む。`@itemize` + `\item` が `!items` と同じ TeX を生むことの実証である。

## 移行ハザード

`!items` の suite は **TeXFlux で唯一「中身を一切走査しない生ブロック」**だった。
失われたのは行末コロンの免除ひとつではなく、**生の行そのもの**である。
`@itemize` の本文は普通のブロックスイートなので、項目本文は文書のどこに書く生の
TeX 行とも同じ制約を受ける:

| 行 | `!items` | `@itemize:` | 回避 |
| --- | --- | --- | --- |
| `まとめ:` / `\item まとめ:` | 項目 | パースエラー | `:{}` |
| 継続行 `\TextCA{注意}::` | 項目本文 | パースエラー（診断は「`-` エントリーが必要」で、二重コロンのシーケンス規則による） | `:{}` |
| `@foo{A}` | 生テキスト | パースエラー | `@@foo{A}` |
| `!foo` | 生テキスト | DirectiveError | **回避策なし** |
| `\item >> \foo` | 生テキスト | **静かに** `\item{` / `\foo` / `}` の3行になる | 名前と `>>` の間に本文を置く |
| `@@foo{A}` | 生テキスト | **静かに** `@foo{A}` になる | `@@@foo{A}` |

下2行はエラーにならず出力だけが変わるので、移行時はこちらのほうが危険である。
`@@` は同時に 3 行目の回避策でもあることに注意。

これとは別に、`_items_handler` は項目間の空行を捨てていたが、ブロックスイートは
空行を文書の内容として保持する。空行で区切ったリストは生成 TeX にその空行が残る。
変換前のコーパスに空行区切りの `!items` は 0 件だったので、バイト一致の主張には
影響しない。

行の規則に関するものは、いずれも生の `\command` 行すべてに元からある規則であって
新しい制限ではない。
ただし `!foo` の行は、これで **TeXFlux のどこにも書けなくなった**。`@@` に相当する
raw-line escape が `!` には存在しないためである。文字列 interpolation の原案 §18.1 は
行頭 `!!` escape を提案していたが、設計書では D9 で見送っている。必要なら独立した
機能として再検討すること。

記載先は `doc/dsl.md` 8 章の表、規範仕様 9 章、`README.md` のチートシート。
`tests/test_compile.py` の `test_an_item_body_is_an_ordinary_raw_line` で固定した。

## 消えた API

`texflux.Item` / `texflux.ast.Item` は公開 API から削除した。`CanonicalNode` は
`RawTex | GenericInvocation | BraceGroup` の 3 種になった。

`!items` は `!block` / `!arg` / `!body` と同じく unknown special として失敗する。
`AGENTS.md` の v1 境界に、リスト用ミニ文法をどの綴りでも復活させない旨を記録した。

---

# §A モジュールシステム

モジュールシステムは `b3e8e33` で `main` に統合済み。
実装・仕様・ドキュメント・テストは揃っている。ここに残すのは、
**意図的に見送った項目**と**承知の上で放置している残件**だけである。

## 参照先

| 知りたいこと | 読む場所 |
| --- | --- |
| 言語としての規範 | `texflux_tex_first_dsl_v1_spec.md` §12 |
| 日本語の利用者向け説明 | `doc/dsl.md` 14 章 |
| 実装の設計と診断一覧（M01〜M27） | `doc/module-system.md` |
| 守るべき不変条件 | `AGENTS.md`（v1 境界とレビューチェックリスト） |
| 動く実例 | `examples/modules.tfx` と `examples/modules/` |

## 検証

```bash
python3 -W error::ResourceWarning -m unittest discover   # 273 tests, OK
PYTHONPATH=src python3 -m texflux compile examples/modules.tfx -o /tmp/m.tex
diff -u examples/modules.tex /tmp/m.tex                  # 差分なし
```

## `AGENTS.md` はローカルのみ

`AGENTS.md` は `.gitignore` 済みなので、モジュールシステムに関する加筆は
**コミットされていない**。clone し直すか別マシンへ移ると失われるため、
その際は再度加筆が要る。

## 意図的に見送った項目

`doc/module-system.md` §16 に全件ある。実運用で欲しくなりそうなのは次の 3 つ。

- **依存関係の出力**（`texflux compile --deps` 相当）。Makefile で `.tfx` / `.tfxm`
  の依存を書くには要る。`session.loaded()` が全ソースを持っているので実装は軽い。
  ただし条件分岐で依存グラフが変わる点をどう扱うかは要設計。
- **`.tfxm` の陳腐化検出**。`.tfxm` はフラグメントを生まないため `.tfxmap` の
  `sources` に載らない。今はビルドシステムの責務としている。
- **`ModuleInstance` のキャッシュ**。現状はパース結果のみキャッシュなので、
  同じモジュールを同じフラグで 2 回 import すると 2 回コンパイルする。
  `(path, frozenset(flags.items()))` をキーにすれば安全に効かせられる。

## 承知の上で放置している残件

- `_check_self_contained` は `_build_environments` のたびにロード済みの全マクロ
  モジュールを走査する（O(コンテンツ数 × マクロモジュール数)）。実測で
  40×40×20 マクロ 0.09 秒。純粋かつ冪等なので害はない。問題になったら
  「検査済み集合」を持てばよい。
- 大文字小文字を区別しないボリュームでは `paths.normalized_path` の `normcase`
  により `b.tfx` と `B.tfx` が同一視される。
- ルートの `filename` に NUL が入ると `compile_with_map` は span のない
  `ValueError` を返す（文書中のパスではなく呼び出し側の引数なので、`FlagError`
  と同じ扱いという判断）。CLI は捕捉するのでクラッシュはしない。
