# TeXFlux v1 言語仕様 fix 前の検討事項（LaTeX 専門家レビュー）

## 0. ステータスと本書の位置づけ

本書は、v1 リリースに向けて言語仕様を確定する前に検討すべき事項を、LaTeX の専門家の視点で
レビューしてまとめたものである。レビューは 2026-09-15 に、`feature/trailing-colon-lookahead`
（7c38930）の作業ツリーに対して行った。規範定義は引き続き `texflux_tex_first_dsl_v1_spec.md`
であり、本書は規範ではない。本書の提案を採用する場合は、規範仕様・`doc/dsl.md`・golden を
同時に更新する。

評価の物差しは TeXFlux の設計契約、すなわち**「TeX 利用者が結果を予測できる挙動をし、
コンパイラは暗黙に判断・補正しない」**である。初版レビューは「LaTeX 利用者に書きやすいか」を
物差しにしていたため、契約を曲げる提案を 4 件含んでいた。本書はそれらを撤回した改訂版であり、
撤回した案も同じ提案が再び出たときの判断材料として 1 章に残す。8 章は、本レビューを受けて
同日に決めた suite suffix の変更（`::` / `:::`）と、その根拠・条件・残リスクを記録する。9 章は、
同日に決めた生成中括弧の配置（F1・F3）を記録する。

**実装状況（2026-09-15）:** 8 章と 9 章の決定は `feature/colon-suffix-and-brace-layout`
ブランチで実装済みである。9 章の中括弧配置は実装後に 9.0 節のとおり一律化した。パーサから先読み機構を削除し、レンダラの中括弧配置を変え、
examples・golden・テスト・仕様・文書を移行した。診断は P038 を追加し、W001 / W002 は
退役させた（`doc/diagnostics.md` §2.7）。残る作業は 7 章の 3・5 番（文書への帰結の追記と
README のエンジン明記）と 4 番（診断の hint）である。

要点は次のとおりである。

- 言語は契約をよく守っている。生 TeX は無傷で、生成物の形は決定論的で、`\item` を特別扱い
  しない判断は TeX の実構造に忠実である。
- 契約からの意図的な逸脱は末尾コロンの先読み規則の 1 か所だけで、その代償である
  「コロンが黙って消える」ケース（F2）が最大の検討事項である。
- 次に大きいのは、コンパイラ自身が壊れていると診断した TeX を成功終了で出力する点（F3）。
- 生成レイアウトの TeX 上の帰結（空白トークン）は決定論的だが文書化されていない（F1）。
- suite suffix を `::`（block）/ `:::`（sequence）に変えることを決めた（8 章）。単独コロンが
  構造トークンから外れ、先読み規則と F2 の silent case が、規則の追加ではなく削除で解消する。
  現行文法では expl3 の `\group_begin:` + 字下げが黙って壊れることも分かった（8.2 節）。
- 生成する中括弧の配置も決めた（9 章）。**最終形は 9.0 節**で、生成される必須引数の中括弧は
  `{%` で開いて独立行にし、値の次の行の `}` で閉じる 1 通りだけになった。値の形は何も決めない。
  別の形が欲しければ `+ {...}` か生 TeX で書く。

| 実測 | 値 |
| --- | --- |
| `python3.13 -m unittest discover` | 478 OK、skip 11（pdflatex / synctex を使う統合・e2e 4 件は実行・合格） |
| LaTeX の癖を突くプローブ入力 | 31 件をコンパイル |
| `content.tex` → `content.tfx` | 829 行 → 602 行。`\begin` / `\end` 283 行が消滅 |
| 生成レイアウトで `\scalebox` の hbox 幅が増えた量 | 7.50 pt → 14.17 pt |

## 1. 評価の物差しと初版からの改訂

契約に照らすと指摘は二種類に分かれる。契約の違反を指すもの（F2、F3）は残し、利便性のために
契約を曲げる提案（初版の F1 閉じ側、F2 の lint、F4、F5）は撤回した。

| 指摘 | 初版（v1） | 改訂（v2） |
| --- | --- | --- |
| F1 空白トークン | 重要。開き `{%` と、著者行末への自動 `%` 付加（オプトイン） | **中**。帰結の文書化と、既存の明示形 `+ {%…}` の案内が主。開き `{%` は決定論的な直列化の選択肢として提示。著者行の自動書き換えは撤回 |
| F2 消えるコロン | 重要。散文らしさを推定する lint | **重要**。推定を撤回。パーサが単独コロンで引数化した行をそのまま列挙する決定論的な可視化に置換。先読み規則そのものの是非も明記 |
| F3 W002 | 中。改行レイアウトに落とす | **中**。維持。W001 が既に持つ `%` 例外の延長であり新しい種類の知識を足さない。エラー化という別の整合的な選択も併記 |
| F4 リストのネスト | 中。直前が生 TeX 行なら構造行の字下げを許す | **小**。**撤回。**同じ字下げが文脈で意味を変える規則は契約違反。`\item` はコンテナではなく、兄弟配置は TeX の実構造どおり。残るのは文書化と診断の hint |
| F5 先頭空行 | 小。引数先頭の空行を落とす案 | **撤回**。空行 = `\par` は TeX 利用者の予測そのもの。現状が正しい |
| F6 診断 hint | 小 | **小**。維持。出力を変えず情報だけ増やす |
| F7 行末コメント | 小。suffix 後の `%` を許す | **留意点へ**。現状のエラーは契約に整合。許すなら深さ 0 の suffix 直後に限定した決定論的規則として |
| F8 README | 文書 | **文書**。維持 |

## 2. 総評

| 評価軸 | 判定 | 要点 |
| --- | --- | --- |
| 契約の遵守（予測可能・非暗黙） | 優 | 3 接頭辞 × 3 suite 形式に閉じ、`>>` は純粋な脱糖。TeX の知識を持ち込まない。唯一の意図的な逸脱は末尾コロンの先読み（行の意味が次行の字下げに依存）で、`doc/raw-mode.md` §6.5 がコストとして明記している。8 章の決定で解消する |
| 生成 TeX の正しさ | 良 | `\end{frame}` の行頭単独、`}{` の密着など LaTeX の要請を押さえている。生成レイアウトの空白トークンは決定論的だが文書化されていない（F1）。診断済みの壊れた TeX を成功終了で出す（F3）。9 章の決定で F1・F3 とも解消する |
| Beamer 実用性 | 良 | 環境、オーバーレイ、columns、fragile は快適。入れ子リストの兄弟配置は TeX の実構造どおりで、outlines のような TeX パッケージも素通りする。残る引っかかりは Beamer で自然に踏む F2 の形と、hbox 引数での F1 |
| ツールチェーン統合 | 良 | SyncTeX リマップは実測で機能。Overleaf では使えず、エディタ支援は未整備。README の手順は和文エンジンが暗黙の前提（F8） |
| 文書・仕様 | 優 | 規範仕様と利用者仕様の分離、却下案と受け入れたコストまで記録されている。足りないのは「生成物を TeX がどう読むか」の記述と、README の一部訴求の強さ |

## 3. 検証したこと

- **精読:** 規範仕様（1,394 行）、`doc/dsl.md`（1,449 行）、`doc/raw-mode.md` §6、AGENTS.md、
  `parser.py` / `normalize.py` / `render.py`、`examples/` 6 件とその golden。
- **実行:** `python3.13 -m unittest discover`。LaTeX の癖（`%`、`\\`、`>>`、verbatim、タブ、
  末尾コロン、空行、outlines 風の `\1` `\2`）を突く 31 件の `.tfx` を `texflux check` / `compile`。
- **TeX 側の実測:** 生成レイアウトの空白挙動を pdflatex で `\wd` / `\ht` により測定（8 章）。
  黙って意味が変わるケースと W002 のケースを pdflatex に通し、pdftotext で本文を確認。
- **手順の追試:** README クイックスタートを記載どおり `latexmk -pdfdvi` でビルドし、
  `synctex remap` 後に `synctex view` / `edit` が `.tfx` へ解決するかを確認。
- **環境:** TeX Live 2026（pdflatex / uplatex / dvipdfmx / synctex）、Python 3.13.15。
  リポジトリには変更を加えていない。

## 4. 契約を守っている点

- **生 TeX が本当に無傷。** `\draw[->>]`、`node {x >> y}`、`#1 & \#`、字下げした `\[ … \]`、
  `\newcommand{…}{%` の複数行定義、`\tikzset{` 内の `>=stealth,` 行がすべてそのまま通った。
  「グループ内は不透明」「`>>` は前後に空白必須」の規則が効いている。
- **`\item` を特別扱いしない。** TeX でも `\item` はコマンドであってコンテナではなく、入れ子の
  `\begin{itemize}` は外側リスト本文の兄弟である。TeXFlux はこの実構造をそのまま映しており、
  outlines の `\1` / `\2` のような TeX 側の入れ子手段も生 TeX として字下げごと素通りする
  （確認済み。5 章 F4）。
- **出力形が LaTeX の要請に沿う。** `\end{frame}` は必ず行頭単独で出る（fragile フレームの要件）。
  `\foo{A}{B}` の `}{` は同一行なので引数間に空白トークンが入らない。1 行の `-` 値は
  `{短い値}` と密着させ、余計な空白を生まない。
- **SyncTeX が本当に動く。** 記載の 3 手順で `.synctex.gz` の Input に `slides.tfx` が追加され、
  `synctex view -i 16:0:slides.tfx` が Page 2 を返した。import 先の行はそのファイルへ戻る
  （e2e テストが担保）。
- **診断が構造化されている。** 安定コード、`check` サブコマンド、JSON 出力、関連位置。
  LSP を作る土台がすでにある。
- **モジュール設計が明示的。** `!import` 先のフラグを暗黙継承しない、`!macroimport` は
  非推移的、名前衝突は shadowing ではなくエラー。`!text` の挿入文字列を再走査しないと定め、
  m4/cpp 型の事故を排除している。
- **抑制が効いている。** 標準フロー制御を 5 名に凍結し「`\vspace` を知る special は作らない」
  と明言。悩んだ末に却下した案とそのコストを設計書に残している。

## 5. 検討事項（重要度順）

### F1（中）生成レイアウトの TeX 上の帰結が文書化されていない

ブロック引数は `{` 改行 … 改行 `}` の形で出力される。この形は規範仕様 §14 と `doc/dsl.md`
6 章に書かれた決定論的な規則であり、TeX 利用者なら「`{` の直後の行末は空白トークンになり、
内容最終行の行末もそうなる」と予測できる。予測できるが、文書のどこにも「TeX がこれをどう読むか」
は書かれていない。TeX-first を掲げる道具は、生成する形の TeX 上の帰結を明記するべきである。

入力（`examples/content.tfx` と同形）:

~~~text
\scalebox{0.9}:
    \input{fig.tex}
~~~

出力:

~~~tex
\scalebox{0.9}{
\input{fig.tex}
}
~~~

pdflatex（10pt Computer Modern）での実測:

| 構文 | 密着 `{X}` | 生成形 `{⏎X⏎}` | 差 |
| --- | ---: | ---: | ---: |
| `\scalebox{1}{…}` | 7.50 pt | 14.17 pt | +6.67 pt（空白 2 個） |
| `\textbf{…}` | 8.69 pt | 16.36 pt | +7.67 pt |
| `\mbox{\includegraphics…}` | 10.00 pt | 16.67 pt | +6.67 pt |
| `a {\small x} b`（段落途中の `@{\small}:`） | 22.10 pt | 28.52 pt | +6.42 pt |
| `\vbox` 内の段落（`center` などの垂直文脈、高さ） | 3.87 pt | 3.87 pt | 0（無害） |

言語にはすでに明示形がある。`+` の明示グループなら著者が全バイトを支配でき、`doc/dsl.md` 5 章は
`+ {%` ⏎ `whitespace-sensitive%` ⏎ `}` という例を載せている。つまり契約の観点では、欠けている
のは機能ではなく、「どの形を選ぶと TeX に何が渡るか」の説明と、模範であるべき examples の書き方
である。`examples/content.tfx` の `\scalebox{0.9}:` は、明示形か生 TeX 行で書くのが TeX-first に
忠実である。和文（pTeX / upTeX / LuaTeX-ja）では和文文字直後の行末は空白にならないため影響は
半減するが、`}` や英数字で終わる行の行末と、`{` 直後の行末はそのまま残る。

**推奨:**

1. `doc/dsl.md` 6 章・15 章と README の FAQ に、生成レイアウトが生む空白トークンと、
   ブロック内の空行が `\par` になること（非 `\long` のコマンドなら Runaway argument）を明記し、
   明示形 `+ {%…}` と「最終行を `%` で終える」イディオムを示す。
2. `examples/content.tfx` の `\scalebox{0.9}:` を明示形に直す。
3. 生成する開き中括弧を `{%` にする案は、W001 / W002 と `--source-comments` がすでに置いている
   「`%` は行末までコメント」という前提以外の知識を要さず、規則としても一律なので契約と両立する。
   ただし規範仕様 §14 が定める形の変更なので設計判断として扱う。
4. 初版が挙げた「著者の最終行に `%` を自動付加するオプトインモード」は、著者の TeX を書き換える
   うえに挙動がオプション依存になるため撤回する。

**決定（2026-09-15）:** 9.2 節のとおり、複数行の値を包む生成中括弧の開き側を `{%` で出す。閉じ側の
直前の空白は残し、仕様に明記する。避けたければ著者が最終行を `%` で終えるか、`+ {...}` か生 TeX で書く。

### F2（重要）`\cmd{…}:` の末尾コロンが黙って消える

ヘッダーとして走査できる `\` 行の末尾コロンは、次の非空行が 4 スペース深ければ suite の suffix
として読まれる。散文としてのコロンだった場合、コロンは出力から消え、続く行は第 2 のグループになる。

入力:

~~~text
\textbf{Key results}:
    @itemize:
        \item first finding
\emph{Caveat}:
    This continuation line is indented.
~~~

出力:

~~~tex
\textbf{Key results}{
\begin{itemize}
\item first finding
\end{itemize}
}
\emph{Caveat}{
This continuation line is indented.
}
~~~

実測: `texflux check` は exit 0 で診断なし。pdflatex は exit 0。pdftotext の本文は
「Key results」「Caveat This continuation line is indented.」で、いずれもコロンがない。

これは言語が契約を意図的に曲げている唯一の場所である。`doc/raw-mode.md` §6.5 は「行の分類が
次行のインデントに依存する」ことと、この silent case を受け入れたコストとして明記している。
曲げた理由も妥当で、以前の規則（単独コロンは常に構造化）では `\textbf{Note}:` が
`\textbf{Note}{}` に黙って変わり、そちらの方が頻度が高かった。どちらの規則にも silent case は
あり、現行規則は稀な方を選んだ。それでも Beamer では「太字の見出し語 + コロン + 字下げした
ネスト箇条書き」は自然な書き方であり、TeX 側ではエラーなくコンパイルされるため PDF を目で見る
まで気づけない。予測可能であることと、忘れた著者に信号が届くことは別である。

**推奨:** 契約に整合する選択は二つある。

- (A) 規則を維持し、パーサの判断を**推定なしにそのまま**可視化する。`check` に、単独コロンで
  引数化した `\` 行を file:line つきで列挙するモードを足せば、著者は自分が書いた形が構造化された
  かを確認できる。
- (B) 契約を最大限に取り、`\` 行の単独コロンを常に構造化（ブロックが無ければエラー）に戻し、
  散文のコロンには `!| ` か `:{}` を要求する。`doc/raw-mode.md` §6.4 が摩擦として退けた選択だが、
  暗黙の判断は消える。

初版が挙げた「suite の先頭行が散文らしいときに警告する lint」は、内容から意図を推定するので
撤回する。

**決定（2026-09-15）:** 8 章のとおり suffix を `::` / `:::` に変える。単独コロンが構造トークン
でなくなるので、(A) も (B) も不要になる。

### F3（中）W002 の値は、壊れていると診断した TeX を exit 0 で出力する

入力:

~~~text
\foo::
    - alpha % beta
    - gamma
~~~

出力（警告 W002、exit 0）:

~~~tex
\foo{alpha % beta}{gamma}
~~~

実測: pdflatex は `! File ended while scanning use of \foo.` で失敗する。改行レイアウト
`\foo{alpha % beta` ⏎ `}{gamma}` は正常にコンパイルし、第 1 引数 `alpha␣` を保って
`[alpha |gamma]` を出力した。

コンパイラは `%` を検出して警告まで出しているのに、成功終了で壊れた TeX を書く。「予測可能」の
観点では、終了コード 0 が成功を約束している以上、これは約束違反である。規範仕様 §14 の
「動かすには著者の TeX を書き換えることになる」は当たらない。動くのは生成した `}` だけであり、
著者の行は 1 文字も変わらない。

**推奨:** 契約に整合する直し方は二つある。

- (a) W001（最終行が丸ごとコメントなら `}` を独立行に置く）という既存の `%` 例外を W002 にも
  延長する。レイアウトはすでに `%` に依存しているので、新しい種類の知識は増えない。
- (b) W002 をエラーにし、複数行の `-` 形で書くよう hint を出す。

`\verb|100%|` のような「コメントでない `%`」では (a) なら空白トークンが 1 つ増えるだけで
コンパイルが通るため、(a) を推す。いずれにせよ `--warnings-as-errors` のような明示的な
オプトインは、挙動を黙って変えないので契約と両立する（現状の警告は stderr に出るだけで終了コードは 0）。

**決定（2026-09-15）:** (a) を採る。9.1 節のとおり、エスケープされていない `%` を含む 1 行値は `}` を
独立行に置く。密着させたければ `+ {...}` で書く。W001 / W002 は規則に吸収され、警告としては退役する。

### F4（小）箇条書きのネスト: 初版の提案を撤回し、文書化と診断に絞る

**撤回:** 初版は「直前の兄弟が生 TeX 行なら、その直後の `@` / `!` 行は base+4 の字下げを許す」
と提案した。これは同じ字下げが文脈によって「子」と「装飾」を意味し分ける規則であり、TeXFlux が
排除している暗黙の判断そのものである。

初版が「書きたい形」とした入力（P025 になる）:

~~~text
@itemize:
    \item Results
        @itemize:
            \item nested
~~~

言語が定める形（TeX の実構造と同じ）:

~~~text
@itemize:
    \item Results
    @itemize:
        \item nested
~~~

初版の不満は「視覚的な木と論理的な木が一致しない」だったが、一致していないのは Markdown 的な
直観の方である。TeX において `\item` はコマンドであってコンテナではなく、入れ子の
`\begin{itemize}` は外側リスト本文の兄弟である。TeXFlux はこの実構造を正確に映している。
生 TeX 行は子を持たないという規則も一貫している。

TeX 側の入れ子手段はそのまま使える。outlines の `\1` / `\2` を 2 スペース字下げで書いた入力は
字下げを保って素通りし、`\1 Second point:` の下に 4 スペースのブロックを置いた入力は P017 で
止まる（黙って変わらない）。

~~~text
@outline:
    \1 Results
      \2 nested via the outlines package
    \1 Second point:
      \2 two-space continuation keeps the colon
~~~

~~~tex
\begin{outline}
\1 Results
  \2 nested via the outlines package
\1 Second point:
  \2 two-space continuation keeps the colon
\end{outline}
~~~

**残る推奨:**

1. `doc/dsl.md` 8 章に「`\item` はコンテナではない。入れ子の環境は兄弟として同じ字下げに置く」
   という理由を一文添える。契約どおりの挙動でも、理由が書かれていれば予測が立つ。
2. この位置で出る P025 に、構造行は suite の基準位置に置くこと、生 TeX 行は子を持たないことを
   hint として載せる。出力は変わらず情報だけが増える。`\item X:` + ブロックで出ていた P011 は、
   8 章の変更後は `\item X:` が常に生 TeX になるため対象外になる。

### F5（撤回）ブロック引数の先頭空行が `\par` になる

初版は「コマンドの `:` suite に限って先頭と末尾の空行を落とす」案を出した。これは著者の行を
コンパイラが黙って捨てる暗黙の補正であり撤回する。TeX 利用者にとって空行は常に `\par` であり、
規範仕様 §2 も「ブロック suite の空行は内容」と定めている。現状の挙動が正しい。`\section` のような
非 `\long` のコマンドで Runaway argument になる点は、F1 の文書化に一文添えれば足りる。

~~~text
\only<2>:

    revealed later
~~~

~~~tex
\only<2>{

revealed later
}
~~~

### F6（小）診断が escape の存在を教えない

| listing 内に書いた行 | 出た診断 | 言語が用意している書き方 |
| --- | --- | --- |
| `@property`（base 位置） | `P026` environment directives require a suite marker | `@@property`、`!\| @property`、raw mode |
| `@property`（深い位置） | `P025` invalid structural indentation | 同上 |
| `!important` | `D001` unknown special directive | `!!important`、`!\| !important`、raw mode |
| Makefile のタブ | `P023` tab characters are not allowed | `!\| ` か `!BEGIN_RAW_MODE` |

いずれもエラーで止まっており、契約は守られている。足りないのは、その位置で使える escape を診断が
示さないことである。意図を推定する必要はなく、言語が定義している書き方を列挙するだけでよい。

**推奨:** この 4 コードと F4 の P011 / P025 に、該当位置で使える escape（`@@` / `!!` / `!| ` /
`!BEGIN_RAW_MODE`）を hint として付ける。RelatedLocation の仕組みがすでにあるので、hint 文字列を
diagnostics に足す変更は小さい。

### F8（文書）README クイックスタートは記載どおりにはビルドできない

~~~text
$ latexmk -pdfdvi -synctex=1 slides.tex
! LaTeX Error: Unicode character 発 (U+767A)
               not set up for use with LaTeX.
[latexmk exit 12]

$ latexmk -pdfdvi -latex=uplatex -synctex=1 slides.tex   # 成功
$ texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
$ synctex view -i 16:0:/abs/path/slides.tfx -o slides.pdf
Page:2
~~~

`\documentclass[…,dvipdfmx]{beamer}` と `-pdfdvi` の組み合わせは (u)pLaTeX を前提にしているが、
README はそれを書いていない。エンジンを足せば成功し、リマップ後の順・逆検索も `.tfx` に解決した。

**推奨:** README に「`.latexmkrc` で `$latex = 'uplatex %O %S'` を設定するか、`lualatex` +
luatexja に置き換える」と明記する。あわせて `$$ … $$` は `\[ … \]` に（LaTeX では `$$` は非推奨）。
`examples/content.tfx` では `\def\SlideTitle` は `\newcommand` に、`align*` 最終行の `\\` は空行を
1 段増やす、`\centering:` は `\centering{…}`（宣言 + グループ）としてたまたま動いている形になって
いる。DSL ではなくサンプルの品質だが、公開サンプルは模範として読まれる。

## 6. 採用上の留意点

- **ヘッダー行末のコメントは書けない（P013）。** TeX 利用者は `\begin{frame}` 行に注釈を付ける
  習慣があるので摩擦はあるが、推定せずエラーにする現状は契約に整合している。許すなら
  「深さ 0 の suffix 直後の `%` のみ」という一律の規則として。
- **Overleaf 等では前処理を走らせられない。** 共同作業は生成 `.tex` の共有になり、`.tfx` が
  唯一の真実でなくなる。
- **エディタ支援がまだない。** 構文ハイライト、補完、TeXstudio / Skim の逆検索で `.tfx` を開く
  設定。diagnostics API がその土台になる。
- **latexmk 統合は README の Makefile 方式が現実的。** `add_cus_dep('tfx','tex',…)` をルート
  ファイルに使う試行では、初回が exit 12 で終わり、`.tfx` の更新も再検知されなかった。
- **`!flag` は Beamer の `\mode<handout>` / `\only<handout:0>` と用途が重なる。** 差別化点は
  「壊れた内容も無効化できる」ことと、クラス非依存であることである。
- **README の「3 年前のマクロ衝突」訴求は TeXFlux マクロにのみ当たる。** `\newcommand` の衝突は
  解決しない（`doc/dsl.md` 14.8 は正直に書いている）。
- **削減されるのは行数と閉じ忘れであって文字数ではない。** `content.tfx` は 29,538 バイトで
  `content.tex` の 27,642 バイトより大きい。インデントと 49 個の `!before{\vspace…}` が原因である。
- **イディオムの案内が要る。** `content.tfx` の `@frame[t]{…} >> !before{\vspace{-0.5em}}:` は、
  本文先頭に `\vspace{-0.5em}` を生 TeX 行として書けば済む。生 TeX 行の方が TeX-first に忠実で、
  TeX 利用者の予測もそのまま当たる。

## 7. 推奨アクション（優先順）

`feature/colon-suffix-and-brace-layout` ブランチで全項目を実施済みである。

1. ✅ suite suffix を `::` / `:::` に変え、先読み規則を削除した（F2、8 章）。
2. ✅ 生成される必須引数の中括弧を 1 通りに揃えた。`{%` で開き、独立行の `}` で閉じる。
   W001 / W002 は退役（F1、F3、9 章）。
3. ✅ `doc/dsl.md` 15.1 節と README の FAQ に、生成レイアウトが TeX に渡す空白トークンと
   空行 = `\par` の帰結、および著者側の書き方（最終行の `%`、`@{RAW%}`、`+ {...}`、生 TeX）を
   明記した。`content.tfx` の `\scalebox` 8 か所の最終行に `%` を付けた（F1、F5）。
4. ✅ P011 / P023 / P025 / P026 / D001 に、その位置で使える escape の hint を付けた（F4、F6）。
5. ✅ README にエンジン（`-latex=uplatex` と `.latexmkrc`、lualatex の代替）を明記し、
   `$$ … $$` を `\[ … \]` に直した。README のクイックスタートは記載どおりビルドでき、
   `synctex view` が `.tfx` に解決することを確認した（F8）。

残る検討事項は、警告チャネル（`RenderWarning` / `Severity.WARNING`）を診断 API に残すか
削除するかである。現在この種別を生成する箇所は無く、`severity: "warning"` は公開済みの
`texflux-diagnostics` v1 スキーマの一部であるため残してある（`doc/diagnostics.md` §2.7）。

`examples/content.tfx` に残る他のサンプル品質の指摘（`\def\SlideTitle` を `\newcommand` へ、
`align*` 最終行の `\\`、`\centering::`）は、著者の文書の意図に関わるため変更していない。

## 8. 決定事項: suite suffix を `::`（block）と `:::`（sequence）に変える

本レビューの F2 を受けて、2026-09-15 に次の仕様変更を決めた。v1 の fix 前に行う。

| 役割 | 現行 | 変更後 |
| --- | --- | --- |
| 完結した右辺の値 | suffix なし | suffix なし |
| ブロック（1 つの本文） | `:` | `::` |
| シーケンス（`-` / `+` ごとに 1 値） | `::` | `:::` |

### 8.1 契約に照らした根拠

- 単独 `:` は TeX の散文も行末に置く唯一の構造トークンであり、そのために次行の字下げを見る先読み
  規則（c52a7e7）が必要になり、F2 の silent case が残った。`:` を構造トークンから外せば、`\` 行の
  分類はその行だけで決まる純関数に戻り、先読み規則は削除できる。穴を塞ぐ規則を足すのではなく、
  原因の記号を TeX に返す解消である。
- `::` は現行でも無条件に行を claim しているので、block を `::` にしても誤検知は増えない。
- `>>` が `>` の重ね打ちであるのと同じく、行の途中・末尾に現れる TeXFlux の印がすべて「同じ記号の
  重ね打ち」になる。1 文字で意味を持つのは行頭の `@` / `!` と、`:::` の下でだけ生きる `-` / `+` に
  限られ、「重ねたら TeXFlux、1 個なら TeX」と一言で説明できる。TeX 散文と共有する 1 文字演算子が
  ゼロになるので、これを規範仕様 §16 の非目標として固定できる。

### 8.2 新たに見つかった根拠: expl3 が黙って壊れる

現行文法では LaTeX3 のコードが黙って壊れる。expl3 は `\group_begin:` `\scan_stop:`
`\prg_return_true:` のように末尾コロンの制御綴を常用し、コードを字下げして書く文化である。

入力:

~~~text
\ExplSyntaxOn
\group_begin:
    \cs_set:Npn \my_cmd:n #1 { \textbf{#1} }
    \my_cmd:n { indented~expl3~style }
\group_end:
\scan_stop:
\ExplSyntaxOff
~~~

現行文法の出力:

~~~tex
\ExplSyntaxOn
\group_begin{
\cs_set:Npn \my_cmd:n #1 { \textbf{#1} }
\my_cmd:n { indented~expl3~style }
}
\group_end:
\scan_stop:
\ExplSyntaxOff
~~~

`\group_begin:` が引数を取るコマンドに読み替えられ、`\group_end:` が対応を失う。診断は出ない。
`::` / `:::` にすれば単独コロンは常に TeX になり、この方言全体が安全になる。

### 8.3 トークンの割り当てと打鍵

| 範囲 | block | sequence |
| --- | ---: | ---: |
| examples + golden 35 ファイル | 246 | 30 |
| content.tfx | 156 | 14 |

最頻の block に短い方を割り当てる。コロンの連打は同じキーの繰り返しで（JIS 配列ではシフトも
不要）、1 個・2 個・3 個の打ち心地はほぼ同じである。sequence の候補として `:<:` / `:>:` も検討した
が、途中に Shift+`,` のコードが入り、`<` / `>` が overlay グループや `>>` と字を共有するため採らな
かった。`:::` は記号の語彙を増やさず、「コロンが増えると値が増える」と説明できる。

### 8.4 今後の TeXFlux にとっての有用性

- 行の分類がその行だけで決まるので、構文ハイライトは行内の正規表現（`::$`、`:::$`、` >> `）で
  書ける。TextMate や tree-sitter で次行を参照するのは困難である。
- LSP の増分解析で、N+1 行目の編集が N 行目の意味を変えない。入力中に診断がばたつかない。
- 今後どんな構文を足しても、単独 `:` の先読みを再導入する動機が生まれない。
- コロンの数で増やせるのは 3 個までだが、suite モデルは固定（規範仕様 §0、§16）なので 4 個目は
  作らない。

### 8.5 実装時に守る条件

1. `\cmd::` に本文が無いものはエラーにする（sequence の P031 と同じ扱い）。空引数は生 TeX の
   `\foo{}` で書く。これで `\texttt{std}::` 単独が黙って `\texttt{std}{}` になる新しい silent case
   は生まれない。
2. `::` / `:::` で終わる `\` 行は、ヘッダー走査に失敗しても生 TeX に落とさずエラーにする。現行の
   「`::` と `>>` は無条件に行を claim する」を引き継ぎ、黙って通す方向を作らない。
3. 削除する機構: `_suite_follows`、`_scan_structural_header` の `single_colon` 分岐、`\foo(x):` の
   特例 `_has_immediate_command_binding`、`- ` payload の先読み。`_has_top_level_trailing_colon` は
   `::` / `:::` 用に一般化して残す。
4. 診断の文言を更新する（P026 の「':' or '::'」など）。診断コードは据え置く。
5. golden: `trailing-colon` を「単独コロンは常に生 TeX」の検証に書き換え、expl3 の回帰テスト
   （`\group_begin:` + 字下げが生 TeX のまま通ること）を足す。
6. 文書: 規範仕様 §0 の suite model、§3、§5.2、§16（単独コロン suffix は構文ではない）、§17 文法、
   AGENTS.md の approved syntax、README のチートシートと FAQ、`doc/dsl.md` 4・6・7・8 章。
   `doc/raw-mode.md` §6 は経緯の記録に格下げする。
7. 順序は規範仕様 → parser とテスト → golden 再生成 → 文書。feature ブランチで行う。

### 8.6 受け入れた残リスク

- `:::` を `::` と打ち間違えると黙って block になる（`-` 行は正当な TeX なので検出できない）。
  逆方向はエントリー無しのエラーで止まる。sequence は全体の 1 割程度で、下に並ぶ `-` 行が目視の
  手がかりになる。
- `\texttt{std}::` の下に字下げブロックを置けば構造化される。`::` は散文の行末に来ない記号なので
  許容する。
- 旧文法の `::`（sequence のつもり）に `-` エントリーを書いた入力は `\foo{` ⏎ `- A` ⏎ `}` になる。
  v1 前の一回限りの移行リスクで、golden の再生成で洗い出す。
- 移行は 35 ファイル 276 ヘッダーとテスト内の文字列で、機械的に済む（`::$` → `:::` を先に、次に
  `:$` → `::`）。

### 8.7 本レビューの他の項目への影響

- F2 は本変更で解消する。5 章の (A) (B) は不要になる。
- F4 の残る推奨のうち、`\item X:` + ブロックに対する P011 の hint は不要になる（常に生 TeX）。
  P025 の hint は残る。
- F1、F3、F5、F6、F8 は影響を受けない。

## 9. 決定事項（追加）: 生成する中括弧の配置

2026-09-15 に F1 と F3 について次を決めた。共通する原則は「レイアウトは一律に決めて仕様に書く。
別の形が欲しければ `+ {...}` か生 TeX で著者が書く」である。TeXFlux は生の LaTeX を常に許すので、
コンパイラが文脈を推定して形を変える必要はない。

### 9.0 最終決定: 生成中括弧の配置は 1 通りにする

9.1 は当初「1 行に収まる `-` の値は `}` を密着させる。ただし `%` を含む場合は独立行に置く」
という規則にしていた。これは実装後の確認で撤回した。密着するかどうかを決めていたのは
`SequenceEntry.spans_one_line`、すなわち**著者がどこで改行したか**であり、言語の中でソースの
形が出力の形を決める唯一の場所だった。しかもその値はパース時に確定するため、
`- !param{x}` のようにテンプレートの形と中身が乖離する場所では直観と外れていた。
空白トークンの有無も改行位置で変わり、`{A}` は 0 個、`{%` ⏎ `A` ⏎ `}` は 1 個だった。

**最終的な規則は次の 1 文である。** 生成される必須引数の中括弧は、`{%` で開いて独立行にし、
値の次の行の `}` で閉じる。値の形は何も決めない。

- `ArgumentLayout.HUGGED` と `SequenceEntry.spans_one_line` は言語と実装から消えた。
  外部 AST の `layout` 列挙も `inline` / `block` / `explicit` の 3 つになった。
- `%` の規則は `+` の明示グループ専用になった。生成中括弧は必ず独立行で閉じるので、
  値の中のコメントがそれに届くことはない。
- 代償は、閉じ中括弧の直前の改行が作る空白トークン 1 個がすべての生成引数に付くことである。
  消したい著者は値の最終行を `%` で終えるか、その引数を `+ {...}` で書く。中括弧が生成される
  位置（構造化コマンドの引数と名前付き環境の begin 引数）は、`+` が使える位置と一致する。
  テンプレート内では、値が 1 個の `RawTex` なら `@block{!text{title}}::` と書ける。
- 実データでの影響は小さい。`examples/content.tfx`（実際の発表スライド）の 1 行 `-` エントリーは
  0 件、examples と golden 全体でも 15 件で、その大半はこの規則のために書いたゴールデンだった。

以下の 9.1 と 9.2 は、この最終決定に至る前の記述である。

### 9.1 F3: エスケープされていない `%` を含む 1 行値は `}` を独立行に置く

| 1 行値 | 現行 | 変更後 |
| --- | --- | --- |
| `- alpha` | `{alpha}` | `{alpha}` |
| `- alpha % beta` | `{alpha % beta}` + W002（TeX が壊れる） | `{alpha % beta` ⏎ `}` |
| `- % comment` | `{% comment` ⏎ `}` + W001 | 同じ形（規則に吸収） |
| `+ {alpha % beta}` | 書いたとおり | 書いたとおり |

規則: 1 行に収まる `-` の値は `}` を密着させる。ただし値にエスケープされていない `%` が含まれる
ときは `}` を次の行に置く。`\%` は `%` ではない。`+` の明示グループは逐語である。

- W001 と W002 はこの規則に吸収され、警告としての役目を終える。診断コードは再利用せず、退役として
  `doc/diagnostics.md` の表に残す。
- `\verb|100%|` のような「コメントでない `%`」を 1 行値に書くと `}` が次行に落ち、空白トークンが
  1 つ増える。密着させたければ `+ {\verb|100%|}` と書く。
- 規範仕様 §14 の「密着のまま警告する」の段落を置き換える。

### 9.2 F1: 複数行の値を包む開き中括弧は `{%` で出す

`{` ⏎ ではなく `{%` ⏎ で出す。`%` は行末までを捨てるので、`{` の直後の行末が空白トークンになる
ことが無くなる。`\newcommand{\foo}{%` と同じ、TeX 利用者が手で書くときの形である。

| 位置 | 変更後 |
| --- | --- |
| コマンドの `::` ブロック引数、複数行の `-` 値 | `\foo{x}{%` ⏎ 本文 ⏎ `}` |
| `@{RAW}::` の中括弧コンテナ | `{%` ⏎ `RAW` ⏎ 本文 ⏎ `}` |
| 1 行値の密着 `{alpha}` | 変更なし（`%` を付けると本文が消える） |
| `+ {...}` の明示グループ | 変更なし（著者の TeX） |
| `\begin{env}` の行 | 変更なし（環境本文は垂直文脈で空白が捨てられる） |

pdflatex での実測（`a{ … \small\color{gray} … x … }b` の hbox 幅）:

| 形 | 幅 | 意味 |
| --- | ---: | --- |
| 現行 `{` ⏎ RAW ⏎ x ⏎ `}` | 21.85 pt | 空白 2 個 |
| `{%` ⏎ RAW ⏎ x ⏎ `}` | 18.52 pt | 開き側の空白が消え、閉じ側の 1 個が残る |
| `{%` ⏎ RAW`%` ⏎ x ⏎ `}` | 18.52 pt | この RAW では行末の空白は元から出ない（下記） |
| `{%` ⏎ RAW`%` ⏎ x`%` ⏎ `}` | 15.44 pt | 密着 `{RAW x}` と同じ |

残る空白トークンと、著者側の書き方:

- 本文最終行の行末（`}` の直前）の空白は残る。消したければ最終行を `%` で終える。
- `@{RAW}::` の RAW 行末は、RAW が `\small` のような制御綴で終わるか、`\color{gray}` のように
  `\ignorespaces` で終わる命令なら空白を生まない。`\setlength{\parskip}{0pt}` のように `}` で終わる
  一般の命令なら空白トークンが 1 つ残る。ヘッダーは不透明な生 TeX なので `@{\setlength{\parskip}{0pt}%}::`
  と書けば消える（ヘッダー内の `%` が現行のコンパイラでもそのまま通ることを確認した）。
- `\url`、`\mint`、`\verb` 系のように引数を verbatim に読むコマンドでは `{%` の `%` が引数の一部に
  なる。これらの引数に生成中括弧を使わず、`+ {...}` か生 TeX で書く。現行でも改行と字下げの除去が
  入るので、注意の内容は変わらない。

### 9.3 仕様と文書に書くこと

- 規範仕様 §14: 開き中括弧の `{%`、1 行値の `%` 規則、`+` は逐語。あわせて「生成レイアウトが TeX に
  渡す空白トークン」を 1 段落で明記する。
- `doc/dsl.md` 15 章と README の FAQ: 同内容と、著者側の書き方（最終行の `%`、`@{RAW%}`、
  `+ {...}`、生 TeX）。
- `doc/diagnostics.md`: W001 / W002 を退役として記録する。
- golden と `examples/*.tex` は全件再生成になる。差分が `{` → `{%` と W002 の 1 件だけであることを
  diff で確認する。

### 9.4 本レビューの他の項目への影響

- F1 と F3 は本決定で解消する。
- 7 章の推奨アクションは本決定を反映して並べ直した。

## 10. 再現手順

~~~bash
python3.13 -m unittest discover                       # 474 OK, skip 11
PYTHONPATH=src python3.13 -m texflux check probe.tfx  # 各プローブ
PYTHONPATH=src python3.13 -m texflux compile probe.tfx -o probe.tex

# README クイックスタート（和文エンジンを明示すると通る）
latexmk -pdfdvi -latex=uplatex -synctex=1 slides.tex
texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
synctex view -i 16:0:/abs/path/slides.tfx -o slides.pdf   # Page:2

# expl3 プローブ（8.2 節）。現行文法では \group_begin{ … } になる
PYTHONPATH=src python3.13 -m texflux compile expl3.tfx -o expl3.tex && cat expl3.tex
~~~

空白トークンの測定に使った `spaces.tex`（pdflatex、`\typeout` の値を読む）:

~~~tex
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\setbox0\hbox{\scalebox{1}{X}}
\typeout{SCALEBOX-HUG: \the\wd0}
\setbox0\hbox{\scalebox{1}{
X
}}
\typeout{SCALEBOX-BLOCK: \the\wd0}
\setbox0\hbox{\textbf{X}}
\typeout{TEXTBF-HUG: \the\wd0}
\setbox0\hbox{\textbf{
X
}}
\typeout{TEXTBF-BLOCK: \the\wd0}
\setbox0\hbox{a {\small x} b}
\typeout{GROUP-INLINE: \the\wd0}
\setbox0\hbox{a
{
\small
x
}
b}
\typeout{GROUP-BLOCK: \the\wd0}
\setbox0\vbox{\hsize=200pt \noindent
{
\small
x
}
\par}
\typeout{VBOX-PARA-BLOCK: \the\ht0}
\setbox0\vbox{\hsize=200pt \noindent{\small x}\par}
\typeout{VBOX-PARA-INLINE: \the\ht0}
\setbox0\hbox{\mbox{
\includegraphics[width=10pt]{example-image}
}}
\typeout{MBOX-IMG-BLOCK: \the\wd0}
\setbox0\hbox{\mbox{\includegraphics[width=10pt]{example-image}}}
\typeout{MBOX-IMG-HUG: \the\wd0}
\end{document}
~~~

結果: SCALEBOX 7.50002pt / 14.16667pt、TEXTBF 8.6944pt / 16.36102pt、GROUP 22.10416pt /
28.5208pt、VBOX 3.87498pt / 3.87498pt、MBOX 10.0pt / 16.66666pt（TeX Live 2026）。
