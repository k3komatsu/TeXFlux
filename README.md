# TeXFlux (テックフラックス)

<p align="center">
  <strong>TeX の表現力そのままに、<code>\begin</code> / <code>\end</code> の呪縛から解放される。</strong><br>
  LaTeX / Beamer 向け TeX-first インデントベース・プリプロセッサ
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-zero-brightgreen.svg" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/SyncTeX-supported-orange.svg" alt="SyncTeX Supported">
</p>

---

## 💡 TeXFlux とは？ なぜ必要なのか？

LaTeX（特に Beamer スライド）を作成するとき、誰もが以下のようなストレスを感じたことがあるはずです。

- **画面の半分が定型句で埋まる**: `\begin{frame}`, `\begin{columns}`, `\begin{column}`, `\begin{itemize}` など、本質的でない `\begin` / `\end` の記述でエディタが埋め尽くされ、スライドの内容に集中できない。
- **ネストの崩れ・閉じ忘れエラー**: 中括弧 `{ ... }` や環境の閉じタグが1つ欠けただけで、意味不明な長いエラーログと格闘することになる。
- **軽量マークダウンツールの限界**: Marp や Slidev、Pandoc などの Markdown ベースのスライドツールは手軽ですが、**「TeXで書いた原稿の再利用」「高度な数式・TikZ 図」「オーバーレイアニメーション（`<1->`）」「自作 TeX マクロ」** を思い通りに使うのは困難です。

**TeXFlux は、このジレンマを「TeX を置き換えるのではなく、構造のボイラープレートだけを消し去る（TeX-first）」ことで解決します。**

TeX の文章、数式、コマンド、パッケージ資産は 100% そのまま。Python のように美しくインデントするだけで、読みやすく保守しやすいスライドが驚くほど快適に書けます。

---

## ⚡ 一目でわかる Before / After

2段組スライド（箇条書き＋画像）を記述した場合の比較です。

### 従来の LaTeX / Beamer（16行・半分以上が定型句）
```latex
\begin{frame}{研究の概要}
  \begin{columns}
    \begin{column}{0.5\textwidth}
      \begin{itemize}
        \item<1-> 背景と課題の整理
        \item<2-> 提案手法のアプローチ
        \item<3-> 従来手法との比較検証
      \end{itemize}
    \end{column}
    \begin{column}{0.5\textwidth}
      \begin{center}
        \includegraphics[width=\linewidth]{architecture.pdf}
      \end{center}
    \end{column}
  \end{columns}
\end{frame}
```

### ✨ TeXFlux（9行・閉じタグゼロで直感的）
```text
@frame{研究の概要}: |
    @columns: |
        @column{0.5\textwidth}: |
            !items:
                -<1-> 背景と課題の整理
                -<2-> 提案手法のアプローチ
                -<3-> 従来手法との比較検証
        @column{0.5\textwidth}: |
            @center >> \includegraphics[width=\linewidth]{architecture.pdf}
```

- **定型句・閉じタグが完全ゼロに**: 従来の 16 行中 12 行（4分の3）を占めていた `\begin` と `\end` のボイラープレートを全廃し、16 行が 9 行に。
- **閉じ忘れエラーは原理的にゼロ**: インデント（半角スペース4個）だけで階層構造を表現。
- **`>>` による1行合成**: `@center >> \includegraphics{...}` のように、単一要素の入れ子は1行でスマートに圧縮。
- **TeX コードは 100% 透過**: 数式（`$E=mc^2$`）やコマンド（`\textbf{...}`）は勝手にエスケープされず、完全な TeX としてコンパイルされます。

---

## 🌟 他のツールとの比較

「なぜ Marp や Typst ではなく TeXFlux なのか？」

| 項目 | 生の LaTeX / Beamer | Marp / Slidev | Typst | **TeXFlux** |
| :--- | :---: | :---: | :---: | :---: |
| **TeX / 数式の表現力** | ◎ 完璧 | △ 制限あり | ○ 独自文法 | **◎ 完全互換（TeXそのもの）** |
| **既存の Beamer テーマ・資産** | ◎ 使える | × 使えない | × 使えない | **◎ 100% そのまま使える** |
| **ボイラープレートの少なさ** | × 非常に多い | ◎ 少ない | ◎ 少ない | **◎ 最小限（インデント記法）** |
| **多重ネストの視認性** | × 崩れやすい | ○ 普通 | ◎ 良好 | **◎ インデントで一目瞭然** |
| **PDF ↔ ソースの逆ジャンプ** | ◎ SyncTeX | △ ツール依存 | ○ 独自プレビュー | **◎ SyncTeX 完全対応** |
| **導入コスト** | 既存のまま | 乗り換えが必要 | 乗り換えが必要 | **◎ 既存プロジェクトに即導入可能** |

> **「既存のテンプレートや発表スライドのクオリティを一切落とさずに、タイピング量とストレスだけを劇的に減らしたい」**  
> TeXFlux は、まさにそんなあなたのためのツールです。

---

## ✨ キラー機能ハイライト

### 1. 🎯 SyncTeX 完全対応（PDF との相互ジャンプを諦めない）
プリプロセッサを導入する際、最大の懸念となるのが「PDF の数式やテキストをクリックしたとき、中間生成物の `.tex` ではなく元のソースファイルに戻れるか？」という点です。  
TeXFlux はソースマップ（`.tfxmap`）を自動生成し、SyncTeX データをリマップします。**PDF ビューアとエディタ間の「クリックで該当行へジャンプ」が、元ファイル（`.tfx`）に対して完全に機能します。**

### 2. ⚡ `>>` パイプライン合成
入れ子になった環境やコマンドを、1行で連続して適用できます。
```text
@center >> @{\small} >> \textit{注: 実験条件は室温 25℃ で統一}
```
展開後:
```latex
\begin{center}
{
\small
\textit{注: 実験条件は室温 25℃ で統一}
}
\end{center}
```

`>>` の直後で改行し、次の行を同じインデントに揃えることもできます。たとえば次の形も同じ出力になります。
```text
@center >>
@{\small} >> \textit{注: 実験条件は室温 25℃ で統一}
```
末尾が完結したコマンドなら suffix は不要です。本文を続ける場合は最後のセグメントに `: |` または `:` を付けます。

### 3. 📝 Beamer 最適化のリスト構文（`!items:`）
Beamer でのアニメーション指定（`<1->`）や項目記号の変更（`[★]`）、ネストした箇条書きを最小限のキーストロークで記述できます。
```text
!items:
    -<1-> 基本方針の策定
    -<2->[※] 特記事項
        - サブ項目の詳細
```

### 4. 🔀 1つのソースから自在に出し分け（`!flag` / `!when`）
「本番スライド」「配布用ハンドアウト」「自分用の発表メモ」「ドラフト」を別々のファイルにコピペして管理していませんか？  
TeXFlux なら、ソースコード内にフラグを宣言し、コンパイル時に切り替えるだけで自在に出し分けられます。
```text
!flag{handout}{off}
!flag{memo}{off}

@frame{提案手法}: |
    提案アルゴリズムの概要です。
    !unless{handout}: |
        \pause
        アニメーションで表示する詳細です。
    !when{memo} >> \marginpar{ここで質疑応答の想定質問を意識}
```
- コマンドラインから `--flag handout` や `--flag memo` を渡すだけで切り替え可能。
- 宣言されていないフラグ名を指定するとエラーになるため、**タイポで内容が黙って消える事故を防止**します。
- **条件によって捨てられた本文は展開も正規化もされません**。ただし、構文解析、`!flag` のトップレベル制約、`!defmacro` / `!each` のスタック形式の検査は行われます（[詳細](doc/dsl.md#135-解決とその範囲)）。

### 5. 🪶 外部依存ゼロ（Zero Dependencies）
Python 3.11 以上の標準ライブラリのみで実装されています。余計なパッケージのインストールや環境構築の競合に悩まされることはありません。

---

## 🚀 クイックスタート

### 1. インストール

```bash
# リポジトリから直接インストール
git clone https://github.com/k3komatsu/TeXFlux.git
cd TeXFlux
pip install .
```

### 2. `.tfx` ファイルの作成

お気に入りのエディタで `slides.tfx` を作成します。

```text
% 普通の TeX コマンドやプリアンブル設定はそのまま記述可能
\documentclass[aspectratio=169,dvipdfmx]{beamer}
\usetheme{Madrid}

\title{TeXFlux デモ}
\author{発表者名}

\begin{document}

@frame: |
    \titlepage

@frame{TeXFlux の魅力}: |
    !items:
        -<1-> \textbf{直感的}: インデントでスッキリ記述
        -<2-> \textbf{安全}: 閉じタグのミスが原理的に発生しない
        -<3-> \textbf{柔軟}: TeX の数式やマクロは 100\% そのまま

    \medskip
    $$ \int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi} $$

\end{document}
```

### 3. コンパイルして PDF を生成

```bash
# 1. TeXFlux で .tex ファイルとソースマップ（.tfxmap）を生成
texflux compile slides.tfx -o slides.tex

# 2. latexmk などでお手元の環境に合わせて PDF をビルド
latexmk -pdfdvi -synctex=1 slides.tex

# 3. SyncTeX 情報を .tfx 向けにリマップ（PDF 逆ジャンプが .tfx に飛ぶようになります）
texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
```

これだけで、美しい Beamer スライド PDF が完成します！

---

##  構文チートシート

TeXFlux の文法は極めてシンプルです。**「3つの接頭辞」** と **「2つのブロック形式」** を知るだけで、すべての機能を使いこなせます。

### 1. 3つの接頭辞（Prefix）

| 接頭辞 | 種別 | 説明・具体例 |
| :---: | :--- | :--- |
| `\` | **TeX コマンド** | `\section{...}`, `\textbf{...}`, `\input{...}` などの TeX コマンド |
| `@` | **構造コンテナ / 環境** | `@frame`, `@columns`, `@center`, `@{...}`（中括弧グループ） |
| `!` | **TeXFlux 特殊機能** | `!items:`, `!when`, `!unless`, `!flag`, `!defmacro`, `!vpad`, `!off`, `!drop` |

※ 接頭辞のない行や数式行（`$ ... $`）は、**生の TeX（raw TeX）** としてそのまま透過されます。

---

### 2. 2つのブロック形式（Suite Suffix）

行末のコロンの書き方で、インデントされた子要素の受け渡し方を指定します。

| 記法 | モード | 意味 | 主な用途 |
| :---: | :--- | :--- | :--- |
| `: \|` | **ブロックモード** | 続くインデント全体を **「1つの本文・ブロック」** として渡す | 環境の本文、単一の長大な引数 |
| `:` | **シーケンスモード** | 各 `-` で始まる要素を **「別々の引数」** として順に渡す | 複数引数を持つコマンド・環境 |

---

### 3. 代表的な記法パターン

#### ① 環境の展開（`@env: |`）
```text
@block{定理 1}: |
    任意の素数 $p$ について……
```
展開後:
```latex
\begin{block}{定理 1}
任意の素数 $p$ について……
\end{block}
```

#### ② リテラル中括弧コンテナ（`@{...}: |`）
フォントサイズや文字色、ローカルなマクロのスコープを `{ ... }` で限定したいときに使います。
```text
@{\small\color{gray}}: |
    ここだけフォントが小さく、グレーで表示されます。
```
展開後:
```latex
{
\small\color{gray}
ここだけフォントが小さく、グレーで表示されます。
}
```

#### ③ 上下の余白調整（`!vpad`）
ブロックの前後に `\vspace` を安全に挿入します。
```text
!vpad{1em}{2em}: |
    上下に適切な余白を自動確保したブロックです。
```
展開後:
```latex
\vspace{1em}
上下に適切な余白を自動確保したブロックです。
\vspace{2em}
```

#### ④ 複数引数のコマンド・環境（`:` と `-`）
```text
\twoargs:
    - 第1引数のテキスト
    - 第2引数のテキスト
```
展開後:
```latex
\twoargs{第1引数のテキスト}{第2引数のテキスト}
```

#### ⑤ スタック要素を一時的に外す（`!off` / `!drop`）

`!off{...}` は指定した断片だけを無視して後続を通し、`!drop` は後続ごと捨てます。

```text
\fuga >> !off{\foo{a}{b}} >> \hoge
!drop >> \hoge >> \fuga
```

1行目は `\fuga >> \hoge` と同じ結果になり、2行目は TeX の内容を出力しません。

#### ⑥ ソースマクロの定義と利用（`!defmacro`）
よく使うデザインやパーツをトップレベルで構造マクロとして定義できます（テキスト置換ではなく AST レベルの安全なマクロです）。
```text
!defmacro{alertbox}{title}{body}: |
    @alertblock:
        - !param{title}
        - !param{body}

!alertbox{注意}: |
    締め切りは厳守してください。
```

---

## 🛠️ おすすめ実践ワークフロー

### Makefile で快適にビルドする例

Makefile を1つ用意しておけば、コマンド一発でコンパイルから SyncTeX リマップまで完結します。

```makefile
SRC = slides.tfx
TEX = $(SRC:.tfx=.tex)
PDF = $(SRC:.tfx=.pdf)
MAP = $(TEX).tfxmap
SYNC = $(SRC:.tfx=.synctex.gz)

all: $(PDF)

$(TEX) $(MAP): $(SRC)
	texflux compile $(SRC) -o $(TEX)

$(PDF) $(SYNC): $(TEX)
	latexmk -pdfdvi -synctex=1 $(TEX)
	texflux synctex remap $(SYNC) --map $(MAP)

clean:
	latexmk -C $(TEX)
	rm -f $(TEX) $(MAP)
```

### デバッグに便利な `--source-comments`
`texflux compile slides.tfx -o slides.tex --source-comments` を指定すると、生成された `.tex` の各要素の直前に由来を示すコメント（`% texflux: slides.tfx:12`）が挿入され、生成結果の検証がより一層容易になります。

---

## 🐍 Python API からの呼び出し

Python スクリプトやビルドツール内から、TeXFlux の変換エンジンを直接呼び出すこともできます。

```python
from texflux import compile_text

source = r"""!flag{draft}{off}

@frame{Python からのコンパイル}: |
    !items:
        - 簡単・高速
        - 外部依存なし
    !when{draft} >> \marginpar{草稿用のメモ}
"""

# テキストから直接 LaTeX コードを生成
latex_code = compile_text(source, filename="example.tfx")
print(latex_code)

# ビルドフラグを上書きしてコンパイル（宣言済みのフラグ名しか渡せません）
latex_draft = compile_text(source, filename="example.tfx", flags={"draft": True})
```

---

## ❓ よくある質問（FAQ）

### Q. 既存の Beamer テーマやスタイルファイル（`.sty`）は使えますか？
**A. はい、100% 使えます。**  
TeXFlux は TeX のプリアンブルやマクロ定義、パッケージ読み込み（`\usepackage{...}`）をそのまま透過します。お手持ちのテンプレートやカスタムスタイルを一切変更することなく利用可能です。

### Q. 数式の中でコロン `:` や縦線 `|` を使っても誤作動しませんか？
**A. 一切問題ありません。**  
TeXFlux が認識するのは、行頭のプレフィックス（`@`, `!`, `\`）や行末のインデント開始コロン（`:`）、前後をスペースで区切られた `>>` だけです。数式の中（`$f: X \to Y$` など）やグループ内の文字を勝手にパースして破壊することはありません。

### Q. 既存の大きな LaTeX プロジェクトに少しずつ導入できますか？
**A. 簡単に部分導入できます。**  
文書・スライド全体を一括で書き換える必要はありません。
ほとんどの場合、既存の `.tex` ファイルをそのまま `.tfx` にリネームするところから始められます。TeXFluxはTeXコマンドや環境をそのまま透過するため、ほとんどの既存のコードはそのまま動作します。
そして、もし必要であれば、`@frame: |` や `!items:` や `@center >> @{\small}:` などの構造化記法を少しずつ導入していくことが可能です。

---

## 📁 サンプル集（`examples/`）

リポジトリ内の `examples/` ディレクトリには、すぐに試せるサンプルと厳密な変換結果（ゴールデンファイル）が含まれています。

- [basic.tfx](examples/basic.tfx) : 基本的な環境、生の TeX、`!items`、オーバーレイ
- [structured.tfx](examples/structured.tfx) : 複数引数（シーケンス）とリテラル中括弧グループ
- [stacked-items.tfx](examples/stacked-items.tfx) : `>>` によるパイプライン合成とネストしたリスト
- [macros.tfx](examples/macros.tfx) : 引数付きマクロ・可変長引数マクロ
- [content.tfx](examples/content.tfx) : 実際の学術研究発表スライド（1000行超の実践コード）

---

## 📚 仕様書・ドキュメント

より詳細な言語仕様や設計原則については、以下をご参照ください。

- [TeXFlux DSL v1 利用者向け言語仕様書 (doc/dsl.md)](doc/dsl.md)（日本語）
- [TeXFlux Normative Specification (texflux_tex_first_dsl_v1_spec.md)](texflux_tex_first_dsl_v1_spec.md)（規範的仕様書・英語）

---

## 🛠️ 開発・テスト

```bash
# テストスイートの実行
python3 -m unittest discover
```
