# 文字列 Interpolation（`!text`）実装設計

## 0. ステータス

- 種別: 実装設計書（詳細設計）。
- 実装済み: `feature/string-interpolation`（2026-09-13）。規範定義は
  `texflux_tex_first_dsl_v1_spec.md` §10.6、利用者向け説明は `doc/dsl.md` §12.7。
- 位置づけ: `texflux_string_interpolation_spec.md` の原案を精査し、実装可能な形に
  確定させたもの。原案と本書が食い違う場合は**本書が優先**する。
- 前提コミット: `112eff5`（2026-09-13）。
- 対象: `src/texflux/` の v1 実装。
- 本書の目的は、これだけを読めば実装者が判断を一切追加せずに実装できることである。
  したがって、字句規則・変更する関数・診断メッセージ全文・テスト一覧まで確定させてある。

この機能の一行要約:

> **AST マクロのテンプレート内にある「不透明な文字列フィールド」にだけ穴を開ける機能**

ソース全体に対する文字列プリプロセッサ（m4 / C preprocessor 相当）ではない。

---

## 1. 決定表

| # | 決定 | 根拠 |
|---|---|---|
| D1 | `!param{name}` は AST 位置、`!text{name}` はテキスト位置。使用側が型を決める（use-site typing）。パラメータ宣言の文法は変更しない | 既存 `!defmacro` の文法と `Value = tuple[SyntaxNode, ...]` を維持できる |
| D2 | 補間対象は **TeXFlux が内容を構文解析しない不透明な文字列フィールドのみ** | 本機能は「文字列 interpolation」であり、構文解析やチェックを伴う場所には持ち込まない |
| D3 | 挿入された文字列は再走査しない（no-rescan） | 再帰的テキスト展開・生成構文を原理的に排除する |
| D4 | コンパイラ metadata（flag 名・マクロ名・パラメータ名・import path・構造名）には一切許可しない | 依存グラフと名前解決を静的に保つ |
| D5 | provenance は fragment 単位。`RawTex` / `Argument` / `BraceGroup` に `parts` を持たせ、renderer が fragment ごとに `emit()` する | `!param` と同等の逆引き品質を保つ。`\foo{pre-!text{x}-post}` で `x` の値が別行にある場合、PDF から値の行へ飛べる |
| D6 | 新しい `RenderRole` `"scaffold"`（rank 1）をテンプレート側リテラルに与える | §10.4 参照。これが無いと列情報なしの SyncTeX 入力で `remap` が `ambiguous source mappings` を送出する |
| D7 | built-in special の引数は fail-closed | 当初の許可リストは `{vpad}` の 1 件だけだった。その後 `!vpad` は削除され、フロー制御は同梱マクロモジュールの普通のソースマクロ（`!before` / `!after` / `!around` / `!off` / `!drop`）になったので、**許可リスト自体が消え、built-in special の group は例外なく metadata 扱い**になった。マクロの値は D-A5 の経路で補間されるので `!before{\vspace{!text{gap}}}` は従来どおり書ける |
| D8 | マクロテンプレート外のテキストフィールドに marker があればエラーにする | 綴り間違いが黙って TeX に流れる事故を防ぐ。リポジトリ内に literal `!text{` は存在しないため実質的な非互換は無い（§15.4） |
| D9 | 補間の escape は `!!text{` と `\!text{` の 2 系統。独立した行頭 `!!` raw-line escape と二層になる（§3.3） | `\!` は TeX の負の細空白という実在コマンドなので backslash escape は必須。行頭 `!!` は `@@` と対になる別機能として導入し、parser が先に1文字剥がす |

---

## 2. スコープと用語

### 2.1 用語

- **structural splice**: `!param{name}`。束縛された `Value`（AST ノード列）を AST 位置へ挿入する。
- **text interpolation**: `!text{name}`。束縛された `Value` から取り出した文字列を、
  文字列フィールドの途中へ挿入する。
- **marker**: 文字列中に現れる `!text{` および `!param{` の 6/7 文字。
- **hole**: `!text{name}` 全体。補間で値に置き換わる範囲。
- **text field**: TeXFlux が内容を構文解析しない `str`。具体的には
  `RawTex.text`、インライン `Argument.value`（`str` の場合）、`BraceGroup.header_raw`。
- **control metadata**: TeXFlux 自身のコンパイル動作を決める文字列。マクロ名、
  パラメータ名、flag 名、`!when`/`!unless` の条件、import path、import binding、
  command / environment / special の構造名。

### 2.2 テキストフィールドの見分け方（規範）

ある `str` が text field であるための必要十分条件は、

> **その文字列が、TeXFlux のどのパスでも内容を解析されず、最終的にそのまま TeX 出力へ
> 落ちること**

である。「Python の型が `str` だから」は理由にならない（D7）。

---

## 3. 構文と字句規則

### 3.1 綴り

```text
!text{NAME}
```

`NAME` の文法は既存のマクロパラメータと同一:

```text
[A-Za-z_][A-Za-z0-9_-]*
```

実装は `macros._PARAM_NAME_RE` を再利用する（`interpolate.py` から import する）。

### 3.2 走査対象

走査するのは次の 2 つの marker だけである。

| marker | 綴り | 長さ |
|---|---|---|
| text hole | `!text{` | 6 |
| param misuse | `!param{` | 7 |

`!param{` を走査するのは、それを**エラーにする**ためである（§7.3）。

### 3.3 escape

次の表の入力は、parser による raw-line escape 処理後のテキストフィールドである。

| 入力 | 出力 | 備考 |
|---|---|---|
| `!!text{NAME}` | literal `!text{NAME}` | 生成した `!text{` は再走査しない |
| `!!param{NAME}` | literal `!param{NAME}` | 同上 |
| `\!text{NAME}` | そのまま（marker と見なさない） | `syntax.is_escaped` による。`\!` は TeX の負の細空白 |

escape の判定順序は **`!!` を先に、`\` の判定をその前に**行う（§3.5 の擬似コード参照）。

行頭では parser の raw-line escape と補間走査器の escape の二層になる。
マクロテンプレート内では次の順で処理する:

```text
!!text{x}   → parser が ! を1つ剥がす → RawTex "!text{x}" → 補間される
!!!text{x}  → "!!text{x}" → 走査器が escape → リテラル "!text{x}"
```

エスケープせず AST 位置に書いた `!text{x}` は引き続き T01 エラーになる。
テンプレート外の `!!text{x}` は parser 後に未エスケープの hole となるため T02 になる。

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
        return None                      # 高速パス。既存文書は完全に無変更
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
                raise T07 at marker_span(j, 6)
            name = text[j + 6 : close]
            if _PARAM_NAME_RE.fullmatch(name) is None:
                raise T06 at marker_span(j, close + 1 - j)
            emit_literal(text[last:j]); emit_hole(name, marker_span(j, close + 1 - j))
            i = last = close + 1; found = True; continue
        if text.startswith("!param{", j):
            raise T08 at marker_span(j, 7)
        i = j + 1
    emit_literal(text[last:])
    return pieces if found else None
```

規範上の要点:

1. `}` は**最初に現れたもの**を終端とする。名前に brace は現れ得ないので入れ子は見ない。
2. 名前の検証は `fullmatch` である。空文字 `!text{}` も T06 になる。
3. `found` が偽（escape も hole も無かった）なら `None` を返す。呼び出し側は
   ノードを一切作り替えない。既存文書のふるまいとバイト列が完全に一致することを保証する。
4. `!!!text{x}` は `!` + escape として読まれ `!!text{x}` を出力する（doubling の自然な帰結）。

### 3.6 `text` の予約

`Reserved` に `TEXT = "text"` を追加する。`macros._RESERVED_NAMES` は
`frozenset(Reserved) | CONDITIONAL_NAMES` なので、これだけで

```text
!defmacro{text}{x}: |
```

が `'!text' is reserved by TeXFlux` として拒否される。追加のコードは不要。

---

## 4. データ構造

### 4.1 `src/texflux/ast.py` への追加

```python
@dataclass(frozen=True, slots=True)
class TextFragment:
    """One provenance-tagged run of a text field."""

    text: str
    span: SourceSpan
    #: Written by the macro template rather than by the caller. It renders
    #: with the "scaffold" role so a caller's value outranks it when a
    #: SyncTeX record carries no column.
    scaffold: bool = False


#: One text field split into the sources its characters came from.
SourceText: TypeAlias = tuple[TextFragment, ...]


def plain_text(parts: SourceText) -> str:
    return "".join(fragment.text for fragment in parts)
```

### 4.2 既存ノードへのフィールド追加

いずれも**既定値つきの末尾フィールド**として足す。既存の位置引数呼び出しは
すべて無変更で通る（`tests/test_ast.py` を含めて確認済み）。

```python
class RawTex:
    text: str
    span: SourceSpan
    parts: SourceText | None = None

class Argument:
    kind: GroupKind
    value: str | Block
    layout: ArgumentLayout
    span: SourceSpan
    parts: SourceText | None = None

class BraceGroup:
    body: Block
    span: SourceSpan
    header_raw: str = ""
    header_parts: SourceText | None = None
```

### 4.3 不変条件

`parts is None` は「フィールド全体が `span` 由来」を意味する既定状態である。
`parts is not None` のときは次を満たさなければならない。`__post_init__` で検査する。

| ノード | 不変条件 |
|---|---|
| `RawTex` | `plain_text(parts) == text` |
| `Argument` | `isinstance(value, str)` かつ `plain_text(parts) == value` |
| `BraceGroup` | `plain_text(header_parts) == header_raw` |

違反時は `ValueError`（利用者向けの診断ではなく実装バグなので `TeXFluxError` ではない）。

### 4.4 `RenderRole` の拡張

`render.RenderRole` に `"scaffold"` を足す。`TextFragment` は role 値を持たない
（`scaffold: bool` だけを持つ）ので、`ast.py` が `render.py` に依存する必要はない。

```python
RenderRole: TypeAlias = Literal["content", "open", "close", "synthetic", "scaffold"]
```

`remap._ROLES` は `get_args(RenderRole)` から導かれるので自動的に受理される。
`remap._ROLE_RANK` にだけ `"scaffold": 1` を**明示的に追加**する（欠けると `KeyError`）。

`.tfxmap` の `version` は 1 のまま据え置く。`remap.py` が
「The role vocabulary is owned by the renderer that writes the map」と宣言している
とおり、role 語彙の拡張は形式の変更ではない。

### 4.5 `Value` は変更しない

`Value: TypeAlias = tuple[SyntaxNode, ...]` はそのまま。text 用の別型は導入しない（D1）。

---

## 5. `src/texflux/interpolate.py`（新規）

`flags.py` / `syntax.py` と同じ粒度の小さなモジュール。TeX parser も TeXFlux parser も
持たない。公開 API は 3 つだけである。

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

- `origin` は**再ターゲット前**のノード span。診断の行・列はここから作る。
  `!param` の既存診断（`m.tfx:2:5: macro error: unknown macro parameter 'nope'`）と
  同じく、エラーはマクロ**定義**の位置を指す。
- `target` は `_Expander._span(node.span, frame)` の結果、すなわち再ターゲット後の
  呼び出し位置。**リテラル部の fragment はこれを名乗る**（原案 §16 の要求 1）。
- `offset` は `origin.start.column` から実際のテキスト先頭までの距離。
  `RawTex` は `0`、インライン `Argument` は `1`（開き delimiter の分）。
- hole は `text_value(name, frame)` が返す `SourceText` を**そのまま連結**する
  （原案 §16 の要求 2）。1 文字も書き換えず、走査もしない（D3）。
- リテラル部は `TextFragment(literal, target, scaffold=lookup is not None)`。
  `scaffold` は「macro template が書いたリテラル」を低優先度に落とすための role
  なので、`lookup is None`（テンプレート文脈の外＝呼び出し側やトップレベルの
  テキスト）で走った補間のリテラルは呼び出し側の content であり、`scaffold` を
  名乗ってはならない。名乗ると、呼び出し側の値に含まれる escape（`!!text{...}`）が
  content の rank を失い、列情報なしの SyncTeX 入力で `ambiguous source mappings`
  になり得る。
  連続するリテラルは 1 個の fragment にまとめてよい（`MappedEmitter` 側でも
  coalesce されるため、出力は同一）。
- 返り値は §3.5 の `found` が真のときだけ `SourceText`、偽なら `None`。

### 5.2 診断 span の作り方

```python
def marker_span(origin: SourceSpan, offset: int, length: int) -> SourceSpan:
    """One range of `origin`, `offset` characters in, clamped to its end."""
```

算出した end が `origin.end` を超える場合は `origin` そのものを返す。
再ターゲットされたノードの span は元のテキストより短いことがあり、span は決して
逆転してはならないためである（`!items` 削除前は `normalize` が同じ clamp を
持っていたが、現在この規則を実装しているのは本モジュールだけになる）。

### 5.3 `text_value` の判定順序（規範）

```python
def text_value(name, frame):
    if name in frame.sequences:
        raise T04            # rest parameter
    if name not in frame.values:
        raise T03            # unknown parameter
    value = frame.values[name]
    if len(value) != 1 or not isinstance(value[0], RawTex):
        raise T05            # not a text value
    node = value[0]
    if node.parts is not None:
        return node.parts
    return (TextFragment(node.text, node.span),)
```

`node.parts` をそのまま返すことが、原案 §14 の nested composition で provenance が
推移的に保たれる理由である。`outer` が組み立てた `sec-intro` を `inner` が
`\label{...}` に埋めても、`sec` と `intro` はそれぞれの出どころを保つ。

返される fragment には `scaffold=True` のものが混ざり得る（外側テンプレートの
リテラル `-` など）。その flag も保存する。

---

## 6. パイプライン上の位置

変更しない。補間は既存の `expand_macros()` の内部で行う。

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

- `>>` は補間より前に desugar 済みなので、stack 専用規則は不要である（原案 §6.3）。
- `canonicalize` より前なので、補間はマクロフレームが生きている間に完了する。
  正準化以降のパスは補間を知らない。
- `_conditional`（`macros.py:537-541`）は drop 時に `self.block()` を呼ばずに `()` を
  返す。したがって **dropped payload 内は補間されない**（原案 §12）。追加実装は不要。

> **静的な事前走査パスを追加してはならない。** `AGENTS.md` は「dropped payload の内側に
> 踏み込む規則は 4 つだけであり、5 つ目を足すなら『壊れた内容を無効化できる』保証を
> 捨てられるか判断せよ」と定めている。本機能はその判断を必要としない設計にしてある。

---

## 7. コンテキストの完全表

### 7.1 許可（補間する）

| # | コンテキスト | 判定を行う関数 |
|---|---|---|
| A1 | マクロテンプレート内の `RawTex.text` | `_Expander.node()` の `RawTex` 分岐 |
| A2 | `ParsedInvocation` のインライン group（`{...}` `[...]` `<...>`、`str` 値） | `_Expander._argument()` |
| A3 | `@{...}` literal brace container の header group | 同上（`InvocationKind.BRACE`） |
| A5 | ユーザーマクロ呼び出しの required compact group（標準フロー制御の呼び出しを含む） | `_Expander._values()` |

A2 は `>>` の desugar 後も同じ経路を通るので、`@foo >> @bar >> @hoge{!text{x}}:` は
自動的に許可される（原案 §6.3）。

A5 は**束縛の前**に補間する。結果のテキストが従来どおり 1 個の `RawTex` Value として
束縛される。これが原案 §14 の macro composition を成立させる。

### 7.2 禁止（marker を検出したらエラー）

| # | コンテキスト | 判定を行う関数 | 診断 |
|---|---|---|---|
| B1 | AST ノード位置の `!text`（`SpecialInvocation(name="text")`） | `_Expander.node()` | T01 |
| B2 | マクロテンプレート外の text field | `interpolate()`（`lookup is None`） | T02 |
| B3 | text field 内の `!param{` | `interpolate()` | T08 |
| B4 | `!when` / `!unless` の group | `_Expander._conditional()` | T10 |
| B5 | `!param` / `!each` の name group | `_Expander._single_name()` | T11 |
| B6 | built-in special の group（`!import` / `!macroimport` と未知の special） | `_Expander._argument()` | T12 |
| B7 | `(...)` binding list（`GroupKind.BINDING`） | `_Expander._argument()` | T13 |

B4〜B7 は文字列に `!text{` / `!param{` の綴りが含まれるかを検査する。
これらは補間対象のテキストではなく、escape の処理も行わないため、
`!!text{` / `\!text{` でメタデータに marker を書くことも許可しない。

### 7.3 既存経路で自然にエラーになるもの（専用診断を設けない）

いずれも top-level 限定の宣言であり、dropped payload の問題も起きない。
専用チェックを足すと変更面が広がるだけなので、既存の診断に委ねる。

| コンテキスト | 実際に出るエラー |
|---|---|
| `!flag{!text{x}}{on}` | `invalid build flag name '!text{x}'`（`flags._declaration`） |
| `!defmacro{foo}{!text{x}}: |` | `invalid macro parameter name '!text{x}'`（`macros._parameters`） |
| `!defmacro{!text{n}}...` | `invalid macro name '!text{n}'`（`macros._definition`） |
| `!macroimport{!text{p}}` | モジュール解決のエラー。`resolve_macro_imports` は `expand_macros` より前に走り、ノードを取り除くので expander は見ない |
| `@!text{env}: |` | `parser` が `!` を環境名に許さず `invalid structural name` |

### 7.4 構造名は生成できない

command / environment / special / macro の名前は `parser.HeaderScanner._segment` が
決める。`!` や `{` は名前文字ではないので、名前位置に marker を書くことは文法上できない。
実装側で追加の防御は不要である。

---

## 8. text-extractable な値

### 8.1 規範

`Value` が **ちょうど 1 個の `RawTex` ノードからなる**ときに限り text-extractable とする。
それ以外は T05。

### 8.2 具体例

| 呼び出し | 束縛される Value | text-extractable |
|---|---|---|
| `!foo{hello}` | `(RawTex("hello"),)` | ✅ `"hello"` |
| `!foo{}` | `(RawTex(""),)` | ✅ `""`（空文字は正当） |
| `!foo: |` ＋ 1 行 | `(RawTex("LINE"),)` | ✅ |
| `!foo: |` ＋ 2 行 | `(RawTex, RawTex)` | ❌ T05 |
| `!foo: |` ＋ `@center: |` | `(ParsedInvocation,)` | ❌ T05 |
| rest パラメータ本体 | `tuple[Value, ...]` | ❌ T04 |
| `!each` の item が 1 行 | `(RawTex("alpha"),)` | ✅ |
| `!each` の item が環境 | `(ParsedInvocation,)` | ❌ T05 |

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

### 9.2 `render.py` の変更

3 箇所を fragment 単位にする。**出力される TeX 文字列は単純連結と完全に同一**でなければ
ならない（`MappedEmitter.emit` を順に呼ぶだけなので自動的に満たされる）。

```python
def _emit_text(emitter, text, parts, span, base_role):
    if parts is None:
        emitter.emit(text, source=span, role=base_role)
        return
    for fragment in parts:
        emitter.emit(
            fragment.text,
            source=fragment.span,
            role="scaffold" if fragment.scaffold else base_role,
        )
```

| 箇所 | 変更 |
|---|---|
| `_render_block` の `RawTex(text=text)` 分岐 | `emitter.line(text, ...)` を `_emit_text(...); emitter.newline()` に置換 |
| `_emit_group` | `emitter.emit(argument.value, ...)` を `_emit_text(..., base_role="content")` に置換。opener / closer は従来どおり `argument.span` |
| `_render_block` の `BraceGroup` 分岐 | `header_raw` の出力を `_emit_text(..., header_parts, node.span, "content"); emitter.newline()` に置換 |

`RawTex(text="")` の分岐（空行）は変更しない。空文字を補間した結果にも `parts` は
付き得るが、文字を持つ fragment はなく、従来どおり空行を出力する。

### 9.3 期待される provenance（規範例）

```text
1: !defmacro{m}{x}: |
2:     \foo{pre-!text{x}-post}
3: !m: |
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

## 10. 既存パスへの影響

### 10.1 `parser.py` — 変更なし

調査で確認した事実:

- `\includegraphics[width=!text{w}]{fig/!text{n}.pdf}` は
  `_scan_command_header`（`parser.py:587-593`）が「閉じた単一 segment の command 行は
  ordinary TeX」と判定して `RawTex` になる。`scan_group` の `[` 走査は brace 深さを
  数えるので `!text{w}` の brace で誤らない。
- `@hoge{!text{x}}: |` は `scan_group` が brace の入れ子を正しく数え、group の中身は
  opaque な `str` のまま残る。
- `!inner{!text{a}-!text{b}}` も 1 個の required group として scan される。

したがって補間の実装に伴う一般文法の変更は不要である。独立機能として導入した
`!!` の行頭 raw-line escape は parser が処理し、補間走査器はその後の文字列を読む
（D9、§3.3）。

### 10.2 `!text` が AST 位置に現れる経路

2 つだけである。どちらも T01 になる。

1. 行頭の `!text{x}` — `_Parser._directive` が `SpecialInvocation(name="text")` を作る。
2. `:` sequence suite の `- !text{x}` — `parser.py:793-805` が同じノードを作る。

### 10.3 `normalize.py` — 変更は 1 箇所のみ

| 箇所 | 変更 |
|---|---|
| `_normalize_invocation` の `InvocationKind.BRACE` 分岐 | `BraceGroup(..., raw)` に `node.groups[0].parts` を `header_parts` として渡す |

`normalize.py` は `RawTex.text` を切り刻まない。`!items` の mini-grammar が
削除されたことで、テキストをスライスして新しいノードを作るパスは存在しなくなり、
`parts` は正準化を素通りする。`_writes_own_braces` は検出のために `.text` を
連結するだけなので変更不要。
（`_vspace(group)` が `Argument` をそのまま渡して `!vpad{!text{gap}}` の provenance を
保つ、という当時の記述は `!vpad` の削除とともに不要になった。同じ provenance は
`!before{\vspace{!text{gap}}}` の呼び出し値として保たれる。）

### 10.4 `remap.py` — `_ROLE_RANK` に 1 行

これは**必須**である。理由:

`remap._select_mapping`（`remap.py:307-325`）は、列情報を持たない SyncTeX レコードに
対して「同一生成行の候補のうち最良 rank のものが複数のソース行を指していたら
`RemapError: ambiguous source mappings` を投げる」。

fragment provenance を素朴に入れると、§9.3 の例で `\foo{pre-` が `m.tfx:3`、
`VALUE` が `m.tfx:4` を指し、**両方 `content`（rank 0）**になって上記の例外が出る。
`"scaffold": 1` を与えることでテンプレート側が rank 1 へ下がり、`!param` と同じく
「呼び出し側の値が勝つ」序列が再現される。

なお `remap._rewrite_link`（`remap.py:423`）は `mapping.source_start.line` **だけ**を
使う。列の精度は逆引き結果に影響しない。差が出るのは「呼び出し行と値の行が異なる」
場合、すなわち `: |` suite で値を渡した場合だけである。

残る曖昧性: 1 つの生成行に**異なるソース行由来の hole が 2 つ以上**並んだ場合
（`\foo{!text{a}!text{b}}` の `a` と `b` を別々の suite 行で渡した場合）は
依然として ambiguous になる。compact group 呼び出しでは起こらない。これは
`!param` が既に持つ制約と同種であり、本機能で新たに対策はしない。

### 10.5 `modules.py` — 1 箇所

| 箇所 | 変更 |
|---|---|
| `_TEMPLATE_NAMES`（`modules.py:100`） | `Reserved.TEXT` を追加 |

`_check_self_contained`（`modules.py:799-820`）は `.tfxm` の template 内の
`SpecialInvocation` 名を走査する。`Reserved.TEXT` を加えないと、AST 位置の `!text` が
`'!text' is not defined in style.tfxm and is not available through its own !macroimport`
という誤った `ModuleError` になる。加えることで、正しい T01 が expander から出るようになる。

lexical scope と import graph は変更しない。`!text` はマクロ名を一切 lookup しないし、
`!import` / `!macroimport` を生成できない（§7.2 B6・§7.3）。したがって依存関係の
発見とキャッシュの前提は不変である。

### 10.6 `source_map.py` — 変更なし

role はそのまま直列化される。形式バージョンも据え置く（§4.4）。

---

## 11. 診断表

メッセージは `errors.TeXFluxError.diagnostic()` により
`{file}:{line}:{column}: {kind}: {message}` の形で提示される。

`MacroExpansionError` は既存の `macros._error()` を経由させ、フレーム内なら
`; while expanding '<chain>' called at <location>` を付す。既存 `!param` の診断と
完全に同じ体裁になる。

| ID | 条件 | メッセージ | span | 例外 | chain |
|---|---|---|---|---|---|
| T01 | `!text` が AST ノード位置に現れた | `!text is only valid inside a textual field; use !param for an AST position` | ノードの span | `MacroExpansionError` | あり（frame がある場合） |
| T02 | マクロテンプレート外の text field に `!text{` | `!text is only valid inside a macro template` | marker の span | `MacroExpansionError` | なし |
| T03 | 未知のパラメータ名 | `unknown macro parameter '<name>'` | hole の span | `MacroExpansionError` | あり |
| T04 | rest パラメータを `!text` した | `'<name>' is a rest parameter; use !each to access its values` | hole の span | `MacroExpansionError` | あり |
| T05 | text-extractable でない値 | `macro parameter '<name>' is not a text value; use !param for structural values` | hole の span | `MacroExpansionError` | あり |
| T06 | 名前が文法に合わない（空を含む） | `invalid !text parameter name '<name>'` | hole 全体の span | `MacroExpansionError` | あり |
| T07 | `}` が見つからない | `unterminated !text{...}` | marker の span | `MacroExpansionError` | あり |
| T08 | text field 内の `!param{` | `!param cannot be used inside a text field; use !text for text interpolation` | marker の span | `MacroExpansionError` | あり |
| T10 | `!when` / `!unless` の group に marker | `interpolation is not allowed in a !<name> flag group; flag names are static` | その group の span | `MacroExpansionError` | あり |
| T11 | `!param` / `!each` の name group に marker | `interpolation is not allowed in a !<name> name group` | その group の span | `MacroExpansionError` | あり |
| T12 | 許可リストに無い special の group に marker | `interpolation is not allowed in !<name>'s arguments` | その group の span | `MacroExpansionError` | あり |
| T13 | `(...)` binding list に marker | `interpolation is not allowed in a '(...)' binding list` | その group の span | `MacroExpansionError` | あり |

（T09 は欠番。`!param{` は閉じているか否かに関わらず T08 になる。）

### 11.1 診断例（そのままテストに書ける形）

```text
m.tfx:2:11: macro error: unknown macro parameter 'nope'; while expanding 'm' called at m.tfx:3:1
m.tfx:2:5: macro error: !text is only valid inside a textual field; use !param for an AST position; while expanding 'm' called at m.tfx:4:1
m.tfx:1:12: macro error: !text is only valid inside a macro template
m.tfx:2:11: macro error: macro parameter 'body' is not a text value; use !param for structural values; while expanding 'm' called at m.tfx:4:1
```

---

## 12. 変更箇所一覧

| ファイル | 変更内容 |
|---|---|
| `src/texflux/interpolate.py` | **新規**。§5 の 3 関数と `marker_span`。`syntax.is_escaped` と `macros._PARAM_NAME_RE` を再利用 |
| `src/texflux/ast.py` | `TextFragment` / `SourceText` / `plain_text` を追加。`RawTex.parts` / `Argument.parts` / `BraceGroup.header_parts` を追加。3 つの `__post_init__` 検査 |
| `src/texflux/macros.py` | §12.1 参照 |
| `src/texflux/render.py` | `RenderRole` に `"scaffold"`。`_emit_text` ヘルパを追加し 3 箇所を置換（§9.2） |
| `src/texflux/remap.py` | `_ROLE_RANK` に `"scaffold": 1` |
| `src/texflux/modules.py` | `_TEMPLATE_NAMES` に `Reserved.TEXT` |
| `src/texflux/normalize.py` | `BraceGroup` 生成時に `header_parts` を渡す（1 箇所） |
| `src/texflux/__init__.py` | `TextFragment` を re-export（`__all__` にも追加） |
| `src/texflux/parser.py` | **変更なし** |
| `src/texflux/flags.py` | **変更なし**（§7.3） |
| `src/texflux/source_map.py` | **変更なし** |
| `src/texflux/cli.py` | **変更なし** |

### 12.1 `src/texflux/macros.py` の詳細

以下は初回処理の概略である。`parts is not None` のフィールドは補間済みなので
`interpolate()` を再度呼ばず、既存の fragment をそのまま保持する。
`parts` を変更するときは `text` / `value` も `plain_text(parts)` に更新する。

1. `Reserved` に `TEXT = "text"` を追加。
2. モジュール定数を追加。`_MODULE_NAMES` と同じく、`normalize` に依存しないよう
   名前を直接綴る。

   ```python
   #: The built-in specials whose groups are output TeX rather than compiler
   #: metadata. Fail-closed: a special not listed here rejects a marker.
   _TEXT_ARGUMENT_SPECIALS: Final = frozenset({"vpad"})
   ```

   （この定数は後に削除された。`!vpad` が無くなり許可リストが空になったので、
   `_argument()` は `isinstance(node, SpecialInvocation)` だけで fail-closed に
   判定する。）

3. `_Expander.node()` に分岐を追加する。`Reserved.PARAM` の分岐の**直後**に置く。

   ```python
   case SpecialInvocation(name=Reserved.TEXT):
       raise _error(
           "!text is only valid inside a textual field; "
           "use !param for an AST position",
           node.span,
           frame,
       )
   ```

4. `_Expander.node()` の `RawTex()` 分岐を書き換える。

   ```python
   case RawTex():
       target = self._span(node.span, frame)
       parts = interpolate(
           node.text,
           origin=node.span,
           target=target,
           offset=0,
           lookup=frame,
       )
       if parts is None:
           return (replace(node, span=target),)
       return (replace(node, text=plain_text(parts), span=target, parts=parts),)
   ```

5. `_Expander._invocation()` から `_argument()` へ「所有者」を渡せるようにする。

   ```python
   def _invocation(self, node, frame):
       special = isinstance(node, SpecialInvocation)
       allowed = not special or node.name in _TEXT_ARGUMENT_SPECIALS
       owner = f"!{node.name}" if special else None
       return replace(
           node,
           groups=tuple(
               self._argument(group, frame, allowed=allowed, owner=owner)
               for group in node.groups
           ),
           ...
       )
   ```

6. `_Expander._argument()` を書き換える。

   ```python
   def _argument(self, argument, frame, *, allowed, owner):
       value = argument.value
       target = self._span(argument.span, frame)
       if isinstance(value, Block):
           return replace(argument, value=self.block(value, frame), span=target)
       if argument.kind is GroupKind.BINDING:
           reject_markers(value, argument.span, "a '(...)' binding list")   # T13
           return replace(argument, span=target)
       if not allowed:
           reject_markers(value, argument.span, f"{owner}'s arguments")     # T12
           return replace(argument, span=target)
       parts = interpolate(
           value, origin=argument.span, target=target, offset=1, lookup=frame
       )
       if parts is None:
           return replace(argument, span=target)
       return replace(argument, value=plain_text(parts), span=target, parts=parts)
   ```

   `reject_markers` が投げる `MacroExpansionError` は `_error()` 経由で chain を
   付ける必要があるため、`reject_markers` は「marker の有無と位置」を返し、
   例外組み立ては `macros.py` 側で行う形にしてもよい。どちらでも診断文字列が
   §11 と一致すればよい。

7. `_Expander._single_name()` に marker 拒否を足す（T11）。

   ```python
   texts = []
   for group in node.groups:
       text = demand_text(group, f"{label} name")
       reject_markers(text, group.span, f"a {label} name group")
       texts.append(text)
   ```

8. `_Expander._conditional()` の先頭、`evaluate_conditional` を呼ぶ**前**に、
   `node.groups` の `str` 値すべてに対して marker を拒否する（T10）。
   `[and]` / `[or]` の modifier group も対象に含める。

9. `_Expander._values()` の compact group を束縛前に補間する。

   ```python
   for group in node.groups:
       text = required_text(group)
       if text is None:
           raise _error("macro calls accept required '{...}' values only", ...)
       target = self._span(group.span, frame)
       parts = interpolate(
           text, origin=group.span, target=target, offset=1, lookup=frame
       )
       if parts is None:
           values.append((RawTex(text, target),))
       else:
           values.append((RawTex(plain_text(parts), target, parts),))
   ```

   `interpolate` に渡す `frame` は**外側の**フレームである。引数は呼び出し側の
   テンプレートに書かれているので、そこで見える束縛で解決するのが正しい。

10. `text_value` が参照するのは `_Frame` の `values` / `sequences` だけである。
    `_Frame` に `lookup` 用のメソッドを 1 つ足すか、`interpolate.py` から
    `_Frame` を型注釈だけで参照する（実行時 import は循環するので `TYPE_CHECKING`）。

---

## 13. 実装チェックリスト

1. `ast.py` に `TextFragment` / `SourceText` / `plain_text` と 3 つの `parts`
   フィールド・不変条件検査を追加する。
2. `render.py` の `RenderRole` に `"scaffold"` を足し、`remap._ROLE_RANK` に
   `"scaffold": 1` を足す。**この 2 つは必ず同時に行う**（片方だけだと `KeyError`）。
3. `interpolate.py` を新設し、§3.5 の走査アルゴリズムと §5 の 3 関数を実装する。
4. `macros.py` に `Reserved.TEXT` と `_TEXT_ARGUMENT_SPECIALS` を追加し、
   §12.1 の 3〜9 を適用する。
5. `modules.py` の `_TEMPLATE_NAMES` に `Reserved.TEXT` を追加する。
6. `normalize.py` の `BraceGroup` 生成に `header_parts` を渡す。
7. `render.py` の 3 箇所を `_emit_text` に置換する。
8. `__init__.py` に `TextFragment` を re-export する。
9. §14 のテストを追加し、`python -m unittest discover` を通す。
10. §15 の既存規範文書を改訂する。

---

## 14. 検証計画

### 14.1 新規テストファイル `tests/test_interpolation.py`

`unittest` のみ（pytest は使わない）。既存の `tests/test_macros.py` の書き方に揃える。
表駆動のケースは `subTest` を使う。

**基本（`InterpolationBasicsTests`）**

- compact `{...}` パラメータの補間。
- 1 行に複数の hole。
- 同一パラメータの複数回利用。
- 隣接する `!text{x}!text{y}`。
- 空文字（`!m{}`）。
- 行頭・行末の hole。

**RawTex（`InterpolationRawTexTests`）**

- `prefix-!text{x}-suffix`。
- `\includegraphics[width=!text{w}]{fig/!text{n}.pdf}` が期待どおりの 1 行になる。

**構造 group（`InterpolationGroupTests`）**

- `@hoge{!text{x}}: |`。
- `@foo >> @bar >> @hoge{!text{x}}:`。
- command の required / optional / overlay group。
- `@{...}` literal brace header。
- `!before{!text{gap}}: |`（標準フロー制御の呼び出し値）。

**AST / text の分離（`InterpolationLayerTests`）**

- AST 位置の `!param{x}` は成功する。
- AST 位置の `!text{x}` は T01。
- text 位置の `!text{x}` は成功する。
- text 位置の `!param{x}` は T08。
- sequence suite の `- !text{x}` も T01。

**control metadata（`InterpolationMetadataTests`）** — いずれも「補間されない」ことの確認。

```text
!when{!text{x}}        -> T10
!unless{!text{x}}      -> T10
!when[and]{!text{x}}{b} -> T10
!param{!text{x}}       -> T11
!each{!text{x}}{i}     -> T11
!each{items}{!text{i}} -> T11
!import{!text{x}}      -> T12
!flag{!text{x}}{on}    -> invalid build flag name（§7.3）
!macroimport{!text{x}} -> ModuleError（§7.3）
```

**値の形（`InterpolationValueShapeTests`）** — §8.2 の表をそのままケースにする。

**nested macro（`InterpolationCompositionTests`）**

- `!inner{!text{prefix}-!text{name}}` による text forwarding。
- 補間された値が `!text{y}` という綴りを含んでも再展開されない（D3）。
- 補間された値が `!foo` / `@frame` / `>>` / `!when{draft}` を含んでも構文にならない。

**conditional（`InterpolationConditionalTests`）**

- 生きている branch では補間される。
- 捨てられる branch では補間されない。
- 捨てられる branch 内の non-text 利用は、それだけを理由にエラーにならない。

**escape（`InterpolationEscapeTests`）**

- `!!text{x}` → literal `!text{x}`、かつ再走査されない。
- `!!param{x}` → literal `!param{x}`。
- 1 行に複数の escape。
- `!!!text{x}` → `!!text{x}`。
- `\!text{x}` はそのまま出力される。
- `!text {x}` / `!textbf{x}` / 単独の `!text` は marker ではない。

**scanner エラー（`InterpolationScannerTests`）**

- `!text{` → T07。
- `!text{}` / `!text{1x}` / `!text{a b}` → T06。

**テンプレート外（`InterpolationOutsideTemplateTests`）**

- トップレベルの `@hoge{!text{x}}: |` → T02。
- トップレベルの生 TeX 行 `\foo{!text{x}}` → T02。
- トップレベルの `\foo{!param{x}}` → T08。

### 14.2 source map（`tests/test_macros.py` に追記）

既存の `MacroSourceMapTests` と同じ `fragments()` ヘルパ形式で:

- §9.3 の例について `VALUE` → 4 行目 5 列、`\foo{pre-` と `-post}` → 3 行目 1 列。
- それぞれの `role` が `"content"` / `"scaffold"` であること。
- `.tfxm` 越しの nested macro でも、すべての fragment がルート `.tfx` の呼び出し行を
  指すこと（テンプレートは常に呼び出し位置へ再ターゲットされるため）。
- 呼び出し側の値に書かれた escape（`!m: |` の suite に `!!!text{literal}`）の
  fragment が `"content"` であり、値の行・列を指すこと。テンプレートのリテラルだけが
  `"scaffold"` になる（§5.1）。
- `result.text` が fragment の単純連結と一致すること。

### 14.3 remap 回帰（`tests/test_remap.py` に追記）

§9.3 のソースをコンパイルし、**列情報を持たない** SyncTeX レコードを `remap_document`
に通して `RemapError` が出ないこと、および飛び先が 4 行目になること。
`"scaffold"` role が無ければこのテストは失敗する。

### 14.4 module（`tests/test_modules.py` に追記）

- `.tfxm` の template 内で `!text` が使える。
- `_check_self_contained` が `!text` を template construct と認識する
  （AST 位置の `!text` が `ModuleError` ではなく T01 になる）。
- lexical scope に関する既存テストが無変更で通る。

### 14.5 golden

`tests/golden/macro-interpolation/` を **1 件だけ**追加する。`test_golden.py` が
ケース名の集合を厳密に持っているので、そこも更新する。内容は §16.1 の `figure` 例に
揃える（`!text` を RawTex 内とインライン group の両方で使い、`!param` と併用する形）。
goldens は一括再生成されるため、既存ケースが覆う形を重ねない。

### 14.6 ベースライン

```bash
python3 -W error::ResourceWarning -m unittest discover
PYTHONPATH=src python3 -m texflux compile examples/modules.tfx -o /tmp/m.tex
diff -u examples/modules.tex /tmp/m.tex     # 差分が無いこと
```

既存ドキュメントの出力がバイト単位で変わらないことが、§3.5 の高速パス（`found` が
偽なら `None`）の設計目標である。

---

## 15. 既存規範文書の改訂

本機能は現行の規範文書と**明示的に矛盾している**。実装と同時に次を改訂しなければ、
リポジトリは自分自身と食い違った状態になる。

### 15.1 `AGENTS.md`（`.gitignore` 済み・ローカルのみ）

「v1 boundaries」の禁止リストから次を削る。

> - textual macros, interpolation into raw TeX, !splice, optional/default/keyword
>   macro parameters, or macro recursion;

改訂後（interpolation を除外し、他は禁止のまま残す）:

> - !splice, optional/default/keyword macro parameters, or macro recursion;
> - textual macros in the m4/cpp sense: a source-to-source string preprocessor,
>   rescanning of interpolated text, token pasting, or any path from generated
>   text back to the parser;

「Source macros are part of v1.」の段落に次を追記する。

> `!text{name}` interpolates one bound value into a textual field of a template.
> It is not a textual macro: the inserted text is never rescanned and never
> parsed, and compiler metadata -- flag names, macro names, import paths,
> structural names -- stays static.

「Review checklist」に次の 4 項目を足す。

> - `!text` is rejected in an AST position and `!param` in a text field;
> - a built-in special's group is interpolated only when it is on the
>   `_TEXT_ARGUMENT_SPECIALS` allow list;
> - interpolated text is not rescanned, and a dropped conditional payload is
>   still never interpolated;
> - a text field's fragments render to exactly the string a plain concatenation
>   would produce.

### 15.2 `doc/dsl.md` §12

§12 冒頭の次の記述が偽になる。

> ソーステキストの単純な置換や、レンダリング後の TeX の再パース、生の TeX グループ内部への
> 文字列展開（interpolation）などは一切行われない。

→「ソーステキストの単純な置換や、レンダリング後の TeX の再パースは一切行われない。
テキストフィールドへの文字列展開は `!text` に限って行われ、挿入された文字列は
再走査も再パースもされない（§12.7）」に置き換える。

§12.3 の次の記述も偽になる。

> TeX グループの内部は解釈されない不透明な生 TeX（opaque raw TeX）であるため、
> グループ内にパラメータを展開（interpolate）することはできない。同じ理由で、
> 生の TeX 行の途中に書かれた `!param` もマクロ展開されず、文字どおりの
> テキストとして残る。

→「`!param` は AST を splice するため、不透明な TeX グループの内部には展開できない。
グループ内へ文字列を差し込むには `!text` を使う（§12.7）」に置き換える。

§12 に新しい小節 **§12.7「文字列 interpolation（`!text`）」** を追加し、利用者向けに
綴り・許可される場所・text-extractable な値・escape・代表例（§16）を書く。実装の
詳細は本書へ参照を張る。

### 15.3 `texflux_tex_first_dsl_v1_spec.md` §10

`doc/dsl.md` と同じ 2 箇所を、規範文書の語彙で同様に改訂する。
§10.6 に `!text` の規範定義（§3 の字句規則、§7 の許可／禁止表、§8 の値の条件）を追加する。

### 15.4 非互換

`!text{` という綴りを text field に literal で含む既存文書は T02 でエラーになる。
リテラルの `!param{` も T08 になる。既存の lexical scope テスト1件が生の TeX 中に
`!param{b}` を書いていたため、`!text{b}` に変更し、同名マクロの非再帰判定という
テストの目的を維持した。既存 examples 6件の出力はバイト単位で不変である。
リテラルの回避手段はテキスト中の `!!text{` / `!!param{` または backslash escape。
行頭の raw-line escape と重なる場合は `!!!text{` / `!!!param{` と書く。

---

## 16. 代表例

### 16.1 画像

```text
!defmacro{figure}{name}{width}{caption}: |
    \includegraphics[width=!text{width}]{fig/!text{name}.pdf}
    @center: |
        @minipage{!text{width}}: |
            !param{caption}
!figure{result}{0.8\textwidth}: |
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

### 16.2 環境引数（text）と本体（AST）の併用

```text
!defmacro{styled}{style}{body}: |
    @foo >> @bar >> @hoge{!text{style}}: |
        !param{body}
```

`style` は text splice、`body` は AST splice。

### 16.3 ラベル生成と macro composition

```text
!defmacro{label}{id}: |
    \label{!text{id}}

!defmacro{sectionlabel}{prefix}{id}: |
    !label{!text{prefix}:!text{id}}
```

### 16.4 `!each` の item

```text
!defmacro{labels}{...items}: |
    !each{items}{item}: |
        \label{item:!text{item}}

!labels:
    - alpha
    - beta
```

### 16.5 不正例

```text
!defmacro{bad1}{x}: |
    @hoge{!param{x}}: |        # T08
        A

!defmacro{bad2}{flag}{body}: |
    !when{!text{flag}}: |      # T10
        !param{body}

!defmacro{bad3}{x}: |
    !text{x}                   # T01

!defmacro{bad4}{...items}: |
    \foo{!text{items}}         # T04
```

---

## 17. 非目標

本機能は次を提供しない。将来必要になっても `!text` に暗黙に足してはならない。
text から AST への変換が本当に要るなら、別の明示的な phase-changing construct として
設計しなければならない。

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

---

## 18. 実装後に真でなければならない不変条件

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
| I11 | marker を含まない文書の出力は、本機能の導入前後でバイト単位に一致する |
