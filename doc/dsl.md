# TeXFlux DSL v1 言語仕様

## 1. ステータスと設計目標

本書は TeXFlux v1 の利用者向け仕様である。言語の規範的な定義は
texflux_tex_first_dsl_v1_spec.md にあり、実装、README、examples、テスト、
そして本書もその定義に一致しなければならない。

TeXFlux は TeX-first の indentation-based preprocessor である。TeX 本文を
解釈・escape・正規化せず、構造上の次の要素だけを短く記述する。

- environment の begin/end
- 構造化 command の required argument
- literal TeX brace group
- itemize の定型
- value の入れ子を表す composition

v1 の中心モデルは「suite が値を作り、container/command/special が値を
消費する」である。suite の形は次の三つだけである。

~~~text
suffix なし  = RHS がすでに閉じた一つの value
:           = '-' ごとに一つの block value を作る sequence
: |         = suite 全体を一つの multiline block value
~~~

## 2. Prefix

prefix の意味は字句的に決まり、TeX package、template、registry、既知の
command 引数数には依存しない。

~~~text
\   TeX command
@   structural container
!   TeXFlux special transformation
~~~

@ は named TeX environment 専用ではない。v1 の @ container は次の通り。

~~~text
@name             named TeX environment container
@{}               anonymous literal TeX brace container
@{RAW_TEX}        leading raw TeX を含む literal brace container
@                 anonymous transparent container
@:                transparent container の suite form
~~~

通常の TeX command line は raw TeX のままである。ただし top-level の
structural suffix または stack separator を含む command lineは構造候補に
なる。unknown named environment はそのまま受理し、LaTeX 側で解決する。
unknown special は error である。

## 3. 物理行と indentation

入力の CRLF/CR は LF に正規化する。tab は禁止する。出力は必ず最後に
newline を一つ持つ。

通常の構造 indentation は ASCII space 四個単位である。suite の direct
child は suite base に置く。raw TeX の追加 indentation は保持するが、
追加 indentation の位置にある構造候補は error である。blank line の扱いは
suite mode によって異なる。

構造位置で raw な @ から始まる行は @@ で escape できる。

~~~text
@@literal-at
~~~

~~~tex
@literal-at
~~~

## 4. Header scanner

header scanner は group 内部を TeX として解釈しない。required group、
optional group、overlay group を balanced に走査する。

~~~text
{...}   required group
[...]   optional group
<...>   overlay group
~~~

group 内部の colon、pipe、>> は常に opaque raw text である。top-level の
末尾 colon と、前後を ASCII space で区切った >> だけが structural token
になる。末尾でない colon を含む通常の command line は raw TeX のままである。
header 末尾の TeX comment は v1 ではサポートしない。

suite suffix は次だけを受理する。

~~~text
:
:|
: |
~~~

colon の後ろに optional whitespace と pipe があると block mode、pipe が
なければ sequence mode になる。それ以外の token は parse error である。
pipe は suite suffix の直後だけ特別扱いし、raw TeX 内の pipe は保持する。

@ の lexical classification は次の優先順位である。

~~~text
@{...}  literal brace container
@      transparent container
@name  named environment container
~~~

@{...} の group は header raw TeX であり、empty name の environment として
扱ってはならない。balanced group の内容は TeXFlux が parse/validate しない。

stack の各 segment は必ず完全な prefix を持つ。省略記法はない。

~~~text
@{} >> @center >> @{\small}
~~~

次は invalid である。

~~~text
@{} >> center >> {\small}
~~~

## 5. Sequence suite

plain colon の suite は明示された '-' value entry の並びである。structured
command の sequence suite には少なくとも一つの entry が必要である。各 '-'
が一つの block value を開始し、次の同じ階層の '-' までがその value に属する。
したがって value の境界は '-' だけで決まり、各 entry は常に block layout
の value になる。

~~~text
\foo:
    - A
      continuation of A
    - B
~~~

~~~tex
\foo{
A
continuation of A
}{
B
}
~~~

blank line は sequence entry 間の separator として無視する。

~~~text
\foo:
    - A

    - B
~~~

これは A と B の二つの block value であり、第三の空 valueではない。
この blank line は value にならない。

~~~text
\foo:
    - short arg
      continuation
    - another arg
~~~

同じ物理行の payload はその block value の最初の行になる。続く行は次の
同じ階層の '-' まで、その entry より深い indentation にあれば同じ
block value の continuation として通常の TeXFlux block parser で parse
する。payload が空の '-' でも、後続の深い行が block value になる。

~~~text
\foo:
    - short arg
      very long
      multiline
      argument
    - another arg
~~~

~~~tex
\foo{
short arg
very long
multiline
argument
}{
another arg
}
~~~

「- |」は block marker ではない。「|」は payload の文字として扱われるため、
multiline value には bare の「-」を使う。

sequence entry の block 内には raw line、または prefix-bearing structural
expression を置ける。

~~~text
\foo:
    - @center: |
        BODY
    - @{\small}: |
        SMALL BODY
    - @center >> \includegraphics{fig.pdf}
~~~

sequence entry の structural expression が作る value は、一つの value と
して command/container に渡る。

## 6. Block suite

: | は suite 全体を exactly one block value にする。

~~~text
\foo: |
    A
    B
    @center: |
        C
~~~

~~~tex
\foo{
A
B
\begin{center}
C
\end{center}
}
~~~

block 内部の raw line、nested container、special、stack は通常どおり parse
する。block 内部の blank line は保持する。

~~~text
\foo: |
    A

    B
~~~

~~~tex
\foo{
A

B
}
~~~

: | の suite は空でもよい。たとえば空の transparent valueは次の通り。

~~~text
@: |
~~~

空 block は出力を生成しない。

## 7. Command

structured command は top-level colon または stack segment として現れる
prefix-bearing commandである。header の compact groups は suite 由来の
value より先に出力する。

sequence mode では受け取ったすべての values を required arguments として
消費する。

~~~text
\foo{COMPACT}:
    - A
    - B
~~~

~~~tex
\foo{COMPACT}{
A
}{
B
}
~~~

block mode では block 全体を一つの long required argument とする。

~~~text
\foo: |
    A
    B
~~~

~~~tex
\foo{
A
B
}
~~~

sequence の structural value は一つの required argument に包む。したがって
literal brace container は argument の outer braces に吸収されない。

~~~text
\foo:
    - @{}: |
        A
        B
~~~

~~~tex
\foo{
{
A
B
}
}
~~~

suite/stack syntax を使わない通常の command line は raw TeX のままである。

~~~text
\foo{A}{B}
~~~

構造化された sequence entry や stack terminal に置かれた command は閉じた
canonical valueとして扱う。

## 8. Named environment

named environment は begin/end を生成する。unknown name と star 付き name
も構造が正しければ受理する。

block mode では block value が body であり、compact groups は begin 側の
arguments になる。

~~~text
@frame{Title}: |
    Hello
    @center: |
        World
~~~

~~~tex
\begin{frame}{Title}
Hello
\begin{center}
World
\end{center}
\end{frame}
~~~

sequence mode では最後の value が body、最後以外の values が required
block argumentsになる。これは registry や template knowledgeなしに構文だけ
で決定する。

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY1
        BODY2
~~~

~~~tex
\begin{myenv}{
ARG1
}{
ARG2
}
BODY1
BODY2
\end{myenv}
~~~

最後の block valueの canonical nodesが bodyになる。

値がない sequence environmentは validation errorである。空 bodyが必要な
場合は empty blockを明示する。

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
~~~

## 9. Anonymous containers

### 9.1 Transparent container

@: | は wrapper を出力せず、block valueの nodesをそのまま出力する。

~~~text
@: |
    A
    B
~~~

~~~tex
A
B
~~~

@: の sequence form は複数 valuesを一つの transparent compositeにまとめる。
空の sequenceも許可され、その場合は何も出力しない。literal brace
containerの空 sequenceは空の TeX brace groupを出力する。multiline の空値を
明示する場合は `@: |` または `@{}: |` を使う。

~~~text
@:
    - A
    - B
~~~

command argumentの中で使えば、AとBを一つの block valueとしてまとめる。

### 9.2 Literal brace container

@{RAW_TEX}: | は常に実際の TeX brace groupを出力する。RAW_TEX は
leading raw TeXとして groupの先頭に置く。

~~~text
@{\small\color{red}}: |
    BODY
~~~

~~~tex
{
\small\color{red}
BODY
}
~~~

literal groupの bracesは、command argument、environment body、stackの
どの文脈でも削除・吸収しない。

## 10. Stack composition

>> は pure structural compositionである。scannerはsegment列を保持し、
normalizationが右から左へnested value treeに変換する。rendererは>>を
知らない。

### 10.1 Closed stack

suffixなしの右端がすでに閉じた valueなら、stackはvalidである。

~~~text
@center >> \includegraphics{fig.pdf}
~~~

~~~tex
\begin{center}
\includegraphics{fig.pdf}
\end{center}
~~~

多段 compositionも同じである。

~~~text
@frame{Title} >> @center >> @{\small} >> \input{fig.tex}
~~~

右端の @name、@、@{...} のような open containerに payloadがない stackは
invalidである。

~~~text
@foo >> @center
~~~

### 10.2 Open stack

suffixは右端 segmentに与える。

~~~text
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

これは右端の literal groupが block valueを受け取り、完成したvalueを center、
frameへ順に渡す。

plain colonも右端へ sequence valuesを与える。

~~~text
\outer >> \inner:
    - A
    - B
~~~

まず innerが A/Bを argumentsとして消費し、その完成した command valueが
outerの一つの block valueになる。

stack segmentに固有の terminal ruleはない。suffixなし stackの最右端だけが
closed valueである必要がある。

## 11. Specials

specialは ! で始まり、registryから AST-to-AST handlerを解決する。
unknown specialは DirectiveErrorである。handlerは TeX stringを返さず、
canonical AST nodeのtupleを返す。

### 11.1 items

!itemsは generic sequenceの itemize transformationである。canonical formは
plain colonである。

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

item markerの直後には overlay、optional label、本文を置ける。

~~~text
!items:
    -<2->[A] first line
      continuation
        - nested item
~~~

continuation、nested list、overlay、labelのtextはraw TeXとして保持する。
multiline itemも bare の '-' と深い indentationで書き、次の同じ階層の
'-' までを一つの item value とする。value boundary は generic sequence
と共通だが、continuation、overlay、label、nested list の indentation は
item mini-grammar に従う。

~~~text
!items:
    -
        long first line
        continuation
    - short item
~~~

### 11.2 vpad

!vpadは一つまたは二つの required inline groupと block suiteを受け取る。
canonical suffixは : | である。

~~~text
!vpad{-1em}{2em}: |
    contents
~~~

~~~tex
\vspace{-1em}
contents
\vspace{2em}
~~~

groupが一つなら leading spacingだけを生成する。sequence suffix、groupの
種類違い、group数0/3以上、suiteなしは validation errorである。
stack segmentとしても同じ contractを使う。

## 12. Source macro

source macroは文字列macroではなく、**Value / container / `>>` composition の
上に載る structural AST macro**である。templateは syntax ASTとして保存され、
callのvalueをparameterにbindしてcloneする。source textの置換、rendered TeXの
再parse、raw TeX group内部へのinterpolationはいずれも行わない。

### 12.1 !defmacro

~~~text
!defmacro{name}{param}{...rest}: |
    TEMPLATE
~~~

定義は `: |` block suiteだけを受け取る。plain `:` は validation errorである。
最初の required groupがmacro名で、`!name` として呼べるよう special-name
grammarに従う。以降の required groupがparameterである。定義自体はTeXを
出力しない。定義の前後にある blank lineは通常のcontentとして残る。

定義は**top-levelのみ**である。他のmacro templateの内部を含め、それ以外の
場所の `!defmacro` は validation errorであり、dynamic definitionはできない。
全定義をcollectしてからcallを展開するので、forward referenceが使える。

~~~text
!foo >> \TextCA{A}

!defmacro{foo}{body}: |
    @{\small} >> !param{body}
~~~

parameter名は `[A-Za-z_][A-Za-z0-9_-]*` である。parameterの重複、予約名
(`defmacro`、`param`、`each`)、built-in specialと同名、macroの二重定義は
すべて definition siteを指す validation errorである。

### 12.2 value binding

macro callは既存の value syntaxをそのまま使う。macro専用の呼び出し構文は
存在しない。bindは左から順で、required compact groupが先、suiteが作る
valueが後である。

~~~text
!foo{A}{B}          inline value 2個
!foo: |             block value 1個
!foo:               '-' ごとに block value 1個
!foo >> VALUE       closed stack payloadを value 1個として
~~~

したがって `!foo{A} >> \bar{B}` は `[A, \bar{B}]` をbindする。optional /
overlay groupは valueではなく、macro callでは拒否する。

通常のparameterは exactly one value、末尾の `{...rest}` parameterは残りの
valueすべてを sequenceとして受け取り、0個でもよい。rest parameterは
optional・unique・最後である。arity不一致は macro名・期待する形・実際の
value数・call-site spanを含む macro errorである。

### 12.3 !param

`!param{name}` は template内でbindされたvalueのASTを差し込む。required group
はちょうど1個、suiteは取らない。template外での使用、未bindのparameter、
rest parameterの指定はいずれも macro errorである。rest parameterはvalueでは
なくsequenceなので、`!each` だけが到達できる。

group内部はopaqueなraw TeXなので、parameterをgroupへinterpolateはできない。
同じopacityが `!items` の raw suiteにも及ぶので、item本文に書いた `!param`
はそのまま literal textになる。代わりに structural formを使う。

~~~text
@infobox:
    - !param{title}
    - !param{body}
~~~

### 12.4 !each

~~~text
!each{rest-param}{item}: |
    TEMPLATE
~~~

`!each` は rest parameterのvalueを source order で走査し、各回を `item` に
bindしてtemplateを展開し、結果を外側のblockへ順に連結する。sequenceが空なら
何も生成しない。`: |` suiteが必要で、template内だけで有効、第一groupは rest
parameterでなければならない。bind済みparameterを隠す item名は macro errorで
ある。`!each` はnestできる。

`!each` は、stackのsuffixが `: |` であれば最右segmentにできる。最右segmentは
そのsuffixを受け取るので、`: |` を自分で書いていることになるからである。

~~~text
@{\bfseries} >> !each{items}{item}: |
    \item
    !param{item}
~~~

それ以外の位置ではsuiteがsyntheticになるため validation errorである。
`!defmacro` は stack segmentにできない。合成すると左のsegmentの下にnestされ、
definitionは top-level statementでなくなるからである。

### 12.5 展開

macro callは常に**一つのvalue**（展開されたtemplate block）である。よって
`>>` chainの中に置ける。

~~~text
@center >> !smallred >> \TextCA{Important}
~~~

template内から別のmacroを呼べる。recursionは禁止で、直接・間接どちらの
循環も明示的に検出し、chainを含む macro errorにする。

~~~text
recursive macro expansion detected: foo -> bar -> foo
~~~

展開は `>>` desugaringの後、value consumptionの前に走る AST-to-AST passで
ある。macro構文はnormalizationにもrendererにも漏れない。`!splice` は v1に
含まない。

### 12.6 source mapping

`!param` 由来の出力は call-siteで渡されたvalueのspanを保持する。template
由来の scaffoldingは definition siteではなく **macro call site** へretarget
される。inverse searchは、ユーザーが書いた本文へはその本文へ、macroが生成
した枠へはmacro呼び出し行へ戻る。

## 13. Blank line、raw TeX、コメント

sequence modeのentry間 blank lineはseparatorで、valueにしない。各 sequence
value block と block mode の内部blank lineはcontentである。

TeX group内部の :、: |、>>、pipeはraw textである。item本文・continuation
もraw textである。構造位置の @@ 以外のraw TeXは、TeXFluxの構文として
再解釈しない。

structural header末尾の TeX commentは未定義ではなく明確に禁止する。通常の
raw TeX lineにある percentはそのまま出力する。

## 14. Syntax AST と canonical AST

parserはsyntax shapeとsource spanを保持する。代表的なsyntax nodeは次の通り。

- RawTex
- ParsedInvocation（command、named environment、brace、transparent）
- SpecialInvocation
- Stack
- SequenceEntry
- Block

suite modeは Sequence または Block を保持する。suffixなし Stackの suiteは
存在しない。SequenceEntryは - marker spanとvalue spanを保持する。

normalizationのpipelineは次の通り。

~~~text
physical lines
  -> syntax AST
  -> pure >> desugaring
  -> value consumption / special expansion
  -> canonical AST
  -> renderer
~~~

canonical ASTは次だけである。

- RawTex
- GenericInvocation
- Argument
- Block
- BraceGroup
- Item

normalization後に ParsedInvocation、SpecialInvocation、Stack、SequenceEntryが
rendererへ届いてはならない。

GenericInvocationの body=Noneはcommand、body=Blockはnamed environmentで
ある。BraceGroupは必ずliteral TeX bracesを生成する。Transparent containerは
wrapper nodeを生成しない。

## 15. SourceSpan、source map、SyncTeX

major syntax node、canonical node、diagnosticはfile、1-based line、1-based
columnを持つ。spanはhalf-openである。少なくとも次の provenanceを保持する。

- suite colonとblock pipe
- sequenceの各 -
- sequence value の block boundary
- @{...} header
- @: header
- stack segment
- closed stack terminal
- sequence/block value boundary
- item metadata
- generated begin/end、argument braces、literal braces、special expansion

rendererはMappedEmitterを通じてgenerated rangeとsource spanを対応づける。
source mapは生成TeXと元の.tfx sourceのhashを検証する。SyncTeX remapはこの
source mapを使ってgenerated TeXのInput tagを元sourceへ戻す。

今回のsyntax変更でも、source map/SyncTeX bridgeを削除・簡略化してはならない。

## 16. Diagnostics と migration

次は新v1ではサポートしない。

- suffixなしの単独 named environment/container
- plain colon suiteの unmarked child
- closed stackの open terminal
- unknown special
- structural headerの trailing token/comment
- suite後の |- やその他 block scalar variant

旧仕様の !block、!arg、!bodyはcompatibility aliasではない。現行v1では
unknown/deprecated specialとしてfailする。

旧仕様の次の形は使わない。

~~~text
\foo:
    A
    B
~~~

multiline一つなら \foo: | を使う。複数の block values は \foo: の下で
各 '-' を一つずつ書く。各 '-' の後ろに続く深い行も同じ value に属する。

旧environmentのbody-only表記も @frame{Title}: | に移行する。利用者は
まずsuffixを選び、次に - で複数valueを明示する。

## 17. Conceptual grammar

これはTeX本文のgrammarではなく、TeXFlux structural syntaxの意味を示す
conceptual grammarである。

~~~text
document          ::= statement*

suite-suffix      ::= ":" SP* ("|")?

sequence-suite    ::= sequence-entry*
sequence-entry   ::= "-" SP* sequence-block
sequence-block    ::= first-line continuation-line*
first-line        ::= inline-value | structural-expression
continuation-line ::= indented TeXFlux line

structural-expression
                  ::= segment (SP+ ">>" SP+ segment)* suite-suffix?

segment           ::= command-segment
                    | named-container-segment
                    | brace-container-segment
                    | transparent-container-segment
                    | special-segment

command-segment  ::= "\\" command-name group*
named-container-segment
                  ::= "@" environment-name group*
brace-container-segment
                  ::= "@{" balanced-raw-tex "}"
transparent-container-segment
                  ::= "@"
special-segment  ::= "!" special-name group*
~~~

実装上のparserは、item metadata、balanced group、source span、raw lineの
indentationを別途保持する。sequence-blockは次の同じ階層の '-' が現れる
まで続く。grammarの要点は、plain colonが「- ごとに一つの block value」、
colon-pipeがone block、suffixなしがclosed valueであることだ。

## 18. Acceptance examples

次の出力はv1のgolden behaviorである。

~~~text
\foo:
    - A
    - B
~~~

~~~tex
\foo{
A
}{
B
}
~~~

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY1
        BODY2
~~~

~~~tex
\begin{myenv}{
ARG1
}{
ARG2
}
BODY1
BODY2
\end{myenv}
~~~

~~~text
@center >> \includegraphics{fig.pdf}
~~~

~~~tex
\begin{center}
\includegraphics{fig.pdf}
\end{center}
~~~

~~~text
\foo: |
    A

    B
~~~

~~~tex
\foo{
A

B
}
~~~
