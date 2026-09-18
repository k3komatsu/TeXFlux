# TeXFlux サンプル集（examples）

このディレクトリには、TeXFlux（`.tfx`）の機能と記法を網羅したサンプルファイルと、その厳密な変換結果（`.tex` ゴールデンファイル）が用意されています。

すべてのサンプルは、実際の TeXFlux コンパイラによって 1 バイトの狂いもなく `.tex` へとコンパイル・テストされています。

---

## 📂 サンプル一覧

| 入力ファイル (`.tfx`) | 変換結果 (`.tex`) | 主な見どころ・解説 |
| :--- | :--- | :--- |
| [basic.tfx](basic.tfx) | [basic.tex](basic.tex) | **最小限の基本構成**: 生の TeX、`@frame` 環境、`@itemize` による箇条書き、Beamer オーバーレイ指定（`<2->[A]`） |
| [structured.tfx](structured.tfx) | [structured.tex](structured.tex) | **構造化引数と中括弧**: 複数引数（シーケンスモード `:::` の `-` / `+`）、ブロックモード（`::`）、リテラル中括弧コンテナ（`@{...}`） |
| [stacked-items.tfx](stacked-items.tfx) | [stacked-items.tex](stacked-items.tex) | **パイプライン合成とネスト**: `>>` による1行の環境連結と、多重にネストした箇条書きリスト |
| [macros.tfx](macros.tfx) | [macros.tex](macros.tex) | **構造マクロ (`!defmacro`)**: ラッパーマクロ、2引数マクロ、可変長引数（`!each`）マクロの実例 |
| [content.tfx](content.tfx) | [content.tex](content.tex) | **学術発表の実践コード**: 実際の Beamer スライド（1000行超）を TeXFlux に移植した実践サンプル |
| [modules.tfx](modules.tfx) | [modules.tex](modules.tex) | **マルチソース構成 (`!import` / `!macroimport`)**: 1ファイル=1モジュール、フラグ束縛（リテラル・`$転送`）、そして古いスライドと新しいスライドが**別バージョンのマクロライブラリのまま共存**する実例 |

---

## 🚀 サンプルの実行・検証方法

リポジトリのルートディレクトリから、以下のコマンドでコンパイルと差分検証を実行できます。

### 基本サンプルのコンパイル
```bash
./bin/texflux compile examples/basic.tfx -o /tmp/basic.tex
diff -u examples/basic.tex /tmp/basic.tex
```

### マルチソースサンプルのコンパイル
`modules.tfx` は `modules/` 以下の `.tfx` / `.tfxm` を取り込みます。import のパスは
**それを書いたファイルからの相対**で解決されるため、`modules/` ごと別の場所へ持ち運べます。

```bash
./bin/texflux compile examples/modules.tfx -o /tmp/modules.tex
diff -u examples/modules.tex /tmp/modules.tex
```

### 実践スライドサンプルのコンパイル
`content.tfx` は、生の TeX コマンドを透過しつつ、環境の構造化、中括弧グループをフル活用したリアルワールドの実例です。

```bash
./bin/texflux compile examples/content.tfx -o /tmp/content.generated.tex
diff -u examples/content.tex /tmp/content.generated.tex
```
（差分が出力されなければ、ゴールデンファイルと完全に一致しています）
