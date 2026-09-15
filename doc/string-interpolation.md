# 文字列 Interpolation（`!text`）設計

## 0. ステータス

- 種別: 設計書。実装済み。
- 規範定義は `texflux_tex_first_dsl_v1_spec.md` §10.6、利用者向け説明は `doc/dsl.md` §12.7、
  診断コードは `doc/diagnostics.md` §2.5、テストは `tests/test_interpolation.py` と
  `tests/golden/macro-interpolation`。実装は `src/texflux/interpolate.py` と `src/texflux/macros.py`。
- 本書が扱うのは、設計判断とその根拠、字句規則、許可・禁止コンテキストの完全表、provenance の規則、
  および実装が守る不変条件である。

この機能の一行要約:

> **AST マクロのテンプレート内にある「不透明な文字列フィールド」にだけ穴を開ける機能**

ソース全体に対する文字列プリプロセッサ（m4 / C preprocessor 相当）ではない。機能名としても
「string macro」ではなく「structural AST macro における string interpolation」である。

---

## 1. 決定表

| # | 決定 | 根拠 |
|---|---|---|
| D1 | `!param{name}` は AST 位置、`!text{name}` はテキスト位置。使用側が型を決める（use-site typing）。パラメータ宣言の文法は変更しない | 既存 `!defmacro` の文法と `Value = tuple[SyntaxNode, ...]` を維持できる。同じパラメータを必要に応じて AST / text として扱え、「まず Value があり、template がその使い方を決める」という設計と整合する |
| D2 | 補間対象は **TeXFlux が内容を構文解析しない不透明な文字列フィールドのみ** | 本機能は「文字列 interpolation」であり、構文解析やチェックを伴う場所には持ち込まない |
| D3 | 挿入された文字列は再走査しない（no-rescan） | 再帰的テキスト展開・生成構文を原理的に排除する |
| D4 | コンパイラ metadata（flag 名・マクロ名・パラメータ名・import path・構造名）には一切許可しない | 依存グラフと名前解決を静的に保つ |
| D5 | provenance は fragment 単位。`RawTex` / `Argument` / `BraceGroup` に `parts` を持たせ、renderer が fragment ごとに `emit()` する | `!param` と同等の逆引き品質を保つ。`\foo{pre-!text{x}-post}` で `x` の値が別行にある場合、PDF から値の行へ飛べる |
| D6 | `RenderRole` `"scaffold"`（rank 1）をテンプレート側リテラルに与える | §10.4 参照。これが無いと列情報なしの SyncTeX 入力で `remap` が `ambiguous source mappings` を送出する |
| D7 | built-in special の group は例外なく metadata 扱い（fail-closed） | 残っている built-in special（`!import` / `!macroimport`）の group はコンパイラ側のメタデータを指す。将来 built-in special を足すときも、その special が group を明示的に「出力 TeX text」だと宣言しない限り補間しない。最初から「すべての SpecialInvocation の str group を補間」してはならない——special が増えたときにコンパイラ metadata が意図せず動的化するからである。マクロの値は A5 の経路で補間されるので `!before{\vspace{!text{gap}}}` は書ける |
| D8 | マクロテンプレート外のテキストフィールドに marker があればエラーにする | 綴り間違いが黙って TeX に流れる事故を防ぐ |
| D9 | 補間の escape は `!!text{` と `\!text{` の 2 系統。独立した行頭 `!!` raw-line escape と二層になる（§3.3）。raw mode 領域と行頭 `!\| ` は補間しない | `\!` は TeX の負の細空白という実在コマンドなので backslash escape は必須。行頭 `!!` は `@@` と対になる別機能として parser が先に 1 文字剥がす。raw mode と `!\| ` は行全体を verbatim にするため、補間走査の対象外になる |

---

## 2. スコープと用語

### 2.1 用語

- **structural splice**: `!param{name}`。束縛された `Value`（AST ノード列）を AST 位置へ挿入する。
- **text interpolation**: `!text{name}`。束縛された `Value` から取り出した文字列を、
  文字列フィールドの途中へ挿入する。
- **marker**: 文字列中に現れる `!text{` および `!param{` の 6 / 7 文字。
- **hole**: `!text{name}` 全体。補間で値に置き換わる範囲。
- **text field**: TeXFlux が内容を構文解析しない `str`。具体的には
  `RawTex.text`、インライン `Argument.value`（`str` の場合）、`BraceGroup.header_raw`。
- **control metadata**: TeXFlux 自身のコンパイル動作を決める文字列。マクロ名、
  パラメータ名、flag 名、`!when` / `!unless` の条件、import path、import binding、
  command / environment / special の構造名。

### 2.2 テキストフィールドの見分け方（規範）

ある `str` が text field であるための必要十分条件は、

> **その文字列が、TeXFlux のどのパスでも内容を解析されず、最終的にそのまま TeX 出力へ落ちること**

である。「Python の型が `str` だから」は理由にならない（D7）。

---

## 3. 構文と字句規則

### 3.1 綴り

```text
!text{NAME}
```

`NAME` の文法は既存のマクロパラメータと同一で、`macros._PARAM_NAME_RE` を共有する:

```text
[A-Za-z_][A-Za-z0-9_-]*
```

### 3.2 走査対象

走査するのは次の 2 つの marker だけである。

| marker | 綴り | 長さ |
|---|---|---|
| text hole | `!text{` | 6 |
| param misuse | `!param{` | 7 |

`!param{` を走査するのは、それを**エラーにする**ためである（§7.2）。

### 3.3 escape

次の表の入力は、parser による raw-line escape 処理後のテキストフィールドである。

| 入力 | 出力 | 備考 |
|---|---|---|
| `!!text{NAME}` | literal `!text{NAME}` | 生成した `!text{` は再走査しない |
| `!!param{NAME}` | literal `!param{NAME}` | 同上 |
| `\!text{NAME}` | そのまま（marker と見なさない） | `syntax.is_escaped` による。`\!` は TeX の負の細空白 |

行頭では parser の raw-line escape と補間走査器の escape の二層になる。マクロテンプレート内では:

```text
!!text{x}   → parser が ! を1つ剥がす → RawTex "!text{x}" → 補間される
!!!text{x}  → "!!text{x}" → 走査器が escape → リテラル "!text{x}"
!| !text{x} → parser が marker を剥がす → RawTex "!text{x}" (verbatim) → 補間されない
```

最後の行が `!!!text{x}` と違うのは、剥がす段ではなく**補間そのものが行われない**点である。
`!| ` は 1 行の raw mode なので、テンプレート内でも値を差し込めない。

`!BEGIN_RAW_MODE` と対応する `!END_RAW_MODE` の間の raw mode 領域と、行頭 `!| ` の本文は、
テキストフィールドであっても補間走査の対象外である。領域内の `!text{...}` はリテラルのまま出力され、
`!!` / `@@` の行頭 escape も働かない。どちらも parser が物理行層で消費し、`RawTex.verbatim` によって
この規則を macro expansion に伝える。

エスケープせず AST 位置に書いた `!text{x}` は E009 になる。テンプレート外の `!!text{x}` は parser 後に
未エスケープの hole となるため E007 になる。

### 3.4 marker と見なさないもの

以下はすべてただの文字列である。専用エラーにもしない。

- `!text {x}` — `{` の直前に空白がある。
- `!textbf{x}` — marker は完全一致でしか成立しない。
- `!text` — 直後に `{` が無い。
- `\!text{x}` — backslash escape（§3.3）。

### 3.5 走査アルゴリズム（規範）

```text
scan(text) -> list[Piece] | None

    if "!" not in text:
        return None                      # 高速パス。marker の無い文書は完全に無変更
    i = 0
    last = 0
    pieces = []
    found = False
    loop:
        j = text.find("!", i)
        if j < 0:
            break
        if is_escaped(text, j):          # syntax.is_escaped
            i = j + 1
            continue
        if text.startswith("!!text{", j):
            emit_literal(text[last:j]); emit_literal("!text{")
            i = last = j + 7; found = True; continue
        if text.startswith("!!param{", j):
            emit_literal(text[last:j]); emit_literal("!param{")
            i = last = j + 8; found = True; continue
        if text.startswith("!text{", j):
            close = text.find("}", j + 6)
            if close < 0:
                raise E005 at marker_span(j, 6)
            name = text[j + 6 : close]
            if _PARAM_NAME_RE.fullmatch(name) is None:
                raise E006 at marker_span(j, close + 1 - j)
            emit_literal(text[last:j]); emit_hole(name, marker_span(j, close + 1 - j))
            i = last = close + 1; found = True; continue
        if text.startswith("!param{", j):
            raise E008 at marker_span(j, 7)
        i = j + 1
    emit_literal(text[last:])
    return pieces if found else None
```

規範上の要点:

1. `}` は**最初に現れたもの**を終端とする。名前に brace は現れ得ないので入れ子は見ない。
2. 名前の検証は `fullmatch` である。空文字 `!text{}` も E006 になる。
3. `found` が偽（escape も hole も無かった）なら `None` を返す。呼び出し側はノードを一切作り替えない。
   marker を含まない文書のふるまいとバイト列が、補間の有無で変わらないことを保証する。
4. `!!!text{x}` は `!` + escape として読まれ `!!text{x}` を出力する（doubling の自然な帰結）。

### 3.6 `text` の予約

`Reserved.TEXT = "text"` により `!defmacro{text}{x}:` は `'!text' is reserved by TeXFlux`（V019）になる。

---

## 4. データ構造

### 4.1 `ast.py`

```python
@dataclass(frozen=True, slots=True)
class TextFragment:
    """One provenance-tagged run of a text field."""

    text: str
    span: SourceSpan
    #: Template literals rank below caller content in columnless SyncTeX.
    scaffold: bool = False


SourceText: TypeAlias = tuple[TextFragment, ...]
```

`RawTex.parts` / `Argument.parts` / `BraceGroup.header_parts` は `SourceText | None` で、既定値 `None` の
末尾フィールドである。

### 4.2 不変条件

`parts is None` は「フィールド全体が `span` 由来」を意味する既定状態である。`parts is not None` のときは
次を満たさなければならない。`__post_init__`（`ast._check_parts`）で検査する。

| ノード | 不変条件 |
|---|---|
| `RawTex` | `plain_text(parts) == text` |
| `Argument` | `isinstance(value, str)` かつ `plain_text(parts) == value` |
| `BraceGroup` | `plain_text(header_parts) == header_raw` |

違反時は `ValueError`（利用者向けの診断ではなく実装バグなので `TeXFluxError` ではない）。

### 4.3 `RenderRole` と `.tfxmap`

`render.RenderRole` は `"scaffold"` を含む。`TextFragment` は role 値を持たず `scaffold: bool` だけを持つので、
`ast.py` が `render.py` に依存する必要はない。`remap._ROLES` は `get_args(RenderRole)` から導かれ、
`remap._ROLE_RANK` には `"scaffold": 1` が**明示的に**ある（欠けると `KeyError`）。

`.tfxmap` の `version` は 1 のままである。role 語彙は「the renderer that writes the map」が所有しており、
その拡張は形式の変更ではない。

### 4.4 `Value` は変更しない

`Value: TypeAlias = tuple[SyntaxNode, ...]` はそのまま。text 用の別型は導入しない（D1）。

---

## 5. `interpolate.py`

`flags.py` / `syntax.py` と同じ粒度の小さなモジュール。TeX parser も TeXFlux parser も持たない。
公開 API は 3 つである。

```python
def interpolate(
    text: str,
    *,
    origin: SourceSpan,      # 診断とオフセット計算に使う「書かれた場所」
    target: SourceSpan,      # リテラル部が名乗る span（retarget 済みの呼び出し位置）
    offset: int,             # origin.start.column から text の先頭までの文字数
    lookup: _Frame | None,  # None はマクロテンプレート外を意味する
) -> SourceText | None:
    """Interpolate one text field, or return None when it has no marker."""


def reject_markers(text: str, span: SourceSpan, where: str) -> None:
    """Reject a marker written where interpolation is forbidden."""


def text_value(name: str, frame: _Frame) -> SourceText:
    """Read one bound parameter as text, or raise the fitting diagnostic."""
```

### 5.1 `interpolate` の意味論

- `origin` は**再ターゲット前**のノード span。診断の行・列はここから作る。`!param` の診断と同じく、
  エラーはマクロ**定義**の位置を指す。
- `target` は `_Expander._span(node.span, frame)` の結果、すなわち再ターゲット後の呼び出し位置。
  **リテラル部の fragment はこれを名乗る**。
- `offset` は `origin.start.column` から実際のテキスト先頭までの距離。`RawTex` は `0`、インライン
  `Argument` は `1`（開き delimiter の分）。
- hole は `text_value(name, frame)` が返す `SourceText` を**そのまま連結**する。1 文字も書き換えず、
  走査もしない（D3）。
- リテラル部は `TextFragment(literal, target, scaffold=lookup is not None)`。`scaffold` は「macro template
  が書いたリテラル」を低優先度に落とすための role なので、`lookup is None`（テンプレート文脈の外＝
  呼び出し側やトップレベルのテキスト）で走った補間のリテラルは呼び出し側の content であり、`scaffold` を
  名乗ってはならない。名乗ると、呼び出し側の値に含まれる escape（`!!text{...}`）が content の rank を失い、
  列情報なしの SyncTeX 入力で `ambiguous source mappings` になり得る。
- 返り値は §3.5 の `found` が真のときだけ `SourceText`、偽なら `None`。

macros.py 側は `_text_field` が「`parts` が既にあれば補間しない」規則を一箇所で持ち、`RawTex` 行・
インライン群・マクロ呼び出しの compact 値の 3 経路がそれを共有する。

### 5.2 診断 span の作り方

`marker_span(origin, offset, length)` は `origin` の中の 1 範囲を返し、算出した end が `origin.end` を
超える場合は `origin` そのものを返す。再ターゲットされたノードの span は元のテキストより短いことがあり、
span は決して逆転してはならないためである。

### 5.3 `text_value` の判定順序（規範）

```python
def text_value(name, frame):
    if name in frame.sequences:
        raise E001           # rest parameter
    if name not in frame.values:
        raise E002           # unknown parameter
    value = frame.values[name]
    if len(value) != 1 or not isinstance(value[0], RawTex):
        raise E003           # not a text value
    node = value[0]
    if node.parts is not None:
        return node.parts
    return (TextFragment(node.text, node.span),)
```

`node.parts` をそのまま返すことが、nested composition で provenance が推移的に保たれる理由である。
`outer` が組み立てた `sec-intro` を `inner` が `\label{...}` に埋めても、`sec` と `intro` はそれぞれの
出どころを保つ。返される fragment には `scaffold=True` のものが混ざり得る（外側テンプレートのリテラル `-`
など）。その flag も保存する。

---

## 6. パイプライン上の位置

補間は `expand_macros()` の内部で行う。

```text
parse
  -> validate_macro_forms / validate_flag_forms / validate_macroimport_forms
  -> desugar(>>)
  -> collect_flags
  -> resolve_macro_imports
  -> collect_macros
  -> expand_macros            ← ここに補間が入る
  -> resolve_content_imports
  -> canonicalize
  -> render
```

帰結:

- `>>` は補間より前に desugar 済みなので、stack 専用規則は不要である。
- `canonicalize` より前なので、補間はマクロフレームが生きている間に完了する。正準化以降のパスは
  補間を知らない。
- `_conditional` は drop 時に `self.block()` を呼ばずに `()` を返す。したがって **dropped payload 内は
  補間されない**。

次の 2 つの実装形は採らない: 「ソーステキスト → 全体の文字列前処理 → parse」と
「TeX を render → 文字列置換」。どちらも no-rescan（D3）と provenance（D5）を壊す。

> **静的な事前走査パスを追加してはならない。** `AGENTS.md` は「dropped payload の内側に踏み込む規則は
> 4 つだけであり、5 つ目を足すなら『壊れた内容を無効化できる』保証を捨てられるか判断せよ」と定めている。
> 本機能はその判断を必要としない設計にしてある。

---

## 7. コンテキストの完全表

### 7.1 許可（補間する）

| # | コンテキスト | 判定を行う関数 |
|---|---|---|
| A1 | マクロテンプレート内の `RawTex.text` | `_Expander.node()` の `RawTex` 分岐 |
| A2 | `ParsedInvocation` のインライン group（`{...}` `[...]` `<...>`、`str` 値） | `_Expander._argument()` |
| A3 | `@{...}` literal brace container の header group | 同上（`InvocationKind.BRACE`） |
| A5 | ユーザーマクロ呼び出しの required compact group（標準フロー制御の呼び出しを含む） | `_Expander._values()` |

A2 は `>>` の desugar 後も同じ経路を通るので、`@foo >> @bar >> @hoge{!text{x}}:` は自動的に許可される。

A5 は**束縛の前**に補間する。結果のテキストが 1 個の `RawTex` Value として束縛される。これが
macro composition（§12.3）を成立させる。`interpolate` に渡す frame は**外側の**フレームである。
引数は呼び出し側のテンプレートに書かれているので、そこで見える束縛で解決するのが正しい。

### 7.2 禁止（marker を検出したらエラー）

| # | コンテキスト | 判定を行う関数 | 診断 |
|---|---|---|---|
| B1 | AST ノード位置の `!text`（`SpecialInvocation(name="text")`） | `_Expander.node()` | E009 |
| B2 | マクロテンプレート外の text field | `interpolate()`（`lookup is None`） | E007 |
| B3 | text field 内の `!param{` | `interpolate()` | E008 |
| B4 | `!when` / `!unless` の group | `_Expander._conditional()` | E004 |
| B5 | `!param` / `!each` の name group | `_Expander._single_name()` | E004 |
| B6 | built-in special の group（`!import` / `!macroimport` と未知の special） | `_Expander._argument()` | E004 |
| B7 | `(...)` binding list（`GroupKind.BINDING`） | `_Expander._argument()` | E004 |

B4〜B7 は文字列に `!text{` / `!param{` の綴りが含まれるかを検査する。これらは補間対象のテキストではなく、
escape の処理も行わないため、`!!text{` / `\!text{` でメタデータに marker を書くことも許可しない。

### 7.3 既存経路で自然にエラーになるもの（専用診断を設けない）

いずれも top-level 限定の宣言であり、dropped payload の問題も起きない。

| コンテキスト | 実際に出るエラー |
|---|---|
| `!flag{!text{x}}{on}` | `invalid build flag name '!text{x}'`（V009） |
| `!defmacro{foo}{!text{x}}:` | `invalid macro parameter name '!text{x}'`（V013） |
| `!defmacro{!text{n}}...` | `invalid macro name '!text{n}'`（V018） |
| `!macroimport{!text{p}}` | モジュール解決のエラー。`resolve_macro_imports` は `expand_macros` より前に走り、ノードを取り除くので expander は見ない |
| `@!text{env}:` | `parser` が `!` を環境名に許さず `invalid structural name` |

### 7.4 構造名は生成できない

command / environment / special / macro の名前は `parser.HeaderScanner._segment` が決める。`!` や `{` は
名前文字ではないので、名前位置に marker を書くことは文法上できない。実装側で追加の防御は不要である。

---

## 8. text-extractable な値

### 8.1 規範

`Value` が**ちょうど 1 個の `RawTex` ノードからなる**ときに限り text-extractable とする。それ以外は E003。

### 8.2 具体例

| 呼び出し | 束縛される Value | text-extractable |
|---|---|---|
| `!foo{hello}` | `(RawTex("hello"),)` | ✅ `"hello"` |
| `!foo{}` | `(RawTex(""),)` | ✅ `""`（空文字は正当） |
| `!foo::` ＋ 1 行 | `(RawTex("LINE"),)` | ✅ |
| `!foo::` ＋ 2 行 | `(RawTex, RawTex)` | ❌ E003 |
| `!foo::` ＋ `@center::` | `(ParsedInvocation,)` | ❌ E003 |
| rest パラメータ本体 | `tuple[Value, ...]` | ❌ E001 |
| `!each` の item が 1 行 | `(RawTex("alpha"),)` | ✅ |
| `!each` の item が環境 | `(ParsedInvocation,)` | ❌ E003 |

### 8.3 やってはならないこと

`AST -> normalize -> render -> string` という経路を `!text` のために実行してはならない。
`!text` は**すでに文字列である値を取り出すだけ**である。

---

## 9. provenance と描画

### 9.1 fragment の role

| fragment | role |
|---|---|
| テンプレートのリテラル部（`scaffold=True`） | `"scaffold"` |
| hole に入った呼び出し側の値 | その位置の既定 role（`RawTex` と group content なら `"content"`） |
| 呼び出し側・トップレベルのテキストを走査して得たリテラル部（`scaffold=False`） | その位置の既定 role |

### 9.2 `render.py`

`_emit_text(emitter, text, parts, span, base_role)` が、`parts` が無ければフィールド全体を 1 回、
あれば fragment ごとに `emit()` する。`RawTex` 行、`_emit_group` の group content、`BraceGroup` の header の
3 箇所がこれを通る。**出力される TeX 文字列は単純連結と完全に同一**である（`MappedEmitter.emit` を順に
呼ぶだけなので自動的に満たされる）。`RawTex(text="")` の分岐（空行）は変更しない。空文字を補間した結果にも
`parts` は付き得るが、文字を持つ fragment はなく、空行を出力する。

### 9.3 期待される provenance（規範例）

```text
1: !defmacro{m}{x}::
2:     \foo{pre-!text{x}-post}
3: !m::
4:     VALUE
```

出力 1 行目 `\foo{pre-VALUE-post}` の fragment:

| text | source | role |
|---|---|---|
| `\foo{pre-` | `m.tfx:3:1`（呼び出し） | `scaffold` |
| `VALUE` | `m.tfx:4:5`（値） | `content` |
| `-post}` | `m.tfx:3:1`（呼び出し） | `scaffold` |

補間されたテキストをマクロ**定義**行（2 行目）へマップしてはならない。

---

## 10. 他のパスとの関係

### 10.1 `parser.py` — 変更なし

- `\includegraphics[width=!text{w}]{fig/!text{n}.pdf}` は「閉じた単一 segment の command 行は ordinary TeX」
  と判定されて `RawTex` になる。`scan_group` の `[` 走査は brace 群を不透明に飛ばすので `!text{w}` の
  brace で誤らない。
- `@hoge{!text{x}}:` は `scan_group` が brace の入れ子を正しく数え、group の中身は opaque な `str` のまま残る。
- `!inner{!text{a}-!text{b}}` も 1 個の required group として scan される。

したがって補間に伴う一般文法の変更はない。行頭 `!!` の raw-line escape は parser が処理し、補間走査器は
その後の文字列を読む（D9、§3.3）。行頭 `!| ` の行は `RawTex.verbatim` が立つので、補間走査器に渡らない。

### 10.2 `!text` が AST 位置に現れる経路

2 つだけである。どちらも E009 になる。

1. 行頭の `!text{x}` — `SpecialInvocation(name="text")` として構造化される。
2. `:::` sequence suite の `- !text{x}` — sequence entry の通常の構造化値として同じノードを作る。
   `+ {!text{x}}` は明示グループ内の raw text field なので、マクロテンプレート内では `!text` が補間される。

### 10.3 `normalize.py`

`_normalize_invocation` の `InvocationKind.BRACE` 分岐が `node.groups[0].parts` を `header_parts` として
`BraceGroup` に渡す。それ以外に `normalize.py` は `RawTex.text` を切り刻まず、`parts` は正準化を素通りする。
シーケンス値の明示配置は `+` エントリーの `argument_kind` で決まり、`-` の本文が `{` で始まるかどうかを
`.text` から推測しない。

### 10.4 `remap.py` — `_ROLE_RANK` の `"scaffold": 1` は必須

`remap._select_mapping` は、列情報を持たない SyncTeX レコードに対して「同一生成行の候補のうち最良 rank の
ものが複数のソース行を指していたら `RemapError: ambiguous source mappings` を投げる」。

fragment provenance を素朴に入れると、§9.3 の例で `\foo{pre-` が `m.tfx:3`、`VALUE` が `m.tfx:4` を指し、
**両方 `content`（rank 0）**になって上記の例外が出る。`"scaffold": 1` を与えることでテンプレート側が rank 1
へ下がり、`!param` と同じく「呼び出し側の値が勝つ」序列が再現される。

なお `remap._rewrite_link` は `mapping.source_start.line` **だけ**を使う。列の精度は逆引き結果に影響しない。
差が出るのは「呼び出し行と値の行が異なる」場合、すなわち `::` ブロックスイートで値を渡した場合だけである。

残る曖昧性: 1 つの生成行に**異なるソース行由来の hole が 2 つ以上**並んだ場合（`\foo{!text{a}!text{b}}` の
`a` と `b` を別々の suite 行で渡した場合）は依然として ambiguous になる。compact group 呼び出しでは起こらない。
これは `!param` が既に持つ制約と同種であり、本機能で対策はしない。

### 10.5 `modules.py`

`_TEMPLATE_NAMES` に `Reserved.TEXT` が含まれる。`_check_self_contained` は `.tfxm` の template 内の
`SpecialInvocation` 名を走査するので、これが無いと AST 位置の `!text` が誤った M028 になり、正しい E009 が
expander から出なくなる。

lexical scope と import graph は補間の有無で変化しない。`!text` はマクロ名を一切 lookup しないし、
`!import` / `!macroimport` を生成できない（§7.2 B6・§7.3）。

### 10.6 `source_map.py` — 変更なし

role はそのまま直列化される。形式バージョンも 1 のままである（§4.3）。

---

## 11. 診断表

メッセージは `file:line:column: macro error: message [CODE]` の形で提示される。`MacroExpansionError` は
`macros._error()` を経由し、フレーム内なら `; while expanding '<chain>' called at <location>` を付け、
関連位置 `called here` を持つ。`!param` の診断と完全に同じ体裁になる。

| コード | 条件 | メッセージ | span | chain |
|---|---|---|---|---|
| E009 | `!text` が AST ノード位置に現れた | `!text is only valid inside a textual field; use !param for an AST position` | ノードの span | あり（frame がある場合） |
| E007 | マクロテンプレート外の text field に `!text{` | `!text is only valid inside a macro template` | marker の span | なし |
| E002 | 未知のパラメータ名 | `unknown macro parameter '<name>'` | hole の span | あり |
| E001 | rest パラメータを `!text` した | `'<name>' is a rest parameter; use !each to access its values` | hole の span | あり |
| E003 | text-extractable でない値 | `macro parameter '<name>' is not a text value; use !param for structural values` | hole の span | あり |
| E006 | 名前が文法に合わない（空を含む） | `invalid !text parameter name '<name>'` | hole 全体の span | あり |
| E005 | `}` が見つからない | `unterminated !text{...}` | marker の span | あり |
| E008 | text field 内の `!param{` | `!param cannot be used inside a text field; use !text for text interpolation` | marker の span | あり |
| E004 | `!when` / `!unless` の group に marker | `interpolation is not allowed in a !<name> flag group; flag names are static` | その group の span | あり |
| E004 | `!param` / `!each` の name group に marker | `interpolation is not allowed in a !<name> name group` | その group の span | あり |
| E004 | built-in special の group に marker | `interpolation is not allowed in !<name>'s arguments` | その group の span | あり |
| E004 | `(...)` binding list に marker | `interpolation is not allowed in a '(...)' binding list` | その group の span | あり |

E001〜E003 は `text_value` が hole の span を知らないので frame の呼び出し位置で送出し、`interpolate` が
hole の span に付け替えて再送出する。E004 は `reject_markers` が送出し、`_Expander._reject` が frame 付きに
包み直す。いずれもコードは引き継ぐ（`doc/diagnostics.md` §2.1）。

### 11.1 診断例

```text
m.tfx:2:11: macro error: unknown macro parameter 'nope'; while expanding 'm' called at m.tfx:3:1 [E002]
m.tfx:2:5: macro error: !text is only valid inside a textual field; use !param for an AST position; while expanding 'm' called at m.tfx:4:1 [E009]
m.tfx:1:12: macro error: !text is only valid inside a macro template [E007]
m.tfx:2:11: macro error: macro parameter 'body' is not a text value; use !param for structural values; while expanding 'm' called at m.tfx:4:1 [E003]
```

---

## 12. 代表例

### 12.1 画像

```text
!defmacro{figure}{name}{width}{caption}::
    \includegraphics[width=!text{width}]{fig/!text{name}.pdf}
    @center::
        @minipage{!text{width}}::
            !param{caption}
!figure{result}{0.8\textwidth}::
    Result of the experiment
```

```tex
\includegraphics[width=0.8\textwidth]{fig/result.pdf}
\begin{center}
\begin{minipage}{0.8\textwidth}
Result of the experiment
\end{minipage}
\end{center}
```

### 12.2 環境引数（text）と本体（AST）の併用

```text
!defmacro{styled}{style}{body}::
    @foo >> @bar >> @hoge{!text{style}}::
        !param{body}
```

`style` は text splice、`body` は AST splice。

### 12.3 ラベル生成と macro composition

```text
!defmacro{label}{id}::
    \label{!text{id}}

!defmacro{sectionlabel}{prefix}{id}::
    !label{!text{prefix}:!text{id}}
```

### 12.4 `!each` の item

```text
!defmacro{labels}{...items}::
    !each{items}{item}::
        \label{item:!text{item}}

!labels:::
    - alpha
    - beta
```

### 12.5 不正例

```text
!defmacro{bad1}{x}::
    @hoge{!param{x}}::       # E008
        A

!defmacro{bad2}{flag}{body}::
    !when{!text{flag}}::     # E004
        !param{body}

!defmacro{bad3}{x}::
    !text{x}                   # E009

!defmacro{bad4}{...items}::
    \foo{!text{items}}         # E001
```

---

## 13. 非目標

本機能は次を提供しない。将来必要になっても `!text` に暗黙に足してはならない。text から AST への変換が
本当に要るなら、別の明示的な phase-changing construct として設計しなければならない。

- 汎用の文字列マクロ / C preprocessor 相当 / m4 相当
- token pasting
- 再帰的テキスト展開
- 正規表現置換
- 生成された TeXFlux 構文、`eval`、生成テキストの `parse` / `read`
- 動的なマクロ名・command 名・environment 名
- 動的な `!import` / `!macroimport`
- 任意 AST の文字列化
- TeX parser、TeX の意味論検証（catcode、math mode、パッケージ意味論）
- パラメータ宣言側の型構文（`{x:text}`）

補間により最終 TeX が不正になった場合は TeX / LaTeX engine がエラーを報告する。TeXFlux と TeX engine の
責務はそこで分離される。

---

## 14. 不変条件

| # | 不変条件 |
|---|---|
| I1 | マクロは `syntax AST -> syntax AST` のままである |
| I2 | `!text` は text field だけを変え、AST の形は変えない |
| I3 | 生成されたテキストが parse される経路は存在しない |
| I4 | flag / import / macro の名前空間は parse と宣言解決だけで確定する |
| I5 | 挿入された fragment は最終テキストである（再走査しない） |
| I6 | TeXFlux は interpolation のために TeX 文法を一切実装しない |
| I7 | 呼び出し側由来のテキストとテンプレート scaffold を source map で区別できる |
| I8 | 捨てられた conditional payload は補間されない |
| I9 | `.tfxm` の名前解決は interpolation の有無で変化しない |
| I10 | `parts` を持つノードの描画結果は、単純連結した文字列と完全に一致する |
| I11 | marker を含まない文書の出力は、補間の有無でバイト単位に一致する |
