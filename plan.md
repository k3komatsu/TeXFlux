# TeXFlux v1 実装計画

この計画は texflux_tex_first_dsl_v1_spec.md と doc/dsl.md に従う。v1 の
中心は、suite が値を作り、command/container/special が値を消費する
小さな pipeline である。

## 1. 固定する意味論

~~~text
\       command
@       structural container
!       TeXFlux special

suffixなし  closed value
:          sequence, one block value per '-'
: |        one block value
-          starts one block value; the next sibling '-' ends it
>>         right-to-left value composition
~~~

plain colonのdirect childは必ずsequence marker - で始まる。各 - が一つの
block valueを開始し、次の同じ階層の - までがそのvalueである。colon-pipe
の内部は通常のTeXFlux block parserでparseする。commandは全sequence
valuesをrequired block argumentsとして消費し、named environmentは最後以外を
arguments、最後をbodyとして消費する。

@nameはnamed environment、@はtransparent、@{RAW_TEX}はliteral brace
containerである。literal bracesはcanonical ASTに残す。!itemsはsequence
consumer、!vpadはblock consumerである。旧specialは実装しない。

## 2. Pipeline

~~~text
physical lines
    -> indentation parser / header scanner
    -> syntax AST (suite mode, entries, stacks, spans)
    -> pure >> desugaring
    -> value consumption and AST-to-AST specials
    -> canonical AST validation
    -> TeX renderer / source provenance
~~~

rendererはcanonical ASTだけを受け取る。syntax-only node、special name、
suite mode、sequence marker、>>をrendererへ漏らさない。

## 3. Syntax AST

既存の軽量ASTを再利用する。

- RawTex
- ParsedInvocation
- SpecialInvocation
- Stack
- SequenceEntry
- Block

ParsedInvocationのkindは command、named environment、brace、transparent
をprefixから決定する。ParsedInvocation/SpecialInvocation/Stackは
suite_mode（Sequence/Block/None）とsuite spanを保持する。SequenceEntryは
- marker spanとvalue spanを保持する。

canonical ASTは次だけである。

- RawTex
- GenericInvocation
- Argument
- Block
- BraceGroup
- Item

GenericInvocationのbody=Noneがcommand、body=Blockがnamed environment。
BraceGroupはliteral bracesを必ずrenderする。

## 4. Parser phases

1. CRLF/CRをLFに正規化し、tabをrejectする。
2. 四space indentationのphysical blockを作る。
3. balanced TeX groupをopaqueにscanする。
4. depth-zeroのcolon、optional pipe、space-separated >>だけを構造tokenにする。
5. colon suiteはSequenceEntry列、colon-pipe suiteは通常Blockにする。
6. 各 - のblock valueとsequence entry内のstructural expressionを再帰parseする。
7. @{...}、@:、@nameをこの順でclassifyする。
8. !itemsのitem metadataはgeneric sequenceのboundaryを共有しつつraw
   item mini-grammarとしてparseする。
9. suffixなしstackも構文ASTとして受理し、normalizationでterminal valueを
   validationする。

suite間のblankはseparator、sequence value block内とblock suite内のblankは
contentとして扱う。
raw TeXはopaqueで、structural candidate以外は変更しない。

## 5. Normalization phases

### 5.1 Stack

Stackは右端から処理する。suffixは右端segmentへ渡し、左segmentには
synthetic block suiteを一つ与える。closed stackの右端はclosed command
valueなどでなければならず、open containerのterminalはvalidation error。

### 5.2 Commands

- header compact groupsを先にnormalizeする。
- Sequence suiteなら各entryをrequired Argumentに変換する。
- Block suiteならnormalized Blockを一つのlong Argumentにする。
- structural entryは一つのBlock valueに包む。
- literal BraceGroupのinner bracesを決して吸収しない。

### 5.3 Environments

- Block suiteならnormalized Blockをbodyにする。
- Sequence suiteならlast block entryをbody、それ以前をrequired Argumentsにする。
- sequence entryはすべてblock valueであり、そのcanonical nodesをbodyにする。
- zero-value sequenceはvalidation errorにする。

### 5.4 Specials

DirectiveRegistryはin-processの小さいAST-to-AST registryに限定する。
unknown specialはDirectiveError。

- items: Sequence suiteからItem/itemizeを生成する。
- vpad: one/two required inline groupとBlock suiteからvspace/body/vspace
  のcanonical sequenceを生成する。

handlerはTeX stringを返さない。旧specialの登録やaliasは作らない。

## 6. Source mapping / SyncTeX

SourceSpanはfile、1-based line/column、half-open rangeを持つ。次をsource
provenanceとして保持する。

- suite colon/pipe
- sequence markerと各valueのblock boundary
- @{...} と @:
- stack segmentsとclosed terminal
- sequence/block value boundary
- item overlay/label/marker
- generated begin/end、argument braces、literal braces、special expansion

MappedEmitter、source-map serialization、SyncTeX remapの既存bridgeは削除
しない。新しいcanonical Argument/BraceGroupのsource spanを使い、early
stringificationでspanを失わない。

## 7. Verification

実装順序は次の通り。

1. affected specと既存testsを読む。
2. scanner/parserのsuite modeとsequence entryテストを追加する。
3. command/environment/container/stack normalizationのgoldenを追加する。
4. items/vpadとnegative testsを追加する。
5. source-map/SyncTeXの代表例を新syntaxへ移行する。
6. examples、README、doc/dsl.mdを更新する。
7. focused unittestを実行する。
8. PYTHONPATH=src python3 -m unittest discoverを実行する。
9. git diff --checkを実行する。

LaTeX integration testはtoolchainがなければskipする。runtime dependency、
TeX全文parser、registryによる形状推論、renderer backend frameworkは追加
しない。
