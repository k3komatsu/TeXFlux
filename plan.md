# TeXFlux v1 実装計画

この計画は texflux_tex_first_dsl_v1_spec.md の実装計画である。
prefix の責務と structured command の long argument を一つの決定的な
pipelineに固定する。

## 1. ゴールと非ゴール

### ゴール

- TeX-first raw line preservation
- 字句的な三分離: backslash command、at environment、exclamation special
- top-level structural header scanner
- 四 space indentation block parser
- environment begin/end emission
- command suite 全体を一個の required long argument とする構文
- 複数 long argument の explicit !arg
- literal brace group を生成する !block
- suite の前後に `\vspace` を挿入する !vpad
- explicit environment argument/body の !arg / !body
- prefix付き stack の pure desugaring
- overlay、label、multiline、nested list を持つ items
- source location の保持
- canonical AST だけを renderer へ渡す
- future AST-to-AST special extension の handler 契約

### 非ゴール

- TeX 全文 parser または template parser
- command/environment discovery
- command registry による形状推論
- TeX argument count/package validation
- 自動 escape または TeX semantic normalization
- variables、expressions、loops、conditions、macro language
- YAML/Python embedded DSL
- runtime dependency
- user-loaded plugin framework
- id/ref reuse、renderer backend、SyncTeX 本体
- suite marker後の block-scalar variant

## 2. 固定する source model

~~~text
\foo    = TeX command
@foo    = TeX environment
!foo    = TeXFlux special
~~~

ordinary command line は raw TeX である。先頭が backslash の行は、
top-level trailing colon または >> が構造候補として見える場合だけ
header scanner に渡す。at header は suite 必須、exclamation header は
special contract に従う。

top-level trailing colon は予約する。group 内部の colon と >> は raw
group content として扱う。environment name は registry なしで scanし、
star や必要な punctuation を許す。

depth 0 の colon を segment の後ろで認識した場合は TeXFlux syntax
として予約し、末尾の non-space token でなければ error にする。segment
と colon の間の空白は許容する。これにより `\textbf{注意}: 本文` は
raw TeX へ戻らない。

## 3. Pipeline

~~~text
physical lines
    -> indentation parser
    -> syntax AST
    -> pure >> desugaring
    -> invocation/special normalization
    -> canonical AST validation
    -> TeX renderer
~~~

parser は raw TeX を TeX AST へ変換しない。normalizer は special を
ASTからASTへ展開する。renderer は special、stack、syntax mode を参照
しない。

## 4. AST 設計

### 4.1 syntax AST

- RawTex(text, loc)
- ParsedInvocation(kind, name, groups, suite, loc)
- SpecialInvocation(name, groups, suite, loc)
- Stack(segments, suite, loc)
- Block(nodes, loc)
- Argument(kind, value, layout, loc)

ParsedInvocation.kind は COMMAND または ENVIRONMENT であり、prefix の
結果を保持する。suite scalar用の別metadataは持たない。

Stack の各 segment は prefix付きの ParsedInvocation または
SpecialInvocation である。

### 4.2 canonical AST

- RawTex
- GenericInvocation(name, arguments, body, loc)
- BraceGroup(body, loc)
- Item
- Argument
- Block

GenericInvocation.body が None なら command、Block なら environment
である。explicit environment で !body が省略された場合は空Blockを
置き、environment であることを保持する。

BraceGroup は renderer が実際の TeX braces を出力する canonical node
である。Argument.value は inline raw string または canonical Block と
し、BraceGroup を暗黙 argument として吸収する表現は持たない。

全 major node に file、1-based line、1-based column を保持する。

## 5. Parser 実装

### Phase 1: physical lines

- CRLF/CR を LF へ正規化
- 末尾 newline を一つ保持
- tab を reject
- blank line を Block 内へ保持
- suite base からの structural indentation を除去
- raw line の追加 indentation を保持
- suite 内の末尾 blank line は root block へ戻して保持

### Phase 2: header scanner

handwritten scanner で次を行う。

- prefix を一文字で確定
- name と compact groups を読む
- required/optional/overlay group の delimiter depthだけを追跡
- top-level trailing colon と空白で区切った >> だけを認識
- >> は前後両方に空白がある場合だけ構造 token として認識
- group 内容を opaque に保つ
- environment name に star/punctuation を許す
- colon 後の trailing token と header comment を reject

backslash raw line は scanner error が出ても、top-level structural token
を見る前なら raw line へ戻す。ただし depth 0 の trailing colon で終わる
行は有効な segment を scanできなくても構造候補として ParseError にする。
structural token を見た後の error も ParseError として返す。これにより
verb、includegraphics 等の通常の raw TeX を不必要に parse しない。

構造構文候補の backslash 行を suite base より深く置く場合は invalid
structural indentation とする。候補でない raw TeX 行の追加 indentation は
保持する。

### Phase 3: directive construction

- at/exclamation line は構造 header として扱う
- at header の suiteなしを reject
- command の colon suite を ParsedInvocation へ保持
- stack の各 segment に prefix を要求
- suite がなければ stack を reject
- suite marker には nonblank indented child を要求
- at escape を raw at として処理
- !items の suite は構造 node を作らず raw lines として item parser に渡す

colon 以下の suite は通常の Block parser へ渡す。suite内の
TeXFlux syntax は command、environment、special を問わず使用できる。

## 6. Normalization 実装

### Phase 4: pure stack

Stack は右から左へ処理し、各 segment へ一個だけ nested suite を与える。

~~~text
A >> B >> C:
    X
~~~

を次の syntax shape へ変換してから通常 normalize する。

~~~text
A:
    B:
        C:
            X
~~~

この phase は segment の command/environment/special 意味論を知らない。
stack terminal や final special による特別扱いは導入しない。

### Phase 5: command

1. compact groups を header arguments へ変換する。
2. direct child に !arg または !body があるか調べる。
3. explicit mode なら direct child を !arg だけに限定し、各 !argを
   一個の required argument にする。
4. implicit mode なら suite 全体を normalize して一個の required block
   argument にする。
5. implicit mode の suite 内に !block があれば BraceGroup として保持し、
   command argument の outer braceも出力する。
6. command の !body と ordinary/other structural child の混在を reject
   する。

command に suite scalar metadataや複数の暗黙 argument boundaryを追加しない。

### Phase 6: environment

1. ordinary suite なら normalize した全体を body にする。
2. direct child に !arg または !body があれば explicit mode にする。
3. explicit mode の direct child は !arg / !body だけに限定する。
4. !arg は !body より前に置く。
5. !body は 0 または 1 個で、存在すれば最後に置く。
6. body がないときは空 Block を canonical body にする。
7. ordinary body との混在は reject する。

### Phase 7: specials

内部 registry は name から AST-to-AST handler を解決する。

- block: suite を BraceGroup 一個に包み、canonical boundaryで normalize
- items: raw item mini-grammar を Item/itemize AST へ変換
- vpad: required inline groupsからcanonicalなvspace commandとnormalized
  suiteのsequenceへ展開
- !items の item text/continuation にある @、\\、! は raw text として保持
- arg/body: 親の structured normalizer だけが消費
- unknown special: DirectiveError

handler は TeX string を返さない。handler の結果と、その中の
body/arguments は canonical boundary で再帰的に normalize する。

## 7. Renderer 実装

renderer は canonical AST だけを受け取る。

- command: backslash、name、arguments
- environment: begin、name、arguments、body、end
- BraceGroup: braces と body
- block argument: outer braces と body
- BraceGroup が block argument の body 内にあれば inner bracesも出力
- Item: item marker、overlay、label、raw text、continuation
- RawTex: text をそのまま

Argument value に BraceGroup を使って outer brace を省略する処理は実装
しない。!block の braceは常にそのまま renderer へ届く。

## 8. Diagnostics / source mapping

ParseError、ValidationError、DirectiveError は発生源の location を指す。
最低限、次を保持する。

- header prefix と name
- compact group
- suite marker
- stack segment
- command suite の long-argument boundary
- !block
- explicit !arg / !body
- item と item prefix

>> desugaring、command suite normalization、!block、!arg、!body、!items
、!vpad の展開で originating location を破棄しない。

## 9. Test plan

### Parser unit

- backslash raw command、at environment、exclamation special の分類
- suiteなし at header error
- top-level trailing colon reservation
- colon 後の trailing token rejection
- group内 colon/>> の opaque 性
- raw verb 相当の非構造 command
- required/optional/overlay group scanning
- starred environment
- prefix付き stack
- missing prefix、missing suite、indent、source location

### Normalization / renderer unit

- command suite 全体が一個の long argument
- compact argument と suite argument の順序
- suite 内の複数 statement が同一 argument
- suite 内 nested TeXFlux node
- !block standalone
- command implicit suite 内の !block が double brace
- explicit !arg の複数 long argument
- explicit !arg が !block を含む double brace
- command explicit mode と mixing error
- environment ordinary body
- environment explicit mode、empty body、ordering error
- pure stack の command/environment/special 組合せ
- unknown environment と unknown special
- items overlay/label/multiline/nested
- !vpad の before/after と stack segment
- custom handler の AST-to-AST contract

### Golden

代表 golden は次を含む。

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

exact output で command suite の一引数化、itemize、rightnotebox、
align* の nesting と source order を確認する。

## 10. Verification order

各 phase で focused unittest を実行し、その後に次を実行する。

~~~bash
PYTHONPATH=src python3 -m unittest discover -v
git diff --check
~~~

LaTeX integration test は pdflatex が存在するときだけ実行し、なければ
skip する。runtime で LaTeX を要求しない。

## 11. Self-review

完了時に次を確認する。

- prefix だけで shape が一意に決まる
- raw TeX を必要以上に scan しない
- top-level token だけを structure とする
- command suite が必ず一個の implicit long argumentになる
- explicit !arg が複数 long argument を表す
- command/environment implicit と explicitを混在させない
- suite 後の block-scalar variant を受理しない
- !block の braceを context で消さない
- !vpad の一個/二個の spacing group と suite を一意に処理する
- stack に semantic terminal rule を入れない
- canonical AST に syntax-only node を残さない
- source location を全変換で保つ
- future AST-to-AST special と SyncTeX bridge を阻害しない
