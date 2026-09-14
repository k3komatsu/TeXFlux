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

## 出力する段階

出力するのは、通常のcompileでrendererに渡すのと同じcanonical ASTである。
すなわち`>>`のdesugar、フラグの解決、マクロ展開、`!import` / `!macroimport`の解決、
値の消費とspecialの展開がすべて終わった後の木で、次のものは出現しない。

- syntax専用ノード: `Stack`、`SequenceEntry`、`ParsedInvocation`、`SpecialInvocation`
- マクロ構文: `!defmacro`、`!param`、`!each`、`!text`
- フラグ構文: `!flag`、`!when`、`!unless`
- モジュール構文: `!import`、`!macroimport`
- 標準フロー制御: `!before`、`!after`、`!around`、`!off`、`!drop`（展開されて消える）

このASTは「ユーザーがどのsyntax sugarを書いたか」を復元することを目的としない。
また、`texflux ast`は必ず`CompilationSession`を通し、`!import` / `!macroimport` /
フラグ束縛 / モジュールごとのマクロスコープを通常のcompileと同じ意味論で解決する。
単純な`normalize(parse(...))`で代用してはならない。

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
`serialize_ast(result, pretty=False)`の`pretty`はCLIの`--pretty`と同じ。
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
`format`は`texflux-ast`、スキーマの`version`は整数`1`、`root`は常に`0`。
`producer.version`はパッケージのバージョンであり、スキーマ判定には使わない。

- `sources`: rootがID `0`、以降はCompilationSessionの読み込み順。
  各要素は`id`、diagnosticsと同じ表記の`file`（区切りは`/`）、元バイト列の
  `sha256`を持つ。出力ノードを生成しないマクロモジュールや空のcontent moduleも
  含む。ただしコンパイラ同梱の標準マクロモジュールはdocument sourceではないため
  含まない（規範仕様 §12.9）。同じファイルは1回だけ記録し、無効な条件分岐の中で
  未読のファイルは含めない。
- `document`: `type: "document"`、`body`、`span`を持つ。
- Block: `type: "block"`、順序付き`nodes`（source orderを保持する）、`span`を持ち、空でもよい。
- `raw`: opaqueな`text`と`span`。1つのraw nodeは原則として1 logical lineを表し、
  末尾LFを含まない。空行は`text: ""`のrawになる。通常の`\command`や`\item`もrawのまま。
  consumerは`text`をTeX・HTML・Markdownのいずれかだと仮定してはならない。
- `invocation`: `name`、`arguments`、`span`を持つ。
  `form: "container"`は`body`を持ち、`form: "command"`では`body`自体を省略する。
  未知の名前も合法。
- `group`: opaqueな`header`、`body`、`span`を持つ。ソースでは`@{\small\color{gray}}:`の
  ようなliteral brace containerから生成され、`@{}:`なら`header`は空文字列である。
  TeX以外のconsumerはgroupをtransparent containerとして扱ってもよい。
- 引数: `kind`は`required`・`optional`・`overlay`。`binding`（`!import`の`(...)`）は
  module resolutionで消えるため、canonical external ASTには出現してはならない。
  `layout: "inline"`の`value`は`{"type": "text", "text": "..."}`、
  `block`・`hugged`・`explicit`の`value`はBlock。各引数は`span`も持つ。
  `layout`はTeXの中括弧の配置を表す情報なので、Web等のconsumerは無視してよい。

`layout: "explicit"` はシーケンスの `+` エントリーから生成される。value の Block には
著者が書いた `{...}`、`[...]`、`<...>` の区切り文字も含まれるため、consumer は別の
区切り文字を追加してはならない。`-` エントリーは `block` または `hugged` となり、
必須グループが生成される。

`span`は`source`（source ID）、`start`、`end`を持つ。位置は1始まりの
`line`・`column`で、Unicode code point単位、LF改行、半開区間`[start, end)`。
import先のノードはimport先の位置を保持するため、親Blockと子Nodeの`source`が
異なっていてもよい。同じモジュールを複数回importした
ノードは同じspanを持ちうるため、spanを一意なノードIDとして使わない。

文字列補間は解決済みの文字列を出力し、内部のfragment情報は出力しない。
consumerは未知のobject memberを無視し、未知のnode typeはエラーにする。
rawやgroup headerの解釈、HTMLへの変換、名前ごとのhandlerはconsumerの責務となる。

## バージョン管理

consumerは未知のobject memberを無視しなければならない。したがって次はv1のまま行える。

- optionalなmemberの追加（メタデータなど）
- `producer.version`の変更

次は`version`のbumpを必要とする。

- node typeの意味変更
- required fieldの削除・変更
- position semanticsの変更
- source ID semanticsの変更
- 既存nodeの構造的な非互換変更
- 新しいnode typeの追加（consumerが未知のtypeをエラーにするため）

## v1に含めないもの

外部ASTは「TeXFluxが解決した構造化document」だけを表す。次は含めない。

- node ID、module instance ID
- import graph、解決済みflag table、macro definition table
- HTML semantics、CSS class、DOM tag、presentation-specificな状態
- runtime binding
