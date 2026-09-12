# TeXFlux

**TeXFlux**（テックフラックス）は、LaTeX / Beamer 向けの軽量な「TeX-first」インデントベース・プリプロセッサです。
TeX の文法や数式・表現力はそのままに、煩雑な `\begin{...}` / `\end{...}` や複雑な中括弧のネストを、Python のようなインデント構文で劇的にシンプルに記述できます。

---

## 💡 TeXFlux とは？ なぜ使うのか？

LaTeX（特に Beamer スライド）を作成するとき、以下のような悩みを抱えたことはありませんか？

- `\begin{frame}`、`\begin{columns}`、`\begin{column}`、`\begin{itemize}` など、**`\begin` と `\end` の boilerplate（定型句）が多すぎて見通しが悪い**
- 中括弧 `{ ... }` の対応が崩れてコンパイルエラーになる
- Markdown ベースのスライドツール（Marp や Pandoc など）では、**数式や Beamer の高度な機能（オーバーレイ `<1->`、独自マクロ、細かなレイアウト調整）が思い通りに使えない**

**TeXFlux は、この問題を「TeX を置き換えるのではなく、構造の記述だけを簡潔にする（TeX-first）」ことで解決します。**

### Before / After 比較

TeXFlux を使うと、Beamer スライドの記述が以下のようにスッキリします。

#### 従来の LaTeX / Beamer:
```latex
\begin{frame}{研究の概要}
  \begin{columns}
    \begin{column}{0.5\textwidth}
      \begin{itemize}
        \item<1-> 背景と課題
        \item<2-> 提案手法のアプローチ
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

#### TeXFlux（`.tfx`）:
```text
@frame{研究の概要}: |
    @columns: |
        @column{0.5\textwidth}: |
            !items:
                -<1-> 背景と課題
                -<2-> 提案手法のアプローチ
        @column{0.5\textwidth}: |
            @center >> \includegraphics[width=\linewidth]{architecture.pdf}
```

- `\begin` や `\end` の閉じ忘れがゼロに
- 多重ネストもインデントで直感的に表現
- `@frame{研究の概要} >> @columns` や `@center >> \includegraphics{...}` のように、環境とコマンドを1行でパイプライン合成可能
- **数式やマクロ、パッケージなどの TeX コードは 100% そのまま動作**

---

## ✨ 主な特徴

1. **TeX-first（TeX の表現力を完全保持）**
   - TeXFlux は TeX の文章や数式を勝手にパース・エスケープ・改変しません。既存の LaTeX パッケージ、コマンド、数式資産がそのまま動作します。
2. **直感的なインデント構文**
   - 半角スペース4個のインデントでブロック構造や引数を表現。
3. **`>>` によるスタック合成**
   - `@center >> @{\small} >> \input{fig.tex}` のように、入れ子構造を1行でスッキリ記述。
4. **SyncTeX 完全対応（PDF とソースの相互ジャンプ）**
   - ソースマップ（`.tfxmap`）を出力し、SyncTeX をリマップすることで、PDF ビューアとエディタ間の相互ジャンプ（前方検索・後方検索）が `.tfx` ファイルに対してそのまま動作します。
5. **1ソースから複数バージョン**
   - `!flag` / `!when` でドラフト用の注記や配布版の差分を切り替え、同じソースから別々の `.tex` を生成。
6. **外部依存ゼロ**
   - Python 3.11 以上の標準ライブラリのみで動作します（追加パッケージのインストール不要）。

---

## 🚀 インストール

TeXFlux は Python 3.11 以上が必要です。

```bash
# リポジトリをクローンしてインストール
git clone https://github.com/komatsu/beamercraft.git
cd beamercraft
python3 -m pip install .
```

---

## 📖 基本的な使い方（CLI）

### 1. `.tfx` ファイルのコンパイル

`.tfx`（TeXFlux ファイル）から標準の `.tex` ファイルを生成します。

```bash
texflux compile slides.tfx -o slides.tex
```

文書が `!flag` を宣言している場合は、`--flag` で既定値を上書きできます。

```bash
texflux compile slides.tfx -o handout.tex --flag draft --flag handout=off
```

### 2. LaTeX コンパイルと SyncTeX の連携（推奨ワークフロー）

TeXFlux はコンパイル時にソースマップファイル（`slides.tex.tfxmap`）を自動生成します。
SyncTeX を有効にして TeX をコンパイルした後、`texflux synctex remap` を実行すると、PDF のクリックから `.tfx` の該当行へ直接ジャンプできるようになります。

```bash
# 1. TeXFlux で .tex と .tfxmap を生成
texflux compile slides.tfx -o slides.tex

# 2. latexmk 等で SyncTeX を有効にして PDF をビルド
latexmk -pdf -synctex=1 slides.tex

# 3. SyncTeX ファイルを .tfx 向けにリマップ
texflux synctex remap slides.synctex.gz --map slides.tex.tfxmap
```

※ LaTeX のビルドツール自体は TeXFlux の依存関係ではありません。お好みの TeX ディストリビューション（TeX Live など）やビルドツール（latexmk, llmk 等）をご利用ください。

---

## 📝 構文クイックガイド

TeXFlux の文法規則はとてもシンプルです。3つのプレフィックス（接頭辞）と2つの suite 形式（`:` と `: |`）を覚えるだけで、すぐに使い始められます。

### 1. 3つのプレフィックス（接頭辞）

- `\` : **TeX コマンド**（例: `\section{...}`, `\textbf{...}`）
- `@` : **構造コンテナ / 環境**（例: `@frame`, `@center`, `@{...}`）
- `!` : **TeXFlux の特殊変換**（例: `!items:`, `!defmacro`, `!when`）

プレフィックスを伴わない通常の行は、すべてそのまま「生の TeX（raw TeX）」として扱われます。

### 2. 2種類のブロック形式（Suite Suffix）

- `: |` （ブロックモード）：続くインデント全体を「**1つのブロック値**」として扱う（環境の本文や複数行の引数に利用）。
- `:` （シーケンスモード）：各 `-` で区切られたエントリーを「**複数の引数の並び**」として扱う。

---

### 3. 主な構文パターン

#### ① 環境（`@env: |`）
`@環境名: |` と書くだけで、`\begin{環境名}` と `\end{環境名}` に展開されます。

```text
@frame{スライドのタイトル}: |
    ここにスライドの本文を書きます。
    $E = mc^2$ などの数式もそのまま記述可能です。
```

展開後:
```latex
\begin{frame}{スライドのタイトル}
ここにスライドの本文を書きます。
$E = mc^2$ などの数式もそのまま記述可能です。
\end{frame}
```

#### ② リスト項目（`!items:`）
`!items:` を使うと、定型的な `itemize` 環境を簡潔に書けます。Beamer のオーバーレイ指定（`<1->`）や項目ラベル（`[★]`）にも対応しています。

```text
!items:
    -<1-> 最初の項目
    -<2->[★] ラベル付きの2番目の項目
        - ネストした箇条書き
```

展開後:
```latex
\begin{itemize}
\item<1-> 最初の項目
\item<2->[★] ラベル付きの2番目の項目
\begin{itemize}
\item ネストした箇条書き
\end{itemize}
\end{itemize}
```

#### ③ スタック合成（`>>`）
`>>` でセグメントを繋ぐと、多重の環境やコマンドを1行で入れ子にできます。

```text
@center >> \includegraphics[width=0.8\linewidth]{chart.pdf}
```

展開後:
```latex
\begin{center}
\includegraphics[width=0.8\linewidth]{chart.pdf}
\end{center}
```

#### ④ 複数引数を持つコマンド・環境（`:` と `-`）
末尾がコロンのみ（`:`）の場合、各 `-` がコマンドの必須引数 `{...}` になります。

```text
\twoargs:
    - 第1引数
    - 第2引数
```

環境の場合は、最後の `-` が環境の本文（body）になり、それ以前の `-` は環境の引数になります。

```text
@myenv:
    - オプション/第1引数
    - @: |
        環境の本文
```

#### ⑤ リテラル中括弧コンテナ（`@{...}: |`）
文字サイズ変更やフォント・色のスコープを中括弧 `{ ... }` で囲みたい場合は、`@{...}: |` を使用します。

```text
@{\small\color{gray}}: |
    ここだけ文字が小さく、グレーになります。
```

展開後:
```latex
{
\small\color{gray}
ここだけ文字が小さく、グレーになります。
}
```

#### ⑥ ソースマクロ（`!defmacro`）
よく使うレイアウトや構文パターンを、トップレベルでマクロとして定義して再利用できます（文字列置換ではなく AST レベルの安全な構造マクロです）。

```text
!defmacro{alertbox}{title}{body}: |
    @block:
        - \textbf >> !param{title}
        - !param{body}

!alertbox{注意}: |
    これは重要な注意事項です。
```

注:
`@block{\textbf{!param{title}}}` のように `group {...}` の中へ `!param` を書くことはできません（group の中身は raw TeX として不可侵なため）。
引数へ渡すときは上のように `-` で構造的に渡します。

#### ⑦ ビルドフラグ（`!flag` / `!when` / `!unless`）
1つのソースから、配布用・発表用・ドラフトなど**複数のバージョン**を生成できます。フラグは真偽値で、既定値を文書側で宣言し、コンパイル時に上書きします。

```text
!flag{draft}{off}
!flag{handout}{on}

@frame{結果}: |
    !when{draft} >> \marginpar{発表前に測り直す}
    実験の結果は次の通りです。
    !unless{handout}: |
        \pause
        @{\small} >> \textit{ここでテールレイテンシに触れる}
```

既定のままコンパイルすると（`draft` は off、`handout` は on）:
```latex
\begin{frame}{結果}
実験の結果は次の通りです。
\end{frame}
```

`texflux compile ... --flag draft --flag handout=off` とすると:
```latex
\begin{frame}{結果}
\marginpar{発表前に測り直す}
実験の結果は次の通りです。
\pause
{
\small
\textit{ここでテールレイテンシに触れる}
}
\end{frame}
```

注:
- `--flag NAME` は on、`--flag NAME=off` は off です。**宣言されていない名前を指定するとエラー**になるので、タイプミスで中身が黙って消えることはありません。
- 複数フラグは `[and]` / `[or]` でまとめられます（`!when[or]{draft}{internal}`）。2つ以上並べるときはモディファイアが必須です。
- `!unless[X]` は `!when[X]` **全体の否定**です。「どちらでもない」は `!unless[or]{a}{b}`、「両方ではない」は `!unless[and]{a}{b}` になります。
- 畳み込めるのは1段だけで、ネストや括弧、一部のフラグだけの否定はありません。`a かつ b でない` は `!when{a} >> !unless{b}` と合成で書きます。
- off になった側の中身は展開も検査もされません。壊れた箇所を一時的に無効化したままビルドできます。

---

## 🐍 Python API からの利用

Python スクリプト内から TeXFlux のコンパイラを直接呼び出すことも可能です。

```python
from texflux import compile_text

source = """@frame{API サンプル}: |
    TeXFlux は Python からも簡単に実行できます。
"""
tex = compile_text(source, filename="slides.tfx")
print(tex)
```

ビルドフラグは `flags` で渡します（`compile_text(source, flags={"draft": True})`）。

---

## 📁 サンプル集（`examples/`）

リポジトリ内の `examples/` フォルダに、実際のサンプルコードと変換結果（ゴールデンファイル）が用意されています。

- `basic.tfx` : 基本的な環境、生の TeX、`!items`、オーバーレイ
- `structured.tfx` : 複数引数（シーケンス）とリテラル中括弧グループ
- `stacked-items.tfx` : `>>` によるスタック合成とネストしたリスト
- `macros.tfx` : 引数付きマクロ・可変長引数マクロ
- `content.tfx` : 実際の Beamer スライドを TeXFlux に変換した実例

実行例:
```bash
python3 -m texflux compile examples/basic.tfx -o /tmp/basic.tex
```

---

## 📚 詳細仕様

言語仕様の完全な解説については、以下のドキュメントをご参照ください。

- [TeXFlux DSL v1 言語仕様書 (doc/dsl.md)](doc/dsl.md)
- [規範的仕様書 (texflux_tex_first_dsl_v1_spec.md)](texflux_tex_first_dsl_v1_spec.md)（英語）

---

## 開発・テスト

```bash
python3 -m unittest discover
```

