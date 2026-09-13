# TeXFlux External AST Schema v1

## 1. 目的

TeXFlux ASTは、`.tfx` ソースをTeX以外のconsumerから利用するための、安定した外部インタフェースである。

主な利用対象として以下を想定する。

- LaTeX renderer
- Web / HTML presentation runtime
- document renderer
- previewer
- editor tooling
- 将来のその他backend

ASTはTeXFluxの内部Pythonクラスのserializationではなく、独立したバージョン付きデータ形式として定義する。

---

## 2. ASTを出力する段階

外部ASTは **canonical AST** を表す。

処理順序は以下とする。

```text
TFX source
    ↓
parse
    ↓
>> desugaring
    ↓
flag resolution
    ↓
macro/module resolution
    ↓
macro expansion
    ↓
content import expansion
    ↓
special normalization
    ↓
canonical AST
    ↓
JSON serialization
```

したがって外部ASTには以下を含めない。

```text
Stack
SequenceEntry
ParsedInvocation
SpecialInvocation
!defmacro
!param
!each
!flag
!when
!unless
!import
!macroimport
!before
!after
!around
!off
!drop
```

これらはすべてAST生成時点までに解決される。

`!items` 等についても、現在のcanonical ASTと同じく正規化された結果を出力する。

たとえば、

```text
!items::
    -<1-> A
    -<2-> B
```

は `itemize` invocation と `item` nodeとして表現される。

ASTは「ユーザーがどのsyntax sugarを書いたか」を復元することを目的としない。

---

## 3. トップレベル形式

```json
{
  "format": "texflux-ast",
  "version": 1,
  "producer": {
    "name": "texflux",
    "version": "0.1.0"
  },
  "root": 0,
  "sources": [],
  "document": {}
}
```

### `format`

常に

```text
texflux-ast
```

とする。

### `version`

AST Schemaのmajor version。

v1では整数 `1`。

TeXFlux本体のバージョンとは独立する。

### `producer`

ASTを生成した実装。

```json
{
  "name": "texflux",
  "version": "0.1.0"
}
```

consumerはproducer versionをSchema判定に使用してはならない。

### `root`

root `.tfx` に対応するsource ID。

v1では通常 `0`。

---

# 4. Source table

```json
{
  "id": 0,
  "file": "slides.tfx",
  "sha256": "..."
}
```

AST生成時に読み込まれた**すべてのソース**を記録する。

これには、

- root `.tfx`
- `!import` された `.tfx`
- `!macroimport` された `.tfxm`

を含む。

macro moduleがAST nodeを直接生成しなくても、dependency trackingのため `sources` には含める。

`id=0` はroot source。

以降はcompiler sessionにおけるload順とする。

同一ファイルは1回だけsources tableに現れる。

`sha256` は読み込んだ元バイト列のSHA-256をlowercase hexadecimalで格納する。

`file` はdiagnosticsで使用されるdisplay spellingを `/` separatorへ正規化したものとする。

absolute pathであることを要求しない。

---

# 5. Source position

すべてのnodeはsource spanを持つ。

```json
{
  "source": 0,
  "start": {
    "line": 12,
    "column": 5
  },
  "end": {
    "line": 12,
    "column": 17
  }
}
```

位置は以下の規則に従う。

| 項目 | 仕様 |
|---|---|
| line | 1-based |
| column | 1-based |
| 文字単位 | Unicode code point |
| range | half-open `[start, end)` |
| 改行 | LFとして計数 |

source spanは**provenance**であってnode identityではない。

同じmoduleを複数回importした場合、同じspanを持つnodeがAST中に複数存在してよい。

またimportにより、親Blockと子Nodeのsourceが異なっていてもよい。

---

# 6. Block

```json
{
  "type": "block",
  "nodes": [],
  "span": {}
}
```

順序は意味を持ち、source orderを保持する。

空Blockも許可する。

---

# 7. Canonical Node

v1のnode typeは以下の4種類のみとする。

```text
raw
invocation
group
item
```

未知のnode typeを受け取ったconsumerは、そのnodeを黙って無視してはならず、unsupported AST errorとする。

---

## 7.1 `raw`

内部ASTの `RawTex` に対応する。

```json
{
  "type": "raw",
  "text": "Received signal is $y=Hx+n$.",
  "span": {}
}
```

`text` はTeXFluxが解釈しないopaque textである。

1つのraw nodeは原則として1 logical lineを表し、末尾LFを含まない。

空行は、

```json
{
  "type": "raw",
  "text": "",
  "span": {}
}
```

として保持する。

consumerはraw contentをTeX、HTML、Markdown等であると仮定してはならない。

その解釈はconsumer/backend側の責務とする。

---

## 7.2 `invocation`

内部ASTの `GenericInvocation` に対応する。

container/environmentとcommandを統一して表現する。

### Container

```json
{
  "type": "invocation",
  "form": "container",
  "name": "frame",
  "arguments": [],
  "body": {
    "type": "block",
    "nodes": []
  },
  "span": {}
}
```

### Command

```json
{
  "type": "invocation",
  "form": "command",
  "name": "vspace",
  "arguments": [],
  "span": {}
}
```

`form` の意味は次の通り。

```text
container  bodyを持つ
command    bodyを持たない
```

`body: null` は使用せず、commandではbody memberそのものを省略する。

`name` はTeXFluxが正規化した名前をそのまま保存する。

AST Schemaは名前の意味を規定しない。

したがってconsumerは、

```text
frame
columns
itemize
vspace
block
...
```

等について独自のhandler/componentを登録できる。

未知のnameは合法である。

---

# 8. Argument

```json
{
  "kind": "required",
  "layout": "inline",
  "value": {
    "type": "text",
    "text": "System Model"
  },
  "span": {}
}
```

`kind` は以下。

```text
required
optional
overlay
```

`binding` はmodule resolution時に消えるため、canonical external ASTには出現してはならない。

`layout` は現在のcanonical ASTとの完全な対応を保つため、以下を保持する。

```text
inline
block
hugged
explicit
```

Web等のconsumerはlayoutを無視してよい。

### Inline value

```json
{
  "type": "text",
  "text": "System Model"
}
```

### Block value

```json
{
  "type": "block",
  "nodes": [],
  "span": {}
}
```

v1では以下のinvariantを持つ。

```text
layout=inline
    → value.type=text

layout=block|hugged|explicit
    → value.type=block
```

`explicit` is used for a sequence entry written with `+`. Its block value
already contains the authored `{...}`, `[...]`, or `<...>` delimiters; the
consumer must not synthesize a second pair. A `-` sequence entry always uses
`block` or `hugged` and receives a generated required group during rendering.

---

# 9. `group`

内部ASTの `BraceGroup` に対応する。

```json
{
  "type": "group",
  "header": "\\small\\color{gray}",
  "body": {
    "type": "block",
    "nodes": []
  },
  "span": {}
}
```

例えば、

```text
@{\small\color{gray}}:
    text
```

に対応する。

`header` は `{...}` の内部に書かれたopaque raw string。

```text
@{}:
```

では空文字列とする。

TeX以外のconsumerはgroupをtransparent containerとして扱ってもよい。

---

# 10. `item`

内部ASTの `Item` に対応する。

```json
{
  "type": "item",
  "overlay": {
    "text": "2-",
    "span": {}
  },
  "label": null,
  "first_line": "提案手法",
  "continuation": {
    "type": "block",
    "nodes": []
  },
  "span": {}
}
```

`overlay` と `label` は存在しない場合 `null`。

overlay:

```text
-<2-> text
```

label:

```text
-[A] text
```

の情報を保持する。

nested listは `continuation.nodes` 内のinvocationとして表現される。

---

# 11. 完全な概念例

入力:

```text
@frame{System Model}:
    !items::
        -<1-> Signal model
        -<2->[A] Proposed method
```

canonical ASTの概念形:

```json
{
  "format": "texflux-ast",
  "version": 1,
  "producer": {
    "name": "texflux",
    "version": "0.1.0"
  },
  "root": 0,
  "sources": [
    {
      "id": 0,
      "file": "slides.tfx",
      "sha256": "..."
    }
  ],
  "document": {
    "type": "document",
    "body": {
      "type": "block",
      "nodes": [
        {
          "type": "invocation",
          "form": "container",
          "name": "frame",
          "arguments": [
            {
              "kind": "required",
              "layout": "inline",
              "value": {
                "type": "text",
                "text": "System Model"
              },
              "span": {}
            }
          ],
          "body": {
            "type": "block",
            "nodes": [
              {
                "type": "invocation",
                "form": "container",
                "name": "itemize",
                "arguments": [],
                "body": {
                  "type": "block",
                  "nodes": [
                    {
                      "type": "item",
                      "overlay": {
                        "text": "1-",
                        "span": {}
                      },
                      "label": null,
                      "first_line": "Signal model",
                      "continuation": {
                        "type": "block",
                        "nodes": [],
                        "span": {}
                      },
                      "span": {}
                    },
                    {
                      "type": "item",
                      "overlay": {
                        "text": "2-",
                        "span": {}
                      },
                      "label": {
                        "text": "A",
                        "span": {}
                      },
                      "first_line": "Proposed method",
                      "continuation": {
                        "type": "block",
                        "nodes": [],
                        "span": {}
                      },
                      "span": {}
                    }
                  ],
                  "span": {}
                },
                "span": {}
              }
            ],
            "span": {}
          },
          "span": {}
        }
      ],
      "span": {}
    },
    "span": {}
  }
}
```

`{}` と `"..."` は説明用省略であり、実際のASTでは完全なspan/hashを出力する。

---

# 12. Serialization

標準serializationはJSONとする。

```text
UTF-8
BOMなし
LF newline
末尾LF 1個
```

defaultはdeterministic compact JSONとする。

```text
texflux ast slides.tfx -o slides.tfxast.json
```

人間が読む場合のみ、

```text
--pretty
```

を指定できる。

同一入力、同一flag、同一dependencyに対してserialization結果はbyte-for-byte deterministicでなければならない。

---

# 13. CLI

新規command:

```bash
texflux ast INPUT -o OUTPUT
```

compileと同じflag syntaxを使用する。

```bash
texflux ast slides.tfx \
    -o slides.tfxast.json \
    --flag handout \
    --flag draft=off
```

stdout出力:

```bash
texflux ast slides.tfx -o -
```

pretty print:

```bash
texflux ast slides.tfx -o - --pretty
```

重要なのは、`texflux ast` が単純な

```python
normalize(parse(...))
```

を使用してはならないことである。

必ず `CompilationSession` を通し、

```text
!import
!macroimport
flag binding
module-local macro scope
```

を通常compileと同じ意味論で解決する。

---

# 14. Python API

以下をpublic APIとして追加する。

```python
result = compile_ast(
    source,
    filename="slides.tfx",
    flags={...},
    source_bytes=...
)
```

概念的なresult:

```python
AstCompilationResult(
    document=Document(...),
    sources=(LoadedSource(...), ...)
)
```

serialization:

```python
serialize_ast(result, pretty=False) -> str
```

既存の

```python
compile_with_map(...)
```

も内部では同じAST compilation pathを使用し、

```text
compile_ast
    ↓
TeX renderer
    ↓
CompilationResult
```

という構造にする。

---

# 15. Versioning

AST Schema versionとTeXFlux package versionを分離する。

v1 consumerは未知のobject memberを無視しなければならない。

したがって、

```json
{
  "type": "raw",
  "text": "...",
  "future_metadata": {}
}
```

のようなoptional metadata追加はSchema v1の範囲内で可能。

一方、以下はversion bumpを必要とする。

- node typeの意味変更
- required fieldの削除・変更
- position semanticsの変更
- source ID semanticsの変更
- 既存nodeの構造的不互換変更

新しいnode typeを追加する場合も原則major versionを上げる。

unknown node typeをconsumerが黙って無視することは禁止する。

---

# 16. v1で意図的に入れないもの

以下はAST v1に含めない。

```text
node ID
module instance ID
import graph
resolved flag table
macro definition table
HTML semantics
CSS class
DOM tag
presentation-specific state
runtime binding
```

理由は、ASTの責務を

> 「TeXFluxが解決した構造化document」

に限定するためである。

特にnode IDをSourceSpanから生成してはならない。

同じmoduleを複数回importできるため、SourceSpanはnode identityとして一意ではない。

---

# 17. HTML / Slides consumerとの境界

TeXFlux ASTはHTMLを知らない。

例えば、

```json
{
  "type": "invocation",
  "form": "container",
  "name": "frame"
}
```

を、

```text
slide
<section>
<div class="frame">
Canvas scene
Web Component
```

のどれとして扱うかはconsumerが決める。

同様に、

```text
name = columns
name = block
name = itemize
name = vspace
```

の意味もconsumer registry側で定義する。

したがって将来のslide runtimeは、

```text
.tfx
  ↓
TeXFlux
  ↓
texflux-ast v1
  ↓
FluxSlides
  ↓
presentation runtime
```

という依存関係になる。

TeXFluxとFluxSlidesのABIはHTMLではなく、このAST Schemaとする。
