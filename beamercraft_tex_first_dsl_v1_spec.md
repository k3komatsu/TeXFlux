# Beamercraft TeX-first DSL v1 仕様

## 0. ステータス

本書は Beamercraft v1 の normative specification である。実装、README、
examples、unit test、golden test は本書に従う。

Beamercraft は Python 3.11 以上で動作する TeX-first preprocessor である。
TeX の意味論を置き換えず、行構造、indentation、environment の
begin/end、構造化した required argument、itemize の定型だけを扱う。

利用者が覚える prefix は次の三つだけである。

~~~text
\ = TeX command
@ = TeX environment
! = Beamercraft special construct
~~~

prefix の意味は字句的に決まり、template knowledge、package、command
registry、environment registry、既知の LaTeX 名には依存しない。

## 1. 設計原則

### 1.1 TeX-first

通常の source line は raw TeX である。Beamercraft は inline/raw TeX を
parse、validate、normalize、escape しない。

~~~text
\vspace{-1em}
\headuline{CA}{タイトル}
\includegraphics[width=.8\textwidth]{fig.pdf}
\TextCA{foo}
~~~

上の行はそのまま出力される。先頭が backslash の行は、top-level の
trailing colon または >> がある構造構文候補だけ header scanner に
渡す。それ以外の command line は raw TeX として保持する。TeX 全文
parser は v1 に存在しない。

### 1.2 prefix の役割

- \name は TeX command。
- @name: は TeX environment。
- !name は Beamercraft special construct。

同じ名前でも prefix が違えば別の構文要素である。@ は environment
専用であり、suite marker のない @name または @name{...} は syntax
error である。

! は Beamercraft special namespace 専用である。未登録の special は
DirectiveError になる。組み込み special は block、items、vpad であり、
arg と body は structured invocation の explicit fallback である。

### 1.3 名前に関する知識を持たない

unknown environment name も構造が正しければ出力する。名前の存在、
package、引数数、TeX 側の定義は LaTeX toolchain の責務である。

environment name は狭い英数字 regex に固定しない。少なくとも
@align*:、@equation*: のような star 付き名を受理する。scanner は
prefix の後から最初の group opener、空白、top-level colon、>> までを
environment name として読む。

## 2. 物理行と indentation

### 2.1 改行

入力の CRLF と CR は LF に正規化してから物理行として扱う。出力は
最後に newline を一つ持つ。tab は v1 では禁止する。

### 2.2 indentation

通常の Beamercraft nesting は ASCII space 四個単位である。

~~~text
@frame{タイトル}:
    @center:
        BODY
~~~

構造 node の direct child は suite base indentation に置く。raw TeX line
に追加 indentation がある場合、その追加空白は raw TeX として保持する。
ただし、追加 indentation の位置にある `@`、`!`、または構造構文候補の
`\\` 行は invalid structural indentation とする。構造構文候補でない通常の
raw TeX 行だけが追加 indentation を保持する。
blank line は現在の block に保持される。block の終了は次の nonblank line
が suite base より dedent した時に決まる。ファイル末尾まで続く blank
line は、開いている suite の外側に戻してから root block に保持する。

### 2.3 literal at

構造位置で raw line を @ から始めたい場合は @@ と書く。先頭の
escape 用 @ 一個だけを除去し、残りを raw TeX として渡す。

~~~text
@@literal-at
~~~

~~~tex
@literal-at
~~~

## 3. structural header

### 3.1 字句分類

header の invocation prefix は次で確定する。

~~~text
\command
@environment
!special
~~~

\command は suite suffix がなければ raw TeX である。top-level trailing
colon または >> があれば structured command 候補になる。

@environment は suite marker colon を必ず持つ。!special は special
handler または親の explicit rule に従う。

### 3.2 inline groups

header の group は次の順序で任意個を持つ。

~~~text
{...}   required group
[...]   optional group
<...>   overlay group
~~~

group の内容は raw text であり、TeX として解釈しない。scanner は
required、optional、overlay の delimiter depth だけを追跡する。

### 3.3 top-level structural token

header scanner が認識する structural token は header の group nesting
depth 0 にあるものだけである。

- 末尾の colon
- 空白で区切った >>

group 内部の colon と >> は opaque raw text である。

~~~text
\foo{A >> B}:
    \bar

\foo{\texttt{A: B}}:
    \bar
~~~

上の group 内部の記号は operator や suite marker にならない。

### 3.4 top-level trailing colon の予約

top-level の末尾 colon は Beamercraft の予約構文である。

~~~text
\textbf{注意}:
    本文
~~~

これは structured command であり、suite のない raw TeX 一行とは解釈
しない。suite がなければ syntax error になる。この予約により v1 は
完全な TeX superset ではない。

colon の後ろには header 終端以外の token を置けない。suite marker の
後ろに vertical bar などを置く block-scalar variant は v1 に存在せず、
parser は syntax error にする。

scanner が depth 0 の colon を segment の後ろで認識した時点で、その
colon は予約される。したがって colon は末尾の non-space token でなければ
ならず、`\textbf{注意}: 本文` のような行は raw TeX へ戻さず syntax
error とする。segment と colon の間の空白は許容する。これは v1 が
完全な TeX superset ではないことを明確にするための意図的な境界である。
有効な segment を最後まで scan できない場合でも、行末の depth 0 colon は
構造構文候補として扱い、parser は syntax error を返す。

### 3.5 header comment

v1 では structural header 末尾の TeX comment をサポートしない。

~~~text
@frame{Title}: % comment
~~~

これは structural header として受理しない。通常の raw TeX line 中の
% はそのまま TeX に渡す。

## 4. TeX environment: @name:

environment の begin/end を @ と suite で簡略化する。

~~~text
@frame[t]{タイトル}:
    BODY
~~~

~~~tex
\begin{frame}[t]{タイトル}
BODY
\end{frame}
~~~

~~~text
@center:
    BODY
~~~

~~~tex
\begin{center}
BODY
\end{center}
~~~

header の compact groups は begin line にそのまま出力する。

~~~text
@infobox{結果}:
    BODY
~~~

~~~tex
\begin{infobox}{結果}
BODY
\end{infobox}
~~~

suite marker のない environment header は、environment 名が unknown
であるかどうかに関係なく syntax error である。

## 5. Structured command: \name:

### 5.1 implicit form

\name: は suite 全体を一個の追加 required long argument とする。
suite 内の statement は source order のまま同じ argument 内へ normalize
される。blank line もその argument の内容として保持され、argument
boundary は作らない。

~~~text
\foo:
    \bar
    \baz
~~~

~~~tex
\foo{
\bar
\baz
}
~~~

compact groups は suite argument より先に出力される。

~~~text
\foo{A}:
    X
    Y
~~~

~~~tex
\foo{A}{
X
Y
}
~~~

suite 内は raw text 限定ではない。nested environment、special、
stack も通常の Beamercraft 構文として normalize される。

~~~text
\foo:
    説明文

    @infobox{結果}:
        Hello
~~~

~~~tex
\foo{
説明文

\begin{infobox}{結果}
Hello
\end{infobox}
}
~~~

### 5.2 explicit form

一個の suite を分割して複数の long arguments にしたい場合だけ、direct
child に !arg を置く。各 !arg が一個の required argument になる。

~~~text
\foo:
    !arg:
        ARG1

    !arg:
        ARG2
~~~

~~~tex
\foo{
ARG1
}{
ARG2
}
~~~

compact arguments は explicit long arguments より前に出力される。
!arg{INLINE} は一個の inline required argument、!arg: は suite
全体を一個の block required argument とする。inline と block は source
order で混在できる。

direct child に !arg または !body が現れた command は explicit mode
になる。command の explicit mode では direct child は !arg だけで
なければならず、ordinary statement、environment、special、!body は
混在できない。!body は command では invalid である。

## 6. Special constructs

### 6.1 !block

!block: は suite の canonical rendering を、実際の TeX { ... } で
囲むだけである。文脈によって brace を吸収したり削除したりしない。

~~~text
!block:
    foo
    \bar
~~~

~~~tex
{
foo
\bar
}
~~~

local TeX scope としても使える。v1 では別の group/scope special は
導入しない。

implicit command suite の中では command 自身の long-argument braceも
生成されるため、!block は意図的に二重 brace になる。

~~~text
\foo:
    !block:
        A
~~~

~~~tex
\foo{
{
A
}
}
~~~

### 6.2 !arg

!arg は structured command または explicit environment の direct
child にだけ置ける。suite 全体を一個の required block argument とし、
inline form !arg{...} は一個の required inline argument とする。

!arg の suite 内に !block がある場合も !block の brace は消えない。

~~~text
\foo:
    !arg:
        !block:
            A
~~~

~~~tex
\foo{
{
A
}
}
~~~

### 6.3 !body

!body: は explicit environment の suite 全体を environment body と
する。inline form は v1 にない。

~~~text
@myenv:
    !arg:
        VERY LONG ARG
    !body:
        BODY
~~~

~~~tex
\begin{myenv}{
VERY LONG ARG
}
BODY
\end{myenv}
~~~

!arg と !body を構造 invocation の外に置くことはできない。

### 6.4 !vpad

`!vpad` は suite の前後に vertical spacing command を挿入する special
である。required inline group を一個または二個受け取り、suite は必須
である。

~~~text
!vpad{-1em}{2em}:
    contents
~~~

~~~tex
\vspace{-1em}
contents
\vspace{2em}
~~~

group が一個だけの場合は leading `\vspace` だけを出力する。
二個目の group がある場合は、suite の正規化された body の後ろに
trailing `\vspace` を出力する。group が 0 個、3 個以上、required inline
以外の group、または suite なしは validation error である。

`!vpad` は AST-to-AST special であり、TeX string を直接返さない。
normalization 後は `vspace` の canonical command、suite の canonical
nodes、必要ならもう一つの `vspace` command の順になる。`>>` は
special の意味を知らずに先に nested suite へ desugar されるため、次の
ような stacking にも使える。

~~~text
@frame[t]{Title} >> !vpad{-.7em} >> \singlecolumn[.11]:
    contents
~~~

`!vpad` の生成した `vspace` command と body はいずれも `!vpad` または
元の suite node の source location を保持する。

## 7. Environment explicit form

通常の environment は suite 全体を body とする。

~~~text
@foo{A}:
    BODY
~~~

~~~tex
\begin{foo}{A}
BODY
\end{foo}
~~~

environment の long arguments を明示する場合は direct child に
!arg/!body だけを置く。

~~~text
@myenv:
    !arg:
        ARG1
    !arg:
        ARG2
    !body:
        BODY
~~~

validation rule は次の通りである。

- !arg は 0 個以上。
- !body は 0 個または 1 個。
- !body がある場合は最後。
- !body の後に !arg は置けない。
- explicit mode の direct child は !arg または !body だけ。
- ordinary body との混在は禁止。

!body を省略した environment はなお environment である。canonical
AST には空の body block を保持し、begin/end を出力する。

~~~text
@myenv:
    !arg:
        ARG ONLY
~~~

~~~tex
\begin{myenv}{
ARG ONLY
}
\end{myenv}
~~~

## 8. >>: pure structural desugaring

>> は command、environment、special の意味論を知らない structural
sugar である。prefix 付きの各 segmentを一個の child suiteとして
右から左へ nested syntax AST に変換し、その後は通常の normalization
を行う。

~~~text
\foo >> @bar >> !block:
    A
~~~

はまず次の syntax shape になる。

~~~text
\foo:
    @bar:
        !block:
            A
~~~

その結果は次である。

~~~tex
\foo{
\begin{bar}
{
A
}
\end{bar}
}
~~~

>> header の各 segment は必ず \、@、! のいずれかで始める。segment
に固有の terminal rule は持たせない。>> は normalization 完了後の
canonical ASTにも rendererにも残らない。

## 9. !items: itemize mini-grammar

!items: は次へ展開される。

~~~text
!items:
    - A
    - B
~~~

~~~tex
\begin{itemize}
\item A
\item B
\end{itemize}
~~~

suite の item level を 0 とし、次の規則を使う。

- - が item marker。
- overlay は marker 直後の <...>。
- optional label はその後の [...]。
- item の本文と continuation は raw TeX。
- continuation は item depth より少なくとも2 space深い。
- nested item は item depth より4 space深い。
- nested list も itemize environment になる。
- overlay、label、multiline、nested list を保持する。

~~~text
!items:
    -<2->[A] first line
      continuation
        - nested
          nested continuation
~~~

!items suite 内では item syntax が予約される。suite の行は item parser
へ raw line として渡し、suite の direct structure node は許可しない。
item text と continuation 内の @、\、! は raw text であり、TeX semantics
を解析しない。したがってこの位置の `@@` も unescape せず、raw text として
保持する。

## 10. AST

### 10.1 syntax AST

概念的な syntax node は次である。

~~~text
RawTex
ParsedInvocation(kind=command | environment)
SpecialInvocation
Stack
Block
Argument
~~~

ParsedInvocation.kind は prefix から確定した command または
environment である。ParsedInvocation は name、compact groups、suite、
source location を持つ。syntax AST に suite scalar variant や
block-scalar metadata は存在しない。

SpecialInvocation は special name、groups、suite、source locationを
持つ。Stack は prefix付き segment 列、suite、source location を持つ。

### 10.2 canonical AST

normalization 後の canonical AST は次で十分である。

~~~text
RawTex
GenericInvocation
BraceGroup
Item
Argument
Block
~~~

GenericInvocation は name、arguments、body、location を持つ。bodyが
None なら TeX command、Block なら TeX environment である。explicit
environment で !body がない場合も空 Block を置くため、environment
であることを失わない。

BraceGroup は単なる metadata ではなく、renderer が常に実際の TeX
braces を出力する canonical node である。Argument.value は inline
raw string または canonical Block であり、literal group を argument
context が吸収する形は持たない。

全 major node と diagnostic は file、1-based line、1-based columnを
保持する。

## 11. Normalization と renderer

pipeline は次の通りである。

~~~text
physical lines
    -> indentation parser
    -> syntax AST
    -> pure >> desugaring
    -> invocation/special normalization
    -> canonical AST validation
    -> TeX renderer
~~~

normalization の規則は次である。

1. >> を nested suite へ desugar する。
2. prefix から command/environment/special を確定する。
3. command の compact groups を header arguments にする。
4. command の通常 suite を normalize して一個の required block argument
   にする。
5. command の direct child に explicit !arg/!body があれば explicit
   validation を行い、各 !arg を一個の argument にする。
6. environment の通常 suite を body にする。
7. environment の explicit !arg/!body を validation して arguments と
   body に分ける。
8. !block は canonical BraceGroup、!items は canonical itemize
   invocation、!vpad は canonical `vspace` command と body の sequence
   に展開する。
9. syntax-only node が canonical AST にないことを検証する。

renderer は canonical AST だけを受け取る。renderer は special name、
>>、structured mode、template、registry を知らない。

- command は \name と compact/long arguments を出力する。
- environment は \begin{name}、arguments、body、\end{name} を出力する。
- BraceGroup は常に {、body、} を出力する。
- block argument は常に outer {...} を出力する。
- BraceGroup が block argument の body 内にあれば inner brace も出力する。
- raw TeX は改変せず出力する。

special handler は TeX string ではなく canonical AST tuple を返す。
handler の出力も canonical boundary で再帰的に normalize する。

## 12. Diagnostics / source mapping

ParseError、ValidationError、DirectiveError は発生源の location を指す。
少なくとも次の location を保持する。

- header prefix と name
- compact group
- suite marker
- stack segment
- command suite の long-argument boundary
- !block
- explicit !arg / !body
- item と item prefix

>> desugaring、command suite normalization、!block、!arg、!body、!items、
!vpad の展開で originating location を破棄しない。

将来の source map は概念的に次の bridge を提供できる。

~~~text
Beamercraft source
    <-> Beamercraft source map
generated .tex
    <-> SyncTeX
PDF
~~~

v1 は PDF や SyncTeX 本体を実装しないが、AST を TeX string へ早期に
潰して location を失う設計にはしない。

## 13. Special extension

将来の user-defined special は、Python 側の軽量な登録関数から
AST-to-AST transformation として追加する。

~~~python
@directive("result")
def result(node):
    ...
~~~

~~~text
!result:
    ...
~~~

v1 で巨大な plugin framework、implicit extension loading、user sourceの
dynamic import は導入しない。in-process registry の handler 契約だけを
維持する。

## 14. v1 で扱わないもの

次は v1 の仕様外である。

- TeX 全文 parser、template parser、package discovery
- command/environment registry による形状推論
- 自動 escape、TeX 引数数 validation、TeX semantic normalization
- variables、expressions、loops、conditions、source macro language
- YAML または Python embedded authoring DSL
- id/ref による subtree reuse
- renderer backend framework
- runtime dependency
- structural header 末尾 comment
- suite marker後の block-scalar variant
- command/environment 以外の新しい prefix 規則

## 15. 形式的な構文スケッチ

次は実装を拘束する簡略 grammar であり、TeX 本文の grammar ではない。

~~~text
document             ::= physical-line*
structural-header    ::= segment (SP+ ">>" SP+ segment)* SP* ":"
segment              ::= command-segment | environment-segment | special-segment
command-segment      ::= "\" command-name group*
environment-segment  ::= "@" environment-name group*
special-segment      ::= "!" special-name group*
command-name         ::= ASCII-letter ASCII-name-char*
environment-name     ::= ASCII-letter environment-char*
special-name         ::= ASCII-letter ASCII-name-char*
~~~

colon、>>、group opener は header nesting depth 0 でだけ認識する。
environment-char は空白、group opener、top-level colon、>> を除く文字
であり、star や必要な environment punctuation を許す。

\command の suite marker なしの行は structural-header grammar に入らず
raw TeX となる。ただし top-level trailing colonを持つ行は suiteを要求
する。@environment の suite markerなしは error となる。!special の
suite 可否は special の契約で決まり、unknown special は error となる。

## 16. 決定性と代表例

同じ source から複数の AST を作らないため、次を固定する。

~~~text
\foo       -> command
@foo:      -> environment header; suiteなしならerror
!foo       -> special
~~~

さらに次を保証する。

- top-level trailing colon だけが suite marker。
- top-level group 内部の colon と >> は opaque。
- \foo: の通常 suite 全体が一個の implicit long argument。
- direct !arg/!body の存在が explicit mode を選ぶ。
- implicit/explicit mode は混在不可。
- !block は常に literal BraceGroup であり context により消えない。
- !vpad は一個または二個の required inline group と suite を受け取り、
  canonical `vspace` sequence に展開する。
- >> は pure desugaring であり renderer に残らない。

代表的な canonical example は次である。

~~~text
\diffdoublecolumn{0.78}{0.27}:
    !items:
        - 直交周波数分割多重（OFDM）より\TextCA{狭い帯域幅で\\同一の伝送速度}で情報を伝送する技術
        - OFDMのように逆高速フーリエ変換（IFFT）を\\用いて\TextCA{低計算量で変調が可能}

    \rightnotebox{Note} >> @align*:
        &\text{圧縮率 } \alpha = N/K < 1 \\
        &\text{サブキャリア数} N \\
        &\text{FFTサイズ} K
~~~

概念的な出力:

~~~tex
\diffdoublecolumn{0.78}{0.27}{
\begin{itemize}
\item 直交周波数分割多重（OFDM）より\TextCA{狭い帯域幅で\\同一の伝送速度}で情報を伝送する技術
\item OFDMのように逆高速フーリエ変換（IFFT）を\\用いて\TextCA{低計算量で変調が可能}
\end{itemize}
\rightnotebox{Note}{
\begin{align*}
&\text{圧縮率 } \alpha = N/K < 1 \\
&\text{サブキャリア数} N \\
&\text{FFTサイズ} K
\end{align*}
}
}
~~~

この例を parser、normalization、renderer の主要 golden testにする。
