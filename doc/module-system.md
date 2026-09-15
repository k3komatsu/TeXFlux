# TeXFlux モジュールシステム 設計書

## 0. ステータスと本書の位置づけ

本書は TeXFlux のマルチソースファイル（モジュールシステム）の**設計書**である。
実装は `src/texflux/modules.py` にあり、規範定義は `texflux_tex_first_dsl_v1_spec.md` §12、
利用者向け説明は `doc/dsl.md` 14 章、動く実例は `examples/modules.tfx` と `examples/modules/`、
テストは `tests/test_modules.py` と `tests/golden/module-*` にある。本書が扱うのは、
それらから読み取れない**設計判断とその根拠**、および実装が守っている不変条件である。

中心的な設計思想は次のとおりである。

> **1 つの `.tfx` ファイルは 1 つの独立したコンテンツモジュールである。**
> 各コンテンツモジュールは、自分の正準 AST が呼び出し側へ挿入される前に、
> 自分自身のフラグとマクロを解決し終えている。
>
> **1 つの `.tfxm` ファイルは 1 つのマクロ定義モジュールである。**
> マクロの取り込みは語彙的（lexical）・私的（private）・非推移的（non-transitive）であり、
> コンテンツを生成しない。

---

## 1. 決定事項

| # | 項目 | 決定 | 理由 |
| --- | --- | --- | --- |
| D1 | フラグ束縛の構文 | `!import{quiz.tfx}(answers=on)`（後置 `(...)` リスト）。オプション群を流用する `!import[...]{...}` 形は不採用 | 影響を `!` セグメント末尾に限定でき、`\command` / `@environment` / 生 TeX の挙動を一切変えない（§4） |
| D2 | `.tfxm` の条件式 | `!when` / `!unless` は `.tfxm` 内の**どこであっても**禁止（テンプレート内部を含む） | `.tfxm` はフラグを宣言できないため、テンプレート内の条件式は「呼び出し側コンテンツモジュールのフラグ」への暗黙依存になり、語彙性・私的性・キャッシュの健全性が壊れる |
| D3 | 「捨てられたペイロードに届く規則」 | 「`!macroimport` はトップレベル宣言であり `>>` セグメントになれない」を 4 件目として追加する。`!import` は追加しない | §6.3 に判断根拠を記す |
| D4 | マクロ再帰検出のキー | `name` ではなく `(定義元モジュール, name)` | 別モジュールの同名マクロを誤って再帰と判定しないため |
| D5 | `.tfx` ローカルマクロの自己完結性検査 | 行わない（`.tfxm` のみ） | 「壊れた内容を `!when` で無効化できる」保証を守るため（§7.3） |
| D6 | モジュールパス | 相対パスのみ。絶対パスは拒否。`..` は許可 | 再利用可能なコンポーネントの可搬性。プラットフォーム差の排除 |
| D7 | キャッシュの単位 | パース結果（`ModuleSource`）のみ。コンパイル結果はキャッシュしない | キャッシュは意味論を変えてはならず、パース結果のキャッシュはこれを満たす |

---

## 2. スコープと用語

### 2.1 用語

| 用語 | 定義 |
| --- | --- |
| コンテンツモジュール | `.tfx` ファイル 1 つ。TeX 出力を生む |
| マクロモジュール | `.tfxm` ファイル 1 つ。マクロ定義のみを提供し、TeX 出力を生まない |
| `ModuleSource` | 1 ファイルのロード結果（バイト列・display パス・パース済み構文 AST）。**正準パス identity でキャッシュされる** |
| モジュールインスタンス | 1 回の `!import` に対応する 1 つのコンパイル実体（束縛フラグ・マクロ環境・正準 AST）。**キャッシュしない** |
| 正準パス identity | `paths.normalized_path()` の戻り値。symlink・相対要素を解決した比較可能な 1 つの綴り |
| display パス | 診断メッセージと `SourceSpan.file` とソースマップに現れる、人間が読む綴り（§9.2） |
| 公開インタフェース | あるマクロモジュールが**自分自身で `!defmacro` したマクロ**の集合。取り込んだマクロは含まない |
| マクロ環境 | あるモジュール内で `!名前` として見えるマクロ名 → `MacroDefinition` の写像 |

### 2.2 依存の種別

2 種類の依存は決して 1 つに統合しない。

```text
コンテンツ依存        !import{foo.tfx}        コンテンツ（正準 AST）を生む
マクロ名前空間依存    !macroimport{foo.tfxm}  名前空間のみを生む。出力ゼロ
```

---

## 3. ファイル種別

### 3.1 `.tfx`（コンテンツモジュール）

トップレベルに書けるもの:

```text
生 TeX / 構造構文 / !flag / !defmacro / !macroimport / !import / !when / !unless / 通常のコンテンツ
```

`.tfx` は 1 つの独立したモジュールインスタンスとしてコンパイルされる。
そのローカルフラグ・マクロ定義・マクロ取り込みは、取り込み側へ一切漏れない。

### 3.2 `.tfxm`（マクロ定義モジュール）

トップレベルに書けるものは次の 4 種類だけである。

```text
!defmacro
!macroimport
コメント行（lstrip して '%' で始まる生 TeX 行）
空行
```

ファイル内のどこにあっても禁止されるもの:

```text
!flag   !when   !unless   !import
```

検査の定義は §7.1 に記す。

---

## 4. 字句の追加: `(...)` 束縛リスト

モジュールシステムが言語に加えた構文追加は**これ 1 つだけ**である。

`(` を汎用のインライン群開始文字にはしない。`(...)` は **`!` プレフィックスのセグメントに
限り、すべてのインライン群の後ろに最大 1 個だけ**置ける末尾リストとして走査する
（`parser.py` の `HeaderScanner._segment`）。

```text
プレフィックス判定 → 名前走査 → インライン群ループ ({ [ <)
                   → 【'!' セグメントのみ】末尾束縛群 1 個 ( ... )
                   → 続く文字は " :>" か行末でなければならない
```

- `GroupKind.BINDING` は区切り表に載るが、ヘッダのインライン群ループを駆動する
  `GROUP_OPENERS` には含めない。束縛群は `SpecialInvocation.groups` の**末尾要素**として
  格納され、新しい AST ノード型は追加しない。
- `(...)` の走査は `[...]` と同じ規則で、括弧はネストし、釣り合ったブレース群の内側は無視し、
  バックスラッシュ・エスケープを尊重する。
- `\` / `@` セグメントは束縛群を走査しないので、そこに `(` が来たときは従来どおり
  `unexpected token after structural name or group` になる。「特殊は `(...)` を 1 個しか取れない」
  「`(...)` は群の後ろに置く」の 2 つの診断は、束縛群を読んだ後にだけ出る。
- コマンド名の直後に括弧が密着した `\foo(x):` は、通常の TeX の書き方として生の TeX のまま扱う
  （`::` には適用されない）。

束縛群はどの `!` セグメントにも字句的には付けられるが、`!import` 以外のすべての構文は
**既存コードのまま**これを拒否する。個別対応は不要であり、追加してはならない。

| 構文 | 拒否経路 |
| --- | --- |
| `!when` / `!unless` | 先頭以外の群は `_flag_names` → `demand_text` → `ValidationError` |
| `!flag` | 群数 2 の検査か `demand_text` → `ValidationError` |
| `!defmacro` / `!param` / `!each` | `demand_text` / `_single_name` → `ValidationError` |
| マクロ呼び出し（標準フロー制御を含む） | `_values` の `required_text(group) is None` → `MacroExpansionError` |
| `!macroimport` | §7.2 の群検査 → `ModuleError` |

結果として `GroupKind.BINDING` の `Argument` は**正準 AST に到達しない**。
`!import` はこれを消費して自身ごと消え、他はすべてエラーになるからである。
`render._emit_group` は念のため `BINDING` を `TypeError` で拒否する。

---

## 5. 束縛リストの文法と意味論

### 5.1 文法

束縛群の**中身のテキスト**（`(` と `)` の間）に対する文法。

```text
binding-list  ::= SP* binding (SP* "," SP* binding)* SP*
binding       ::= flag-name SP* "=" SP* binding-value
binding-value ::= "on" | "off" | "$" flag-name
flag-name     ::= [A-Za-z][A-Za-z0-9_-]*
```

- ヘッダは 1 物理行なので、束縛リストに改行は現れない。
- **空リスト `()` および空白のみのリストはエラー**（M007）。束縛が不要なら `(...)` 自体を書かない。
- 末尾カンマや空の要素、`on` / `off` / `$名前` 以外の値はすべて M008。式言語は導入しない。
- `flag-name` の正規表現は `flags.FLAG_NAME_PATTERN` を共有し、重複定義しない。

値の文法にカンマも括弧も現れないので、カンマでの単純分割が一意に正しい（`parse_bindings`）。
各束縛は `FlagBinding(name, literal, caller, span, name_span, value_span)` になり、
`literal` と `caller` はちょうど一方が `None` である。部分 span は群の `(` の次の列からの
オフセットで求める（`_sub_span`）。

### 5.2 意味論（`bind_import_flags`）

1. 呼び出し先の宣言済み既定値から始める。**束縛のない callee フラグは callee 自身の既定値**。
2. 束縛をソース順に処理する。callee が宣言していない名前は M009、同じ名前の二重束縛は M010、
   caller が宣言していない `$名前` は M011。値はリテラルまたは caller の解決済みの値。

**同名フラグの暗黙継承は存在しない**。`answers=$answers` のように明示的に転送したときだけ伝播する。
コマンドラインの `--flag` はビルドを設定するものなので、ルートモジュールにのみ適用される。

### 5.3 例

```text
% quiz.tfx
!flag{answers}{off}

@frame{Question}::
    Question text

    !when{answers}::
        Answer: 42
```

```text
% main.tfx
!flag{answers}{off}

!import{quiz.tfx}                          % callee 既定値 (off)
!import{quiz.tfx}(answers=on)              % リテラル上書き
!import{quiz.tfx}(answers=$answers)        % caller フラグの転送
!import{quiz.tfx}(answers=$answers, memo=off)   % memo が未宣言なら M009
```

---

## 6. コンパイルパイプライン

### 6.1 順序

1 つのコンテンツモジュールは `CompilationSession._compile` が次の順序でコンパイルする。

```text
 1. validate_macro_forms / validate_flag_forms / validate_macroimport_forms
 2. desugar                                    純粋な >> 脱糖
 3. collect_flags                              宣言の収集
        ルートモジュール: --flag 上書きを渡す（FlagError）
        被 import モジュール: 直後に bind_import_flags を適用（ModuleError）
 4. resolve_macro_imports                      トップレベル !macroimport の剥ぎ取り
 5. _build_environments                        マクロモジュール閉包のロードと環境構築（§7）
 6. collect_macros(imported=取り込み分)        ローカル定義と衝突検査 → このモジュールの環境
 7. expand_macros(environments=環境表)         条件の解決と語彙的スコープでの展開
 8. resolve_content_imports                    !import を被 import モジュールの正準 AST へ置換
 9. canonicalize                               値の消費と正準 AST の検証
```

マクロモジュール（`.tfxm`）の手順は §7.1 に別途定義する。

### 6.2 `normalize()` との関係

`normalize()` は**モジュール機能を持たない低水準経路**としてそのまま残る。手順 1 のうち
`validate_macro_forms` / `validate_flag_forms`、および手順 2・3・6・7・9 を行い、手順 4・5・8 と
`validate_macroimport_forms` は行わない（後者は `modules.py` にある）。`normalize.py` は `modules.py` を
import してはならない（循環 import になる）ので、末尾の `canonicalize()` を公開し、`modules.py` がそれを呼ぶ。

`normalize()` の直呼びで `!import` / `!macroimport` に出会ったときのために、
`BUILTIN_DIRECTIVES` にはこの 2 名のガードハンドラだけが登録されている。ガードは M029 を
送出し、副次効果として 2 名を `!defmacro` から予約する。モジュール経路では手順 4 と 8 で
取り除かれるので、ガードに到達することはない。標準フロー制御（`!before` 等）はセッションが
環境に seed する普通のマクロなので、`normalize()` の直呼びでは未知の special になる。
モジュール対応のコンパイル（`compile_text` / `compile_with_map` / `compile_ast` / CLI）が
規範的な公開挙動であり、低水準関数は呼び出し側が環境を用意することを要求してよい。

手順 8 は**構文 AST 上を歩き、`!import` ノードを被 import モジュールの正準ノード列で置換する**。
手順 9 の `_normalize_node` は `GenericInvocation` / `BraceGroup` を受理して冪等に再正規化する
ので、差し込みは安全である。走査器（`_ImportResolver`）は自分が差し込んだ正準ノードを
再訪しない。置換結果は親のノード列へ `extend` されるだけで、再帰対象にならないからである。
この不変条件を崩す実装（差し込み後にもう一度ブロック全体を歩く等）にしてはならない。

### 6.3 「捨てられたペイロードに届く規則」（D3 の判断根拠）

条件分岐で捨てられたペイロードにも届く規則は**ちょうど 4 つ**である。

1. ペイロードは構文として解析できなければならない。
2. `!flag` はトップレベル宣言でなければならない。
3. `!defmacro` と `!each` のテンプレート規則。テンプレートに `!import` / `!macroimport` を
   書けないこともここに含まれる。
4. **`!macroimport` はトップレベル宣言であり、`>>` セグメントになれない。**

4 の理由: マクロ環境はマクロ展開（= 条件分岐解決）の**前**に確定していなければならない。
`!when{x} >> !macroimport{a.tfxm}` を許すとマクロ名前空間がビルドフラグ依存になり、
語彙的スコープ・非推移性・`.tfxm` キャッシュの健全性がすべて壊れる。`!flag` を
トップレベル限定にしている理由とまったく同じである。

3 の拡張は既存の検査地点（`!defmacro` 収集時のテンプレート走査）に 2 つの名前を足すだけで、
新しい検査地点は作らない。「マクロテンプレートに何を書けるか」はもともと条件分岐より前に決まる。

4 つの規則はいずれも「この行がここに書けるか」という純粋に構文的な検査であり、
**ファイルを 1 つも開かない**。トップレベル以外の `!macroimport` は形式検査の時点で落ちるので、
その参照先ファイルは決して開かれない。したがって「壊れた依存や存在しないファイルを `!when` で
無効化して文書をビルドする」用途は維持される。

**`!import` の形式検査はこの規則に追加しない**。suite を取らないこと、`>>` のラッパーになれないこと、
パスが解決できること、循環がないことは、すべて手順 8、すなわち条件分岐が解決された**後**に検査する。

```text
!when{appendix}::
    !import{broken-or-missing.tfx}
```

`appendix` が off のとき、このファイルは開かれず、存在しなくてもよい。

---

## 7. `!macroimport`: 語彙的・私的・非推移

### 7.1 マクロモジュールのロード手順

`.tfxm` 1 ファイルの処理は `collect_macro_module` に集約されており、同梱の標準マクロモジュール
（§7.7）も同じ関数を通る。

```text
 1. parse
 2. validate_macro_forms / validate_macroimport_forms
 3. validate_macro_module_purity（§7.1.1）
 4. desugar
 5. resolve_macro_imports          → 直接 import 先の一覧
 6. collect_macros(module=path)    → own（公開インタフェース）
```

`own` と直接 import 先の一覧が §7.4 の 2 フェーズ構築のフェーズ 1 の成果物である。
環境の構築（フェーズ 2）と自己完結性検査（§7.3）は閉包全体をロードし終えた後に行う。

#### 7.1.1 純粋性検査

パース直後の `Document`（脱糖前）に対して行う。

- **ファイル全体**: `syntax.walk` で `flag` / `when` / `unless` / `import` の `SpecialInvocation`
  を探し、見つかれば M013（D2）。
- **トップレベル**: `document.body.nodes` の各ノードは、空行かコメントの `RawTex`、
  `!defmacro`、`!macroimport` のいずれかでなければならない。それ以外は M014。

剥ぎ取り後の残存ノード検査は置かない。手順 5・6 で `!macroimport` と `!defmacro` を剥ぎ取った後に
残るのは空行とコメント行だけであり、これはトップレベル検査から自動的に従う。

### 7.2 `resolve_macro_imports`

トップレベルの `!macroimport` を剥ぎ取り、`MacroImport(path, display, span)` の列を返す。
`span` はパス群の位置で、作者が直すべき場所である。

- suite を持つ → M015。`(...)` 群 → M016。群数が 1 以外 → M017。必須群でない → M018。
- パスは §9.1 で解決する（種別は `.tfxm`）。同一正準パスの 2 度目は M019。
- 剥ぎ取り後の本体に `!macroimport` が残っていれば、それは非トップレベルなので M020。

`validate_macroimport_forms` は `flags.validate_flag_forms` と同形で、脱糖前の構文 AST の
`syntax.stacks()` を走査し、`!macroimport` が `>>` のセグメントとして現れたら M012 にする。

### 7.3 公開インタフェースと自己完結性

**公開インタフェース**: `.tfxm` が公開するのは**自分が `!defmacro` したマクロだけ**である。
`!macroimport` で得たマクロは私的な実装依存であり、再輸出されない。
`public` / `private` / `export` 構文は導入しない。

**自己完結性検査**（`_check_self_contained`）: 各マクロモジュールの環境が確定した後、
そのモジュールの `own` に属する各テンプレートを `syntax.walk` で走査し、すべての
`SpecialInvocation` の名前が次のいずれかに属することを検査する。

```text
{"param", "each", "text"}  ∪  registry（組み込み特殊）  ∪  そのモジュールの環境
```

属さなければ M028。呼び出し側の無関係な名前空間が、本来不正なマクロモジュールを
正当化することは決してない。環境は一度構築されたら変わらないので、各モジュールは
ちょうど 1 回だけ検査する。検査は閉包の新しい環境を**すべて**構築し終えた後に行うので、
名前の衝突（M021）は自己完結性違反（M028）より常に先に報告される。

**この検査は `.tfx` のローカルマクロには適用しない**（D5）。未使用マクロのテンプレートに
未知の `!名前` があってもエラーにしないことで、`!when` で無効化されたマクロ定義を含む `.tfx`
を壊さない。

### 7.4 環境の 2 フェーズ構築（循環に強い）

`.tfxm` の循環 import は許容される。アルゴリズムは**再帰を一切使わない**ので、循環はロード済み
集合だけで自然に安全になる。

**フェーズ 1 — 閉包のロード**（`_load_macro_modules`）: `MacroImport` を単位に幅優先で走査する。
`_public` にすでに登録済みのパスは即座に読み飛ばすので、`A.tfxm ↔ B.tfxm` のような循環でも
ループは必ず停止する。各モジュールについて `own` と直接 import 先を別々の辞書に置く。
作業キューは**先入れ先出し**でなければならない。§8.5.1 の診断規則がこれに依存する。

**フェーズ 2 — 環境の構築**（`_build_environments`）: 各モジュールの環境は
`標準マクロ ∪ own ∪ ⋃ own(直接 import 先)` であり、**直接 import 先の `own` にしか依存しない**。
`own` はフェーズ 1 で確定済みなので、フェーズ 2 は再帰しない。循環があっても両方向で同じ結果になる。
結合は `.tfx` とも共有する自由関数 `merge_imports(base, imports, public)` に置く。
反復順は `own`（`dict`、ソース順）と `imports`（タプル、ソース順）に依存するので決定的である。

- `.tfxm`: `merge_imports({**standard, **own}, imports, public)` がそのまま環境。
- `.tfx`: `merge_imports(dict(standard), imports, public)` を `collect_macros(imported=...)` に渡し、
  ローカル定義を足した戻り値が環境。取り込み同士の衝突は `merge_imports` が M021 で、
  取り込みとローカルの衝突は `collect_macros` が V022 で、標準名との衝突は `collect_macros` の
  `standard=` 引数が V021 で、それぞれ報告する。

M021 の span は**取り込み側の `!macroimport` 行**にする。作者が直すべき行だからである。
メッセージと関連位置は元の定義位置を示す。

セッションは `environments: dict[str, MacroEnvironment]` を 1 つだけ持ち、ロード済みのすべての
マクロモジュールとコンパイル中のすべてのコンテンツモジュールを正準パスで登録する。
同梱マクロモジュールも合成識別子 `texflux:prelude` で登録されるので、`frame.macro.module` が
表に無いことは起こり得ない。

### 7.5 語彙的スコープ

`MacroDefinition.module` は定義元モジュールの正準パスである。display パスが必要な診断では
`definition.span.file` を使う。

`expand_macros(..., environments=, module=)` の `_Expander._env(frame)` は、フレームが無ければ
コンパイル中のモジュールの環境を、テンプレート内であれば `frame.macro.module` の環境を返す。
マクロ呼び出しの判定はこの環境に対して行う。これだけで次が自動的に正しくなる。

- テンプレート内に書かれた**引数**は呼び出し側（現フレーム）の環境で展開される。
- テンプレート本体は定義元の環境で展開される。
- `!each` は同じマクロのフレームを派生させるので環境は不変。
- `!param` は展開済みの値を差し込むだけなので環境に無関係。

したがって `main.tfx` が `A.tfxm` を取り込んでも、`A.tfxm` が私的に使う `core.tfxm` の
`!wrapper` は `main.tfx` からは見えない。

再帰検出（D4）は `(module, name)` の組で行う。展開チェーンは 1 モジュールに閉じている間は
名前だけで `foo -> bar -> foo` と綴り、モジュールをまたいだ時点で各要素を `name@file` にする
（`chain_text`）。またいだ先では名前だけではマクロを特定できないからである。

### 7.6 マクロライブラリのバージョン併存

マクロライブラリのバージョン併存という要件は、以上の設計から自動的に満たされる。

```text
old-slide.tfx:  !macroimport{style-v1.tfxm}
new-slide.tfx:  !macroimport{style-v3.tfxm}

main.tfx:
    !import{old-slide.tfx}
    !import{new-slide.tfx}
```

各コンテンツモジュールは、自分の正準 AST が `main.tfx` に差し込まれる前に自分のマクロを
展開し終えている。`A.tfxm → core-v1.tfxm` と `B.tfxm → core-v2.tfxm` が同時に使われても、
それぞれが私的な語彙的依存なので衝突しない。

### 7.7 同梱の標準マクロモジュール

標準フロー制御（`!before` / `!after` / `!around` / `!off` / `!drop`）は
`src/texflux/prelude.tfxm` に置かれた普通の純粋な `.tfxm` である。`load_standard_macros` が
パッケージリソースとして読み、`collect_macro_module` で検証・収集する。セッションは 1 回だけ
読み、すべてのモジュール環境の `base` として seed する。合成識別子 `PRELUDE_MODULE`
（`texflux:prelude`）は語彙的スコープと span のキーであり、インストール先のパスが
言語意味論・span・ソースマップ・外部 AST の `sources` に現れることはない。
読み込みや検証の失敗は文書ではなく配布物の不具合なので、`TeXFluxError` ではなく
`InternalError` にする。利用者の AST に合成された `!macroimport` ノードを差し込むことはしない。

---

## 8. `!import`: コンテンツモジュールのインスタンス化

### 8.1 形式規則

`!import` は手順 8（条件分岐解決後）で処理される。検査はすべてこの時点で行う。

| 条件 | 診断 | span |
| --- | --- | --- |
| suite を持つ（明示的 suite と、`>>` の左側に置かれて合成 suite が付いた場合の両方） | M022 | ノード |
| 必須インライン群が 2 個以上 / 0 個 | M023 / M025 | ノード |
| `[...]` または `<...>` 群がある | M024 | 群 |

有効な用法:

```text
!import{foo.tfx}                 % valid
@{} >> !import{foo.tfx}          % valid（TeX グループで囲む）
@center >> !import{foo.tfx}      % valid
```

### 8.2 配置規則

`!import` は**トップレベル限定ではない**。ブロック値・シーケンス値・条件分岐ペイロードの
いずれの中でも有効である。

```text
!when{appendix}::
    !import{appendix.tfx}

\twocolumn:::
    - !import{left.tfx}
    - !import{right.tfx}
```

禁止されるのは**マクロテンプレート内に書くこと**だけである（V024、`!defmacro` 収集時に検出）。
コンテンツ依存の発見がマクロ展開に依存しなくなり、ソース解析とキャッシュが単純に保たれる。

一方、**マクロの引数として渡された `!import` は有効**である。

```text
!defmacro{framed}{body}::
    @frame::
        !param{body}

!framed::
    !import{slide.tfx}
```

`!param{body}` がテンプレート内で 2 回参照されれば、`slide.tfx` は**2 つの独立した
モジュールインスタンス**としてコンパイルされる。

### 8.3 循環検出

コンテンツ import の循環は M026 である。判定は**アクティブな import スタック**
（正準パスのタプル、ルートが先頭）に対して行い、「一度でも見たパスか」では判定しない。
経路は display パスで、スタック順＋対象を連結して表示する。

同じモジュールを繰り返し import すること自体は合法であり、その都度別インスタンスになる。

### 8.4 `resolve_content_imports`

`_ImportResolver` は脱糖済みの構文 AST を `syntax.map_children` で歩き、`!import` ノードだけを
被 import モジュールの正準ノード列で置換する。`_expand` の手順:

1. §8.1 の形式検査と群の仕分け（必須インライン群 → パス、`BINDING` 群 → 束縛）。
2. パスを §9.1 で解決し、束縛群があれば §5 で解析する。
3. `session.load(...)`、循環検査、`session.compile_content(...)` を**1 つの `try` の中**で行う（§8.5）。
   ロードは対象をパースするので、callee のパースエラーも validation エラーと同じように
   連鎖に載らなければならない。
4. 戻り値 `Document` の `body.nodes` を返す。

### 8.5 import チェーンの診断

被 import モジュールのコンパイル中に起きた `TeXFluxError` は、**span を callee のまま保持**し、
メッセージ末尾に import 元を追記し、関連位置 `imported from here` を足して同型で再送出する
（`error.chained(...)`）。

被 import モジュールは呼び出し元と必ず別ファイルである（同一なら循環検出が先に落とす）。
したがって「span のファイルが import 行のファイルと同じ」エラーは、callee 由来ではなく
**呼び出し側の束縛リストのエラー**だけである。そこに `imported from` を付けても同じ位置を
2 度言うだけなので付けない。深い連鎖では、内側で付かなかったエラーも 1 段上では別ファイルに
なるため、そこから正しく積み上がる。

- span を書き換えないので、エディタの逆引きは**実際に壊れている行**へ飛ぶ。
- 入れ子の import では、内側から順に接尾辞が積み上がる。

```text
c.tfx:4:16: validation error: duplicate macro parameter 'x';
imported from b.tfx:3:1; imported from main.tfx:7:1 [V014]
```

#### 8.5.1 マクロ import の連鎖

同じ規則を `!macroimport` にも適用する。`.tfxm` は複数の `.tfx` から共有されるため、
「どの `!macroimport` がそのモジュールを引き込んだか」は作者が最初に見るべき情報である。

セッションは、各マクロモジュールを**最初に名指しした `!macroimport` のパス群 span** を
`_imported_from` に記録する。各モジュールの記録は 1 つだけなので、この記録の集合は**木**であり、
根は `.tfx` に書かれた `!macroimport` である（`.tfx` は登録されない）。マクロモジュール由来の
エラー（パース・純粋性・自己完結性・名前衝突・パス解決・ロード失敗）は、`_importing`
コンテキストマネージャがこの木を根まで辿って各段の site を追記する。

- **1 段ごとに** `error.span.file == site.file` を判定し、等しい段だけ読み飛ばす。そこで
  **打ち切ってはならない**。ロード失敗（M027）やパス解決の失敗は span が `!macroimport` を書いた
  側にあるので、その段は読み飛ばされるが、その 1 つ上の段——そのファイル自身がどう取り込まれたか——
  は依然として必要である。
- 木を辿るので必ず停止する。念のため訪問済み集合も持つ。
- 1 つのモジュールが複数箇所から取り込まれている場合、記録されるのは**幅優先探索で最初に到達した
  1 箇所**、すなわち最も浅い段のものである。深さをまたぐと「ソース順で先」とは限らない
  （`main.tfx` の 2 行目が `a.tfxm` の 1 行目に勝つ）。
- ルートモジュール自身に書かれた `!macroimport` 行で起きたエラーには何も付かない。

したがって `main.tfx → mid.tfx →(macroimport) impure.tfxm` は次のようになる。

```text
impure.tfxm:2:5: module error: '!nosuchmacro' is not defined in impure.tfxm
and is not available through its own !macroimport;
imported from mid.tfx:1:13; imported from main.tfx:1:1 [M028]
```

内側がマクロ import の site、外側がコンテンツ import の site である。

### 8.6 生 TeX とグルーピング

TeXFlux は被 import モジュールの生 TeX を検証しない。`\newcommand` などの生 TeX マクロは
TeXFlux の名前空間システムの外にある。TeX レベルのグルーピングが必要なら、既存の構造構文で
明示的に書く。import 専用のグルーピングオプションは追加しない。

```text
@{} >> !import{legacy-slide.tfx}
```

---

## 9. パス解決とモジュール識別

### 9.1 解決規則（`resolve_module_path`）

コンテンツ import もマクロ import も、**import を書いたファイルのディレクトリを基準に**解決する。
親がどこから取り込んだかには依存しない。検査順:

1. 空 → M001。
2. NUL を含む → M002。NUL を含むパスは OS が stat すら拒否して `OSError` ではなく `ValueError`
   を投げるので、ファイルシステムに触る前に弾き、どちらの構文から来ても span 付きの
   `ModuleError` にする。
3. `\` を含む → M003。
4. 絶対パス → M004（D6）。
5. 種別の拡張子で終わらない → M005。
6. display の算出（§9.2）。読み込みに失敗すれば M027（`OSError`・`UnicodeError`・`ValueError` を
   `load()` が捕まえる）。
7. identity = `paths.normalized_path(display)`。

`..` は許可する。`compile_with_map(filename=...)` に渡された**ルートのファイル名**は文書中のパスでは
なく呼び出し側の引数なので、ここでは扱わない。壊れたファイル名は `ValueError` のまま呼び出し元へ
返る（CLI はこれを捕捉する）。ビルド構成の誤りに span がない点で `FlagError` と同じ扱いである。

### 9.2 display パス

```python
display = os.path.normpath(
    os.path.join(os.path.dirname(importer), *written.split("/"))
)
```

- ルートモジュールの display は `compile_with_map(filename=...)` に渡された綴り（CLI では
  `str(input_path)`）。
- `display` は `SourceSpan.file` に入り、診断・`% texflux:` コメント・`.tfxmap` の
  `sources[].path` に現れる。
- 同じ identity に異なる display が到達した場合、**最初にロードした display が採用される**
  （パース結果が identity でキャッシュされ、その span は最初の display を持つため）。ロード順は
  決定的なので採用される display も決定的である。ただし「最初」が読み順で最初とは限らない。
  コンテンツ import はソース順の深さ優先だが、マクロ import は幅優先であり、最も浅い段が先に
  ロードされる（§8.5.1）。
- 大文字小文字を区別しないボリュームでは、`normalized_path` の `normcase` が恒等な macOS では
  `b.tfx` と `B.tfx` は 2 つのソースになり、Windows では 1 つになる。

### 9.3 モジュール識別の用途

正準パス identity は、パースキャッシュ、マクロモジュールのグラフ走査、
コンテンツ import の循環検出、依存関係の把握に使う。

---

## 10. セッション API

`CompilationSession` は 1 回のコンパイルに閉じ、ソースキャッシュ・マクロ環境表・読んだファイルの
一覧を持つ。プロセスを跨いで再利用しない。

| メンバー | 役割 |
| --- | --- |
| `CompilationSession(registry=, reader=)` | `reader` は表示綴りからバイト列を返すフック。エディタが未保存バッファを差し込むために使う（`doc/diagnostics.md`） |
| `load(display, span)` | 1 ファイルを読んでパースし、正準パス identity でキャッシュする。失敗は `span` を責める M027 |
| `loaded()` | 読んだ全ファイル（`LoadedSource`）。ルートが先頭、以降はロード順。パースより前に記録するので、パースに失敗したファイルも載る |
| `display(path)` | ロード済みモジュールの display 綴り |
| `compile_root(text, filename=, data=, flags=)` | 呼び出し側が文字列で渡した文書のコンパイル |
| `compile_content(source, bindings=, caller_flags=, stack=)` | 被 import モジュールを 1 インスタンスとしてコンパイル |

`compile_with_map` / `compile_ast` / `diagnose` はすべてこのセッションを通る。`source_bytes` は
ルートモジュールのハッシュを実ファイルのバイト列と一致させるための引数で、CLI は必ず渡す。
被 import モジュールはセッションが自分でバイト列を読むので常に正確である。import の基準
ディレクトリは `os.path.dirname(filename)` であり、`filename="<string>"` ならカレントディレクトリ
基準になる。

`render.CompilationResult.sources` はソースマップに渡す `LoadedSource` の列である。
`LoadedSource` はレンダリング結果に付随するメタデータなので `render.py` に置き、`modules.py` が
import する（逆向きの依存を作らない）。

---

## 11. ソースマップの複数ソース化

`serialize_source_map(result, generated_path=, map_path=, generated_bytes=)` はソース一覧を
`result.sources` から取る。

- **id 0 は必ずルート**。以降はフラグメント内の初出順。どちらも決定的である。
- `sources` 配列にはフラグメントを持つファイルだけを書く。ロードはされたがフラグメントを 1 つも
  持たないファイル（`.tfxm`、内容が全部落ちた `.tfx`）は載せない。`load_source_map` は載っている
  ソースを全部ハッシュ検証するので、無関係なファイルを載せると SyncTeX に不要な `Input` レコードが
  増える。
- import を使わない単一ファイル文書では `sources` は 1 要素・全 mapping の `id` は 0 になり、
  モジュール機構が無かった頃の `.tfxmap` とバイト単位で同一である。フォーマットの `version` は 1。
- 被 import モジュールの正準 AST ノードは**自分の span を保持する**。呼び出し側の `!import` 行へ
  付け替えてはならない。`--source-comments` の `% texflux: <file>:<line>` もこれにより自動的に
  正しいファイル名を出す。
- `remap.py` は複数ソースに対応しており、`--map` 1 個で複数ソースを扱える。

---

## 12. CLI

```bash
texflux compile main.tfx -o main.tex
texflux compile main.tfx -o main.tex --flag draft --flag handout=off
```

- `--flag` は**ルートモジュールにのみ**適用される。被 import モジュールへの伝播は
  `!import{...}($flag)` による明示的な転送だけである。
- `ModuleError` は `TeXFluxError` 派生なので、他の診断と同じ 1 行で終了コード 1 になる。
- 深すぎる import ネストは `RecursionError` として拾う。import 段数に人工的な上限は設けない
  （循環は §8.3 で検出される）。

---

## 13. 診断の一覧

すべて `ModuleError`（`kind = "module"`、表示は `module error`）。コード・生成箇所・メッセージの
正本は `doc/diagnostics.md` §2.6 にある。

| コード | 条件 | span |
| --- | --- | --- |
| M001 | パスが空 | 群 |
| M002 | パスが NUL を含む | 群 |
| M003 | `\` を含む | 群 |
| M004 | 絶対パス | 群 |
| M005 | 構文に対する拡張子の不一致 | 群 |
| M006 | 束縛リストがインラインテキストでない | 群 |
| M007 | 束縛リストが空 | 群 |
| M008 | 束縛の構文不正 | 部分 |
| M009 | callee が未宣言のフラグ | 名前 |
| M010 | 同じ callee フラグの二重束縛（関連位置: 最初の束縛） | 名前 |
| M011 | `$x` が caller に無い | 値 |
| M012 | `!macroimport` が `>>` セグメント | セグメント |
| M013 | `.tfxm` 内の禁止特殊 | ノード |
| M014 | `.tfxm` のトップレベル純粋性違反 | ノード |
| M015 | `!macroimport` に suite | ノード |
| M016 | `!macroimport` に `(...)` | 群 |
| M017 | `!macroimport` の群が 1 個でない | ノード |
| M018 | `!macroimport` のパスが必須群でない | 群 |
| M019 | 同一モジュールの二重取り込み（関連位置: 最初の取り込み） | 群 |
| M020 | `!macroimport` が非トップレベル | ノード |
| M021 | 取り込みマクロ名の衝突（関連位置: 定義位置） | `!macroimport` |
| M022 | `!import` に suite | ノード |
| M023 / M025 | `!import` の必須群が 2 個以上 / 0 個 | ノード |
| M024 | `!import` に `[...]` / `<...>` | 群 |
| M026 | コンテンツ import の循環 | ノード |
| M027 | ファイルが読めない | 群 |
| M028 | `.tfxm` の自己完結性違反 | ノード |
| M029 | `normalize()` 直呼び | ノード |

モジュール構文に関係するが他の種別で報告するもの:

| コード | 条件 |
| --- | --- |
| V024 | テンプレート内の `!import` / `!macroimport`（モジュール経路では `!macroimport` は M020 が先に出るので、この分岐が実際に使われるのは `normalize()` 直呼びの経路と `!import`） |
| V022 | 取り込み名とローカル定義名の衝突 |
| V021 | 標準フロー制御と同名の定義 |
| P005 / P007 / P008 / P019 / P020 | `(...)` の走査（ブレースの不釣り合い、閉じない、群より前、2 個以上） |

---

## 14. 明示的な非対象

```text
パッケージレジストリ
セマンティックバージョン解決
lockfile
リモートパッケージ取得
public / private / export 宣言
修飾名（qualified name）
再エクスポート構文
任意のフラグ式
真偽値以外のモジュール引数
動的 import パス
マクロテンプレートから生成される !import
生 TeX マクロの自動隔離
LaTeX の意味検証
!asset
```

加えて次も対象外とする。必要になったときの設計の出発点を添える。

- **依存関係の出力コマンド**（`--deps` 相当）。`session.loaded()` が全ソースを持っているので
  実装は軽いが、条件分岐で依存グラフが変わる点をどう扱うかは要設計。
- **モジュールインスタンスの正準 AST キャッシュ**（D7）。同じモジュールを同じフラグで 2 回
  import すると 2 回コンパイルする。`(path, frozenset(flags.items()))` をキーにすれば安全に効かせられる。
- **`.tfxm` の変更検出**。`.tfxm` はフラグメントを持たないため `.tfxmap` の `sources` に載らず、
  ソースマップだけでは陳腐化を検出できない。これはビルドシステムの責務とする。

---

## 15. 規範的要約

```text
1 ファイル = 1 モジュール

.tfx:
    コンテンツモジュール
    ローカル !flag / ローカル !defmacro / !macroimport / !import

.tfxm:
    純粋なマクロ定義モジュール
    !defmacro / !macroimport / コメント / 空行 のみ
    フラグなし、条件式なし、コンテンツなし、!import なし

!macroimport:
    私的・非推移的・語彙的
    取り込むのは対象モジュールが自分で定義したマクロだけ
    取り込んだ依存は再輸出されない
    トップレベル宣言であり >> セグメントになれない

!import:
    独立したコンテンツモジュールのインスタンス化
    (...) による明示的フラグ束縛（on / off / $callerFlag）
    マクロ・フラグの名前空間は一切漏れない
    結果の正準 AST が import 位置に差し込まれる
    suite を取らず、>> のラッパーになれない
    マクロテンプレート内には書けない

同一コンテンツモジュールの繰り返し import:
    許可。各 import は別インスタンス

コンテンツ import の循環:
    エラー（アクティブな import スタックで判定）

マクロモジュールの循環:
    許可（.tfxm が純粋宣言的である限り）

マクロ名のシャドーイング:
    禁止。last-import-wins は存在しない

ソースマッピング:
    被 import 正準 AST は callee の span を保持する

レンダラ:
    正準 AST しか知らない。モジュール境界は到達前に消える
```
