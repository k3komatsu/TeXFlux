# TeXFlux DSL v1 言語仕様

## 1. ステータスと設計目標

本書は、TeXFlux v1 の利用者向け言語仕様書である。言語の規範的な定義（Normative Specification）は
`texflux_tex_first_dsl_v1_spec.md` に記載されており、実装、README、各種サンプル、テスト、
そして本書の記述もすべてその定義に準拠しなければならない。

TeXFlux は、「TeX-first」を掲げるインデントベースのプリプロセッサである。TeX 本文そのものを
解析・エスケープ・正規化することはせず、主に文書構造に関する以下の要素のみを簡潔に記述できるようにする。

- TeX 環境（environment）の `\begin{...}` / `\end{...}`
- 構造化コマンド（command）の必須引数（required argument）
- リテラルな TeX の中括弧グループ（`{ ... }`）
- `itemize` 環境などの定型的なリスト構造
- 値の入れ子関係を表現する合成（composition）

v1 の中心的な設計モデルは、「**suite（インデントブロック）が値を生成し、container / command / special がその値を消費する**」というものである。suite の形式は以下の3種類のみである。

~~~text
suffix なし  = 右辺（RHS）ですでに完結している単一の値
:           = '-' ごとに1つのブロック値を生成するシーケンス（sequence）
: |         = suite 全体を1つの複数行ブロック値とするブロック（block）
~~~

## 2. プレフィックス（Prefix）

行頭やセグメント先頭に付くプレフィックス（接頭辞）の意味は字句的に決定され、TeX パッケージやテンプレート、コマンド登録情報（レジストリ）、あるいは既知のコマンド引数の数などには一切依存しない。

~~~text
\   TeX コマンド（command）
@   構造コンテナ（structural container）
!   TeXFlux の特殊変換（special transformation）
~~~

`@` は名前付き TeX 環境（`\begin{...}`〜`\end{...}`）専用ではない。v1 で利用可能な `@` コンテナは以下の通りである。

~~~text
@name             名前付き TeX 環境コンテナ（named TeX environment container）
@{}               無名のリテラル中括弧コンテナ（anonymous literal TeX brace container）
@{RAW_TEX}        先頭に生の TeX コードを含むリテラル中括弧コンテナ
@                 無名の透過コンテナ（anonymous transparent container）
@:                透過コンテナの suite 形式
~~~

通常の TeX コマンド行は、そのまま生の TeX（raw TeX）として扱われる。ただし、トップレベルに構造化 suffix（末尾の `:` や `: |`）またはスタック区切り文字（`>>`）を含むコマンド行は、構造化構文の候補として解釈される。
未知の名前を持つ環境（unknown named environment）であっても構文としてそのまま受け付けられ、LaTeX 側での解決に委ねられる。一方、未知の special（unknown special）はエラーとなる。

## 3. 物理行とインデント

入力テキストの改行コード（CRLF / CR）はすべて LF に正規化される。タブ文字（tab）の使用は禁止されている。出力末尾には必ず改行が1つ付加される。

構造を表すインデントは、半角スペース4個（ASCII space 4文字）単位である。suite の直下の子要素（direct child）は、その階層の基準インデント位置（suite base）に配置しなければならない。生の TeX コード（raw TeX）行における余分なインデントはそのまま保持されるが、不適切な追加インデント位置に構造化構文の候補が置かれている場合は構文エラーとなる。空行（blank line）の扱いは suite のモードによって異なる。

構造化構文と認識される位置で、文字どおりの `@` から始まる生の行を出力したい場合は、`@@` と記述してエスケープできる。

~~~text
@@literal-at
~~~

展開後:
~~~tex
@literal-at
~~~

## 4. ヘッダースキャナ（Header scanner）

ヘッダースキャナは、グループ内部を TeX コードとして解釈・展開することはない。必須グループ、オプション引数グループ、Beamer のオーバーレイ指定グループについて、括弧の対応（バランス）を保ちながら機械的に走査する。

~~~text
{...}   必須グループ（required group）
[...]   オプション引数グループ（optional group）
<...>   オーバーレイグループ（overlay group）
~~~

各グループ内部に含まれるコロン（`:`）、パイプ（`|`）、二重山括弧（`>>`）は、常に解釈されない不透明な生テキスト（opaque raw text）として扱われる。トップレベル行の末尾にあるコロンと、前後を空白で区切られた `>>` だけが構造化トークン（structural token）として認識される。行末以外にコロンを含む通常のコマンド行は、そのまま生の TeX として扱われる。なお、ヘッダー末尾に置く TeX 形式のコメント（`% ...`）は v1 ではサポートされない。

suite を開始する suffix（接尾辞）としては、以下の形式のみを受け付ける。

~~~text
:
:|
: |
~~~

コロンの後に空白（省略可）を挟んでパイプ `|` が続く場合は**ブロックモード（block mode）**となり、パイプがなければ**シーケンスモード（sequence mode）**となる。これら以外のトークンが末尾に現れた場合はパースエラーとなる。パイプ `|` が特別扱いされるのは suite suffix の直後だけであり、生の TeX 行に含まれるパイプ文字はそのまま保持される。

`@` から始まるコンテナの字句分類は、以下の優先順位で行われる。

~~~text
@{...}  リテラル中括弧コンテナ（literal brace container）
@       透過コンテナ（transparent container）
@name   名前付き環境コンテナ（named environment container）
~~~

`@{...}` のグループはヘッダーに含まれる生の TeX であり、「名前が空の環境」として扱ってはならない。対応する括弧で囲まれた中身について、TeXFlux が内容をパースしたり妥当性を検証したりすることはない。

スタック（`>>`）を構成する各セグメントには、必ず完全なプレフィックス（`\`、`@`、`!`）を明示しなければならない。プレフィックスを省略する記法は存在しない。

~~~text
@{} >> @center >> @{\small}
~~~

以下の例はプレフィックスが不足しているため無効（invalid）である。

~~~text
@{} >> center >> {\small}
~~~

## 5. シーケンススイート（Sequence suite）

末尾がコロンのみ（`:`）の行に続く suite は、`-` で明示された値エントリー（value entry）の並び（シーケンス）を表す。構造化コマンドのシーケンススイートには、少なくとも1つのエントリーが必要である。各 `-` が1つのブロック値の開始を示し、同じインデント階層にある次の `-` の直前までがその値に含まれる。したがって値の境界は `-` のみによって決定され、各エントリーは常にブロックレイアウトの値となる。

~~~text
\foo:
    - A
      continuation of A
    - B
~~~

展開後:
~~~tex
\foo{
A
continuation of A
}{
B
}
~~~

エントリー間に挟まれた空行（blank line）は単なる区切りとして無視される。

~~~text
\foo:
    - A

    - B
~~~

上記の例は「A」と「B」という2つのブロック値を表しており、間に第3の空の値が生成されるわけではない。この空行は値の一部にはならない。

~~~text
\foo:
    - short arg
      continuation
    - another arg
~~~

`-` と同じ物理行に書かれたペイロード（文字列）は、そのブロック値の先頭行となる。後続の行がそのエントリーよりも深くインデントされていれば、同じ階層の次の `-` が現れるまで、同一ブロック値の継続行（continuation）として TeXFlux の通常のブロックパーサーにより処理される。`-` の行自体にペイロードが書かれていなくても、後続の深くインデントされた行群がブロック値となる。

~~~text
\foo:
    - short arg
      very long
      multiline
      argument
    - another arg
~~~

展開後:
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

なお、「`- |`」というブロックマーカーは存在しない。「`|`」は単なるペイロードの文字として扱われるため、複数行の値を記述する場合でも記号は `-` のみ（bare の `-`）を使用する。

シーケンスエントリーのブロック内には、生の TeX 行だけでなく、プレフィックスを伴う構造化式（structural expression）を配置することもできる。

~~~text
\foo:
    - @center: |
        BODY
    - @{\small}: |
        SMALL BODY
    - @center >> \includegraphics{fig.pdf}
~~~

シーケンスエントリー内の構造化式によって生成された値は、1つのまとまった値として親のコマンドやコンテナに渡される。

## 6. ブロックスイート（Block suite）

`: |` は、それに続く suite 全体をちょうど1つのブロック値（exactly one block value）として扱う。

~~~text
\foo: |
    A
    B
    @center: |
        C
~~~

展開後:
~~~tex
\foo{
A
B
\begin{center}
C
\end{center}
}
~~~

ブロック内部に含まれる生の行、ネストしたコンテナ、special、スタック構文などは通常どおり解析される。また、ブロック内部の空行は保持される。

~~~text
\foo: |
    A

    B
~~~

展開後:
~~~tex
\foo{
A

B
}
~~~

`: |` に続く suite は空であってもよい。例えば、空の透過値（transparent value）は以下のように記述できる。

~~~text
@: |
~~~

空のブロックは出力を生成しない。

## 7. コマンド（Command）

構造化コマンド（structured command）とは、トップレベル行の末尾にコロンを伴うか、またはスタックセグメント（`>>`）として記述される `\` プレフィックス付きのコマンドである。ヘッダー部分にインラインで記述されたコンパクトグループ（`{...}` や `[...]` など）は、suite から渡される値よりも先に出力される。

シーケンスモードでは、受け取ったすべての値を順に必須引数（`{...}`）として消費・出力する。

~~~text
\foo{COMPACT}:
    - A
    - B
~~~

展開後:
~~~tex
\foo{COMPACT}{
A
}{
B
}
~~~

ブロックモードでは、ブロック全体を1つの複数行必須引数（long required argument）として展開する。

~~~text
\foo: |
    A
    B
~~~

展開後:
~~~tex
\foo{
A
B
}
~~~

シーケンス内の構造化された値は、1つの必須引数グループ（`{...}`）の中に内包される。そのため、リテラル中括弧コンテナ（`@{}`）の中括弧が引数自体の外側の中括弧に吸収されて消えることはない。

~~~text
\foo:
    - @{}: |
        A
        B
~~~

展開後:
~~~tex
\foo{
{
A
B
}
}
~~~

suite やスタック構文（`>>`）を伴わない通常のコマンド行は、そのまま生の TeX（raw TeX）として出力される。

~~~text
\foo{A}{B}
~~~

構造化されたシーケンスエントリーの内部や、スタックの終端（terminal）に置かれたコマンドは、すでに完結した正準な値（canonical value）として扱われる。

## 8. 名前付き環境（Named environment）

名前付き環境（named environment）は、LaTeX の `\begin{...}` と `\end{...}` のペアを生成する。未知の環境名やアスタリスク付きの環境名（例: `@tabular*`）であっても、構文として正しければそのまま受け付けられる。

ブロックモード（`: |`）では、ブロック値全体が環境の本文（body）となり、ヘッダーに記述されたインライングループは `\begin{...}` 側の引数となる。

~~~text
@frame{Title}: |
    Hello
    @center: |
        World
~~~

展開後:
~~~tex
\begin{frame}{Title}
Hello
\begin{center}
World
\end{center}
\end{frame}
~~~

シーケンスモード（`:`）では、**最後の値が環境の本文（body）**となり、**最後以外の値はすべて環境の必須引数（required block arguments）**となる。この割り当ては、レジストリやテンプレートの知識を必要とせず、構文規則のみに基づいて決定される。

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY1
        BODY2
~~~

展開後:
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

シーケンスの最後のブロック値に含まれる正準ノード群が、そのまま環境の本文となる。

エントリー（値）が1つも存在しないシーケンス環境はバリデーションエラーとなる。空の本文が必要な場合は、末尾に空ブロック（`@: |`）を明示的に記述する。

~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
~~~

## 9. 無名コンテナ（Anonymous containers）

### 9.1 透過コンテナ（Transparent container）

`@: |` は外側のラッパー（`\begin` や中括弧など）を一切出力せず、ブロック値に含まれるノードをそのまま出力する。

~~~text
@: |
    A
    B
~~~

展開後:
~~~tex
A
B
~~~

`@:` のシーケンス形式は、複数の値を1つの透過的な合成値（transparent composite）にまとめる。空のシーケンスも許容されており、その場合は何も出力されない。なお、リテラル中括弧コンテナ（`@{}:`）で空シーケンスを指定した場合は、空の TeX 中括弧グループ（`{}`）が出力される。複数行の空の値を明示したい場合は、`@: |` または `@{}: |` を使用する。

~~~text
@:
    - A
    - B
~~~

これをコマンドの引数の中で使用すると、「A」と「B」がまとまって1つのブロック値としてコマンドに渡される。

### 9.2 リテラル中括弧コンテナ（Literal brace container）

`@{RAW_TEX}: |` は、常に実際の TeX 中括弧グループ（`{ ... }`）を出力する。波括弧内の `RAW_TEX` は、先頭の生の TeX コード（leading raw TeX）としてグループ内の先頭に配置される。

~~~text
@{\small\color{red}}: |
    BODY
~~~

展開後:
~~~tex
{
\small\color{red}
BODY
}
~~~

このリテラルグループの中括弧は、コマンドの引数内、環境の本文内、あるいはスタックの途中など、どのような文脈であっても削除されたり外側の中括弧に吸収されたりすることはない。

## 10. スタック合成（Stack composition）

二重山括弧 `>>` は、純粋な構造的合成（pure structural composition）を表す。スキャナがセグメントの並びを保持し、正規化処理（normalization）によって右から左へと入れ子の値ツリー（nested value tree）に変換される。そのため、最終的なレンダラーは `>>` の存在を意識しない。

### 10.1 閉じたスタック（Closed stack）

suffix を持たないスタックにおいて、最右端のセグメントがすでに完結した値（closed value）であれば、そのスタックは有効である。

~~~text
@center >> \includegraphics{fig.pdf}
~~~

展開後:
~~~tex
\begin{center}
\includegraphics{fig.pdf}
\end{center}
~~~

多段の合成も同様に記述できる。

~~~text
@frame{Title} >> @center >> @{\small} >> \input{fig.tex}
~~~

最右端が `@name`、`@`、`@{...}` などのように値を受け取る余地を残したオープンなコンテナ（open container）であるにもかかわらず、その値（payload）が与えられていないスタックは無効（invalid）である。

~~~text
@foo >> @center
~~~

### 10.2 開いたスタック（Open stack）

suite を後続させる場合、suffix（`:` または `: |`）は最右端のセグメントに付与する。

~~~text
@frame{Title} >> @center >> @{\small}: |
    BODY
~~~

この場合、最右端のリテラルグループ（`@{\small}`）が後続のブロック値（`BODY`）を受け取り、それによって完成した値が手前の `@center`、さらに `@frame{Title}` へと順に渡される。

末尾がコロンのみ（`:`）の場合も同様に、最右端のセグメントにシーケンス値群が渡される。

~~~text
\outer >> \inner:
    - A
    - B
~~~

まず `\inner` が「A」と「B」を引数として消費してコマンドの値を完成させ、その完成した値が `\outer` の1つのブロック引数値となる。

スタックセグメントに固有の複雑な終端規則はない。suffix を持たないスタックの最右端だけが完結した値（closed value）である必要がある、というシンプルなルールである。

## 11. 特殊構文（Specials）

Special は `!` で始まり、レジストリに登録された AST-to-AST ハンドラによって処理される。未知の special は `DirectiveError` となる。ハンドラは TeX 文字列を直接返すのではなく、正準 AST ノード（canonical AST node）のタプルを返す。

### 11.1 items

`!items` は、一般的なシーケンスを `itemize` 環境へと変換する構文である。正準な形式（canonical form）はコロンのみの suffix（`:`）である。

~~~text
!items:
    - A
    - B
~~~

展開後:
~~~tex
\begin{itemize}
\item A
\item B
\end{itemize}
~~~

各項目のマーカー（`-`）の直後には、Beamer のオーバーレイ指定（`<...>`）、オプションラベル（`[...]`）、および項目本文を記述できる。

~~~text
!items:
    -<2->[A] first line
      continuation
        - nested item
~~~

継続行、ネストしたリスト、オーバーレイ、ラベルのテキストは、すべて生の TeX として保持される。複数行にわたる項目も、単独の `-` と深いインデントによって記述し、同じ階層の次の `-` が現れるまでを1つの項目の値とする。値の境界決定は通常のシーケンスと共通であるが、継続行、オーバーレイ、ラベル、ネストしたリストのインデント規則は `!items` 独自のミニ文法に従う。

~~~text
!items:
    -
        long first line
        continuation
    - short item
~~~

### 11.2 vpad

`!vpad` は、1つまたは2つの必須インライングループ（`{...}`）とブロックスイートを受け取る。正準な suffix は `: |` である。

~~~text
!vpad{-1em}{2em}: |
    contents
~~~

展開後:
~~~tex
\vspace{-1em}
contents
\vspace{2em}
~~~

グループが1つだけ指定された場合は、前方のスペース（`\vspace{...}`）のみを生成する。シーケンスの suffix（`:`）が使われた場合、グループの括弧の種類が異なる場合、グループ数が0個または3個以上の場合、あるいは後続の suite が存在しない場合は、すべてバリデーションエラーとなる。スタックセグメント（`>>`）として使用する場合もこれと同じ規則が適用される。

## 12. ソースマクロ（Source macro）

ソースマクロは、単なる文字列置換マクロではなく、**値・コンテナ・スタック合成（`>>`）の上に構築された構造的な AST マクロ（structural AST macro）**である。テンプレートは構文 AST として保存され、マクロ呼び出し側で渡された値をパラメータにバインドしてクローン（複製）される。ソーステキストの単純な置換や、レンダリング後の TeX の再パース、生の TeX グループ内部への文字列展開（interpolation）などは一切行われない。

### 12.1 !defmacro

~~~text
!defmacro{name}{param}{...rest}: |
    TEMPLATE
~~~

マクロの定義には `: |` ブロックスイートのみを受け付ける。末尾がコロンのみ（`:`）のシーケンス形式はバリデーションエラーとなる。最初の必須グループがマクロ名であり、後から `!name` として呼び出せるよう special 名の文法規則に従う必要がある。2つ目以降の必須グループがパラメータ名となる。マクロ定義自体は TeX コードを出力しない。定義行の前後に置かれた空行は、通常の文書コンテンツとして保持される。

マクロの定義は**トップレベルのみ**で許可される。他のマクロテンプレートの内部を含め、トップレベル以外の場所に置かれた `!defmacro` はバリデーションエラーとなり、動的な定義は行えない。すべての定義をあらかじめ収集した上でマクロ呼び出しを展開するため、定義より前の位置でマクロを呼び出す前方参照（forward reference）が可能である。

~~~text
!foo >> \TextCA{A}

!defmacro{foo}{body}: |
    @{\small} >> !param{body}
~~~

パラメータ名には `[A-Za-z_][A-Za-z0-9_-]*` の文字パターンが使用できる。パラメータ名の重複、予約名（`defmacro`、`param`、`each`）の使用、組み込みの special と同名の使用、およびマクロ自体の多重定義は、いずれも定義位置（definition site）を指し示すバリデーションエラーとなる。

### 12.2 値のバインド（Value binding）

マクロ呼び出しには、既存の値構文がそのまま適用される。マクロ専用の特別な呼び出し構文は存在しない。値のバインドは左から順に行われ、ヘッダーに記述されたインラインの必須グループが先、suite が生成する値が後になる。

~~~text
!foo{A}{B}          インライン値 2個
!foo: |             ブロック値 1個
!foo:               '-' ごとにブロック値 1個（シーケンス）
!foo >> VALUE       閉じたスタックのペイロードを1個の値として
~~~

したがって、例えば `!foo{A} >> \bar{B}` という記述は `[A, \bar{B}]` という2つの値をバインドする。オプション引数グループ（`[...]`）やオーバーレイグループ（`<...>`）は値ではないため、マクロ呼び出しに付与することはできない（エラーとなる）。

通常のパラメータは**ちょうど1つの値（exactly one value）**を受け取る。末尾に `{...rest}` のように記述した可変長パラメータ（rest parameter）は、残りのすべての値をシーケンスとして受け取り、0個の値であっても許容される。rest parameter は省略可能かつ一意であり、必ずパラメータリストの最後に置かなければならない。引数の個数（arity）が一致しない場合は、マクロ名、期待される引数形式、実際の引数の数、および呼び出し位置のスパンを含むマクロエラーとなる。

### 12.3 !param

`!param{name}` は、テンプレート内でバインドされた値の AST をその位置に挿入する。必須グループをちょうど1個だけ取り、suite は取らない。テンプレート外での使用、未バインドのパラメータ名の指定、および rest parameter の指定は、すべてマクロエラーとなる。rest parameter は単一の値ではなくシーケンスであるため、後述の `!each` を通じてのみアクセスできる。

TeX グループの内部は解釈されない不透明な生 TeX（opaque raw TeX）であるため、グループ内にパラメータを展開（interpolate）することはできない。この不透明性は `!items` の生テキスト suite にも適用されるため、リスト項目の本文中に書かれた `!param` はマクロ展開されず、文字どおりのテキストとして残る。パラメータを渡したい場合は、以下のような構造化された形式を使用する。

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

`!each` は、rest parameter に格納された一連の値を出現順（source order）に走査し、各要素を一時変数 `item` にバインドしながらテンプレートを展開して、その結果を外側のブロックへ順に連結する。シーケンスが空の場合は何も生成されない。`: |` suite を伴う必要があり、マクロテンプレート内でのみ有効である。第1グループには必ず rest parameter を指定しなければならない。すでにバインドされているパラメータ名を覆い隠す（シャドウイングする）変数名を指定した場合はマクロエラーとなる。なお、`!each` はネストして使用できる。

`!each` は、スタックの末尾 suffix が `: |` である場合に限り、スタックの最右端セグメントとして配置できる。これは、最右端セグメントがその suffix を受け取るため、自身で `: |` を持っているのと等価になるからである。

~~~text
@{\bfseries} >> !each{items}{item}: |
    \item
    !param{item}
~~~

スタックの最右端以外の位置に置いた場合は、suite が合成（synthetic）扱いとなってしまうためバリデーションエラーとなる。また、`!defmacro` をスタックセグメントに含めることはできない（合成すると左側のセグメントの下にネストされ、マクロ定義がトップレベル文ではなくなってしまうため）。

### 12.5 展開（Expansion）

マクロ呼び出しは、常に**1つの値**（展開されたテンプレートブロック）として扱われる。したがって、`>>` の合成チェーンの途中にマクロ呼び出しを配置することも可能である。

~~~text
@center >> !smallred >> \TextCA{Important}
~~~

マクロテンプレート内から別のマクロを呼び出すことも可能である。ただしマクロの再帰呼び出し（recursion）は禁止されており、直接的・間接的な循環参照はすべて明示的に検出され、循環経路を含むマクロエラーとなる。

~~~text
recursive macro expansion detected: foo -> bar -> foo
~~~

マクロの展開は、`>>` の脱糖（desugaring）が行われた後、値の消費（value consumption）が行われる前に実行される AST-to-AST パスである。マクロ固有の構文が正規化処理やレンダラーに漏れ出ることはない。なお、スプライス構文（`!splice`）は v1 には含まれない。

### 12.6 ソースマッピング（Source mapping）

`!param` から出力されたコードは、マクロ呼び出し側（call site）で渡された元の値の位置情報（source span）を保持する。一方、テンプレート自体に由来する構造・枠組み（scaffolding）の位置情報は、定義位置ではなく**マクロ呼び出し位置（macro call site）**へとリターゲットされる。これにより、逆方向検索（SyncTeX inverse search）を行った際、ユーザーが書いた本文をクリックした場合はその本文の記述位置へ、マクロが生成した枠組みをクリックした場合はマクロ呼び出し行へと正確に戻ることができる。

## 13. 空行、生の TeX、コメント

シーケンスモードにおいて、各エントリー（`-`）の間に置かれた空行は単なる区切り文字（separator）として扱われ、値の一部には含まれない。一方、各シーケンスエントリーのブロック内部や、ブロックモード（`: |`）の内部にある空行は、意味のある内容（content）としてそのまま保持される。

TeX グループ内部に書かれた `:`、`: |`、`>>`、パイプ `|` は、すべて生のテキストとして扱われる。また、リスト項目（`!items`）の本文や継続行も生のテキストである。構造化構文の先頭位置でエスケープに用いる `@@` を除き、生の TeX コード行が TeXFlux の構文として再解釈されることはない。

構造化ヘッダーの末尾に TeX 形式のコメント（`% ...`）を付加することは、未定義ではなく明確に禁止されている（パースエラーとなる）。通常の生の TeX 行に含まれるパーセント記号（`%`）は、そのまま出力される。

## 14. 構文 AST と正準 AST（Syntax AST / Canonical AST）

パーサーは、元の構文の形状（syntax shape）とソースコード上の位置情報（source span）を保持する。代表的な構文ノード（syntax node）は以下の通りである。

- `RawTex`
- `ParsedInvocation`（command、named environment、brace、transparent）
- `SpecialInvocation`
- `Stack`
- `SequenceEntry`
- `Block`

suite のモード情報としては `Sequence` または `Block` を保持する。suffix を持たないスタックには suite は存在しない。`SequenceEntry` は、`-` マーカー自体のスパンと、値全体のスパンを保持する。

正規化パイプライン（normalization pipeline）の流れは以下の通りである。

~~~text
物理行（physical lines）
  -> 構文 AST（syntax AST）
  -> 純粋な >> の脱糖（pure >> desugaring）
  -> 値の消費 / special の展開（value consumption / special expansion）
  -> 正準 AST（canonical AST）
  -> レンダラー（renderer）
~~~

正準 AST（canonical AST）を構成するノードは以下のものだけに限定される。

- `RawTex`
- `GenericInvocation`
- `Argument`
- `Block`
- `BraceGroup`
- `Item`

正規化処理を経た後に、`ParsedInvocation`、`SpecialInvocation`、`Stack`、`SequenceEntry` などの構文固有ノードがレンダラーに届くことは決してない。

`GenericInvocation` は、`body=None` の場合はコマンドを表し、`body=Block` の場合は名前付き環境を表す。`BraceGroup` は必ずリテラルな TeX 中括弧（`{ ... }`）を出力する。透過コンテナ（Transparent container）はラッパーノードを生成しない。

## 15. 位置情報（SourceSpan）、ソースマップ、SyncTeX

主要な構文ノード、正準ノード、およびエラー診断情報（diagnostic）は、ファイル名、1から始まる行番号（1-based line）、1から始まる列番号（1-based column）の情報を保持する。スパンは半開区間（half-open）で表される。少なくとも以下の要素について、元のソースコードの位置（provenance）が保持される。

- suite のコロン（`:`）およびブロックパイプ（`|`）
- シーケンスの各 `-`
- シーケンス値のブロック境界
- `@{...}` ヘッダー
- `@:` ヘッダー
- スタックの各セグメント（`>>`）
- 閉じたスタックの終端要素
- シーケンスおよびブロック値の境界
- リスト項目（item）のメタデータ
- 生成された `\begin` / `\end`、引数の中括弧、リテラル中括弧、special 展開部分

レンダラーは `MappedEmitter` を介して、生成された TeX の出力範囲と元のソースコードの位置情報を対応づける。ソースマップ（source map）は、生成された TeX と元の `.tfx` ソースファイルのハッシュ値を検証する。SyncTeX のリマップ処理は、このソースマップを利用して生成された TeX の `Input` タグを元のソースファイルへとマッピングし直す。

言語仕様や構文の拡張にあたっても、このソースマップおよび SyncTeX の連携機構を削除したり簡略化したりしてはならない。

## 16. エラー診断と旧構文からの移行（Diagnostics / Migration）

以下の記法は、現行の v1 ではサポートされない。

- suffix を持たない単独の名前付き環境 / コンテナ
- 末尾がコロンのみの suite（`:`）において、マーカー（`-`）の付いていない子要素
- 閉じたスタック構文において、終端がオープンなコンテナで終わっているもの
- 未知の special（`!`）
- 構造化ヘッダー行末尾の不要なトークンやコメント
- suite の末尾に `|-` などの他のブロックスカラー変種を付加する記法

旧仕様に存在した `!block`、`!arg`、`!body` は互換用エイリアスとしては提供されない。現行の v1 では未定義または廃止された special としてエラーとなる。

旧仕様にあった以下の記述形式は使用できない。

~~~text
\foo:
    A
    B
~~~

単一の複数行引数を渡す場合は `\foo: |` を使用する。複数のブロック値を引数として渡す場合は、`\foo:` の下に各エントリーを `-` で1つずつ記述する。各 `-` の行に続く深くインデントされた行も同じ引数値に属する。

以前の環境構文における本文のみの表記も、`@frame{Title}: |` のように `: |` を用いた形式へと移行する。ユーザーはまず適切な suffix（`:` または `: |`）を選択し、複数の値を渡す場合は `-` を使って明示する。

## 17. 概念文法（Conceptual grammar）

以下の文法定義は、TeX 本文の文法ではなく、TeXFlux の構造化構文の意味関係を表すための概念文法（conceptual grammar）である。

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

実際の実装におけるパーサーは、リスト項目のメタデータ、括弧の対応関係、ソースコード上の位置情報（source span）、および生の TeX 行のインデントなどを別途管理している。`sequence-block` は、同じインデント階層に次の `-` が現れるまで継続する。この文法の要点は、**コロンのみ（`:`）が「`-` ごとに1つのブロック値」を意味し、コロンとパイプ（`: |`）が「全体で1つのブロック値」を意味し、suffix なしが「すでに完結した値」を意味する**という点にある。

## 18. 受け入れテスト例（Acceptance examples）

以下の変換結果は、v1 における標準的な期待動作（ゴールデンビヘイビア）を示している。

### 例1: 複数引数を持つコマンド
~~~text
\foo:
    - A
    - B
~~~

展開後:
~~~tex
\foo{
A
}{
B
}
~~~

### 例2: 引数と本文を持つ環境
~~~text
@myenv:
    - ARG1
    - ARG2
    - @: |
        BODY1
        BODY2
~~~

展開後:
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

### 例3: スタック合成（>>）
~~~text
@center >> \includegraphics{fig.pdf}
~~~

展開後:
~~~tex
\begin{center}
\includegraphics{fig.pdf}
\end{center}
~~~

### 例4: 空行を含むブロック引数
~~~text
\foo: |
    A

    B
~~~

展開後:
~~~tex
\foo{
A

B
}
~~~
