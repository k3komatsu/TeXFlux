# Beamercraft TeX-first DSL v1 実装計画

## 1. 方針・スコープ・仕様の確定

### v1 の成果物

Python 3.11+、runtime dependency なしの小さな preprocessor とする。

```text
UTF-8 .bmc
  → line/header parser
  → syntax AST
  → recursive normalization + @! expansion
  → canonical AST
  → deterministic TeX renderer
  → UTF-8 .tex
```

提供する操作は次に限定する。

```bash
beamercraft input.bmc -o content.tex
python -m beamercraft input.bmc -o content.tex
```

ライブラリ API は最小限とする。

```python
def compile_text(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
) -> str: ...
```

### non-goals

TeX parser、template 解析、command/environment registry、escaping、argument-count 検査、LaTeX 実行、変数・式・loop・condition・source macro、暗黙 extension discovery、汎用 backend frameworkは実装しない。

### 仕様上の確定・追記

実装開始時、正本仕様へ次の明確化を先に反映する。

| 問題 | 理由 | 確定する規則 | v1 で必要か |
|---|---|---|---|
| long arg の一行例に出力形式の揺れがある | AST と golden output が一意にならない | `@!arg:` は常に block layout、`@!arg{A}` は inline layout | 必須 |
| `@!arg{...}` が現仕様に明記されていない | 今回の設計判断で追加された構文 | direct child 専用。required group をちょうど1個取り、suite は持たない | 必須 |
| items continuation が2 spaces、通常 block が4 spaces | 同じ indentation rule では両方を説明できない | 通常 block と items 内 mini-grammar を分離する | 必須 |
| compact と long form の「同一 AST」 | source location と改行形式まで同一にはできない | 同じ canonical node shape にするが、`Argument.layout` と location は保持する | 必須 |

`@!arg{A}` と `@!arg:` は混在可能で、出現順を保持する。

```text
@foo:
    @!arg{INLINE}
    @!arg:
        BLOCK
```

`@!body{...}` は導入しない。

---

## 2. Architecture・AST・主要 interface

### 最小構成

```text
pyproject.toml
src/beamercraft/
    __init__.py       # compile_text と supported public API
    __main__.py       # python -m beamercraft
    ast.py            # syntax/canonical dataclass
    errors.py         # location と user-facing errors
    parser.py         # physical-line処理 + header scanner + block parser
    normalize.py      # structured form、stack、special registry/items
    render.py         # canonical AST → TeX
    cli.py            # argparse、I/O、exit status
tests/
    unit/
    golden/
    integration/
```

独立 lexer/token stream や `scanner.py` は作らない。TeX を字句解析せず、directive header 一行だけを走査するため、`parser.py` 内の小さな `HeaderScanner` で十分である。built-in が3個だけなので `directives.py` も分けず、registry と handlers は `normalize.py` に置く。

### AST sketch

すべて `frozen=True, slots=True` の dataclass とし、collection は tuple にする。

```python
@dataclass(frozen=True, slots=True)
class SourceLocation:
    file: str
    line: int       # 1-based
    column: int     # 1-based Unicode character column

class GroupKind(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    OVERLAY = "overlay"

class ArgumentLayout(StrEnum):
    INLINE = "inline"
    BLOCK = "block"

@dataclass(frozen=True, slots=True)
class Block:
    nodes: tuple["Node", ...]
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class Argument:
    kind: GroupKind
    value: str | Block
    layout: ArgumentLayout
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class RawTex:
    text: str
    loc: SourceLocation

# parser output only
@dataclass(frozen=True, slots=True)
class ParsedGeneric:
    name: str
    groups: tuple[Argument, ...]   # inline only
    suite: Block | None
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class SpecialInvocation:
    name: str
    groups: tuple[Argument, ...]
    suite: Block | None
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class Stack:
    segments: tuple[ParsedGeneric | SpecialInvocation, ...]
    suite: Block
    loc: SourceLocation

# canonical output
@dataclass(frozen=True, slots=True)
class GenericInvocation:
    name: str
    arguments: tuple[Argument, ...]
    body: Block | None
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class Item:
    overlay: Argument | None
    label: Argument | None
    first_line: str
    continuation: Block
    loc: SourceLocation

@dataclass(frozen=True, slots=True)
class Document:
    body: Block
    loc: SourceLocation
```

Canonical AST に `Stack`、`SpecialInvocation`、`@!arg`、`@!body` は残さない。command/environment の別 class も作らない。

```text
GenericInvocation.body is None     → command
GenericInvocation.body is Block    → environment
```

### 処理 interface

```python
def parse(source: str, filename: str) -> Document: ...

def normalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> Document: ...

def render(
    document: Document,
    *,
    source_comments: bool = False,
) -> str: ...
```

`normalize()` は一つの recursive transform として実装する。内部の論理順序は次のとおり。

1. generic suite の ordinary/structured 判定
2. argument/body の子 block を再帰 normalize
3. special handler dispatch
4. stack の right-to-left desugar
5. canonical node だけが残ったことを検証

---

## 3. Parser・normalization・renderer の確定仕様

### Physical line と indentation

- 入力は UTF-8。CRLF/CR は読み込み時に LF として扱い、出力は LF に統一する。
- TAB は位置を問わず禁止し、最初の TAB の location で `ParseError` にする。
- 通常 structural level は4 ASCII spaces。
- colon suite の logical base indent は親 directive の indent + 4。
- directive は、その block の logical base indent と完全一致しなければならない。
- raw line は logical base 以上なら所属できる。base 分だけ除去し、それを超える spaces は raw TeX として保持する。
- root raw line の先頭 spaces も保持する。
- colon のない directive の直後に、1 level 以上深い nonblank lineが来た場合は「suite には `:` が必要」とする。
- blank run は次の nonblank lineまで保留し、その line が所属する blockへ付ける。これにより dedent 前の空行が誤って内側 argument の末尾へ入らない。
- EOF 直前の実在する blank lineは、その時点の最内 blockへ属する。単なる最終 newline は blank nodeにしない。
- `:` の後は、blank lineを飛ばした次の nonblank lineが suite base 以上でなければエラー。

### raw/directive/escape

logical base を除いた後、最初の non-space が次の場合に分岐する。

- `@@`：先頭の `@` を1個だけ除き、行全体を `RawTex` にする。
- `@`：directive header として走査する。
- その他：TeX を一切解析せず `RawTex` にする。

行途中の `@` は常に raw。directive に見える raw continuation は `@@` で明示する。

### HeaderScanner

header 一行だけを左から右へ走査し、次を認識する。

```text
@name groups
@!name groups
@segment >> segment >> !special:
```

- generic name: `[A-Za-z][A-Za-z0-9_]*\*?`
- `*` は末尾に1個だけ許可する。
- name と直後の group の間には空白を許可しない。
- `>>` は group depth 0 のときだけ separator。
- canonical readability のため `>>` の左右に1文字以上の ASCII spaceを要求する。
- 二番目以降の segment に `@` は許可しない。
- `:` は group depth 0、かつ末尾の non-space character の場合だけ suite marker。
- trailing spaces は無視するが、その他の trailing token はエラー。
- chain は必ず `:` と nonempty suite を持つ。
- compact header は物理一行のみ。

Group scanning は TeX semantics を解析せず、境界発見だけを行う。

- `{...}`：brace depth を数える。
- `[...]`：nested bracket を数え、balanced braces 内の `]` は終端とみなさない。
- `<...>`：non-nesting。最初の unescaped `>` で終了。
- delimiter 直前の連続 backslash が奇数なら escaped delimiter とする。
- `>>`、`:`、spaces、`@` は group 内では単なる文字列。
- unclosed/mismatched group は opener location を含む `ParseError`。
- `%` comment や TeX macro expansion は解釈しない。

### structured long form

generic invocation の direct child に `@!arg` または `@!body` が1つでもあれば structured form とする。direct blank linesは separator として無視する。

許可する direct child は次だけ。

```text
@!arg{INLINE}
@!arg:
    BLOCK
@!body:
    BLOCK
```

規則:

- header compact groups が最初、続いて direct `@!arg` を source order で追加する。
- `@!arg{...}` は required inline argument exactly one。
- `@!arg:` は required block argument。suite は必須で、常に multiline render。
- `@!body:` は0または1個、groupなし、必ず最後。
- `@!body` がなければ command、あれば environment。
- raw text、通常 directive、`@!items`、unknown special の direct 混在は `ValidationError`。
- `@!arg` / `@!body` が generic structured suite の direct child 以外に現れたら `DirectiveError`。
- compact + long の例では `{SHORT}` の後に block argument を追加する。

ordinary suite では、suite 全体を environment body とする。中の special directive は通常どおり展開する。

### stacking

parser は chain を `Stack` として保持し、normalizer が right-to-left に消去する。

```text
@A{x} >> B[y] >> C:
    BODY
```

は canonical AST 上で以下になる。

```text
A(body=[
  B(body=[
    C(body=BODY)
  ])
])
```

- chain 内の全 generic segment は名前知識に関係なく environment。
- 前方 segment の groups は inline environment arguments。
- 最後の generic segment は ordinary suite、または `@!body` を持つ structured suiteを取れる。
- 最後が structured `@!arg` only で command になる場合は、container にならないためエラー。
- v1 の special segment は最後の `!items` だけを許可する。
- `!arg`、`!body`、unknown special、special の中間配置は location 付きエラー。
- `{a >> b}` 内の文字列は scanner depth により separator にならない。

### `@!items` mini-grammar

`@!items:` は nonempty suite 必須、groups 不可。suite は raw lineだけを許可し、generic/special directive node があればエラーとする。

suite base を column 0 とした相対 indentationで解析する。

- list depth `d` は0, 4, 8, ... spaces。
- `d` にある `-` が item marker。`-` の次は end、space、`<`、`[` のいずれか。
- item continuation は `d + 2` spaces以上。最初の `d + 2` を構造用として除去し、それ以上は raw indentationとして保持する。
- `d + 4` に item marker があれば nested itemize。
- nested marker が一度に2 level以上飛ぶ場合はエラー。
- depth `d` の nonblank non-item lineはエラー。
- blank separator は、次の nonblank lineが同一 depth の次 itemなら無視し、同じ item の continuationなら continuation内に保持する。
- item text は raw TeXで、inline macroを解析しない。
- continuation の directive-looking raw lineは `@@` が必要。

prefix は次の順序だけを許可する。

```text
- text
-<overlay> text
-[label] text
-<overlay>[label] text
```

overlay は non-nesting angle scanner、label は bracket scannerを再利用する。label→overlay の逆順、重複 prefix、unclosed group はエラー。empty `\item` は許可する。

展開結果は TeX string ではなく次の canonical AST。

```text
GenericInvocation(name="itemize", body=[
  Item(...),
  Item(..., continuation=[
    GenericInvocation(name="itemize", body=[...])
  ])
])
```

### special directive registry

v1 は内部 registry のみ持つ。

```python
SpecialHandler = Callable[
    [SpecialInvocation, "TransformContext"],
    tuple["NormalizedNode", ...],
]

@dataclass(frozen=True)
class DirectiveSpec:
    handler: SpecialHandler
    stack_terminal: bool = False

class DirectiveRegistry:
    def register(self, name: str, spec: DirectiveSpec) -> None: ...
    def lookup(self, name: str) -> DirectiveSpec: ...
```

- `arg` / `body` は structural metadirective として generic normalizer が消費する。
- `items` handler は `Item` と generic `itemize` environmentへ展開する。
- unknown `@!foo` は `DirectiveError`。
- handler は AST のみ返し、TeX string を返す API は設けない。
- renderer は registry や special names を知らない。

### TeX rendering

生成側の indentation は付けない。raw text に残った追加 indentationだけを出す。最終出力は常に1個の LF で終わる。

| Canonical node | 出力 |
|---|---|
| command + inline args | `\foo<...>[...]{...}` を一行 |
| command + block arg | command prefix と `{`、block、`}` |
| environment | `\begin{name}` + args、body、`\end{name}` |
| consecutive block args | 前 argument の `}` と次の `{` を同じ境界行の `}{` とする |
| item | `\item<...>[...] first_line`、continuation は後続行 |
| blank `RawTex` | 空行 |

例:

```text
@foo{SHORT}:
    @!arg:
        LONG
```

は必ず次になる。

```tex
\foo{SHORT}{
LONG
}
```

`@!arg{LONG}` なら inline argumentとして描画する。

`--source-comments` 有効時は、source-originを持つ各 nonblank `RawTex`、invocation、item の直前に次を出す。

```tex
% beamercraft: slides.bmc:42
```

default は無効。LaTeX error自体の解析・書き換えはしない。

### error model

```python
class BeamercraftError(Exception):
    message: str
    loc: SourceLocation

class ParseError(BeamercraftError): ...
class ValidationError(BeamercraftError): ...
class DirectiveError(BeamercraftError): ...
```

CLI 表示:

```text
slides.bmc:42:9: parse error: unclosed required group
```

切り分け:

- `ParseError`: TAB、indentation、header/name/group/colon/chain syntax
- `ValidationError`: structured suite、body order、stack shape、items structure
- `DirectiveError`: unknown special、不正位置、stack非対応
- LaTeX compile error: Beamercraft の責務外。必要時は source comments で追跡
- 内部バグ以外では traceback を表示しない

---

## 4. CLI・package・将来 extension

### Packaging

- `pyproject.toml` の build backend は setuptools。
- `requires-python = ">=3.11"`。
- console script は `beamercraft = "beamercraft.cli:main"`。
- runtime dependencies は空。
- tests は標準ライブラリ `unittest` を使用する。

### CLI behavior

```python
def main(argv: Sequence[str] | None = None) -> int: ...
```

引数:

```text
beamercraft INPUT -o OUTPUT [--source-comments]
```

- `INPUT` と `OUTPUT` は必須 path。
- input/output が同一 pathなら source破壊防止のため拒否。
- 全 parse/normalize/render が成功してから outputを開く。
- 存在しない親 directory は暗黙作成しない。
- success 0、DSL/I/O failure 1、argparse usage 2。
- diagnostics は stderr。
- stdout mode、watch、LaTeX compile、template optionは v1 に入れない。

### 将来の user extension

v1 では `--extensions`、decorator API、dynamic import を実装しない。ただし内部 registry と canonical AST により、将来は次の明示ロードへ拡張できる。

```bash
beamercraft slides.bmc \
    --extensions ./beamercraft_ext.py \
    -o content.tex
```

将来の extension module contract は概念的に次とする。

```python
def register(registry: DirectiveRegistry) -> None:
    registry.register("result", DirectiveSpec(handler=result))

def result(node: SpecialInvocation, ctx: TransformContext):
    return (
        GenericInvocation(
            name="infobox",
            arguments=(...),
            body=node.suite,
            loc=node.loc,
        ),
    )
```

- explicit path のみロードし、cwd scan、entry-point auto discovery、暗黙 importはしない。
- Python extension が任意コードを実行することは CLI/documentation で明示する。
- source locationは入力 node/bodyから引き継ぐ。
- expansion cycle detection、versioned plugin API、sandboxは実需要が出るまで追加しない。

---

## 5. Test-first 実装順序・完了条件

### Unit tests

表駆動で最低限次を網羅する。

- raw TeX、blank line、structural indent除去、追加 raw indent保持
- `@@`、行途中の `@`、literal directive-looking continuation
- generic unknown command/environment
- `[optional]`、`{required}`、`<overlay>` の順序
- nested braces/brackets、escaped delimiter
- group 内の `>>` と `:` が syntax tokenにならないこと
- `align*`、invalid names、二番目 segment の `@`
- colon missing suite、colonなしの見かけ上の nested suite
- TAB、invalid directive indentation、dedent、location
- ordinary environment と structured command/environment
- `@!arg{...}`、`@!arg:`、両者の混在
- compact header + long arguments
- duplicate/misordered `@!body`
- structured suiteへの raw/generic/special混在
- nested directives inside `@!arg:` と `@!body:`
- stack 1/複数 segment、arguments、final `!items`
- group 内 `a >> b`、special中間配置、arg-only final stack error
- items basic、overlay、label、併用、empty item
- multiline continuation、追加 raw indentation
- nested items、多段 nesting、indent jump
- raw-only boundary、items内 directive rejection、`@@`
- unknown `@!foo`
- source location propagation、source comment rendering
- final LF、CRLF normalization、Unicode column

### Golden tests

`tests/golden/<case>/input.bmc` と `expected.tex` を exact LF byte comparisonする。controlled whitespace normalizationは行わない。

最低限の cases:

1. generic commands/groups
2. ordinary nested environments
3. structured long command
4. structured long environment
5. compact + inline/block `@!arg`
6. three-level stack
7. stack ending in `!items`
8. multiline/nested/overlay/label items
9. 正本の「実スライド風の例」
10. source-comments enabled

### CLI tests

temporary directoryを使い、console entry相当の `main()` と `python -m beamercraft` を検証する。

- successful output
- syntax failureで既存 outputを変更しない
- same input/output rejection
- missing input/parent directory
- UTF-8 Japanese content
- exit codes と stderr format

### Optional LaTeX integration

`shutil.which("pdflatex")` がない場合は skip。存在する場合だけ、生成 contentを最小の Beamer documentへ `\input` し、temporary directory内で `-halt-on-error` compileする。LaTeX は runtime/build dependency にしない。

### 段階的な実装順序

1. 正本仕様へ今回確定した明確化を追記し、golden expectationsを固定。
2. packaging、location/errors、AST dataclassを作り、constructor/equality testsを追加。
3. physical-line処理と HeaderScannerを test-first で実装。
4. indentation block parser、raw/escape、generic/special/stack syntax ASTを実装。
5. structured long form と command/environment normalizationを実装。
6. stack desugaring と validationを実装。
7. internal special registry と `@!items` parser/AST expansionを実装。
8. deterministic TeX renderer と source commentsを実装。
9. golden testsを追加し、正本の全例を照合。
10. CLI、module entry point、I/O safetyを実装。
11. optional LaTeX integration と利用者向け最小 READMEを追加。

### Definition of Done

- `python -m unittest discover` が Python 3.11+ で成功する。
- 全 golden output が完全一致する。
- LaTeX がある環境では integration test が成功し、ない環境では明示的に skipされる。
- wheel metadata上の runtime dependency が0。
- 未登録 `@foo` / `@foo:` が名前 lookupなしで変換される。
- command/environment 判定が suite構造と stack以外を参照しない。
- parser が raw TeX本文、inline macro、templateを解析しない。
- canonical AST から `Stack`、`SpecialInvocation`、`@!arg`、`@!body` が消えている。
- renderer が special registryを参照しない。
- 全 user-facing error が file/line/columnを持つ。
- syntax/validation failure時に outputを作成・破損しない。
- extension loadingや過剰な plugin/backend abstractionを含まない。

### 最終セルフレビュー結果

1. unknown generic TeX: 名前登録不要で通過する。
2. command/environment: source structureのみで決まる。
3. TeX parsing: header delimiter境界以外は行わない。
4. namespaces: `@foo` と `@!foo` は parser/registry上で分離される。
5. arg/body: canonical `Argument` / `body` へ自然に吸収される。
6. stack: syntax-only nodeとして完全に desugarされる。
7. extension: handlerは AST → AST で、location/bodyを保持できる。
8. plugin scope: v1 は内部 registryのみ。
9. complexity: standalone lexer、parser generator、backend frameworkを置かない。
10. coverage: 正本の全機能に unit または golden testが対応する。
