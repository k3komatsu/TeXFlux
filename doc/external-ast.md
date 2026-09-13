# 外部AST出力

`texflux ast`は、マクロ・フラグ・import・specialを解決したcanonical ASTを
`texflux-ast` version 1のJSONとして出力する。HTMLなどへの変換は、このJSONを
読むconsumer側で実装する。TeXFluxはHTMLのタグや名前の意味を決めない。

```bash
texflux ast slides.tfx -o slides.tfxast.json
texflux ast slides.tfx -o - --pretty --flag handout --flag draft=off
```

`--flag`の意味とエラーは`compile`と共通。`-o -`はstdoutへ出力する。
標準出力・ファイル出力ともUTF-8、BOMなし、LF改行、末尾LF 1個。
既定はcompact JSONで、`--pretty`はインデントのみを変更する。
同じ入力・フラグ・依存ソース・ファイル表記では出力バイト列が同一になる。
TeXや`.tfxmap`は生成しない。

## Python API

```python
from pathlib import Path
from texflux import compile_ast, serialize_ast

path = Path("slides.tfx")
data = path.read_bytes()
result = compile_ast(
    data.decode("utf-8"),
    filename=str(path),
    source_bytes=data,
    flags={"handout": True},  # ソースで宣言したフラグのみ
)
Path("slides.tfxast.json").write_bytes(serialize_ast(result).encode("utf-8"))
```

`AstCompilationResult.document`は内部のcanonical `Document`、`sources`は
読み込んだ`LoadedSource`のtuple。`source_bytes`を省略すると、渡した文字列を
UTF-8で符号化したバイト列をハッシュに使用する。CRLFなどを含む元ファイルの
ハッシュが必要なら、上の例のように元バイト列を渡す。
`compile_with_map`も同じ`compile_ast`の結果をTeX rendererへ渡す。

## Consumerが読む形式

機械可読の定義は[JSON Schema](../schemas/texflux-ast-v1.schema.json)
（Draft 2020-12）にある。未知のobject memberを許可し、未知のnode type、
commandの`body`、引数のlayoutとvalueの不一致などを拒否する。
source IDの読み込み順・一意性・参照先の存在、spanの開始と終了の順序、
実ファイルとハッシュの一致はJSON Schemaでは検証しないため、consumer側で確認する。

スキーマのテストは開発用の`jsonschema`をインストールした環境で実行できる。
TeXFluxの実行時依存には追加していない。未インストール時はこのテストのみskipする。

```bash
python -m pip install jsonschema
PYTHONPATH=src python -m unittest tests.test_ast_schema
```

トップレベルは`format`、`version`、`producer`、`root`、`sources`、`document`。
`format`は`texflux-ast`、スキーマの`version`は整数`1`。
`producer.version`はパッケージのバージョンであり、スキーマ判定には使わない。

- `sources`: rootがID `0`、以降はCompilationSessionの読み込み順。
  各要素は`id`、diagnosticsと同じ表記の`file`（区切りは`/`）、元バイト列の
  `sha256`を持つ。出力ノードを生成しないマクロモジュールや空のcontent moduleも
  含む。同じファイルは1回だけ記録し、無効な条件分岐の中で未読のファイルは含めない。
- `document`: `type: "document"`、`body`、`span`を持つ。
- Block: `type: "block"`、順序付き`nodes`、`span`を持ち、空でもよい。
- `raw`: opaqueな`text`と`span`。通常の`\command`や`\item`もrawのまま。
- `invocation`: `name`、`arguments`、`span`を持つ。
  `form: "container"`は`body`を持ち、`form: "command"`では`body`自体を省略する。
  未知の名前も合法。
- `group`: opaqueな`header`、`body`、`span`を持つ。
- 引数: `kind`は`required`・`optional`・`overlay`。
  `layout: "inline"`の`value`は`{"type": "text", "text": "..."}`、
  `block`・`hugged`・`explicit`の`value`はBlock。各引数は`span`も持つ。

`layout: "explicit"` はシーケンスの `+` エントリーから生成される。value の Block には
著者が書いた `{...}`、`[...]`、`<...>` の区切り文字も含まれるため、consumer は別の
区切り文字を追加してはならない。`-` エントリーは `block` または `hugged` となり、
必須グループが生成される。

`span`は`source`（source ID）、`start`、`end`を持つ。位置は1始まりの
`line`・`column`で、Unicode code point単位、LF改行、半開区間`[start, end)`。
import先のノードはimport先の位置を保持する。同じモジュールを複数回importした
ノードは同じspanを持ちうるため、spanを一意なノードIDとして使わない。

文字列補間は解決済みの文字列を出力し、内部のfragment情報は出力しない。
consumerは未知のobject memberを無視し、未知のnode typeはエラーにする。
rawやgroup headerの解釈、HTMLへの変換、名前ごとのhandlerはconsumerの責務となる。

## 外部AST仕様案の旧リスト記述について

`texflux_external_ast_spec.md`の`!items`／`Item`の例は、削除済みの構文を前提にしている。
現行のAGENTS.mdとDSL規範仕様に従い、実装は`!items`を復活させず、`item`ノードも
生成しない。リストは`@itemize`のcontainerと、その中のrawな`\item`行で表現する。
