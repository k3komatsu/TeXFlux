# 作業メモ

最終更新: 2026-09-13。

| 主題 | 状態 |
| --- | --- |
| モジュールシステム | `b3e8e33` で `main` に統合済み（§A） |
| 文字列 interpolation（`!text`） | **設計完了・実装未着手**（§B） |
| `!items` の削除 | `feature/remove-items` で実施済み（§C） |
| 行頭 `!!` raw escape | **設計決定済み・実装未着手**（§D） |

---

# §B 文字列 interpolation（`!text`） — 次の作業

## 状態

- 詳細設計書 `doc/string-interpolation.md` を作成した。**実装はまだ入っていない。**
- 原案 `texflux_string_interpolation_spec.md`（リポジトリ直下・未追跡）は
  設計の出発点。設計書と食い違う場合は `doc/string-interpolation.md` が優先する。
- 設計上の分岐はユーザー確認済みで、決定は設計書 §1 の決定表 D1〜D9 に全件ある。

## 実装を始めるときに読む順序

1. `doc/string-interpolation.md` §1（決定表）と §6（パイプライン上の位置）
2. §3（字句規則）と §5（`interpolate.py` の API）
3. §12（変更箇所一覧）と §13（実装チェックリスト）
4. §11（診断表 T01〜T13）と §14（検証計画）

## 実装前に必ず把握しておくべき非自明な点

- **`RenderRole` に `"scaffold"` を足す変更と `remap._ROLE_RANK` への追加は必ず同時に
  行う。** 片方だけだと `KeyError` になる。そしてこの role が無いと、列情報を持たない
  SyncTeX 入力で `RemapError: ambiguous source mappings` が出る（設計書 §10.4）。
- **エラーの span は再ターゲット前のノード span、provenance の span は再ターゲット後の
  呼び出し位置**を使う。既存 `!param` の診断（定義行を指す）と挙動を揃えるため
  （設計書 §5.1）。
- **静的な事前走査パスを足してはならない。** `AGENTS.md` が定める「dropped payload の
  内側に踏み込む規則は 4 つだけ」に 5 つ目を作ることになる。すべての検査を
  `expand_macros` の中（遅延）で行う設計にしてある（設計書 §6）。
- **`parser.py` と `flags.py` は変更しない。** 変更が要るように見えるが、調査の結果
  不要と確認済み（設計書 §10.1、§7.3）。
- 設計書は `!items` 削除後の木を前提にしている。`normalize.py` はもうテキストを
  スライスしないので、`parts` は正準化を素通りする。

## 実装と同時に必要な規範文書の改訂

設計書 §15 に改訂前後の文言まで書いてある。忘れると、リポジトリが自分自身と矛盾する。

- `AGENTS.md` の v1 boundaries（現状は interpolation を明示的に禁止している）と
  review checklist。**`AGENTS.md` は `.gitignore` 済みなのでコミットされない。**
- `doc/dsl.md` §12 冒頭と §12.3（現状は「interpolation は一切行われない」と明記）、
  および新設する §12.7。
- `texflux_tex_first_dsl_v1_spec.md` §12。

## 検証

```bash
python3 -W error::ResourceWarning -m unittest discover
PYTHONPATH=src python3 -m texflux compile examples/modules.tfx -o /tmp/m.tex
diff -u examples/modules.tex /tmp/m.tex     # 差分が無いこと
```

marker を含まない既存文書の出力がバイト単位で変わらないことが設計目標である
（設計書 §3.5 の高速パス、不変条件 I11）。

## ブランチ

複数コミットにまたがる作業なので `feature/string-interpolation` を切って進める
（`AGENTS.md` の branch rule）。`main` へは直接コミットしない。

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
3. 行単位で効く `!raw: |` が欲しくなるが、それは parser が special 名をハードコード
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

| 行 | `!items` | `@itemize: \|` | 回避 |
| --- | --- | --- | --- |
| `まとめ:` / `\item まとめ:` | 項目 | パースエラー | `:{}` |
| 継続行 `\TextCA{注意}:` | 項目本文 | パースエラー（診断は「`-` エントリーが必要」で、コロン規則と結びつかない） | `:{}` |
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
