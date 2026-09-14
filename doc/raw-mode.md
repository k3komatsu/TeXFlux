# 行単位 raw mode（`!BEGIN_RAW_MODE` / `!END_RAW_MODE`）実装設計

## 0. ステータス

- 種別: 実装設計書（詳細設計）。**実装済み**。
- 位置づけ: 本書が確定仕様である。実装後、規範定義を
  `texflux_tex_first_dsl_v1_spec.md` §2 に、利用者向け説明を `doc/dsl.md` §3 に
  反映する（反映先の一覧は §5）。
- 前提コミット: `e8cad26`（2026-09-13）。
- 対象: `src/texflux/` の v1 実装。
- 本書の目的は、これだけを読めば実装者が判断を一切追加せずに実装できることである。
  したがって、確定仕様・変更する関数のコード・診断メッセージ全文・テスト一覧・
  検証手順まで確定させてある。却下した代案とその理由も §3.9 に残してある。

## 背景

`!items` の削除（handoff.md §C）で、TeXFlux には「中身を一切走査しない生ブロック」が
一つも無くなった。その穴を行単位で塞いだのが行頭 `!!` / `@@` escape（§D）だが、
verbatim / lstlisting のような**長い生ブロック**では全行に `!` か `@` を足す必要があり、
実用に耐えない。本変更は、行単位の escape を補完する**領域単位の raw mode** を追加する。

`!BEGIN_RAW_MODE` の次の行から `!END_RAW_MODE` の前の行までを、ヘッダースキャンも
構造解釈も補間もせずそのまま生の TeX 行として出力する。

---

## 1. 言語仕様レビュー（提案どおりで問題ないかの判断）

### 1.1 採用してよいと判断した点

- **通常の文書では現在エラーになる綴りを使っている。** `BEGIN_RAW_MODE` は既存の特殊名文法
  （[parser.py:62-63](../src/texflux/parser.py#L62-L63) の `_NAME_START` / `_NAME_CHARS`）に
  適合するため、今日 `!BEGIN_RAW_MODE` と書くと `DirectiveError: unknown special
  directive` になる。リポジトリ全体に `RAW_MODE` の出現は 0 件（grep 確認済み）。
  したがって通常の文書の意味は変えない。ただし、従来この2つの名前でユーザーが
  `!defmacro` を定義していた文書は、raw mode の予約名と衝突するため意図的に
  バリデーションエラーになる。
- **行区切りのマーカーであってスイートではない。** handoff.md §D-3 が `!raw:` を
  却下した理由は「パーサが special 名を決め打ちして suite を raw にする仕組み
  （= 削除した `raw_suite`）の復活」だった。BEGIN/END 形式は suite marker も
  インデント規定の本体も持たないので、`_parse_suite` にも正準 AST にも
  `normalize.py` にも一切触れない。出力は素の `RawTex` のみ。
- **インデントできる。** `!raw{...}` が使えなかった最大の理由（§D-1）は解消される。
  マーカーはブロック基準位置に置かれ、本文はその内側に自由に書ける。

### 1.2 受け入れるべきコストとして明示する点

- **パーサが名前を2つ決め打ちする。** これは `!items` 削除で取り戻した
  「プレフィックスだけで分類する」原則の唯一の例外を、再び作ることを意味する。
  ただし `!items` との差は決定的で、正準 AST のノード種別も `normalize` の
  ハンドラもレンダラの知識も増えない（`!items` は `normalize.py` の 37% を占めていた）。
  コストは `syntax.py` の定数2つと `_block` の1分岐に閉じる。**意図的な例外として
  `AGENTS.md` の v1 境界に記録する**こと。
- **リテラルの `!END_RAW_MODE` 行を領域内の基準位置には書けない。** 領域内には
  escape が存在しないため。終端判定を「BEGIN と同じインデント」に限定することで、
  **別のインデントに書けばリテラルとして出力できる**逃げ道を用意する（§2.4）。
  領域外なら `!!END_RAW_MODE` が使える。この制約は文書化する。
- **`!!` / `@@` は領域内では働かない。** raw は raw なので `!!foo` は `!!foo` のまま
  出力される。周囲のブロックと挙動が変わるので必ず文書化する。

### 1.3 ユーザー決定事項（確認済み）

| 論点 | 決定 |
| --- | --- |
| 領域内のタブ文字 | **許可する。** 現在の全ファイル事前走査による一律禁止を、領域内だけ免除する |
| 領域内の `!text{...}` 補間 | **補間しない（完全 verbatim）** |

### 1.4 こちらで決めた細目

| 論点 | 決定 | 根拠 |
| --- | --- | --- |
| 本文のインデント除去 | 基準位置まで **最大 `base` 個の先頭スペースを除去**する | ブロックスイート内の生 TeX 行（`_emit_raw`）と同じ規則。ここだけ全桁 verbatim にすると不整合 |
| `- !BEGIN_RAW_MODE` | エラー | 「1行に単独でしか存在できない」というユーザー指定 |
| `!BEGIN_RAW_MODE >> @center` | エラー | 同上 |
| 入れ子 | しない。領域内の `!BEGIN_RAW_MODE` はただの本文 | 終端が一意に定まる |
| 未終端 | `ParseError`（BEGIN 行を指す） | 黙って EOF まで飲み込むと、ブロック終了規則を壊した結果が見えなくなる |
| 領域内の空行 | 本文として保存。`_blank_run` の巻き戻し規則は適用しない | verbatim の定義 |

---

## 2. 確定仕様（規範記述）

### 2.1 マーカー

`!BEGIN_RAW_MODE` と `!END_RAW_MODE` は、**その行が（前後のスペースを除いて）
マーカー文字列そのものである場合にのみ**マーカーとして認識される。
群・スイート suffix・`>>`・末尾コメントを伴うことはできない。

`!BEGIN_RAW_MODE` は、他の `!` 行と同じくブロックの基準インデント位置ちょうどに
置かなければならない（ずれていれば既存の `invalid structural indentation`）。

### 2.2 領域

`!BEGIN_RAW_MODE` の**次の物理行から**、対応する `!END_RAW_MODE` の**前の物理行まで**が
領域である。マーカー行自体は出力されない。領域内では以下を**一切行わない**:

- ヘッダースキャン（`{}` の均衡、末尾 `:`、`>>` 継続、`%` コメント禁止のいずれも見ない）
- `@@` / `!!` の escape 展開
- ブロック終了判定（インデントが基準位置より浅くても領域は閉じない）
- 空行の巻き戻し（`_blank_run`）
- 文字列 interpolation（`!text{...}` はリテラル）

### 2.3 出力

領域内の各物理行は、先頭スペースを**最大 `base` 個**取り除いた上で、1行の生 TeX 行
（`RawTex`）として出力される。`base` より浅い行は、その行の先頭スペースがすべて
除かれる。タブおよび行内のスペースは一切変更しない。空行は空の生行になる。

2つのマーカー行は**ノードを1つも生まない**。空行すら出力されない。したがって
`!BEGIN_RAW_MODE` の直後が `!END_RAW_MODE` である空の領域は、ソース2行に対して
出力0行になる（囲みブロックが空になった場合は、空の `:` スイートが何も出さない
という既存規則どおりの結果になる）。マーカーに対して `RawTex("")` を出さないこと。

### 2.4 終端

`!END_RAW_MODE` は、対応する `!BEGIN_RAW_MODE` と**インデントが一致する場合にのみ**
終端として働く。インデントの異なる `!END_RAW_MODE` 行、および領域内の
`!BEGIN_RAW_MODE` 行は、ただの本文である。

### 2.5 タブ

タブ文字は領域の内側でのみ許される。マーカー行を含む領域外のすべての行では
従来どおり `tab characters are not allowed` である。

### 2.6 書ける位置

ブロックが現れるあらゆる位置、すなわち文書のトップレベル、`:` ブロックスイートの中、
シーケンスエントリ（`-` / `+`）の継続行ブロックの中、`!defmacro` のテンプレートの中。

`-` / `+` の payload には書けない。`>>` のセグメントにもなれない。`.tfxm` の
トップレベルに置いた場合は、生成された生行が既存の macro-module purity 検査
[modules.py:355-368](../src/texflux/modules.py#L355-L368) の対象になる。空行・コメント行は
既存規則どおり許可され、それ以外の生行は R07 で拒否される（テンプレートの中は可）。

シーケンスの値を raw 領域にしたい場合は、payload を空にした `-` の継続行に書く:

```text
@itemize::
    -
        !BEGIN_RAW_MODE
        \item !literal
        !END_RAW_MODE
```

`+` の継続行ではグループを開いた後の継続行にも同じ raw mode を置ける。グループの最初の
非空行が基準インデントになり、単独行のマーカーはその基準位置に限って認識される。より
深い位置に置いたマーカー行は `invalid structural indentation` になる。

### 2.7 マクロとの関係

`!defmacro` のテンプレート内に置ける。展開時、領域が生んだ行は補間されず、
span は他のテンプレートリテラル行と同様に**呼び出し位置へ retarget** される
（doc/dsl.md §12.6 の規則を維持する）。`BEGIN_RAW_MODE` / `END_RAW_MODE` は
予約名になり、`!defmacro` で定義できない。

### 2.8 例

```text
@lstlisting[language=Python]:
    !BEGIN_RAW_MODE
    def f(x):
    	return !x        # タブ・不均衡でない任意の文字列
    @foo{bar}            # 構造として解釈されない
    !!baz                # そのまま "!!baz"
      !END_RAW_MODE      # インデントが違うのでリテラル
    !END_RAW_MODE
```

出力:

```tex
\begin{lstlisting}[language=Python]
def f(x):
	return !x        # タブ・不均衡でない任意の文字列
@foo{bar}            # 構造として解釈されない
!!baz                # そのまま "!!baz"
  !END_RAW_MODE      # インデントが違うのでリテラル
\end{lstlisting}
```

---

## 3. 実装詳細設計

変更するのは 4 ファイル（`syntax.py`, `parser.py`, `ast.py`, `macros.py`）。
`normalize.py` / `render.py` / `source_map.py` / `external_ast.py` / `flags.py` /
`modules.py` は**変更しない**。

### 3.1 `src/texflux/syntax.py` — マーカー定数

`parser.py` と `macros.py` の両方から要るが、両者に依存関係が無いので、
すでに双方が import している `syntax.py` に置く。`from typing import Final` を追加。

```python
#: The two whole-line markers that delimit a raw region. The physical-line
#: layer matches them as complete lines before any header scanning, so they
#: are the only two names below the prefix-only classification rule.
RAW_BEGIN_MARKER: Final = "!BEGIN_RAW_MODE"
RAW_END_MARKER: Final = "!END_RAW_MODE"

#: The same two, spelled as special names, for the checks that see a scanned
#: name rather than a line.
RAW_MODE_NAMES: Final = frozenset({RAW_BEGIN_MARKER[1:], RAW_END_MARKER[1:]})
```

`__all__` に 3 つとも追加する。

### 3.2 `src/texflux/ast.py` — `RawTex.verbatim`

[ast.py:146-154](../src/texflux/ast.py#L146-L154) の `RawTex` に第4フィールドを足す:

```python
@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    span: SourceSpan
    parts: SourceText | None = None
    #: A raw-region line, which macro expansion must not interpolate.
    verbatim: bool = False
```

`__post_init__` は変更しない。既定値付きなので既存の全構築箇所と `replace()` は無改修。

> **なぜ `parts` で代用しないか。** `parts` を埋めれば
> [macros.py:407-412](../src/texflux/macros.py#L407-L412) の「既存 fragment は確定済み」
> 経路で補間は確かに止まる。しかし `TextFragment.span` は retarget されないため、
> テンプレート内の raw 行だけが呼び出し位置ではなく定義位置へマップされ、
> doc/dsl.md §12.6 の規則に反する。専用フラグなら `node.span` の retarget が
> そのまま効き、`parts=None` のまま render は `source=node.span, role="content"` を
> 出す ＝ マーカーを含まない通常のテンプレート生行と完全に同じ扱いになる。

### 3.3 `src/texflux/parser.py` — 領域の事前対応付け

`_PhysicalLine`（[parser.py:42-53](../src/texflux/parser.py#L42-L53)）の直後に、
モジュールレベルのヘルパを 3 つ追加する。

```python
def _marker_span(filename: str, line: _PhysicalLine) -> SourceSpan:
    text = line.text.strip(" ")
    start = SourcePosition(line.number, line.indent + 1)
    return SourceSpan(filename, start, SourcePosition(line.number, line.indent + 1 + len(text)))


def _line_marker(line: _PhysicalLine) -> str | None:
    """The raw-mode marker a whole line consists of, or None.

    A marker carries no group, no suite suffix and no comment, so matching the
    entire line is the whole rule.
    """

    text = line.text.strip(" ")
    return text if text in (RAW_BEGIN_MARKER, RAW_END_MARKER) else None


def _scan_raw_regions(
    lines: tuple[_PhysicalLine, ...],
    filename: str,
) -> dict[int, int]:
    """Pair the raw-region markers, as ``begin index -> end index``.

    Pairing is a line-level property, so it is resolved before parsing: the
    tab prohibition has to know which lines are verbatim, and an unpaired
    marker leaves the rest of the file's line structure meaningless.
    """

    regions: dict[int, int] = {}
    begin: int | None = None
    indent = 0
    for index, line in enumerate(lines):
        marker = _line_marker(line)
        if marker is None:
            continue
        if begin is None:
            if marker == RAW_END_MARKER:
                raise ParseError(
                    f"'{RAW_END_MARKER}' has no matching '{RAW_BEGIN_MARKER}'",
                    _marker_span(filename, line),
                )
            begin, indent = index, line.indent
        elif marker == RAW_END_MARKER and line.indent == indent:
            regions[begin] = index
            begin = None
    if begin is not None:
        raise ParseError(
            f"'{RAW_BEGIN_MARKER}' is not closed by '{RAW_END_MARKER}'",
            _marker_span(filename, lines[begin]),
        )
    return regions
```

状態機械の性質（そのまま §2.4 の規則になる）:
- 領域内では `marker == RAW_BEGIN_MARKER` は `elif` に落ちて無視される ＝ 入れ子なし
- 領域内のインデントの違う END も `elif` の条件が偽になり無視される ＝ リテラル

`_block` が BEGIN を受理するのは `line.indent == base` のときだけなので、
記録した `indent` は必ずその領域の `base` に一致する。事前走査とパーサで
領域境界がずれることはない。

### 3.4 `src/texflux/parser.py` — `_Parser.__init__` のタブ検査

[parser.py:435-446](../src/texflux/parser.py#L435-L446) を差し替える。

```python
        self.index = 0
        self.raw_regions = _scan_raw_regions(self.lines, filename)
        verbatim = {
            index
            for begin, end in self.raw_regions.items()
            for index in range(begin + 1, end)
        }
        for index, line in enumerate(self.lines):
            if index in verbatim:
                continue
            tab = line.text.find("\t")
            if tab >= 0:
                raise ParseError(
                    "tab characters are not allowed",
                    SourceSpan(
                        filename,
                        SourcePosition(line.number, tab + 1),
                        SourcePosition(line.number, tab + 2),
                    ),
                )
```

**診断の順序**: 対応付けの失敗はタブ検査より先に出る。未終端の領域はファイル全体の
行構造を無意味にするので、個別の行の違反より上位の階層として扱う。既存テストに
マーカーを含むものは無いので回帰は生じない。

### 3.5 `src/texflux/parser.py` — `_block` での領域の消費

[parser.py:540-542](../src/texflux/parser.py#L540-L542) の `if first in {"@", "!"}:` 分岐を
置き換える。**位置が重要**で、`!!` escape（:520）とインデント検査（:528）の**後ろ**に
置く。こうすると `!!BEGIN_RAW_MODE` はリテラル、位置ずれの `!BEGIN_RAW_MODE` は
従来どおり `invalid structural indentation` になる。

```python
            if first in {"@", "!"}:
                if rest.rstrip(" ") == RAW_BEGIN_MARKER:
                    nodes.extend(self._raw_region(base))
                    continue
                nodes.append(self._directive(line, base))
                continue
```

この分岐に来る時点で `line.indent == base`、つまり `extra == 0` なので
`rest` に先頭スペースは無い。

`_emit_raw`（[parser.py:554-564](../src/texflux/parser.py#L554-L564)）の直後に追加:

```python
    def _raw_region(self, base: int) -> list[Node]:
        """Consume one '!BEGIN_RAW_MODE' region and return its verbatim lines.

        The region's extent was fixed before parsing, so nothing here scans a
        header, honours an escape, or lets a dedent close the enclosing block.
        """

        end = self.raw_regions[self.index]
        nodes: list[Node] = []
        for index in range(self.index + 1, end):
            line = self.lines[index]
            cut = min(base, line.indent)
            text = line.text[cut:]
            nodes.append(
                RawTex(text, self._line_span(line, cut + 1, text), verbatim=True)
            )
        self.index = end + 1
        return nodes
```

`cut = min(base, line.indent)` が §2.3 の「最大 `base` 個の先頭スペースを除去」。
空行 `""` は `indent == 0` なので `RawTex("")` になり、render の
[render.py:245-246](../src/texflux/render.py#L245-L246) が改行のみを出す。

### 3.6 `src/texflux/parser.py` — マーカー単独行の強制

[parser.py:394-396](../src/texflux/parser.py#L394-L396)、`_segment` が special を返す直前に:

```python
        segment_span = self._span(segment_start, position)
        if prefix == "!":
            if name in RAW_MODE_NAMES:
                raise self._error(
                    f"'!{name}' must stand alone on its own line",
                    segment_start,
                )
            return SpecialInvocation(name, tuple(groups), None, segment_span), position
```

ヘッダースキャナは唯一の絞り込み点なので、この1箇所で `- !BEGIN_RAW_MODE`、
`!BEGIN_RAW_MODE >> @center`、`!END_RAW_MODE:`、`!BEGIN_RAW_MODE{x}`、
`>>` 継続行に置かれたマーカーのすべてを拒否できる。単独行のマーカーは
`_block` が先に捕まえるのでここには来ない。

import に `RAW_BEGIN_MARKER`, `RAW_END_MARKER`, `RAW_MODE_NAMES` を追加する
（[parser.py:29](../src/texflux/parser.py#L29) の `from .syntax import is_escaped`）。

### 3.7 `src/texflux/macros.py` — 補間の抑止と予約名

補間の抑止、[macros.py:405-418](../src/texflux/macros.py#L405-L418):

```python
            case RawTex():
                target = self._span(node.span, frame)
                # Existing fragments are final, including literal escaped markers;
                # a raw-region line is verbatim and is never a text field at all.
                parts = node.parts
                if parts is None and not node.verbatim:
                    parts = interpolate(
                        node.text, origin=node.span, target=target, offset=0, lookup=frame,
                    )
                return (replace(
                    node,
                    text=node.text if parts is None else plain_text(parts),
                    span=target,
                    parts=parts,
                ),)
```

予約名、[macros.py:60](../src/texflux/macros.py#L60):

```python
_RESERVED_NAMES: Final = frozenset(Reserved) | CONDITIONAL_NAMES | RAW_MODE_NAMES
```

これで `!defmacro{BEGIN_RAW_MODE}:` が既存の
`'!BEGIN_RAW_MODE' is reserved by TeXFlux`（[macros.py:164](../src/texflux/macros.py#L164)）
で落ちる。`from .syntax import ...` に `RAW_MODE_NAMES` を追加する。

### 3.8 変更しないことの確認

- **`normalize.py`**: `RawTex` は [normalize.py:140-141](../src/texflux/normalize.py#L140-L141)
  で素通りする。`verbatim` は正準 AST に残るが誰も読まない。
- **`render.py`**: `verbatim` 行は `parts is None` なので
  [render.py:185-186](../src/texflux/render.py#L185-L186) の従来経路。出力も `.tfxmap` も
  生の TeX 行と1バイトも変わらない。
- **`external_ast.py`**: `RawTex` は `{"type":"raw","text":...,"span":...}` に平坦化される
  （[external_ast.py:69-70](../src/texflux/external_ast.py#L69-L70)）。スキーマ変更なし。
- **`flags.py`**: `!when` で捨てられる payload の中の領域も、パースは通り、展開も
  正規化もされない。従来どおり。
- **`normalize.py`**: `RawTex` は [normalize.py:140-141](../src/texflux/normalize.py#L140-L141)
  で素通りする。シーケンス値の明示配置は `+` マーカーが構文 AST に記録したグループ種類
  だけで決まり、raw 領域の本文が `{` で始まるかどうかによる推測は行わない。raw 領域を
  `-` の継続ブロックに置いた場合は、通常の `-` と同じく生成必須引数として扱われる。

### 3.9 却下した代案: パース前にプレースホルダへ差し替えて後から代入する

「`!BEGIN_RAW_MODE`〜`!END_RAW_MODE` をパース前に抜き出し、無害なプレースホルダ行に
差し替えて従来どおりパースし、パース後に本文を代入し直す」案を検討し、採らなかった。

**採り入れた部分**: 領域の対応付けをパースより前に確定させる点は正しく、`__init__` の
`_scan_raw_regions`（§3.3）がまさにそれである。採らなかったのは「書き換えたソースを
実体化して後から代入する」段だけである。

**却下の理由**:

1. **AST 書き換えパスが新規に要る。** プレースホルダの `RawTex` は `Block.nodes` の他に
   `ParsedInvocation.suite` / `SpecialInvocation.suite` / `Stack.suite` /
   `SequenceEntry.value` の下にも現れる。AST は `frozen=True, slots=True` なので
   その場更新できず、5 種のノードを再構築する再帰パス（25〜30 行）が必要になる。
   `syntax.walk()` は読み取り専用なので流用できない。`_raw_region`（12 行、行インデックスを
   飛ばすだけ）より大きく、ノード型が増えたときに同期が漏れる種類のコードである。
2. **パイプラインに段が増える。** 現状 `parse(source, filename)` が実ソースを見る唯一の
   入口で、コンパイル順序は doc/dsl.md §14.9 と modules.py:911-960 に規範として
   書かれている。パース前段はコンパイラ初の「parse より前の段」になり、規範記述の
   改訂を伴う。物理行レイヤは `@@`/`!!` escape・タブ禁止・空行 run・CRLF 正規化と
   同種の「構造以前の行分類」をすでに担当しており、raw 領域はその層に属する。
3. **行番号を保つ制約で独立性が失われる。** span を壊さないため領域を1行に畳めず
   1行1プレースホルダになり、さらにプレースホルダを BEGIN と同じインデントに
   置かないと `line.indent < base: break` で囲みブロックを閉じてしまう。結局
   「BEGIN のインデント＝ブロック基準位置」というパーサ側の知識を前処理が
   先読みすることになり、独立した前処理にならない。
4. **代入の同定手段が要る。** テキスト一致で代入するとユーザーがプレースホルダを
   書けてしまうため、行番号で stash するなどの追加規約が必要になる。

**代案の唯一の実質的な利点**はタブ検査に手を触れずに済むことだが、現行設計では
そこは §3.4 の 8 行で、既存の「ファイル内で最初のタブを報告する」順序も保たれる。
`verbatim` による補間抑止（§3.2, §3.7）はどちらの案でも同じく必要で、差は出ない。

---

## 4. 診断一覧

| ID | 条件 | メッセージ | span |
| --- | --- | --- | --- |
| R01 | `!BEGIN_RAW_MODE` に対応する `!END_RAW_MODE` が無い | `'!BEGIN_RAW_MODE' is not closed by '!END_RAW_MODE'` | BEGIN 行のマーカー |
| R02 | 領域外の `!END_RAW_MODE` 単独行 | `'!END_RAW_MODE' has no matching '!BEGIN_RAW_MODE'` | その行のマーカー |
| R03 | マーカーが単独行でない（payload / `>>` / 群 / suffix / コメント付き） | `'!BEGIN_RAW_MODE' must stand alone on its own line` | セグメント先頭 |
| R04 | マーカーが基準インデント位置にない | `invalid structural indentation`（既存） | 行の先頭非空白 |
| R05 | 領域外のタブ | `tab characters are not allowed`（既存） | タブの位置 |
| R06 | `!defmacro{BEGIN_RAW_MODE}` | `'!BEGIN_RAW_MODE' is reserved by TeXFlux`（既存） | 名前の群 |
| R07 | `.tfxm` トップレベルの領域 | `a .tfxm macro module may contain only !defmacro, ...`（既存） | 生行のノード |

いずれも `ParseError` / `ValidationError` / `ModuleError` として既存の
`file:line:column: kind: message` 形式に乗る。

---

## 5. ドキュメント更新

| ファイル | 箇所 | 内容 |
| --- | --- | --- |
| `texflux_tex_first_dsl_v1_spec.md` | §2（:55-72） | raw mode の規範記述を追加。`Tabs are rejected.` を「raw 領域の内側を除く」に修正 |
| `doc/dsl.md` | §3（:49-68） | 同内容の日本語。`@@` / `!!` との使い分け（1行なら escape、領域なら raw mode） |
| `doc/dsl.md` | §12.7 の escape 表（:735-738） | raw 領域内では `!text{...}` が補間されないことを明記 |
| `doc/dsl.md` | §19 診断（:1255-1270） | R01〜R03 を追加 |
| `doc/dsl.md` | §20 EBNF（:1276-1315） | `raw-mode-region ::= "!BEGIN_RAW_MODE" NEWLINE verbatim-line* "!END_RAW_MODE"` を `statement` の選択肢に追加 |
| `doc/string-interpolation.md` | D9 と §3.3 | handoff §D が指示済みの `!!` 追記に加え、raw 領域は補間対象外である旨 |
| `README.md` | チートシート（:243 付近） | 1行 escape と領域 raw mode の2つを併記 |
| `AGENTS.md` | source-of-truth 規則 / v1 境界 / review checklist | **パーサが名前を決め打ちする意図的な例外**として記録。正準 AST・`normalize`・レンダラに raw mode 固有の知識を足さないことを不変条件として明記 |
| `handoff.md` | 新規 §E | 冒頭の表に1行、`!!` 単独では足りなかった理由と本設計の決定を記録 |

`doc/` の日本語は事実が固まってから `ja-doc-polish` にかけてよい（AGENTS.md:13-16）。

---

## 6. テスト計画

### `tests/test_parser.py`
既存の `@@` / `!!` のテスト（:47-50, :128-129）と対にする。
- 領域が verbatim な `RawTex` 列を生み、`verbatim=True` を持つ
- 不均衡な `{`、末尾 `:`、末尾 `>>`、`%` コメント、`@foo{A}`、`!foo` が領域内でそのまま残る
- 領域内の `!!foo` が `!!foo` のまま（escape が効かない）
- 基準位置より浅い行・深い行のインデント除去（`min(base, indent)`）
- 領域内の空行が本文として保存され、末尾の空行が巻き戻されない
- 領域内のタブは通り、領域外のタブは従来どおり落ちる
- 領域内のインデントの違う `!END_RAW_MODE` と `!BEGIN_RAW_MODE` がリテラルになる
- マーカー行が空行すら生まないこと。とくに空の領域がノード0個になり、
  `@center:` の本文がそれだけなら `\begin{center}` と `\end{center}` が
  連続すること
- R01〜R04 を `assertRaisesRegex` で `x.tfx:L:C: parse error` まで固定
- `- !BEGIN_RAW_MODE` と `!BEGIN_RAW_MODE >> @center` が R03 で落ちる

### `tests/test_compile.py`
handoff §D の「なぜ要るか」表の4ケースを、領域版でも書けることを固定する
（`@verbatim` 本文の `!important`、`@lstlisting` の `!! # ...`、散文 `!重要`）。

### `tests/test_macros.py` / `tests/test_interpolation.py`
- テンプレート内の raw 領域の `!text{x}` がリテラルのまま出ること
- その行の span が**呼び出し位置**へ retarget されること（`tests/test_spans.py` か
  `tests/test_sourcemap.py` に置くほうが自然なら移してよい）
- `!defmacro{BEGIN_RAW_MODE}` / `{END_RAW_MODE}` が R06 で落ちること

### `tests/test_modules.py`
`.tfxm` トップレベルの領域では、空行・コメント以外の生行が R07 で落ち、テンプレート内の
領域は通ること。空行・コメントだけのトップレベル領域は既存 purity 規則どおり許可する。

### `tests/golden/raw-mode/`
新規 golden を1件。`tests/test_golden.py:13-34` の明示リストへの登録を忘れないこと。
既存 golden が覆っていない形（AGENTS.md:257-260）だけを入れる:

```text
@lstlisting:
    !BEGIN_RAW_MODE
    def f(x):
    	return !x
    @foo{bar
    !!baz
      !END_RAW_MODE
    !END_RAW_MODE
```

タブ・不均衡な `{`・効かない `!!` escape・リテラル END・インデント除去を1件に収める。

---

## 7. 検証

```bash
cd /Users/komatsu/GoogleDrive/github/beamercraft

# 1. 全スイート
python3 -W error::ResourceWarning -m unittest discover

# 2. 既存出力が1バイトも変わらないこと（examples は test_golden が
#    バイト比較しているが、明示的にも確認する）
for f in examples/*.tfx; do
  PYTHONPATH=src python3 -m texflux compile "$f" -o "/tmp/$(basename "${f%.tfx}").tex" \
    && diff -u "${f%.tfx}.tex" "/tmp/$(basename "${f%.tfx}").tex"
done

# 3. 手で挙動確認（§2.8 の例）
PYTHONPATH=src python3 -m texflux compile /tmp/raw.tfx -o -   # compile は -o にファイルが要るので実際は一時ファイルへ
PYTHONPATH=src python3 -m texflux ast /tmp/raw.tfx -o - --pretty   # RawTex のみになっていること

# 4. 診断の確認（R01〜R03 が file:line:column 付きで出て exit 1）
```

受け入れ条件:
- 既存 `examples/` 6件と全 golden の出力・`.tfxmap` がバイト単位で不変
- 正準 AST に新ノード種別が増えていない（`ParsedInvocation` / `SpecialInvocation` /
  `Stack` がレンダラに到達しない既存の検証も通ること）
- `normalize.py` / `render.py` / `external_ast.py` / `schemas/` に差分が無い

---

## 8. 追補: 行単位 raw escape `!| `（実装済み）

### 8.1 なぜ足したか

本書の raw mode は領域単位なので、**1行だけ**を raw にするには 3 行必要である。
一方、行頭 escape `@@` / `!!` は「先頭の1文字を剥がす」規則なので、`!` / `@` で
始まる行しか救えない。この2つの間に、**`\` で始まる行**という穴が残っていた。

実測（実装前、トップレベル1行）:

| 入力 | 結果 |
| --- | --- |
| `\item Note:` | `ParseError: unexpected token in structural header` |
| `\item 手順:` | 同上 |
| `\textbf{Note}:` | **黙って** `\textbf{Note}{\n}` を出力 |
| `\emph{x} >> \emph{y}` | **黙って** `\emph{x}{\n\emph{y}\n}` を出力 |

同じ穴はシーケンス payload（`- \item Note:` など）にもそのまま空いていた。
`!!` では救えない（`!!\item Note:` は `!\item Note:` にしかならない）ので、
現状の回避策は 3 行の raw mode 領域だけだった。後半2件は**エラーにならず黙って
誤出力する**ため、書き手は踏んだことに気づけない。

`!|` の綴りは、実装前も構造化構文として読まれる位置ではすべて `invalid structural
name` だった（raw mode 領域の内側と `+` グループ本文では従来からただの本文である）。
リポジトリ全体の出現は 0 件だったので、後方互換の破壊は無い。

### 8.2 確定仕様

ブロック基準位置以降の最初の非空白から `!`, `|`, 半角スペース の 3 文字。
`!|` の直後が半角スペースでも行末でもなければ構文エラー。

| 行 | 結果 |
| --- | --- |
| `!\| foo` | raw 行 `foo` |
| `!\|` （行末） | 空の raw 行 |
| `!\|   foo` | raw 行 `  foo` |
| `!\| <TAB>foo` | raw 行 `<TAB>foo` |
| `!\|foo` | エラー L01 |
| `!\|<TAB>foo` | エラー L01 |

処理位置は `@@` / `!!` と同じ（インデント検査より前）なので、基準位置より深い
字下げを保持し、不均衡な `{`・末尾 `:`・末尾 `>>`・`%` を走査しない。
生成するノードは `RawTex(..., verbatim=True)` であり、マクロ展開は補間しない。
文字どおりの `!| ` は `!!| foo` と書く（doubling 規則の自然な帰結で、新しい規則は
要らない）。`-` の payload にも使えるが、payload は escape より前に右側の空白が
除去されるため、そこでは末尾のスペースが保持されない（`@@` / `!!` と同じ）。
`+` の明示グループとその継続ブロックは従来どおり対象外で、`+` の継続ブロックの中の
`!| ` 行はただの本文である。

### 8.3 `@@` / `!!` との関係 — 2 階層になった

| 階層 | 書き方 | 取り除くもの | 補間 | タブ |
| --- | --- | --- | --- | --- |
| escape | `@@` / `!!` | 先頭の1文字 | される | 禁止 |
| raw | `!\| `（1行） | マーカーとスペース1個 | されない | 許可（`+` 本文を除く行頭のみ） |
| raw | `!BEGIN_RAW_MODE`（領域） | マーカー行ごと | されない | 許可 |

補間とタブの両方で `!| ` が BEGIN/END 側に揃うので、「1行 raw mode」という
位置づけが一貫する。`!| ` は**プレフィックス**なので、§1.2 で受け入れた
「パーサが名前を決め打ちする」例外は **2 個のまま増えていない**。

### 8.4 タブ免除の設計（唯一の注意点）

タブ禁止は `_Parser.__init__` のファイル全体事前走査なので、免除対象を
**行レベルの述語**で決める必要がある。`_block` は `line.indent >= base` を保証した
上で先頭スペースを飛ばすため `rest[extra:] == line.text.lstrip(" ")` が常に成り立ち、
「lstrip した行が `!|` で始まるか」は base を知らずに決まる（`_line_marker` と同じ層）。

述語を `"!|"` までとし `"!| "` までにしないのは、`!|<TAB>foo` をタブ診断ではなく
**L01 で落とす**ためである。

物理行の消費者を全て当たった結果、`!|` 行が本文になり得るのに `_block` を通らない
経路は `_raw_sequence_continuation`（`+` の明示グループ継続ブロック）1 つだけだった。
そこは全行 opaque なので `!| ` が escape として働かないまま免除だけ効いてしまう。
この経路でのみ `_reject_tab` を呼び直して塞いでいる。

`- !| ` の payload のタブは**免除しない**。述語を「`-`/`+` とスペースを剥がした
残りが `!|` で始まる」まで広げると、`-` がシーケンスマーカーかどうかは囲む suite が
`::` か `:` かで変わる（`@center:` の中の `- !| x` はただの生の行）ため、escape が
1 つも関わっていない散文行のタブがすり抜ける。失うのは「`- !| ` の行にタブを書く」
ことだけで、必要なら `-` の継続ブロックに書けば行頭 `!| ` になり免除される。

### 8.5 診断

| ID | 条件 | メッセージ |
| --- | --- | --- |
| L01 | `!\|` の直後が半角スペースでも行末でもない | `'!\|' must be followed by one space or end the line` |
| L02 | 行頭以外の `!\|`（`>>` セグメント等） | `invalid structural name`（既存） |
| L03 | `+` の明示グループ継続ブロック内の `!\|` 行のタブ | `tab characters are not allowed`（既存） |

診断の順序: 領域の対応付け（R01/R02）→ タブ禁止（R05）→ L01。`!|` で始まる行は
タブ検査から免除されるので、`!|<TAB>foo` は R05 ではなく L01 になる。

### 8.6 変更したファイル

`src/texflux/syntax.py`（定数 `RAW_LINE_MARKER` 1 個）と `src/texflux/parser.py`
（`_raw_escape_line` / `tab_exempt` / `_reject_tab` / `_raw_line_tail`、`_block` と
`_sequence_entry` の分岐、`_raw_sequence_continuation` の再検査）だけである。
`ast.py` / `macros.py` / `normalize.py` / `render.py` / `external_ast.py` /
`schemas/` は 1 行も変更していない。`RawTex.verbatim` と macros.py の補間抑止は
本書 §3.2 / §3.7 で既に用意されていたものをそのまま使っている。
