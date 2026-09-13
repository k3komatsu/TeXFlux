# TeXFlux 文字列 Interpolation 仕様

- Status: 設計仕様 / 実装提案
- 対象: TeXFlux v1 互換拡張
- Repository: `k3komatsu/TeXFlux`
- 精査基準: `main` commit `112eff5cf32969cb9ae2a381a70336ebb3ac37c5`（2026-09-13）

## 0. 結論

TeXFlux の既存マクロは **AST マクロ**のまま維持し、別系統の「文字列マクロ」は導入しない。

代わりに、既存マクロテンプレートに **文字列 interpolation** を追加する。

```text
!param{name}   AST splice
!text{name}    text interpolation
```

最重要ルールは以下である。

1. `!param{name}` は AST ノード / Value の位置でのみ使用する。
2. `!text{name}` は、すでに文字列であるフィールドの内部でのみ使用する。
3. `!text` の展開結果は **TeX として構文解析しない**。
4. `!text` の展開結果は **TeXFlux として再 parse しない**。
5. `!text` で挿入された文字列を再走査して、さらに `!text` やマクロを展開しない。
6. `!when`、`!unless`、`!flag`、`!import`、`!macroimport` 等のコンパイラ制御情報には interpolation を許可しない。
7. command / environment / macro 名そのものを interpolation で生成しない。
8. パラメータ宣言側には `{x:text}` 等の型構文を追加しない。`!param{x}` / `!text{x}` という**使用位置で型を決める**。
9. `>>` の desugar、マクロの lexical scope、conditional の lazy/drop semantics、source mapping を壊さない。

つまり、この機能は

> **AST マクロの中に text hole を追加する機能**

であり、C preprocessor や m4 のような source-to-source 文字列プリプロセッサではない。

---

## 1. 現行実装の確認

今回の仕様は、以下の現行実装を前提に設計する。

### 1.1 AST

`src/texflux/ast.py` では、主要な syntax AST は以下の形である。

```python
@dataclass(frozen=True, slots=True)
class Argument:
    kind: GroupKind
    value: str | Block
    layout: ArgumentLayout
    span: SourceSpan

@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    span: SourceSpan
```

したがって現在でも、TeXFlux が構造として扱う部分と、不透明な文字列として扱う部分は明確に分かれている。

### 1.2 structural header の group は opaque text

`src/texflux/parser.py` の `HeaderScanner` は structural header を解析するが、inline group の中身は `scan_group()` で境界だけを認識し、その内容自体は `str` として保持する。

つまり、例えば

```text
@hoge{abc}: |
```

における `abc` は TeX AST ではなく、TeXFlux から見れば opaque な文字列である。

この性質をそのまま `!text` に利用する。

### 1.3 現行マクロは AST-to-AST

`src/texflux/macros.py` の現行設計では、

```text
!defmacro
```

が syntax AST の template を保存し、マクロ呼び出しで渡された値を `Value` として bind し、

```text
!param{name}
```

がその `Value` を AST として splice する。

現行の型は概念的に

```python
Value = tuple[SyntaxNode, ...]
```

である。

### 1.4 compact `{...}` は一つの RawTex Value になる

ユーザーマクロ呼び出しの

```text
!foo{abc}
```

の `{abc}` は、現行 `_values()` で概念的に

```python
(RawTex("abc", group.span),)
```

へ変換される。

したがって、compact required group はすでに「AST Value ではあるが中身は一つの RawTex」という形になっている。

これは `!text` の text extraction と非常に相性がよい。

### 1.5 `>>` はマクロ展開前に desugar される

現行 pipeline は概ね

```text
parse
  -> validate
  -> desugar(>>)
  -> collect flags/imports/macros
  -> expand_macros
  -> canonicalize
  -> render
```

である。

したがって、文字列 interpolation も `expand_macros` の一部として実行すればよい。

### 1.6 conditional は payload を lazy に展開する

現行 `_conditional()` は `!when` / `!unless` を判定し、捨てる branch は内部を展開しない。

したがって `!text` も macro expansion pass に置けば、

```text
!when{off}: |
    ... !text{x} ...
```

の内部は評価されない。

この挙動は維持する。

### 1.7 macro module は lexical scope

`.tfxm` の macro definition は定義元 module の environment で名前解決される。

`!text` はこの macro namespace を動的に変更してはならない。

したがって、interpolation から macro 名・import path・flag 名等を生成することは禁止する。

### 1.8 renderer は provenance を保持する

`src/texflux/render.py` では `MappedEmitter` が出力 fragment ごとに source span を保持する。

現行 `!param` では、call site から渡された AST は元の span を保持し、template scaffolding は macro call site へ retarget される。

`!text` でも同じ思想を維持する。

---

## 2. 用語

### 2.1 Structural splice

AST Value を syntax AST の位置へ挿入する操作。

```text
!param{name}
```

### 2.2 Text interpolation

文字列フィールドの途中へ、bound value から取得した文字列を挿入する操作。

```text
!text{name}
```

### 2.3 Control metadata

TeXFlux 自身のコンパイル動作を決定するための文字列。

例:

- macro 名
- macro parameter 名
- `!param` / `!text` の parameter 名
- `!each` の sequence 名 / item 名
- flag 名
- `!when` / `!unless` の条件
- import path
- import binding
- macroimport path
- command / environment / special の structural name

これらには interpolation を許可しない。

---

## 3. 構文

文字列 interpolation は以下で記述する。

```text
!text{name}
```

`name` の文法は既存 macro parameter と同一とする。

```regex
[A-Za-z_][A-Za-z0-9_-]*
```

例:

```text
!text{x}
!text{width}
!text{file-name}
!text{_internal}
```

`text` は macro system の予約名に追加する。

したがって、以下は禁止する。

```text
!defmacro{text}{x}: |
    ...
```

---

## 4. parameter 宣言は変更しない

以下のような型付き parameter syntax は導入しない。

```text
{x:text}
{body:ast}
```

既存のまま

```text
!defmacro{foo}{x}{body}: |
    ...
```

と定義する。

使用側が要求する型を決める。

```text
!text{x}      # x を text として要求
!param{body}  # body を AST Value として要求
```

これを **use-site typing** とする。

理由:

- `!defmacro` の既存 grammar を変更しない。
- backward compatibility が高い。
- 現行 `Value = tuple[SyntaxNode, ...]` を維持できる。
- 同じ parameter を必要に応じて AST / text として扱える。
- TeXFlux の「まず Value があり、template がその使い方を決める」という設計と整合する。

---

## 5. text-extractable Value

`!text{name}` は任意の AST を文字列化する機能ではない。

### 5.1 定義

bound `Value` が以下を満たす場合に限り text-extractable とする。

```text
Value がちょうど一つの RawTex node からなる
```

概念コード:

```python
def text_of(value: Value) -> str:
    if len(value) == 1 and isinstance(value[0], RawTex):
        return value[0].text
    raise MacroExpansionError(...)
```

### 5.2 compact group

```text
!foo{hello}
```

の `hello` は現行実装で一つの `RawTex` Value になるため text-extractable である。

### 5.3 一行の raw block

展開時点で一つの `RawTex` node として表現されている Value なら text-extractable としてよい。

### 5.4 text-extractable ではないもの

以下を暗黙に text 化してはならない。

- environment AST
- command AST
- brace/container AST
- 複数 node からなる Value
- 複数行 RawTex block
- rest parameter sequence 自体

特に、

```text
AST
 -> normalize
 -> render
 -> string
```

という処理を `!text` のために実行してはならない。

`!text` は **すでに text である Value を取り出すだけ**である。

---

## 6. 許可する context

### 6.1 RawTex の内部

macro template 内の `RawTex.text` では `!text{name}` を認識する。

```text
!defmacro{fig}{name}{width}: |
    \includegraphics[width=!text{width}]{fig/!text{name}.pdf}
```

```text
!fig{result}{0.8\textwidth}
```

出力:

```tex
\includegraphics[width=0.8\textwidth]{fig/result.pdf}
```

ここで TeX の構文解析は一切不要である。

### 6.2 ParsedInvocation の inline group

`ParsedInvocation` の inline group は TeXFlux の AST 構造ではあるが、その `Argument.value` は出力される TeX text である。

したがって interpolation を許可する。

```text
!defmacro{wrap}{style}{body}: |
    @hoge{!text{style}}: |
        !param{body}
```

### 6.3 `>>` 内の structural argument

以下を許可する。

```text
@foo >> @bar >> @hoge{!text{variable}}:
```

`>>` を desugar すると概念的には

```text
@foo:
    @bar:
        @hoge{!text{variable}}:
            ...
```

となるだけであり、`!text{variable}` は一貫して `@hoge` の text argument の中にある。

したがって stack 専用ルールは不要である。

### 6.4 required / optional / overlay group

通常の TeX command / environment の出力用 group であれば、

```text
{...}
[...]
<...>
```

の text field に interpolation を許可してよい。

例:

```text
@foo[mode=!text{mode}]<!text{overlay}>: |
    ...
```

ただし、その group が TeXFlux compiler metadata として使用される special の引数である場合は別であり、後述の禁止規則を優先する。

### 6.5 literal brace container の raw header

`@{...}` の header に含まれる raw TeX text も出力テキストなので interpolation を許可する。

### 6.6 `!items` 等が後で読む RawTex

`!items` の suite は macro expansion 時点では `RawTex` であり、normalization 時に独自 mini-grammar として解釈される。

そのため、macro template 内の item text で `!text` を使用してよい。

```text
!defmacro{listitem}{label}: |
    !items:
        - item-!text{label}
```

処理順序は

```text
macro text interpolation
 -> interpolated RawTex
 -> !items mini-grammar
```

となる。

これは TeXFlux source の再 parse ではない。

### 6.7 別の user macro に渡す compact group

macro composition のため、user-defined macro call の required compact group 内でも interpolation を許可する。

```text
!defmacro{inner}{name}: |
    \label{!text{name}}

!defmacro{outer}{prefix}{name}: |
    !inner{!text{prefix}-!text{name}}
```

```text
!outer{sec}{intro}
```

では `inner` に `sec-intro` という text Value が渡される。

---

## 7. 禁止する context

### 7.1 AST node position

`!text` は AST splice ではない。

したがって以下は禁止する。

```text
!defmacro{bad}{x}: |
    !text{x}
```

AST Value を挿入したいなら

```text
!param{x}
```

を使用する。

### 7.2 text field 内の `!param`

逆方向も禁止する。

```text
@foo >> @bar >> @hoge{!param{variable}}:
```

は invalid とする。

理由:

```text
Argument.value: str
```

の内部へ

```text
Value: tuple[SyntaxNode, ...]
```

を挿入することはできないためである。

つまり、

```text
str + AST + str
```

という暗黙変換は存在しない。

`!param` を TeX に render して text 化するような処理も行わない。

### 7.3 `!when` / `!unless`

禁止:

```text
!when{!text{flag}}: |
    ...

!unless{!text{flag}}: |
    ...
```

flag name は compiler metadata であり静的である。

### 7.4 `!flag`

禁止:

```text
!flag{!text{name}}{on}
```

### 7.5 `!param` / `!text` / `!each` の name 引数

禁止:

```text
!param{!text{name}}
!text{!text{name}}
!each{!text{sequence}}{item}: |
    ...
!each{items}{!text{name}}: |
    ...
```

これらは variable lookup の metadata である。

### 7.6 `!import`

禁止:

```text
!import{!text{path}}
```

また binding list 内も禁止する。

```text
!import{foo.tfx}(answers=!text{x})
```

content dependency は静的でなければならない。

### 7.7 `!macroimport`

禁止:

```text
!macroimport{!text{path}}
```

macro dependency graph を interpolation で変化させてはならない。

### 7.8 structural name

command / environment / special / macro の名前そのものを interpolation で生成してはならない。

禁止概念:

```text
@!text{env}: |
    ...
```

```text
\!text{command}{...} >> ...
```

```text
!<dynamic-name>{...}
```

AST shape は parse 時点で確定する。

---

## 8. context 表

| context | `!param{x}` | `!text{x}` | 備考 |
|---|---:|---:|---|
| AST node / block Value position | OK | NG | AST splice |
| macro template 内の `RawTex.text` | NG | OK | text interpolation |
| `ParsedInvocation` の出力用 inline group | NG | OK | TeX text field |
| `@foo >> @bar >> @hoge{...}` の `{...}` | NG | OK | stack 後も text field |
| user macro の compact required value | 埋め込みとしては NG | OK | nested macro composition |
| `!when` / `!unless` の flag group | NG | NG | compiler metadata |
| `!flag` の declaration group | NG | NG | compiler metadata |
| `!param` の parameter-name group | NG | NG | compiler metadata |
| `!text` の parameter-name group | NG | NG | compiler metadata |
| `!each` の sequence/item name | NG | NG | compiler metadata |
| `!import` path / binding | NG | NG | static dependency |
| `!macroimport` path | NG | NG | static dependency |
| structural command/environment/special name | NG | NG | AST shape |

---

## 9. 展開 timing

### 9.1 macro expansion の一部とする

文字列 interpolation は既存 `expand_macros()` の責務として実行する。

行ってはならない方式:

```text
source text
 -> global string preprocessor
 -> parse
```

または

```text
render TeX
 -> string replace
```

正しい位置:

```text
parse
 -> validate
 -> desugar(>>)
 -> resolve declarations
 -> expand macros + interpolate text
 -> resolve content imports
 -> canonicalize
 -> render
```

### 9.2 `>>` より後

現行通り `>>` を先に desugar する。

interpolation のために stack grammar を変更しない。

### 9.3 canonicalize より前

`!items` 等、RawTex を後から解釈する built-in が存在するため、interpolation は canonical normalization より前でなければならない。

---

## 10. no-rescan 原則

これは本仕様の最重要規則の一つである。

`!text{x}` により挿入された文字列は **再走査しない**。

例えば `x` の文字列が

```text
!text{y}
```

だった場合、

```text
!text{x}
```

の結果は文字どおり

```text
!text{y}
```

である。

`y` は展開しない。

同様に挿入文字列が

```text
!when{draft}
!foo
@frame
>>
```

を含んでいても、それらは新しい TeXFlux syntax にならない。

つまり

```text
text interpolation
 -> generated text
 -> TeXFlux parse again
```

という経路は存在しない。

これにより、m4 / C preprocessor 型の recursive textual expansion を避ける。

---

## 11. TeX 構文解析をしない

`!text` の実装に TeX parser は不要であり、導入してはならない。

例えば

```text
\includegraphics[width=!text{width}]{fig/!text{name}.pdf}
```

は概念的に

```text
Literal("\\includegraphics[width=")
TextHole("width")
Literal("]{fig/")
TextHole("name")
Literal(".pdf}")
```

として文字列 fragment を連結するだけでよい。

TeXFlux は以下を判定しない。

- `\includegraphics` が存在するか
- `width=` の grammar が正しいか
- interpolation 後の `{}` が意味論的に正しいか
- math mode かどうか
- catcode
- LaTeX package semantics

interpolation により最終 TeX が不正になった場合は、最終的に TeX / LaTeX engine がエラーを報告する。

TeXFlux と TeX engine の責務を分離する。

---

## 12. conditional との関係

現行の lazy/drop semantics をそのまま使用する。

```text
!defmacro{m}{x}: |
    !when{feature}: |
        \foo{!text{x}}
```

`feature=off` の場合、payload 内の `!text{x}` は評価しない。

したがって、例えば `x` が text-extractable でない場合でも、その branch が drop される限り、それを理由として error にしてはならない。

これは既存 macro expansion の「捨てられた content は展開しない」という方針と一致する。

---

## 13. `!each` との関係

rest parameter 自体は sequence なので `!text` できない。

禁止:

```text
!defmacro{m}{...items}: |
    \foo{!text{items}}
```

推奨 error:

```text
'items' is a rest parameter; use !each to access its values
```

一方、`!each` の item binding は一つの `Value` なので、その item が text-extractable なら `!text` 可能である。

```text
!defmacro{labels}{...items}: |
    !each{items}{item}: |
        \label{item:!text{item}}
```

```text
!labels:
    - alpha
    - beta
```

は有効である。

item が environment 等の structural AST なら `!text{item}` は error とする。

---

## 14. user macro composition

以下を正式にサポートする。

```text
!defmacro{inner}{name}: |
    \label{!text{name}}

!defmacro{outer}{prefix}{name}: |
    !inner{!text{prefix}-!text{name}}
```

`outer` が展開される際、`inner` の compact group は

```text
!text{prefix}-!text{name}
```

という text field として先に interpolation される。

その結果の text が、現行 `_values()` と同様に一つの `RawTex` Value として `inner` に bind される。

この仕様により、AST macro 同士の composition を維持しつつ文字列値を組み立てられる。

---

## 15. Built-in SpecialInvocation の扱い

ここは fail-closed とする。

### 15.1 原則

`SpecialInvocation` の group は、単に Python 上で `str` だからという理由だけで interpolation 対象にしてはならない。

原則:

> built-in special の引数は、その special が明示的に「出力 TeX text」であると宣言したものだけ interpolation 可能とする。

### 15.2 常に禁止する control special

少なくとも以下では interpolation を禁止する。

```text
!defmacro
!param
!text
!each
!flag
!when
!unless
!import
!macroimport
```

### 15.3 output-oriented special

例えば `!vpad{...}` の値は最終的に `\vspace{...}` へ出力されるため、将来的に明示的 opt-in で interpolation を許可してよい。

ただし、最初から「すべての SpecialInvocation の str group を interpolation」してはならない。

将来 special が増えた際に compiler metadata が意図せず動的化するためである。

---

## 16. source provenance

`!param` と同じ思想を維持する。

例えば

```text
!defmacro{m}{x}: |
    \foo{pre-!text{x}-post}

!m{VALUE}
```

について、生成 text の provenance は概念的に以下とする。

```text
"\\foo{pre-"  -> macro call site
"VALUE"        -> caller の `{VALUE}`
"-post}"       -> macro call site
```

interpolated text を macro definition line へ map してはならない。

### 16.1 現行 AST への影響

現在は

```python
RawTex(text: str, span: SourceSpan)
Argument(value: str | Block, ..., span: SourceSpan)
```

なので、一つの文字列内で provenance を分割できない。

したがって精密な source map を維持するには、文字列 fragment の provenance を表す仕組みが必要である。

概念案:

```python
@dataclass(frozen=True, slots=True)
class TextFragment:
    text: str
    span: SourceSpan

@dataclass(frozen=True, slots=True)
class SourceText:
    fragments: tuple[TextFragment, ...]

    def plain(self) -> str:
        return "".join(x.text for x in self.fragments)
```

実装型名はこれでなくてよい。

必要な性質は以下である。

1. literal template 部分は macro call span を持つ。
2. `!text` 挿入部分は bound RawTex の span を持つ。
3. fragment を plain string に flatten できる。
4. `!items` 等、既存 mini-parser は plain string として読める。
5. render 時には fragment ごとに `MappedEmitter.emit()` できる。

### 16.2 coarse mapping は避ける

単純実装として

```text
pre-VALUE-post
```

全体を macro call site に map することは可能だが、既存 `!param` が持つ provenance 品質を下げる。

したがって正式仕様としては fragment-level provenance を推奨ではなく要求事項とする。

---

## 17. parser への要求

### 17.1 TeXFlux main parser は interpolation を AST special として parse しない

inline group 内の

```text
!text{x}
```

は group の opaque text のまま保持する。

RawTex 内も parse 時点では RawTex のままでよい。

`!text` hole は macro expansion pass の text scanner が認識する。

### 17.2 小さな専用 scanner を使用する

必要なのは以下だけを認識する scanner である。

```text
!text{NAME}
```

TeX parser も TeXFlux general parser も不要である。

`NAME` は macro parameter grammar で validation する。

### 17.3 AST position の `!text`

物理行の先頭等で parser が

```text
!text{x}
```

を `SpecialInvocation(name="text")` として parse した場合、それは text interpolation ではない。

この場合は明示的に

```text
!text is only valid inside a textual field of a macro template
```

相当の error にする。

---

## 18. literal `!text{...}` の escape

`!text{NAME}` が macro-template text 内で予約構文になるため、literal 出力用 escape を設けることを推奨する。

推奨構文:

```text
!!text{NAME}
```

出力:

```text
!text{NAME}
```

interpolation scanner は `!!text{` を先に認識し、生成した `!text{` を再走査しない。

### 18.1 行頭

現行 parser には `@@` による raw `@` escape が存在する。

必要であれば同じ思想で、行頭 `!!` を raw `!` escape として追加すると整合的である。

ただし、この raw-line escape は interpolation の core semantics とは独立した補助機能である。

---

## 19. error semantics

`!text` の macro binding / use error は `MacroExpansionError` を使用し、既存 `!param` と同様に expansion chain と call site を付加する。

### 19.1 macro template 外

```text
!text is only valid inside a macro template
```

### 19.2 unknown parameter

```text
unknown macro parameter 'x'
```

### 19.3 rest parameter

```text
'items' is a rest parameter; use !each to access its values
```

### 19.4 non-text Value

推奨:

```text
macro parameter 'body' is not a text value; use !param for structural values
```

### 19.5 invalid name

以下は error。

```text
!text{}
!text{1x}
!text{a b}
```

### 19.6 text context 内の `!param`

以下は typo / layer misuse として明示 error にすることを推奨する。

```text
@hoge{!param{x}}: |
    ...
```

推奨:

```text
!param cannot be used inside a text field; use !text for text interpolation
```

これにより `!param` が単なる literal text として黙って出力される事故を防ぐ。

---

## 20. module system との関係

### 20.1 `Reserved.TEXT`

`src/texflux/macros.py` の `Reserved` に

```python
TEXT = "text"
```

を追加する。

### 20.2 `.tfxm` self-contained validation

`src/texflux/modules.py` の `_TEMPLATE_NAMES` に `Reserved.TEXT` を追加する。

これにより `.tfxm` template 内の `!text` が undefined macro と誤判定されない。

### 20.3 lexical scope は不変

`!text` は bound parameter の参照だけを行う。

macro name lookup は行わない。

したがって、定義元 module の lexical environment は現行通り維持される。

### 20.4 import graph は不変

interpolation から

```text
!import
!macroimport
```

を生成することはできない。

したがって dependency discovery / caching assumptions は変わらない。

---

## 21. 実装方針

### 21.1 `src/texflux/macros.py`

主な変更点:

1. `Reserved.TEXT = "text"` を追加。
2. `text` を macro name として予約。
3. text interpolation scanner を追加。
4. `RawTex` の処理時、macro frame 内なら text field を interpolate。
5. `ParsedInvocation` の string-valued `Argument` を、text context の場合だけ interpolate。
6. user macro call の compact required group を `_values()` で bind する前に interpolate。
7. `SpecialInvocation(name=Reserved.TEXT)` が AST position に現れたら専用 error。
8. rest / unknown / non-text errors は既存 `_error()` を通し expansion chain を維持。
9. inserted text は再走査しない。

### 21.2 `src/texflux/ast.py`

最終 TeX の source map 品質を維持するため、text fragment provenance を表せる内部表現を追加する。

設計候補:

```text
SourceText = sequence<TextFragment>
```

ただし既存コードの多くが `str` API を使用しているため、実装時は影響範囲を評価すること。

代替として

```text
plain text + per-range provenance map
```

を node に保持してもよい。

規範仕様として必要なのは型名ではなく、§16 の provenance semantics である。

### 21.3 `src/texflux/parser.py`

一般 TeXFlux grammar は変更しない。

inline group の中身は引き続き opaque text。

必要なら `!!` raw-line escape を追加する。

### 21.4 `src/texflux/normalize.py`

interpolation は normalization 前に完了していること。

`!items` 等は interpolation 済み plain text を従来通り処理する。

interpolation の意味論を canonicalizer に持ち込まない。

### 21.5 `src/texflux/modules.py`

`_TEMPLATE_NAMES` に `Reserved.TEXT` を追加。

その他の import resolution semantics は変更しない。

### 21.6 `src/texflux/render.py`

source-aware text fragment を持つ場合、fragment ごとに

```python
emitter.emit(...)
```

する。

出力される TeX 文字列自体は単純 concatenation と完全に同一であること。

---

## 22. 必須テスト

### 22.1 基本

- compact `{...}` parameter の interpolation
- 一行に複数 hole
- 同一 parameter の複数回利用
- 隣接する `!text{x}!text{y}`
- empty text

### 22.2 RawTex

```text
prefix-!text{x}-suffix
```

が正しく展開されること。

### 22.3 structural group

以下をテストする。

```text
@hoge{!text{x}}: |
```

```text
@foo >> @bar >> @hoge{!text{x}}:
```

command required group、optional group、overlay group、literal brace header についても確認する。

### 22.4 AST / text 分離

- AST position の `!param{x}` は成功。
- AST position の `!text{x}` は失敗。
- text position の `!text{x}` は成功。
- text position の `!param{x}` は専用 error。

### 22.5 control metadata

以下が interpolation されないこと。

```text
!when{!text{x}}
!unless{!text{x}}
!flag{!text{x}}{on}
!param{!text{x}}
!each{!text{x}}{i}
!import{!text{x}}
!macroimport{!text{x}}
```

### 22.6 Value shape

- compact RawTex: success
- one RawTex block: success
- multi-node Value: error
- environment Value: error
- command Value: error
- rest parameter: error
- `!each` item が RawTex: success
- `!each` item が structural AST: error

### 22.7 nested macro

```text
!inner{!text{x}}
```

による text forwarding。

interpolated value が

```text
!text{y}
```

を含んでも再展開されないこと。

interpolated value が macro call spelling を含んでも実行されないこと。

### 22.8 conditional

- kept branch では interpolation される。
- dropped branch では interpolation されない。
- dropped branch 内の non-text use は、それだけを理由に error にならない。

### 22.9 module

- `.tfxm` template 内で `!text` が使用できる。
- `_check_self_contained` が `!text` を template construct と認識する。
- `!text` で macroimport/import dependency を動的生成できない。
- lexical scope が既存 test と同じまま。

### 22.10 source map

```text
!defmacro{m}{x}: |
    \foo{pre-!text{x}-post}
!m{VALUE}
```

について

```text
VALUE      -> caller value span
pre/post   -> macro call span
```

になること。

`.tfxm` 越しの nested macro でも確認する。

### 22.11 escape

`!!text` を採用する場合:

- `!!text{x}` -> literal `!text{x}`
- escape 後に再走査されない。
- 複数 escape。
- 行頭 `!!` を採用するなら parser test。

---

## 23. 代表例

### 23.1 画像

```text
!defmacro{figure}{name}{width}{caption}: |
    \includegraphics[width=!text{width}]{fig/!text{name}.pdf}
    @center: |
        !param{caption}
```

```text
!figure{result}{0.8\textwidth}: |
    Result of the experiment
```

### 23.2 environment argument + AST body

```text
!defmacro{styled}{style}{body}: |
    @foo >> @bar >> @hoge{!text{style}}: |
        !param{body}
```

ここで

```text
style -> !text -> text splice
body  -> !param -> AST splice
```

となる。

### 23.3 label generation

```text
!defmacro{sectionlabel}{id}{body}: |
    \label{sec:!text{id}}
    !param{body}
```

### 23.4 nested macro

```text
!defmacro{label}{id}: |
    \label{!text{id}}

!defmacro{sectionlabel}{prefix}{id}: |
    !label{!text{prefix}:!text{id}}
```

### 23.5 不正: AST in text

```text
!defmacro{bad}{x}: |
    @hoge{!param{x}}: |
        A
```

### 23.6 不正: dynamic condition

```text
!defmacro{bad}{flag}{body}: |
    !when{!text{flag}}: |
        !param{body}
```

---

## 24. 非目標

本機能は以下を提供しない。

- general-purpose string macro
- C preprocessor 相当
- m4 相当
- token pasting
- recursive textual expansion
- regex replacement macro
- generated TeXFlux syntax
- `eval`
- generated text の `parse` / `read`
- dynamic macro name
- dynamic command/environment name
- dynamic import
- dynamic macroimport
- arbitrary AST stringify
- TeX parser
- TeX semantic validation

将来 text -> AST がどうしても必要になった場合は、`!text` とは別の明示的な phase-changing construct として設計しなければならない。

`!text` に暗黙に追加してはならない。

---

## 25. 設計上の invariants

実装後も以下が真でなければならない。

### I1. AST macro が主である

```text
macro: syntax AST -> syntax AST
```

という基本モデルは変わらない。

### I2. `!text` は text field だけを変更する

`!text` により AST topology は変化しない。

### I3. generated text は parse されない

```text
!text
 -> string
 -> parse
```

という path は存在しない。

### I4. compiler metadata は静的

flag/import/macro namespace は source parse と declaration resolution で確定する。

### I5. no rescan

interpolated fragment は最終 text である。

### I6. TeX を理解しない

TeXFlux は interpolation のために TeX grammar を実装しない。

### I7. provenance を保持する

caller 由来 text と template scaffold を source map で区別できる。

### I8. conditional lazy semantics を維持する

捨てられた branch は interpolate しない。

### I9. lexical macro scope を維持する

`.tfxm` の name resolution は interpolation の有無で変化しない。

---

## 26. 最終モデル

```text
                      macro parameter
                            |
             +--------------+--------------+
             |                             |
        AST context                    text context
             |                             |
       !param{name}                   !text{name}
             |                             |
        AST splice                    text splice
             |                             |
             +--------------+--------------+
                            |
                      expanded AST
                            |
                       canonicalize
                            |
                          render
                            |
                           TeX
```

重要なのは、`!text` の右側から左側へ戻る矢印を作らないことである。

```text
text --X--> TeXFlux AST reparsing
```

を禁止することで、TeXFlux は

- AST macro の安全性
- syntax tree の静的構造
- module の lexical scope
- static dependency discovery
- source mapping
- deterministic expansion

を維持したまま、実用上必要な

```text
\label{prefix-!text{id}}
\includegraphics[width=!text{width}]{!text{file}}
@hoge{!text{option}}: |
```

のような文字列組み立てを自然に記述できる。

このため、機能名としても **string macro** ではなく、

> **String Interpolation in Structural AST Macros**

と位置づけるのが適切である。
