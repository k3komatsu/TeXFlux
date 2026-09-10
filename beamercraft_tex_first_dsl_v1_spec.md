# Beamercraft TeX-first DSL v1 仕様

## 0. ステータス

この文書は、Beamercraft の **TeX-first / ultra-thin preprocessor DSL v1** の正本仕様である。

v1 の設計目標は、LaTeX/Beamer の意味論を置き換えることではなく、LaTeX で特に冗長になりやすい

- `\\begin{...}` / `\\end{...}`
- 深い environment nesting
- 巨大な複数行 `{...}` 引数
- `itemize` の反復記述

だけを、インデントベースの薄い構文で圧縮することである。

最重要原則は次のとおり。

> **TeX の良いところはそのまま残し、構造上の boilerplate だけを Beamercraft が削る。**

特に、文章・数式・inline macro は原則として raw TeX のまま記述する。

---

# 1. コア設計原則

## 1.1 通常の `@foo` は TeX そのもの

Beamercraft は `foo` という名前の意味を知らなくてよい。

```text
@foo{A}{B}
```

は常に、

```tex
\\foo{A}{B}
```

へ変換する。

```text
@foo{A}{B}:
    BODY
```

は通常形では、

```tex
\\begin{foo}{A}{B}
BODY
\\end{foo}
```

へ変換する。

したがって、Beamercraft に未登録・未定義の command/environment であっても、とりあえず TeX へ変換できる。

command/environment が実際に LaTeX 側で定義されているか、引数数が正しいか、package が読み込まれているか等は **LaTeX コンパイラの責務**とする。

Beamercraft は TeX の型検査器にはならない。

---

## 1.2 `@!foo` は Beamercraft 専用名前空間

```text
@foo
```

は generic TeX directive である。

一方、

```text
@!foo
```

は Beamercraft 自身が意味を持つ special directive / macro である。

v1 の最低限の built-in は次の3つとする。

```text
@!arg:
@!body:
@!items:
```

原則:

```text
@foo   = TeX
@!foo  = Beamercraft
```

この境界は v1 で固定する。

---

## 1.3 TeX 本文は parse しない

通常行は raw TeX である。

```text
@infobox{結果}:
    圧縮率$\\alpha=N/K<1$では
    \\TextCA{良好な誤り率特性}を達成
```

Beamercraft は `\\alpha` や `\\TextCA{...}` の意味を理解しない。そのまま TeX へ渡す。

したがって、`ca(...)`, `math(...)`, `raw_inline(...)`, `text(...)`, `br(...)` のような inline abstraction は基本的に作らない。

---

# 2. lexical / indentation rule

## 2.1 directive line

現在の indentation level において、最初の non-whitespace character が `@` の行を directive line とする。

それ以外の行は raw TeX とする。

## 2.2 literal `@`

raw TeX として行頭 `@` を出したい場合は `@@` を使用する。

```text
@@example
```

は、

```text
@example
```

として出力する。

行途中の `@` は特別扱いしない。

## 2.3 indentation

v1 では、

```text
1 level = 4 spaces
```

に固定する。tab は禁止する。

通常の DSL block の nesting は常に4 ASCII spaces単位で表す。`@!items`
suite 内だけは別の mini-grammar を持ち、item continuation の構造 prefix は
2 spaces、nested list の各 level は4 spacesとする（12.3、12.4参照）。

## 2.4 blank line

blank line は indentation stack を終了させない。block の終了は、次の non-blank line の dedent により決定する。

## 2.5 structural indentation の除去

DSL 構造のための indentation は生成 TeX から除去する。ただし raw TeX 本文中で追加された indentation は保持する。

---

# 3. generic TeX directive

## 3.1 command compact form

```text
@name[opt]{arg1}{arg2}
```

は、

```tex
\\name[opt]{arg1}{arg2}
```

へ変換する。

例:

```text
@vspace{-1em}
@includegraphics[width=.8\\textwidth]{fig.pdf}
@headuline{CA}{モジュールA}
```

## 3.2 environment compact form

```text
@name[opt]{arg}:
    BODY
```

は、

```tex
\\begin{name}[opt]{arg}
BODY
\\end{name}
```

へ変換する。

---

# 4. TeX-style argument groups

generic directive は次の group をそのまま保持する。

```text
{...}   required argument
[...]   optional argument
<...>   overlay / angle argument
```

例:

```text
@foo<2->[fragile]{Title}
```

↓

```tex
\\foo<2->[fragile]{Title}
```

方針:

- group の出現順序は保持する。
- `{...}` と `[...]` は nested delimiter を認識する。
- `<...>` は v1 では non-nesting group とする。
- group 内部の TeX は解釈しない。
- compact header は v1 では 1 physical line に限定する。
- multiline argument が必要なら long form を使用する。

---

# 5. generic name

v1 の generic name は原則として、

```text
[A-Za-z][A-Za-z0-9_]*\\*?
```

を許可する。

例:

```text
@align*:
@equation*:
@frame:
@TextCA{...}
```

特殊な control sequence は raw TeX を直接記述する。

---

# 6. `:` の意味

`:` の意味は一つに統一する。

> **この行に indented suite が続く。**

`:` 自体が command/environment を意味するわけではない。

つまり「下に何かぶら下がるなら必ず `:`」である。

---

# 7. long-form arguments: `@!arg`

TeX の巨大な複数行 `{...}` 引数は `@!arg:` へ展開できる。`@!arg:` は
常に block layout の required argument を1個追加する。

```text
@mycommand:
    @!arg:
        超長い複数行の第1引数

    @!arg:
        超長い複数行の第2引数
```

↓

```tex
\\mycommand{
超長い複数行の第1引数
}{
超長い複数行の第2引数
}
```

`@!arg:` は required brace argument `{...}` を1個追加する。出現順が argument order になる。

`@!arg0`, `@!arg1` のような番号は使用しない。

structured generic invocation の direct child では、短い inline form
`@!arg{...}` も使用できる。この form は required group をちょうど1個取り、
inline layout の argument を1個追加する。suite は持てず、structured generic
invocation の direct child 以外では使用できない。

```text
@foo{SHORT}:
    @!arg{INLINE}
    @!arg:
        BLOCK
```

inline と block の form は混在でき、出現順をそのまま argument order とする。
compact header の groups と long-form arguments は同じ canonical argument node
shapeへ正規化するが、source location と `inline` / `block` の layout metadata
は保持する。`@!body{...}` は v1 には導入しない。

## 7.1 compact + long-form の混在

```text
@foo{SHORT}:
    @!arg:
        LONG ARG
```

↓

```tex
\\foo{SHORT}{
LONG ARG
}
```

optional / overlay group は header 側にそのまま置ける。

---

# 8. environment body: `@!body`

`@!body:` は environment の body を表す。

```text
@myenv:
    @!arg:
        ARG1

    @!arg:
        ARG2

    @!body:
        BODY
```

↓

```tex
\\begin{myenv}{
ARG1
}{
ARG2
}
BODY
\\end{myenv}
```

`@!body:` 内には raw TeX と通常の Beamercraft directive の両方を書ける。
`@!body{...}` の inline form は v1 では使用しない。

---

# 9. command / environment の決定規則

v1 の重要原則:

> **TeX shape は directive 名ではなく、ソース構造だけで100%決定する。**

Beamercraft は `foo` が command か environment かを registry や template から調べない。

## 9.1 suite なし

```text
@foo{A}{B}
```

→ command

```tex
\\foo{A}{B}
```

## 9.2 ordinary suite

```text
@foo{A}:
    BODY
```

→ environment

```tex
\\begin{foo}{A}
BODY
\\end{foo}
```

command/environment という構造上は、次の structured form と同じである。
ただし、`@foo{A}:` の header group は inline layout のままであり、
`@!arg:` に置き換えた場合は block layout になるため、TeX 出力の改行は
同一とは限らない。

```text
@foo:
    @!arg{A}
    @!body:
        BODY
```

compact sugar として扱う。

## 9.3 structured suite + `@!arg` only

```text
@foo:
    @!arg:
        A
    @!arg:
        B
```

→ command

```tex
\\foo{
A
}{
B
}
```

## 9.4 structured suite + `@!body`

```text
@foo:
    @!arg:
        A

    @!body:
        BODY
```

→ environment

```tex
\\begin{foo}{
A
}
BODY
\\end{foo}
```

## 9.5 structured suite の制約

direct child に `@!arg{...}`、`@!arg:`、または `@!body:` が現れた場合、その invocation は structured long form とする。

structured long form では direct child に raw text や通常 directive を混在させてはならない。

NG:

```text
@foo:
    @!arg:
        A

    これは曖昧なので禁止
```

body が必要なら必ず、

```text
@foo:
    @!arg:
        A

    @!body:
        これは本文
```

とする。

さらに、

- `@!arg{...}` と `@!arg:` は合わせて0個以上
- `@!arg{...}` は required inline argument exactly one
- `@!arg:` は required block argument exactly one
- `@!body:` は0個または1個
- `@!body:` は最後
- `@!body:` の後に `@!arg{...}` / `@!arg:` は置けない

とする。

---

# 10. 既存テンプレート型の重要ケース

## 10.1 environment

```text
@infobox{目的}:
    本文
```

↓

```tex
\\begin{infobox}{目的}
本文
\\end{infobox}
```

## 10.2 ordinary command

```text
@headuline{CA}{モジュールA}
```

↓

```tex
\\headuline{CA}{モジュールA}
```

## 10.3 body-like argument を持つ command

```tex
\\rightnotebox{Note}{
    BODY
}
```

は v1 では明示的に command argument として書く。

```text
@rightnotebox:
    @!arg:
        Note

    @!arg:
        BODY
```

`@!arg:` は block layout なので、生成される TeX は改行を含む
`\\rightnotebox{\nNote\n}{\nBODY\n}` となる。上の TeX と意味は同じだが、空白は byte-for-byte に同一とは限らない。

## 10.4 `diffdoublecolumn`

```tex
\\diffdoublecolumn{.78}{.27}{
    LEFT
}{
    RIGHT
}
```

は、

```text
@diffdoublecolumn{.78}{.27}:
    @!arg:
        LEFT

    @!arg:
        RIGHT
```

と書く。

`@!body:` がないので command である。

## 10.5 `framewithnote`

```tex
\\framewithnote[.5]{Title}{
    BODY
}{
    NOTE
}
```

は、

```text
@framewithnote[.5]{Title}:
    @!arg:
        BODY

    @!arg:
        NOTE
```

と書ける。

## 10.6 引数付き environment

```tex
\\begin{myenv}{
ARG1
}{
ARG2
}
BODY
\\end{myenv}
```

は、

```text
@myenv:
    @!arg:
        ARG1

    @!arg:
        ARG2

    @!body:
        BODY
```

と書ける。

---

# 11. stacking `>>`

一本道の environment nesting は `>>` で圧縮できる。

```text
@A >> B >> C:
    BODY
```

は、

```text
@A:
    @B:
        @C:
            BODY
```

と同値である。

生成 TeX:

```tex
\\begin{A}
\\begin{B}
\\begin{C}
BODY
\\end{C}
\\end{B}
\\end{A}
```

## 11.1 引数付き stacking

```text
@frame{本発表の概要} >> center >> minipage{.9\\textwidth}:
    ...
```

## 11.2 `@` は先頭だけ

canonical syntax:

```text
@frame{Title} >> center:
```

v1 では、

```text
@frame{Title} >> @center:
```

は禁止する。

## 11.3 `@!` special directive との stacking

chain 内では `!name` と書く。

```text
@frame{Title} >> !items:
    - A
    - B
```

先頭から special directive の場合は、

```text
@!items:
    - A
    - B
```

とする。

## 11.4 stacking の制約

`>>` は一本道の container composition のみを意味する。

- generic TeX segment は `>>` 内では environment として扱う。
- special directive は stacking 対応のものだけ利用可能。
- long-form `@!arg:` / `@!body:` を chain の途中には置かない。
- chain header は v1 では1 physical line。

---

# 12. `@!items`

`itemize` boilerplate を減らすため、v1 に `@!items:` を持つ。

```text
@!items:
    - 項目A
    - 項目B
    - 項目C
```

↓

```tex
\\begin{itemize}
\\item 項目A
\\item 項目B
\\item 項目C
\\end{itemize}
```

item 本文は raw TeX。

```text
@!items:
    - OFDMより\\TextCA{狭い帯域幅で\\\\同一の伝送速度}で情報を伝送
    - FFTサイズ$N\\ge512$で\\TextCA{良好な誤り率特性}
```

## 12.1 item overlay

```text
@!items:
    -<only@1> 1枚目だけ
    -<only@2> 2枚目だけ
```

↓

```tex
\\item<only@1> 1枚目だけ
\\item<only@2> 2枚目だけ
```

## 12.2 optional item label

```text
@!items:
    -[A] 項目A
    -[B] 項目B
```

overlay と併用する場合は、

```text
-<2->[A] 項目
```

→

```tex
\\item<2->[A] 項目
```

## 12.3 multiline item

item より構造上2 spaces深い non-`-` 行は、その item の continuation body とする。
最初の2 spacesは構造用 prefixとして除去し、それを超える spacesは raw TeX の
indentationとして保持する。

```text
@!items:
    - 長い項目の1行目
      2行目も同じitem
      \\TextCA{TeXもそのまま}
```

## 12.4 nested items

item の continuation level に `-` が現れた場合、nested `itemize` とする。

```text
@!items:
    - 確率推論
        - GaBP
        - AMP
        - EP
    - 固有モード伝送
```

`@!items` suite は suite base をcolumn 0とした相対 indentationで解析する。
list depth は0, 4, 8, ... spaces、item continuation は現在のdepth + 2 spaces
以上、nested itemize は現在のdepth + 4 spacesである。nested markerが一度に
2 level以上飛ぶ場合、またdepthにあるnon-item lineはエラーとする。同じdepth
の次のitemまでのblank separatorは無視し、同じitemのcontinuationに属する
blank lineはcontinuation内に保持する。

---

# 13. `@!` namespace と extension

v1 では `@!` を Beamercraft の special namespace として予約する。

最低限:

```text
@!arg
@!body
@!items
```

方針:

- `@!arg` / `@!body` は structural metadirective。
- `@!items` は authoring sugar。
- unknown `@foo` は generic TeX として許可。
- unknown `@!foo` は compile error。

将来的には、

```text
@!cols
@!col
@!inset
@!fig
@!result
```

などの built-in / project-defined macro を追加できる。

ただし v1 では source-level の macro definition language、variables、loops、conditions、expression evaluator は導入しない。

必要になった special directive は extension/registry 層で追加する。

---

# 14. template integration

Beamercraft は `template.tex` を解析しない。

典型構成:

```text
Beamercraft source
    ↓
preprocessor
    ↓
generated content.tex
    ↓
user template.tex
    ↓
LaTeX
    ↓
PDF
```

template 側が、

```tex
\\newcommand{\\foo}[2]{...}
\\newenvironment{bar}[1]{...}{...}
\\newtcolorbox{infobox}[1]{...}
```

などを定義していても、Beamercraft に登録する必要はない。

利用側は、

```text
@foo{A}{B}
```

または、

```text
@bar{A}:
    BODY
```

または、

```text
@infobox{Title}:
    BODY
```

と書けばよい。

---

# 15. raw TeX escape hatch

通常行はすでに raw TeX なので、専用 `raw(...)` API は不要。

Beamercraft 構文では表現しにくいものはそのまま書く。

```text
@frame{特殊な例}:
    \\somecrazycommand{...}
    \\begin{some-special-environment}
        ...
    \\end{some-special-environment}
```

v1 では `@\\foo` のような追加 escape syntax は導入しない。

generic `@foo` と raw TeX の2経路だけで十分とする。

---

# 16. error policy

Beamercraft が検査するのは DSL structure であり、TeX semantics ではない。

Beamercraft error:

- 不正 indentation
- tab indentation
- `:` の後に indented suite がない
- structured long form に raw body を直接混在
- `@!body:` が複数存在
- `@!body:` の後に `@!arg{...}` / `@!arg:` がある
- `@!arg:` / `@!body:` が不正な位置にある
- unknown `@!foo`
- stacking 非対応 special directive を `>>` に入れる
- directive header の delimiter が閉じていない
- v1 で禁止される multiline header

Beamercraft error にしない:

```text
@ThisCommandDoesNotExist{A}
@NoSuchEnvironment:
    BODY
```

これらは TeX へ変換し、未定義なら LaTeX がエラーを報告する。

---

# 17. parser / AST

Parser は template や TeX command definition を知らない。

概念 AST:

```text
Document
├── RawTex
├── GenericInvocation
│   ├── name
│   ├── header_groups
│   ├── long_args[]
│   └── body?
├── Stack
└── SpecialInvocation
```

parser と canonical AST の major node は file、1-based line、1-based Unicode
character column の source location を保持する。compact header group と
long-form `@!arg` は canonical AST 上で同じ argument shapeになるが、argument
の source location と inline/block layout metadata は失わない。

command/environment を別 Node type にする必要はない。

```text
GenericInvocation(..., body=None)
```

なら command、

```text
GenericInvocation(..., body=...)
```

なら environment。

---

# 18. normalization

syntax sugar は早い段階で canonical AST に正規化する。

```text
@foo{A}:
    BODY
```

と、inline `@!arg{A}` を使う次の structured form は、
同じ canonical node kinds と argument layout になる。

```text
@foo:
    @!arg{A}
    @!body:
        BODY
```

block `@!arg:` を使う structured form では、同じ command/environment 構造を
保ちながら argument layout metadata が `block` になる。

また、

```text
@A >> B >> C:
    BODY
```

は、

```text
A(body=B(body=C(body=BODY)))
```

相当に desugar する。

正規化後の canonical AST には syntax-only の `Stack`、`SpecialInvocation`、
`@!arg`、`@!body` node を残さない。

Renderer はできるだけ dumb / deterministic に保つ。

---

# 19. source location

各 AST node は最低限、

```text
file
line
column
```

を保持する。

オプションで generated TeX に、

```tex
% beamercraft: slides.bmc:42
```

のような source comment を出力可能にする。

---

# 20. canonical style

### 短い command

```text
@foo{A}{B}
```

### 短い environment

```text
@foo{A}:
    BODY
```

### 長い command arguments

```text
@foo:
    @!arg:
        A

    @!arg:
        B
```

### 長い environment arguments + body

```text
@foo:
    @!arg:
        A

    @!arg:
        B

    @!body:
        BODY
```

### stacking

```text
@frame{Title} >> center:
    BODY
```

### itemize

```text
@!items:
    - A
    - B
```

### raw TeX

```text
$\\alpha=N/K$
\\TextCA{そのままTeX}
```

---

# 21. 実スライド風の例

```text
@frame{スペクトル有効周波数分割多重：SEFDM}:

    @vspace{-.5em}

    @diffdoublecolumn{.48}{.48}:
        @!arg:
            @scalebox{.9}:
                @!arg:
                    @input{figs/sefdm_ofdm.tex}

        @!arg:
            @small
            @scalebox{.9}:
                @!arg:
                    @input{figs/sefdm_mod.tex}

    @diffdoublecolumn{.78}{.27}:
        @!arg:
            @!items:
                - 直交周波数分割多重（OFDM）より\\TextCA{狭い帯域幅で\\\\同一の伝送速度}で情報を伝送する技術
                - OFDMのように逆高速フーリエ変換（IFFT）を用いて\\TextCA{低計算量で変調が可能}

        @!arg:
            @rightnotebox:
                @!arg:
                    Note

                @!arg:
                    @align*:
                        &\\text{圧縮率 } \\alpha = N/K < 1 \\\\
                        &\\text{サブキャリア数} N \\\\
                        &\\text{FFTサイズ} K
```

ここでは、

- inline content は完全に TeX
- `diffdoublecolumn` / `rightnotebox` を Beamercraft が知らなくてもよい
- command の巨大な body-like argument は `@!arg:` で展開
- `align*` は generic environment
- `input`, `small`, `vspace` は generic command
- list boilerplate だけ `@!items` が軽量化

という v1 の思想が一通り現れる。

---

# 22. v1 で意図的にやらないこと

- TeX parser
- template.tex の解析
- LaTeX command/environment definition の自動検出
- command/environment registry の必須化
- inline TeX AST
- 自動 escaping
- Python embedded DSL
- YAML authoring
- source-level variables
- loops
- conditions
- expression evaluator
- source-level macro definition language
- implicit columns
- TeX command の argument count 検査
- LaTeX package dependency 検査

必要なら raw TeX にそのまま逃げられることを優先する。

---

# 23. v1 grammar sketch

```text
document          ::= line*

line              ::= blank
                    | raw_tex
                    | escaped_at
                    | directive_line

directive_line    ::= indent "@" chain [":"]

chain             ::= segment (" >> " segment)*

segment           ::= generic_segment
                    | special_segment

generic_segment   ::= name group*

special_segment   ::= "!" name group*

group             ::= required_group
                    | optional_group
                    | angle_group

required_group    ::= "{" balanced_brace_content "}"

optional_group    ::= "[" balanced_bracket_content "]"

angle_group       ::= "<" angle_content ">"

name              ::= identifier ["*"]

identifier        ::= letter (letter | digit | "_")*

escaped_at        ::= indent "@@" raw_content

indent            ::= ("    ")*

raw_tex           ::= any line whose first non-whitespace
                      character is not "@"
```

suite parsing:

```text
directive without ":":
    generic -> command
    special -> special leaf

directive with ":":
    parse indented suite

    if generic and suite is structured long form:
        @!arg* + @!body?
        body absent -> command
        body present -> environment

    else if generic:
        ordinary suite -> environment body

    if special:
        special directive owns suite semantics
```

stacking:

```text
@A >> B >> C:
    BODY
```

は canonical AST 上で、

```text
A(body=B(body=C(body=BODY)))
```

へ正規化する。

---

# 24. 利用者の mental model

利用者が最初に覚えるのは次だけでよい。

```text
普通の行       → そのまま TeX

@foo           → \\foo
@foo:          → 原則 \\begin{foo}...\\end{foo}

>>             → environment を横に stack

@!arg:         → 長い {...} 引数
@!body:        → environment body
@!items:       → 簡潔な itemize

@@             → literal @
```

より厳密には `@foo:` が structured long form の command にも使われるが、判断は常にソース構造だけで行う。

最重要な不変条件:

> **Beamercraft が `foo` の意味を知らなくても、ソース構造だけから TeX の形を決定できる。**

そして、

> **TeX の意味論は TeX に任せる。**

この2点を v1 の中心原則とする。
