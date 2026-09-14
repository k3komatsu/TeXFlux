# TeXFlux モジュールシステム 詳細設計書

## 0. ステータスと本書の位置づけ

本書は TeXFlux にマルチソースファイル（モジュールシステム）を導入するための**実装設計書**である。
読者は本リポジトリの実装者であり、本書だけを読んで一意に実装できることを目標とする。

前提となる文書:

- `texflux_tex_first_dsl_v1_spec.md` — v1 言語の規範仕様。本書が追加する構文・意味論は、
  実装後にこの規範仕様へ反映されなければならない。
- `AGENTS.md` — 実装上の境界条件。

設計上の主要な判断は §1 に列挙した。

中心的な設計思想は次のとおりである。

> **1 つの `.tfx` ファイルは 1 つの独立したコンテンツモジュールである。**
> 各コンテンツモジュールは、自分の正準 AST が呼び出し側へ挿入される前に、
> 自分自身のフラグとマクロを解決し終えている。
>
> **1 つの `.tfxm` ファイルは 1 つのマクロ定義モジュールである。**
> マクロの取り込みは語彙的（lexical）・私的（private）・非推移的（non-transitive）であり、
> コンテンツを生成しない。

---

## 1. 決定事項

| # | 項目 | 決定 | 理由 |
| --- | --- | --- | --- |
| D1 | フラグ束縛の構文 | **`!import{quiz.tfx}(answers=on)`**（後置 `(...)` リスト）を採用。オプション群を流用する `!import[...]{...}` 形は不採用。 | 実装上の影響は §4 の方式で `!` セグメント末尾に限定できる。 |
| D2 | `.tfxm` の条件式 | `!when` / `!unless` は `.tfxm` ファイル内の**どこであっても**禁止（マクロテンプレート内部を含む）。 | `.tfxm` はフラグを宣言できないため、テンプレート内の条件式は「呼び出し側コンテンツモジュールのフラグ」への暗黙依存になり、マクロ取り込みの語彙性・私的性とキャッシュ健全性が壊れる。 |
| D3 | 「捨てられたペイロードに届く規則」 | 現行の 3 件に **4 件目「`!macroimport` はトップレベル宣言であり `>>` セグメントになれない」** を追加する。`!import` は追加しない。 | `AGENTS.md` は 4 件目の追加に明示的な判断を要求している。§6.4 に判断根拠を記す。 |
| D4 | マクロ再帰検出のキー | `name` から **`(定義元モジュール, name)`** へ変更。 | 別モジュールの同名マクロを誤って再帰と判定しないため。 |
| D5 | `.tfx` ローカルマクロの自己完結性検査 | **行わない**（`.tfxm` のみ）。 | 現行挙動と「壊れた内容を `!when` で無効化できる」保証を守るため。§7.3 参照。 |
| D6 | モジュールパス | **相対パスのみ**。絶対パスは拒否する。`..` は許可。 | 再利用可能なコンポーネントの可搬性。プラットフォーム差の排除。 |
| D7 | `ModuleInstance` のキャッシュ | パース結果（`ModuleSource`）のみキャッシュし、コンパイル結果はキャッシュしない。 | v1 の単純性。キャッシュは意味論を変えてはならず（§10）、パース結果のキャッシュはこれを満たす。 |

---

## 2. スコープと用語

### 2.1 用語

| 用語 | 定義 |
| --- | --- |
| コンテンツモジュール | `.tfx` ファイル 1 つ。TeX 出力を生む。 |
| マクロモジュール | `.tfxm` ファイル 1 つ。マクロ定義のみを提供し、TeX 出力を生まない。 |
| `ModuleSource` | 1 ファイルのロード結果（バイト列・テキスト・パース済み構文 AST）。**正準パス identity でキャッシュされる**。 |
| `ModuleInstance` | 1 回の `!import` に対応する 1 つのコンパイル実体（束縛フラグ・解決済みフラグ・マクロ環境・正準 AST）。**キャッシュしない**。 |
| 正準パス identity | `paths.normalized_path()` の戻り値。symlink・相対要素・大文字小文字を解決した比較可能な 1 つの綴り。 |
| display パス | 診断メッセージと `SourceSpan.file` とソースマップに現れる、人間が読む綴り。§9.2 で定義する。 |
| 公開インタフェース | あるマクロモジュールが**自分自身で `!defmacro` したマクロ**の集合。取り込んだマクロは含まない。 |
| マクロ環境 | あるモジュール内で `!名前` として見えるマクロ名 → `MacroDefinition` の写像。 |

### 2.2 依存の種別

2 種類の依存は決して 1 つに統合しない。

```text
コンテンツ依存        !import{foo.tfx}        コンテンツ（正準 AST）を生む
マクロ名前空間依存    !macroimport{foo.tfxm}  名前空間のみを生む。出力ゼロ
```

---

## 3. ファイル種別

### 3.1 `.tfx`（コンテンツモジュール）

トップレベルに書けるもの:

```text
生 TeX / 構造構文 / !flag / !defmacro / !macroimport / !import / !when / !unless / 通常のコンテンツ
```

`.tfx` は 1 つの独立したモジュールインスタンスとしてコンパイルされる。
そのローカルフラグ・マクロ定義・マクロ取り込みは、取り込み側へ一切漏れない。

### 3.2 `.tfxm`（マクロ定義モジュール）

トップレベルに書けるものは以下の 4 種類のみである。

```text
!defmacro
!macroimport
コメント行（lstrip して '%' で始まる生 TeX 行）
空行
```

ファイル内のどこにあっても禁止されるもの:

```text
!flag   !when   !unless   !import
```

検査の正確な定義は §7.1 に記す。

---

## 4. 字句の追加: `(...)` 束縛リスト

本設計が言語に加える構文追加は**これ 1 つだけ**である。

### 4.1 方針

`(` を汎用のインライン群開始文字にはしない。`(...)` は **`!` プレフィックスのセグメントに
限り、すべてのインライン群の後ろに最大 1 個だけ**置ける末尾リストとして走査する。
これにより、`\command` / `@environment` / 生 TeX の既存挙動は一切変わらない。

ヘッダ走査は次の順序に固定される（`parser.py` の `HeaderScanner._segment`）。

```text
プレフィックス判定 → 名前走査 → インライン群ループ ({ [ <)
                   → 【'!' セグメントのみ】末尾束縛群 1 個 ( ... )
                   → 続く文字は " :>" か行末でなければならない
```

### 4.2 `ast.py` の変更

`GroupKind` に `BINDING` を追加し、区切り表に載せる。ただし
**ヘッダのインライン群ループを駆動する `GROUP_OPENERS` には入れない**。

```python
class GroupKind(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    OVERLAY = "overlay"
    BINDING = "binding"


_GROUP_DELIMITERS: Final = {
    GroupKind.REQUIRED: ("{", "}"),
    GroupKind.OPTIONAL: ("[", "]"),
    GroupKind.OVERLAY: ("<", ">"),
    GroupKind.BINDING: ("(", ")"),
}
_GROUP_OPENERS: Final = {
    opener: kind for kind, (opener, _) in _GROUP_DELIMITERS.items()
}

#: The group kinds a structural header scans after a name, in order.
_INLINE_KINDS: Final = (
    GroupKind.REQUIRED,
    GroupKind.OPTIONAL,
    GroupKind.OVERLAY,
)

#: Every character that can open an inline group, in declaration order.
#: BINDING is deliberately absent: '(' is a trailing list on a '!' segment,
#: not a general inline group, so the header loop must not scan it.
GROUP_OPENERS: Final = "".join(
    _GROUP_DELIMITERS[kind][0] for kind in _INLINE_KINDS
)

#: The opener of the trailing '(...)' binding list.
BINDING_OPENER: Final = _GROUP_DELIMITERS[GroupKind.BINDING][0]
```

`GroupKind.from_opener` は 4 種すべてを引けるため、`HeaderScanner._inline_group` を
そのまま束縛群の走査にも再利用できる。

### 4.3 `scan_group` の `(` ケース

`[` のケースと同形にする。括弧はネストし、釣り合ったブレース群の内側は無視し、
`is_escaped` によるバックスラッシュ・エスケープを尊重する。

```python
case "(":
    # Parentheses nest, but only outside a balanced brace group.
    paren_depth = 1
    brace_depth = 0
    index = start + 1
    while index < len(text):
        char = text[index]
        if not is_escaped(text, index):
            if char == "{":
                brace_depth += 1
            elif char == "}":
                if brace_depth == 0:
                    raise ParseError("mismatched group delimiter", span)
                brace_depth -= 1
            elif brace_depth == 0 and char == "(":
                paren_depth += 1
            elif brace_depth == 0 and char == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    return index + 1, text[start + 1 : index]
        index += 1
    raise ParseError("unclosed binding list", span)
```

### 4.4 `_segment` の変更

インライン群ループの直後に次を挿入する。

```python
binding: Argument | None = None
if prefix == "!" and position < self.end and self.text[position] == BINDING_OPENER:
    binding, position = self._inline_group(position)
    groups.append(binding)

if position < self.end and self.text[position] not in " :>":
    if binding is not None and self.text[position] in GROUP_OPENERS:
        raise self._error(
            "a special's '(...)' list must follow its groups",
            position,
        )
    if binding is not None and self.text[position] == BINDING_OPENER:
        raise self._error(
            "a special accepts at most one '(...)' list",
            position,
        )
    raise self._error(
        "unexpected token after structural name or group",
        position,
    )
```

束縛群は `SpecialInvocation.groups` の**末尾要素**として格納される。
新しい AST ノード型は追加しない。

2 つの特殊メッセージは、いずれも **`binding is not None` で必ず保護する**。
`\` / `@` セグメントは束縛群を走査しないので、そこに `(` が来たときは
従来どおり `"unexpected token after structural name or group"` でなければならない
（`\vspace{1em}(x):` に「特殊は `(...)` を 1 個しか取れない」と言ってはならない）。

### 4.5 影響範囲の確認（実装時に必ず再確認すること）

| 箇所 | 影響 |
| --- | --- |
| `_has_top_level_trailing_colon` ([parser.py:125](../src/texflux/parser.py#L125)) | `GROUP_OPENERS` を走査するので `(` は見ない。生 TeX の判定は不変。 |
| 環境名走査（`"{[<:> "` で切る） | 不変。`@foo(x):` は今も環境名 `foo(x)` のまま。 |
| `\command` セグメント | 不変。`(` が来れば従来どおり `"unexpected token after structural name or group"`。 |
| シーケンス引数の `-` / `+` | `-` は常に生成必須引数、`+` はパーサーが記録した明示グループとして扱われ、束縛群の走査には影響しない。 |
| `render._emit_group` | 不変（§4.7）。 |

### 4.6 後方互換性

現行実装では `!name(` は必ず `ParseError` になる（名前・群の走査後に `(` が来ると
`"unexpected token after structural name or group"`）。したがって
**この追加で意味が変わる既存の正当なプログラムは存在しない**。純粋な追加である。

### 4.7 束縛群を受け取った既存構文の扱い

束縛群はどの `!` セグメントにも字句的には付けられるが、`!import` 以外のすべての既存構文は
**既存コードのまま**これを拒否する。個別対応は不要であり、追加してはならない。

| 構文 | 拒否経路 |
| --- | --- |
| `!when` / `!unless` | 先頭以外の群は `_flag_names` → `demand_text` → `ValidationError` |
| `!flag` | 群数 2 の検査か `demand_text` → `ValidationError` |
| `!defmacro` / `!param` / `!each` | `demand_text` / `_single_name` → `ValidationError` |
| マクロ呼び出し（標準フロー制御の `!before` / `!after` / `!around` / `!off` / `!drop` を含む） | `_values` の `required_text(group) is None` → `MacroExpansionError` |
| `!macroimport` | §7.2 の群検査 → `ModuleError` |

結果として `GroupKind.BINDING` の `Argument` は**正準 AST に到達しない**。
`!import` はこれを消費して自身ごと消え、他はすべてエラーになるからである。
`render.py` は変更しない。

---

## 5. 束縛リストの文法と解析

### 5.1 文法

束縛群の**中身のテキスト**（`scan_group` が返す `(` と `)` の間の文字列）に対する文法。

```text
binding-list  ::= SP* binding (SP* "," SP* binding)* SP*
binding       ::= flag-name SP* "=" SP* binding-value
binding-value ::= "on" | "off" | "$" flag-name
flag-name     ::= [A-Za-z][A-Za-z0-9_-]*
```

- ヘッダは 1 物理行なので、束縛リストに改行は現れない。
- **空リスト `()` および空白のみのリストはエラー**。束縛が不要なら `(...)` 自体を書かない。
- 末尾カンマはエラー（空の要素になるため）。
- `on` / `off` / `$名前` 以外の値はすべて構文エラー。式言語は導入しない。

`flag-name` の正規表現は `flags._FLAG_NAME_RE` と同一にする（重複定義しない）。

### 5.2 データ構造

```python
@dataclass(frozen=True, slots=True)
class FlagBinding:
    """One ``name=value`` entry of an ``!import`` binding list."""

    name: str                 # the callee flag being bound
    literal: bool | None      # True/False for on/off, None when forwarding
    caller: str | None        # the caller flag name for '$name', else None
    span: SourceSpan          # the whole 'name=value' text
    name_span: SourceSpan
    value_span: SourceSpan
```

`literal` と `caller` はちょうど一方が `None` である。

### 5.3 部分 span の算出

束縛群の `Argument.span` は `(` から `)` までを覆う。1 物理行なので行番号は一定であり、
中身のオフセット `offset` に対応する列は次式で求まる。

```python
def _sub_span(argument: Argument, start: int, end: int) -> SourceSpan:
    """The span of ``content[start:end]`` inside one '(...)' group."""

    line = argument.span.start.line
    # +1 skips the '(' the group's own span starts at.
    column = argument.span.start.column + 1
    return SourceSpan(
        argument.span.file,
        SourcePosition(line, column + start),
        SourcePosition(line, column + end),
    )
```

### 5.4 解析アルゴリズム

値の文法にカンマも括弧も現れないので、カンマでの単純分割が一意に正しい。

```python
_BINDING_RE: Final = re.compile(
    r"([A-Za-z][A-Za-z0-9_-]*)[ ]*=[ ]*(on|off|\$[A-Za-z][A-Za-z0-9_-]*)"
)


def parse_bindings(argument: Argument) -> tuple[FlagBinding, ...]:
    text = argument.value            # opaque group content
    if not text.strip(" "):
        raise ModuleError(
            "!import binding list is empty; omit '(...)' instead",
            argument.span,
        )

    bindings: list[FlagBinding] = []
    start = 0
    while True:
        comma = text.find(",", start)
        stop = len(text) if comma < 0 else comma

        chunk_start = start
        chunk_stop = stop
        while chunk_start < chunk_stop and text[chunk_start] == " ":
            chunk_start += 1
        while chunk_stop > chunk_start and text[chunk_stop - 1] == " ":
            chunk_stop -= 1

        chunk = text[chunk_start:chunk_stop]
        match = _BINDING_RE.fullmatch(chunk)
        if match is None:
            raise ModuleError(
                "!import bindings are written 'flag=on', 'flag=off' or "
                f"'flag=$callerFlag'; got '{chunk}'",
                _sub_span(argument, chunk_start, max(chunk_stop, chunk_start + 1)),
            )
        name, value = match.group(1), match.group(2)
        name_stop = chunk_start + len(name)
        value_start = chunk_stop - len(value)
        bindings.append(
            FlagBinding(
                name,
                None if value.startswith("$") else FLAG_VALUES[value],
                value[1:] if value.startswith("$") else None,
                _sub_span(argument, chunk_start, chunk_stop),
                _sub_span(argument, chunk_start, name_stop),
                _sub_span(argument, value_start, chunk_stop),
            )
        )
        if comma < 0:
            return tuple(bindings)
        start = comma + 1
```

`chunk` が空文字列（`(a=on,)` や `(,)`）のときは `fullmatch` が `None` になり、
同じ診断で弾かれる。span が空にならないよう `max(chunk_stop, chunk_start + 1)` とする。

### 5.5 意味論（フラグ束縛の解決）

```python
def bind_import_flags(
    declared: Mapping[str, bool],
    bindings: Sequence[FlagBinding],
    caller_flags: Flags,
    *,
    module: str,          # callee display path, for diagnostics
) -> dict[str, bool]:
```

1. `resolved = dict(declared)` から始める。**束縛のない callee フラグは callee 自身の既定値**。
2. 束縛をソース順に処理する。
   - `binding.name not in declared` →
     `ModuleError("imported module '<module>' does not declare build flag '<name>'; <宣言一覧>", binding.name_span)`
   - 同じ `binding.name` が 2 度目 →
     `ModuleError("build flag '<name>' is bound twice; first bound at <loc>", binding.name_span)`
   - `binding.caller is not None` かつ `binding.caller not in caller_flags` →
     `ModuleError("unknown build flag '<caller>' in this module; <宣言一覧>", binding.value_span)`
   - 値 = `binding.literal` または `caller_flags[binding.caller]`
3. `resolved` を返す。

`<宣言一覧>` は `flags._declared_flags()` と同じ文言を使う。同関数を
`declared_flags_hint(flags)` として公開し、重複実装しない。

**同名フラグの暗黙継承は存在しない**。`answers=$answers` のように
明示的に転送したときだけ伝播する。

### 5.6 例

```text
% quiz.tfx
!flag{answers}{off}

@frame{Question}:
    Question text

    !when{answers}:
        Answer: 42
```

```text
% main.tfx
!flag{answers}{off}

!import{quiz.tfx}                          % callee 既定値 (off)
!import{quiz.tfx}(answers=on)              % リテラル上書き
!import{quiz.tfx}(answers=$answers)        % caller フラグの転送
!import{quiz.tfx}(answers=$answers, memo=off)   % memo が未宣言なら ModuleError
```

---

## 6. コンパイルパイプライン

### 6.1 確定順序

1 つのコンテンツモジュールをコンパイルする手順は以下に固定する。

```text
 1. validate_macro_forms(document)          既存
 2. validate_flag_forms(document)           既存
 3. validate_macroimport_forms(document)    新規  (>> セグメント禁止)
 4. desugar(document)                       既存
 5. collect_flags(document, overrides)      既存  → 宣言済みフラグ
 6. フラグ確定
        ルートモジュール: 手順 5 に --flag 上書きを渡す (FlagError)
        被 import モジュール: 手順 5 は overrides=None、直後に
                              bind_import_flags(...) を適用 (ModuleError)
 7. resolve_macro_imports(document, importer)  新規
        トップレベル !macroimport を剥ぎ取り、取り込みマクロ環境を作る
 8. collect_macros(document, registry, imported=env, module=path)  既存 + 衝突検査
 9. expand_macros(document, macros, flags, environments=..., module=path)
        既存 + 語彙的スコープ
10. resolve_content_imports(document, session, ...)  新規
        !import を被 import モジュールの正準 AST へ置換
11. canonicalize(document, registry)        既存の _normalize_block +
                                            _assert_canonical_block を公開化
```

マクロモジュール（`.tfxm`）の手順は §7.1 に別途定義する。

### 6.2 `normalize()` との関係

`normalize()` は**モジュール機能を持たない従来経路**としてそのまま残す。
実行するのは手順 1・2・4・5・8・9・11 であり、手順 3・6・7・10 は行わない。

`normalize.py` は `modules.py` を import してはならない（循環 import になる）。
代わりに `normalize.py` から次を公開し、`modules.py` が呼ぶ。

```python
def canonicalize(
    document: Document,
    registry: DirectiveRegistry = BUILTIN_DIRECTIVES,
) -> Document:
    """Turn an expanded syntax AST into validated canonical AST."""

    result = Document(_normalize_block(document.body, registry), document.span)
    _assert_canonical_block(result.body)
    return result
```

`normalize()` の末尾はこの呼び出しに置き換える。

### 6.3 正準 AST を構文 AST へ差し込む正当性

手順 10 は**構文 AST 上を歩き、`!import` ノードを被 import モジュールの正準ノード列で
置換する**。手順 11 の `_normalize_node` は `GenericInvocation` / `BraceGroup` を
受理し、`_normalize_canonical` で冪等に再正規化する
（`normalize.py` の `_normalize_node` / `_normalize_canonical`）。
`RawTex` はそのまま通る。したがって差し込みは安全である。

手順 10 の走査器は、**自分が差し込んだ正準ノードを再訪しない**。
置換結果は親のノード列へ `extend` されるだけで、再帰対象にならないからである。
この不変条件を崩す実装（差し込み後にもう一度ブロック全体を歩く等）にしてはならない。

### 6.4 「捨てられたペイロードに届く規則」（D3 の判断根拠）

`AGENTS.md` と規範仕様 §11.5 は、条件分岐で捨てられたペイロードにも届く規則は
**ちょうど 3 つ**だと定めている。

1. ペイロードは構文として解析できなければならない。
2. `!flag` はトップレベル宣言でなければならない。
3. `!defmacro` と `!each` のスタック形式規則。

本設計はここに **4 件目**を追加し、**3 件目を明示的に広げる**。

4. **`!macroimport` はトップレベル宣言であり、`>>` セグメントになれない。**

理由: マクロ環境はマクロ展開（=条件分岐解決）の**前**に確定していなければならない。
`!when{x} >> !macroimport{a.tfxm}` を許すとマクロ名前空間がビルドフラグ依存になり、
語彙的スコープ・非推移性・`.tfxm` キャッシュの健全性がすべて壊れる。`!flag` を
トップレベル限定にしている理由とまったく同じである。

3'. **マクロテンプレートは `!import` / `!macroimport` を含めない**（§7.5.2）。

これは既存の規則 3 と同じ検査地点（`!defmacro` 収集時のテンプレート走査）にある。
現行実装でも「テンプレート内の入れ子 `!defmacro`」は、そのテンプレートが
捨てられる `!when` ペイロードの中にあっても報告される。つまり
**「マクロテンプレートに何を書けるか」は、もともと条件分岐より前に決まる**。
本設計はその既存の集合に 2 つの名前を足すだけであり、新しい検査地点は作らない。

**「壊れた内容を無効化できる」保証への影響**: 3' も 4 も「この行がここに書けるか」という
純粋に構文的な検査であり、**ファイルを 1 つも開かない**。壊れた依存や存在しないファイルを
`!when` で無効化して文書をビルドする用途は、どちらの規則でも維持される。

**「壊れた内容を無効化できる」保証への影響**: 追加される規則は「この 1 行がトップレベルに
あるか」という純粋に構文的な検査であり、ファイルを開かない。トップレベル以外の
`!macroimport` は**形式検査の時点で落ちるので、その参照先ファイルは決して開かれない**。
したがって「壊れた依存を `!when` で無効化して文書をビルドする」用途は維持される。

**`!import` の形式検査は 3 つの規則に追加しない**。suite を取らないこと、`>>` の
ラッパーになれないこと、パスが解決できること、循環がないことは、すべて手順 10、
すなわち条件分岐が解決された**後**に検査する。これにより次が成立する。

```text
!when{appendix}:
    !import{broken-or-missing.tfx}
```

`appendix` が off のとき、このファイルは開かれず、存在しなくてもよい。

---

## 7. `!macroimport`: 語彙的・私的・非推移

### 7.1 マクロモジュールのロード手順

`.tfxm` 1 ファイルに対する処理順序。

```text
 1. parse                                     既存
 2. validate_macro_forms(document)            既存
 3. validate_macroimport_forms(document)      新規
 4. validate_macro_module_purity(document)    新規  (§7.1.1)
 5. desugar(document)                         既存
 6. resolve_macro_imports(document, importer)  新規  → 直接 import 先の一覧
 7. collect_macros(document, registry, module=path)  → own（公開インタフェース）
```

剥ぎ取り後の残存ノード検査は**置かない**（§7.1.2）。
手順 7 までで得られる `own` と直接 import 先の一覧が、§7.4 の 2 フェーズ構築の
フェーズ 1 の成果物である。環境の構築（フェーズ 2）と自己完結性検査（§7.3）は
閉包全体をロードし終えた後に行う。

#### 7.1.1 純粋性検査

パース直後の `Document`（脱糖前）に対して行う。

**(a) トップレベル**: `document.body.nodes` の各ノードは次のいずれかでなければならない。

- `RawTex` で `text.strip()` が `""` または `"%"` で始まる。
- `SpecialInvocation` で `name` が `"defmacro"`。
- `SpecialInvocation` で `name` が `"macroimport"`。

それ以外はエラー。

```text
a .tfxm macro module may contain only !defmacro, !macroimport,
comment lines and blank lines
```

span は当該ノードの `span`。

**(b) ファイル全体**: `syntax.walk(document.body)` を用い、`SpecialInvocation` で
`name` が `flag` / `when` / `unless` / `import` のいずれかであるものを探す。
見つかればエラー（D2）。

```text
'!<name>' is not allowed in a .tfxm macro module
```

#### 7.1.2 残存ノード検査を置かない理由

手順 6・7 で `!macroimport` と `!defmacro` を剥ぎ取った後、
`document.body.nodes` に残るのは空行とコメント行だけである。
これはトップレベル検査 (a) がその 3 種類しか通さないことから自動的に従うので、
独立した検査段は置かない。手順 7 の後、剥ぎ取り済みの `Document` は破棄する。
検査を足すとすれば、それは (a) が不完全だと判明したときであり、
そのときに直すべきなのは (a) の側である。

### 7.2 `resolve_macro_imports`

```python
@dataclass(frozen=True, slots=True)
class MacroImport:
    path: str          # canonical identity
    display: str       # display path
    span: SourceSpan   # the path group, which is what an author would fix


def resolve_macro_imports(
    document: Document,
    importer: str,
) -> tuple[Document, tuple[MacroImport, ...]]:
```

1. `document.body.nodes` を走査し、`SpecialInvocation(name="macroimport")` を剥ぎ取る。
   各ノードに対し:
   - `node.suite is not None` → `ModuleError("!macroimport does not accept a suite", node.span)`
   - 群はちょうど 1 個の必須インライン群でなければならない。
     - 群数が 1 以外 → `ModuleError("!macroimport requires one '{path}' group", node.span)`
     - `GroupKind.BINDING` の群 → `ModuleError("!macroimport does not accept a '(...)' list", group.span)`
     - `required_text(group) is None` → `ModuleError("!macroimport path must be a required '{...}' group", group.span)`
   - パスを §9.1 で解決（種別は `.tfxm`）。
   - 同一正準パスが 2 度目 →
     `ModuleError("macro module '<display>' is already imported at <loc>", group.span)`
2. 剥ぎ取り後の本体を `syntax.walk` で走査し、`macroimport` が残っていれば
   `ModuleError("!macroimport is only valid at the top level", node.span)`。
3. 剥ぎ取り済み `Document` と `MacroImport` のタプル（ソース順）を返す。

`validate_macroimport_forms(document)` は `flags.validate_flag_forms` と同形で、
脱糖前の構文 AST の `syntax.stacks()` を走査し、`!macroimport` が `>>` の
セグメントとして現れたらエラーにする。

```text
!macroimport must be a top-level declaration and cannot be a '>>' segment
```

### 7.3 公開インタフェースと自己完結性

**公開インタフェース**: `.tfxm` が公開するのは**自分が `!defmacro` した
マクロだけ**である。`!macroimport` で得たマクロは私的な実装依存であり、再輸出されない。
`public` / `private` / `export` 構文は導入しない。

**自己完結性検査**: 各マクロモジュールの環境が確定した後、
そのモジュールの `own` に属する各 `MacroDefinition` の `template` を
`syntax.walk` で走査し、すべての `SpecialInvocation` の名前 `n` が
次のいずれかに属することを検査する。

```text
{"param", "each"}  ∪  registry（組み込み特殊）  ∪  そのモジュールの環境
```

属さなければエラー。

```text
'!<n>' is not defined in <display> and is not available through its own
!macroimport
```

span は当該ノードの `span`。呼び出し側の無関係な名前空間が、
本来不正なマクロモジュールを正当化することは決してない。

**この検査は `.tfx` のローカルマクロには適用しない**（D5）。理由は 2 つ。

- 現行の `.tfx` は、未使用マクロのテンプレートに未知の `!名前` があってもエラーにならない。
  適用すると既存挙動が変わる。
- `!when` で無効化されたマクロ定義を含む `.tfx` を壊さないため。

### 7.4 環境の 2 フェーズ構築（循環に強い）

`.tfxm` の循環 import は許容される。以下のアルゴリズムは
**再帰を一切使わない**ので、循環はロード済み集合だけで自然に安全になる。

**フェーズ 1 — 閉包のロード**

走査は `MacroImport`（正準パスと display の両方を持つ）を単位に行う。
`load` は display を受け取り、正準パス identity でキャッシュする。

```python
def _load_macro_modules(self, roots: Sequence[MacroImport]) -> None:
    # 先入れ先出し。記録される import 位置が最も浅い段のものになる（§8.5.1）。
    pending = deque(roots)
    while pending:
        macro_import = pending.popleft()
        if macro_import.path in self._public:
            continue
        self._imported_from.setdefault(macro_import.path, macro_import.span)
        source = self.load(macro_import.display, ModuleKind.MACRO, macro_import.span)
        document, imports = <§7.1 手順 1-6>
        own = <§7.1 手順 7: collect_macros(..., module=source.path) の結果>
        self._public[source.path] = own
        self._imports[source.path] = imports
        pending.extend(imports)
```

`own`（公開インタフェース）と直接 import 先は別々の辞書に置く。
`self._public` にすでに登録済みのパスを即座に読み飛ばすので、
`A.tfxm ↔ B.tfxm` のような循環でもループは必ず停止する。
キューが先入れ先出しであることは §8.5.1 の診断規則が依存しているので、
後入れ先出しに変えてはならない。

**フェーズ 2 — 環境の構築**

各モジュールの環境は `own ∪ ⋃ own(直接 import 先)` であり、
**直接 import 先の `own` にしか依存しない**。`own` はフェーズ 1 で確定済みなので、
フェーズ 2 は再帰しない。循環があっても両方向で同じ結果になる。

フェーズ 1 でロードした各パスのうち、まだ `environments` に無いものについて
結合を 1 回ずつ実行する（`_build_environments`）。すでに構築済みの環境は
（直接 import 先の `own` が不変なので）作り直しても同じ結果になるため、読み飛ばしてよい。

結合そのものは `.tfx` とも共有する自由関数に置く。

```python
def merge_imports(
    base: Mapping[str, MacroDefinition],
    imports: Sequence[MacroImport],
    public: Mapping[str, Mapping[str, MacroDefinition]],
) -> dict[str, MacroDefinition]:
    environment = dict(base)
    for macro_import in imports:                     # source order
        for name, definition in public[macro_import.path].items():  # source order
            if name in environment:
                raise ModuleError(
                    f"macro '!{name}' is already available here, defined at "
                    f"{environment[name].span.location}",
                    macro_import.span,
                )
            environment[name] = definition
    return environment
```

`.tfxm` の環境は `merge_imports(own, self._imports[path], self._public)` である。

衝突診断の span は**取り込み側の `!macroimport` 行**にする。作者が直すべき行だからである。
メッセージは元の定義位置を `file:line:column` で示す。

反復順は `public[...]`（`dict`、ソース順）と `imports`（タプル、ソース順）に依存するので決定的である。

#### 7.4.1 `.tfx` の環境

コンテンツモジュールも同じ `merge_imports` を使う。ただし `own` を先に置けないので、
`base = {}` から取り込み分だけを結合し、ローカル定義は `collect_macros` が後から足す。

```python
# .tfxm:  base = dict(own)  → 取り込み分を結合 → それが環境
# .tfx :  base = {}         → 取り込み分を結合 → collect_macros(imported=それ) が環境を返す
```

したがって結合関数は `merge_imports(base, imports)` として切り出し、両者で共有する。
`.tfx` の「取り込み同士の衝突」は `merge_imports` が M25 で報告し、
「取り込みとローカルの衝突」は `collect_macros` が既存メッセージで報告する（§7.5.2）。

（この文書を書いた時点の話である。その後、標準フロー制御が同梱マクロモジュールに
移り、`base` は両者とも**標準マクロ表から始まる**ようになった——`.tfxm` は
`merge_imports({**standard, **own}, ...)`、`.tfx` は `merge_imports(dict(standard), ...)`
である。標準名との衝突は `merge_imports` ではなく `collect_macros` の新しい
`standard=` 引数が定義位置で報告するので、`.tfx` と `.tfxm` で診断が揃う。
§7.4.2 の環境表にも合成識別子 `texflux:prelude` が登録され、これが同梱マクロの
`frame.macro.module` を必ず解決可能にしている。dsl.md 14.10 節を参照。）

#### 7.4.2 環境表

セッションは `environments: dict[str, MacroEnvironment]` を 1 つだけ持ち、
**ロード済みのすべてのマクロモジュールと、コンパイル中のすべてのコンテンツモジュール**を
正準パスで登録する。`expand_macros` にはこの表をそのまま渡す。
`frame.macro.module` が表に無いことは起こり得ない（定義を持つモジュールは必ず登録済み）。

### 7.5 語彙的スコープ（`macros.py` の変更）

#### 7.5.1 `MacroDefinition`

```python
@dataclass(frozen=True, slots=True)
class MacroDefinition:
    name: str
    parameters: tuple[MacroParameter, ...]
    template: Block
    span: SourceSpan
    #: Canonical path of the module that defines this macro. Its template's
    #: names resolve in that module's environment, never in the caller's.
    module: str = ""
```

display パスが必要な診断では `definition.span.file` を使う。これは定義元 `.tfxm` の
display パスそのものなので、`module` に display を持たせる必要はない。

#### 7.5.2 `collect_macros`

```python
def collect_macros(
    document: Document,
    builtins: Container[str],
    *,
    imported: MacroEnvironment = {},
    module: str = "",
    standard: Container[str] = (),   # 後から追加。§7.4.1 の補足を参照
) -> tuple[Document, dict[str, MacroDefinition]]:
    macros: dict[str, MacroDefinition] = dict(imported)
    ...
```

`macros` を `imported` で初期化するだけで、

- 取り込み名とローカル定義名の衝突（既存の `name in defined` 判定が既存メッセージ
  `macro '!X' is already defined at <loc>` で報告する。`<loc>` は取り込み元の定義位置を指す）
- 戻り値がそのまま「このモジュールの環境（取り込み ∪ ローカル）」になること

の両方が得られる。`_definition` は生成する `MacroDefinition` に `module` を設定する。

`!defmacro` のテンプレート走査（現在は入れ子 `!defmacro` を検出している箇所）に、
`import` と `macroimport` の検出を追加する。

```text
!import is not allowed inside a macro template
!macroimport is not allowed inside a macro template
```

#### 7.5.3 `_Expander`

```python
def expand_macros(
    document: Document,
    macros: Mapping[str, MacroDefinition],
    flags: Flags,
    *,
    environments: Mapping[str, MacroEnvironment] | None = None,
    module: str = "",
) -> Document:
    """``environments`` gives each definition's module its own lexical
    environment; without it every definition resolves in ``macros``."""
```

```python
def _env(self, frame: _Frame | None) -> Mapping[str, MacroDefinition]:
    if self._environments is None:
        return self._macros
    return self._environments[self._module if frame is None else frame.macro.module]
```

`node()` のマクロ呼び出し判定を `case SpecialInvocation(name=name) if name in self._env(frame)`
に変更する。以下はこの変更で自動的に正しくなる。

- テンプレート内に書かれた**引数**は `self.block(node.suite, frame)` により
  **呼び出し側**（現フレーム）の環境で展開される。
- テンプレート本体は `self.block(macro.template, inner)` により
  **定義元**の環境で展開される。
- `!each` は `frame.bound(...)` で同じマクロのフレームを派生させるので環境は不変。
- `!param` は展開済みの値を差し込むだけなので環境に無関係。

したがって `main.tfx` が `A.tfxm` を取り込んでも、`A.tfxm` が私的に使う
`core.tfxm` の `!wrapper` は `main.tfx` からは見えない。

#### 7.5.4 再帰検出（D4）

`_Frame.chain` を `tuple[str, ...]` から `tuple[MacroDefinition, ...]` に変更し、
同一性の判定を `(module, name)` で行う。

```python
key = (macro.module, macro.name)
if any((d.module, d.name) == key for d in outer):
    raise _error("recursive macro expansion detected: " + chain_text(chain), ...)
```

チェーンの表示規則:

```python
def chain_text(chain: tuple[MacroDefinition, ...]) -> str:
    files = {definition.span.file for definition in chain}
    if len(files) <= 1:
        return " -> ".join(definition.name for definition in chain)
    return " -> ".join(
        f"{definition.name}@{definition.span.file}" for definition in chain
    )
```

1 モジュールに閉じた従来のケースは `foo -> bar -> foo` のままであり、
規範仕様 §10.5 と既存テスト（[test_macros.py:304](../tests/test_macros.py#L304),
[test_macros.py:317](../tests/test_macros.py#L317)）の文言は変わらない。

`_Frame.where()` も同じ表示規則を使う。

### 7.6 マクロライブラリのバージョン併存

マクロライブラリのバージョン併存という要件は、以上の設計から自動的に満たされる。

```text
old-slide.tfx:  !macroimport{style-v1.tfxm}
new-slide.tfx:  !macroimport{style-v3.tfxm}

main.tfx:
    !import{old-slide.tfx}
    !import{new-slide.tfx}
```

各コンテンツモジュールは、自分の正準 AST が `main.tfx` に差し込まれる前に
自分のマクロを展開し終えている。`A.tfxm → core-v1.tfxm` と `B.tfxm → core-v2.tfxm` が
同時に使われても、それぞれが私的な語彙的依存なので衝突しない。

---

## 8. `!import`: コンテンツモジュールのインスタンス化

### 8.1 形式規則

`!import` は手順 10（条件分岐解決後）で処理される。検査はすべてこの時点で行う。

| 条件 | 診断 | span |
| --- | --- | --- |
| `node.suite is not None` | `!import produces content: it does not accept a suite and cannot wrap a '>>' payload` | `node.span` |
| 必須インライン群がちょうど 1 個でない | `!import requires exactly one '{path}' group` | `node.span` |
| `[...]` または `<...>` 群がある | `!import does not accept '[...]' or '<...>' groups` | `group.span` |
| 束縛群が空 | `!import binding list is empty; omit '(...)' instead` | `group.span` |

`node.suite is not None` の 1 条件が、次の**両方**を捕捉する。

```text
!import{foo.tfx}:        % 明示的な suite
!import{foo.tfx} >> @center % 脱糖で合成 suite が付く（ラッパー用法）
```

有効な用法:

```text
!import{foo.tfx}                 % valid
@{} >> !import{foo.tfx}          % valid（TeX グループで囲む）
@center >> !import{foo.tfx}      % valid
```

### 8.2 配置規則

`!import` は**トップレベル限定ではない**。ブロック値・シーケンス値・条件分岐ペイロードの
いずれの中でも有効である。

```text
!when{appendix}:
    !import{appendix.tfx}

\twocolumn::
    - !import{left.tfx}
    - !import{right.tfx}
```

禁止されるのは**マクロテンプレート内に書くこと**だけである。
これは `!defmacro` 収集時に検出する（§7.5.2）。コンテンツ依存の発見がマクロ展開に
依存しなくなり、ソース解析とキャッシュが単純に保たれる。

一方、**マクロの引数として渡された `!import` は有効**である。

```text
!defmacro{framed}{body}:
    @frame:
        !param{body}

!framed:
    !import{slide.tfx}
```

`!param{body}` がテンプレート内で 2 回参照されれば、`slide.tfx` は
**2 つの独立したモジュールインスタンス**としてコンパイルされる。

### 8.3 循環検出

コンテンツ import の循環はエラーである。判定は
**アクティブな import スタック**に対して行い、「一度でも見たパスか」では判定しない。

```python
stack: tuple[str, ...]   # canonical paths, root first
```

`target.path in stack` ならエラー。

```text
content import cycle: main.tfx -> a.tfx -> b.tfx -> a.tfx
```

経路は display パスで、スタック順＋対象を連結して表示する。span は `!import` ノード。

同じモジュールを繰り返し import すること自体は合法であり、その都度別インスタンスになる。

```text
!import{slide.tfx}
!import{slide.tfx}      % valid: 2 instances
```

### 8.4 `resolve_content_imports`

```python
def resolve_content_imports(
    document: Document,
    session: "CompilationSession",
    *,
    module: "ModuleSource",   # the importer
    flags: Flags,             # the importer's resolved flags
    stack: tuple[str, ...],   # active content imports, including module.path
) -> Document:
```

走査は `normalize._desugar_tree` と同じ形を取る。

```python
def _block(block: Block) -> Block:
    nodes: list[Node] = []
    for node in block.nodes:
        nodes.extend(_node(node))
    return replace(block, nodes=tuple(nodes))


def _node(node: Node) -> tuple[Node, ...]:
    match node:
        case SpecialInvocation(name="import"):
            return _expand(node)
        case ParsedInvocation() | SpecialInvocation():
            return (
                replace(
                    node,
                    groups=tuple(_argument(g) for g in node.groups),
                    suite=None if node.suite is None else _block(node.suite),
                ),
            )
        case SequenceEntry():
            return (replace(node, value=_block(node.value)),)
        case _:
            return (node,)
```

この時点で `Stack` は残っていない（手順 4 で脱糖済み）。`_expand` が返す正準ノードは
親のノード列へ `extend` されるだけで再訪されない（§6.3）。

`_expand(node)` の手順:

1. §8.1 の形式検査。
2. 群の仕分け（必須インライン群 1 個 → パス、`BINDING` 群 → 束縛）。
3. `demand_text(path_group, "!import path")` でパス文字列を取得し、§9.1 で解決する。
4. §8.3 の循環検査。
5. 束縛群があれば §5.4 で解析する。
6. `session.load(...)`、循環検査、`session.compile_content(...)` を
   **1 つの `try` の中**で行う（§8.5）。ロードは対象をパースするので、
   callee のパースエラーも validation エラーと同じように連鎖に載らなければならない。
7. 戻り値 `Document` の `body.nodes` を返す。

### 8.5 import チェーンの診断

被 import モジュールのコンパイル中に起きた `TeXFluxError` は、
**span を callee のまま保持**し、メッセージ末尾に import 元を追記して同型で再送出する。

```python
try:
    instance = session.compile_content(target, ...)
except TeXFluxError as error:
    # A binding is written at the import site and its span says so, so only
    # an error that came from the callee names this site.
    if error.span.file == node.span.file:
        raise
    raise type(error)(
        f"{error.message}; imported from {node.span.location}",
        error.span,
    ) from error
```

被 import モジュールは呼び出し元と必ず別ファイルである（同一なら循環検出が先に落とす）。
したがって「span のファイルが import 行のファイルと同じ」エラーは、callee 由来ではなく
**呼び出し側の束縛リストのエラー**だけである。そこに `imported from` を付けても
同じ位置を 2 度言うだけなので付けない。深い連鎖では、内側で付かなかったエラーも
1 段上では別ファイルになるため、そこから正しく積み上がる。

- すべての `TeXFluxError` 派生は `(message, span)` シグネチャを持つので `type(error)(...)` が使える。
- span を書き換えないので、エディタの逆引きは**実際に壊れている行**へ飛ぶ
  （呼び出し側の `!import` 行へ付け替えない）。
- 入れ子の import では、内側から順に接尾辞が積み上がる。

```text
c.tfx:4:16: validation error: duplicate macro parameter 'x';
imported from b.tfx:3:1; imported from main.tfx:7:1
```

#### 8.5.1 マクロ import の連鎖

同じ規則を `!macroimport` にも適用する。`.tfxm` は複数の `.tfx` から共有されるため、
「どの `!macroimport` がそのモジュールを引き込んだか」は作者が最初に見るべき情報である。

セッションは、各マクロモジュールを**最初に名指しした `!macroimport` のパス群 span** を
`imported_from: dict[str, SourceSpan]` に記録する。各モジュールの記録は 1 つだけなので、
この記録の集合は**木**であり、根は `.tfx` に書かれた `!macroimport` である
（`.tfx` は `imported_from` に登録されない）。マクロモジュール由来のエラー
（パース・純粋性・自己完結性・名前衝突・パス解決・ロード失敗）には、
**この木を根まで辿って各段の site を追記する**。

- 対象は `_load_macro_modules` のモジュール 1 件分の処理と、
  `_build_environments` の環境構築（M25）・自己完結性検査（M26）。
- **1 段ごとに** `error.span.file == site.file` を判定し、等しい段だけ読み飛ばす。
  そこで**打ち切ってはならない**。ロード失敗（M06）やパス解決の失敗は
  span が `!macroimport` を書いた側にあるので、その段は読み飛ばされるが、
  その 1 つ上の段——そのファイル自身がどう取り込まれたか——は依然として必要である。
  ここで打ち切ると、入れ子の `.tfxm` にある壊れた `!macroimport` が
  文脈を完全に失う。
- 木を辿るので**必ず停止する**。念のため訪問済み集合も持つ。
- 1 つのモジュールが複数箇所から取り込まれている場合、記録されるのは
  **幅優先探索で最初に到達した 1 箇所**、すなわち最も浅い段のものである。
  同じ深さなら発見順になる。深さをまたぐと「ソース順で先」とは限らない点に注意
  （`main.tfx` の 2 行目が `a.tfxm` の 1 行目に勝つ）。
  そのため `_load_macro_modules` の作業キューは後入れ先出しではなく
  **先入れ先出し**（`deque.popleft`）でなければならない。
- エラーが**ルートモジュール自身**に書かれた `!macroimport` 行で起きた場合、
  そのモジュールは `imported_from` を持たないので何も付かない。
  ルートの `!macroimport` が指す先が壊れている場合は、そのモジュールに site があるので付く。

したがって `main.tfx → mid.tfx →(macroimport) impure.tfxm` は次のようになる。

```text
impure.tfxm:2:5: module error: '!nosuchmacro' is not defined in impure.tfxm
and is not available through its own !macroimport;
imported from mid.tfx:1:13; imported from main.tfx:1:1
```

内側がマクロ import の site、外側がコンテンツ import の site である。

この try/except は**手順 6 だけ**を包む。手順 1〜5（import 側のエラー）は包まない。
手順 6 にはロードと循環検査も含める。ロードは対象ファイルをパースするので、
これを外に出すと callee のパースエラーが連鎖を 1 段取りこぼす。
ロード自体の失敗（M06）とパス解決の失敗は span が import 側にあるので、
上の `error.span.file` 判定によって自動的に接尾辞が付かない。

### 8.6 生 TeX とグルーピング

TeXFlux は被 import モジュールの生 TeX を検証しない。
`\newcommand` などの生 TeX マクロは TeXFlux の名前空間システムの外にある。
TeX レベルのグルーピングが必要なら、既存の構造構文で明示的に書く。

```text
@{} >> !import{legacy-slide.tfx}
```

import 専用のグルーピングオプションは追加しない。

---

## 9. パス解決とモジュール識別

### 9.1 解決規則

コンテンツ import もマクロ import も、**import を書いたファイルのディレクトリを基準に**
解決する。親がどこから取り込んだかには依存しない。

```python
class ModuleKind(StrEnum):
    CONTENT = ".tfx"
    MACRO = ".tfxm"


def resolve_module_path(
    importer: str,
    written: str,
    kind: ModuleKind,
    span: SourceSpan,
) -> str:
    """Return the display path of one import. The canonical identity is
    ``normalized_path()`` of it, which the caller derives when it needs one."""
```

検査順:

1. `written` が空 → `ModuleError("module path must not be empty", span)`
1'. `"\0" in written` →
   `ModuleError("module path must not contain a NUL character", span)`
   NUL を含むパスは OS が stat すら拒否して `OSError` ではなく `ValueError` を投げる。
   ファイルシステムに触る前にここで弾くことで、`!import` と `!macroimport` の
   どちらから来ても span 付きの `ModuleError` になる。
2. `"\\" in written` → `ModuleError("module paths use '/' separators", span)`
3. `os.path.isabs(written.replace("/", os.sep))` →
   `ModuleError("module paths must be relative to the importing file", span)`（D6）
4. `not written.endswith(kind.value)` →
   `ModuleError("!<construct> requires a '<拡張子>' module; got '<written>'", span)`
   （`<construct>` は `import` / `macroimport`）
5. display の算出（§9.2）。
6. 読み込みに失敗 → `ModuleError("cannot read module '<display>': <OS の理由>", span)`
   （存在検査は読み込みに統合する。`OSError`・`UnicodeError`・`ValueError` の 3 つを
   ここで捕まえ、パスの正規化も同じ `try` の中に入れる。`ValueError` は手順 1' で
   先に弾いているので二重防御である）

なお `compile_with_map(filename=...)` に渡された**ルートのファイル名**は文書中のパスでは
なく呼び出し側の引数なので、ここでは扱わない。壊れたファイル名は `ValueError` のまま
呼び出し元へ返る（CLI はこれを捕捉する）。ビルド構成の誤りに span がない点で
`FlagError` と同じ扱いである。
7. identity = `paths.normalized_path(display)`。

`..` は許可する。`written` は `.strip()` 済みの群テキストを使う（`demand_text` が行う）。

### 9.2 display パス

```python
display = os.path.normpath(
    os.path.join(os.path.dirname(importer), *written.split("/"))
)
```

- ルートモジュールの display は `compile_with_map(filename=...)` に渡された綴り
  （CLI では `str(input_path)`）。
- `display` は `SourceSpan.file` に入り、診断・`% texflux:` コメント・
  `.tfxmap` の `sources[].path` に現れる。
- 同じ identity に異なる display が到達した場合、**最初にロードした display が採用される**
  （パース結果が identity でキャッシュされ、その span は最初の display を持つため）。
  ロード順は決定的なので、採用される display も決定的である。ただし
  「最初」が読み順で最初とは限らない。コンテンツ import はソース順の深さ優先だが、
  マクロ import は幅優先であり、最も浅い段が先にロードされる（§8.5.1）。

### 9.3 モジュール識別の用途

正準パス identity は次の 4 つに使う。

```text
パースキャッシュ
マクロモジュールのグラフ走査
コンテンツ import の循環検出
依存関係の把握
```

---

## 10. データ構造とセッション API

### 10.1 `modules.py`（新規）

```python
class ModuleKind(StrEnum):
    CONTENT = ".tfx"
    MACRO = ".tfxm"


@dataclass(frozen=True, slots=True)
class ModuleSource:
    """One loaded file, cached by canonical path identity."""

    path: str            # canonical identity
    display: str         # spelling used in spans and diagnostics
    kind: ModuleKind
    data: bytes          # file bytes, for the source map's sha256
    document: Document   # parsed syntax AST, before desugaring


@dataclass(frozen=True, slots=True)
class LoadedSource:
    """One source file the compilation read, for the source map."""

    file: str    # the SourceSpan.file spelling
    path: str    # absolute filesystem path
    data: bytes


MacroEnvironment: TypeAlias = Mapping[str, MacroDefinition]


class ModuleError(TeXFluxError):
    kind = "module"
```

`ModuleError` は `errors.py` に置き、`modules.py` から re-export する。

### 10.2 `CompilationSession`

```python
class CompilationSession:
    """One compilation: source cache, macro environments, loaded sources."""

    def __init__(self, registry: DirectiveRegistry = BUILTIN_DIRECTIVES) -> None: ...

    # -- sources ------------------------------------------------------------
    def load(
        self, display: str, kind: ModuleKind, span: SourceSpan
    ) -> ModuleSource:
        """Read and parse one file, caching it by canonical path identity.
        A failure is a ModuleError blaming ``span``."""

    def loaded(self) -> tuple[LoadedSource, ...]:
        """Every source read, root first, then in load order."""

    # -- macro modules ------------------------------------------------------
    def display(self, path: str) -> str:
        """The display spelling of one loaded module."""

    # -- content modules ----------------------------------------------------
    def compile_root(
        self,
        text: str,
        *,
        filename: str,
        data: bytes,
        flags: Flags | None = None,
    ) -> Document: ...

    def compile_content(
        self,
        source: ModuleSource,
        *,
        bindings: Sequence[FlagBinding],
        caller_flags: Flags,
        stack: tuple[str, ...],
    ) -> Document: ...
```

- `load` は identity でキャッシュする。同じ identity を別 `kind` で要求されることはない
  （拡張子が種別を決めるため）。
- `loaded()` はソースマップ用。**ルートを必ず先頭**に、以降はロード順。
- セッションは 1 回のコンパイルに閉じる。プロセスを跨いで再利用しない。
- キャッシュは意味論を変えてはならない。`ModuleInstance` はキャッシュしない（D7）。

### 10.3 `render.CompilationResult`

```python
@dataclass(frozen=True, slots=True)
class CompilationResult:
    text: str
    rendered: RenderedDocument
    sources: tuple[LoadedSource, ...] = ()
```

`LoadedSource` は `render.py` からは見えないので、循環を避けるため
`LoadedSource` は `paths.py` か新規の軽量モジュールに置くか、
`CompilationResult` を `modules.py` へ移す。**推奨は `LoadedSource` を `render.py` に置く**
（`SourceSpan` と同じく、レンダリング結果に付随するメタデータだから）。
`modules.py` はそれを import する。

### 10.4 公開 API

```python
def compile_with_map(
    source: str,
    *,
    filename: str = "<string>",
    source_comments: bool = False,
    flags: Flags | None = None,
    source_bytes: bytes | None = None,
) -> CompilationResult:
    session = CompilationSession()
    document = session.compile_root(
        source,
        filename=filename,
        data=source.encode("utf-8") if source_bytes is None else source_bytes,
        flags=flags,
    )
    rendered = render_with_provenance(document, source_comments=source_comments)
    return CompilationResult(rendered.text, rendered, session.loaded())
```

- `source_bytes` は**ルートモジュールのハッシュを実ファイルのバイト列と一致させる**ために
  追加する。CLI は必ず渡す。被 import モジュールはセッションが自分でバイト列を読むので常に正確。
- import の基準ディレクトリは `os.path.dirname(filename)`。`filename="<string>"` なら
  カレントディレクトリ基準になる（現行の `.tfxmap` の挙動と同じで、特別扱いはしない）。
- `compile_text` は変更しない。

### 10.5 `normalize()` 直呼びのガード

`BUILTIN_DIRECTIVES` に 2 件のガードハンドラを登録する。

```python
def _module_guard(name: str) -> SpecialHandler:
    def handler(node: SpecialInvocation, _registry: DirectiveRegistry):
        raise ModuleError(
            f"'!{name}' requires module compilation; use "
            "texflux.compile_with_map or the texflux CLI",
            node.span,
        )
    return handler


BUILTIN_DIRECTIVES: Final[DirectiveRegistry] = {
    "import": _module_guard("import"),
    "macroimport": _module_guard("macroimport"),
}
```

（この文書を書いた時点では `drop` / `off` / `vpad` のハンドラも並んでいた。
その後 `!before` / `!after` / `!around` / `!off` / `!drop` は同梱マクロ
モジュールの普通のソースマクロになり、`!vpad` は削除されたので、レジストリに
残るのはモジュール構文の 2 つだけである。dsl.md 11.1 節と 14.10 節を参照。）

副次効果として `import` と `macroimport` が**マクロ名として予約される**
（`_definition` の `name in builtins` 検査）。これは組み込み特殊名との衝突をエラーとする方針と一致する。

モジュール経路では `!macroimport` は手順 7 で、`!import` は手順 10 で取り除かれるので、
このハンドラに到達することはない。

---

## 11. ソースマップの複数ソース化

### 11.1 現状

`serialize_source_map` は単一ソース固定で、
`sources` に `id: 0` を 1 件だけ書き、すべての span の `file` が `source_path` と
一致することを検証する（[source_map.py:64-110](../src/texflux/source_map.py#L64-L110)）。

一方 `remap.py` の `load_source_map` と `_allocate_targets` は
**すでに複数ソースに対応済み**である
（[remap.py:190-211](../src/texflux/remap.py#L190-L211),
[remap.py:363-386](../src/texflux/remap.py#L363-L386)）。
`remap.py` は変更しない。

### 11.2 新しいシグネチャ

```python
def serialize_source_map(
    result: CompilationResult,
    *,
    generated_path: PathLike,
    map_path: PathLike,
    generated_bytes: bytes | None = None,
) -> str:
```

`source_path` と `source_bytes` は削除する。ソース一覧は `result.sources` から取る。

### 11.3 id の割り当て

```python
by_file = {source.file: source for source in result.sources}
order = [result.sources[0].file]          # id 0 is always the root
for fragment in result.rendered.fragments:
    if fragment.source is None:
        continue
    name = fragment.source.file
    if name not in by_file:
        raise ValueError(f"source span names a file that was not loaded: {name}")
    if name not in order:
        order.append(name)
ids = {name: index for index, name in enumerate(order)}
```

- **id 0 は必ずルート**。以降は `fragments` 内の初出順。どちらも決定的である。
- `sources` 配列には `order` に入ったファイルだけを書く。ロードはされたが
  フラグメントを 1 つも持たないファイル（`.tfxm`、内容が全部落ちた `.tfx`）は載せない。
  `load_source_map` は載っているソースを全部ハッシュ検証するので、
  無関係なファイルを載せると SyncTeX に不要な `Input` レコードが増える。
- 各要素の `path` は `_stored_path(by_file[name].path, map_path)`、
  `sha256` は `hashlib.sha256(by_file[name].data).hexdigest()`。
- 各 mapping の `source.id` は `ids[fragment.source.file]`。

### 11.4 回帰保証

import を使わない単一ファイル文書では `order == [root]` となり、
`sources` は 1 要素・全 mapping の `id` は 0 になる。
`_stored_path` は先に `os.path.abspath` するので、
現行 CLI が渡していた相対 `input_path` と `LoadedSource.path`（絶対パス）は同じ結果になる。
したがって **`.tfxmap` は現行とバイト単位で同一**である。これはテストで固定する。

フォーマットの `version` は 1 のまま据え置く。`sources` 配列の要素数が増えるだけで、
スキーマは変わらないからである。

### 11.5 ソース span の保持

被 import モジュールの正準 AST ノードは**自分の span を保持する**。
呼び出し側の `!import` 行へ付け替えてはならない。

```text
generated line 10 -> main.tfx
generated line 20 -> a.tfx
generated line 30 -> b.tfx
```

`--source-comments` が出す `% texflux: <file>:<line>` も、これにより
自動的に正しいファイル名を出す（`render._source_comment` は `span.file` を使うため、変更不要）。

---

## 12. CLI

コマンドの表面は変えない。

```bash
texflux compile main.tfx -o main.tex
texflux compile main.tfx -o main.tex --flag draft --flag handout=off
```

- `--flag` は**ルートモジュールにのみ**適用される。被 import モジュールへの伝播は
  `!import{...}($flag)` による明示的な転送だけである。
  未宣言の名前を `--flag` で指定すればこれまでどおり `FlagError`。
- `_compile` の変更は 2 箇所。

```python
result = compile_with_map(
    source_bytes.decode("utf-8"),
    filename=str(input_path),
    source_comments=args.source_comments,
    flags=_flags(args.flags),
    source_bytes=source_bytes,
)
...
map_text = serialize_source_map(
    result,
    generated_path=output_path,
    map_path=map_path,
    generated_bytes=output_bytes,
)
```

- `ModuleError` は `TeXFluxError` 派生なので、既存の `except TeXFluxError` 節が
  `diagnostic()` を出力して終了コード 1 を返す。追加の except 節は要らない。
- 深すぎる import ネストは既存の `except RecursionError` が拾う。
  import 段数に人工的な上限は設けない（循環は §8.3 で検出される）。
- `texflux synctex remap` は変更しない。`--map` 1 個で複数ソースを扱える。

---

## 13. 診断の一覧

すべて `ModuleError`（`kind = "module"`、表示は `module error`）。span は表のとおり。
旧 M01〜M27 との対応表は `doc/diagnostics.md` §2.6 にある。

| # | 条件 | メッセージ | span |
| --- | --- | --- | --- |
| M001 | パスが空 | `module path must not be empty` | 群 |
| M002 | パスが NUL を含む | `module path must not contain a NUL character` | 群 |
| M003 | `\` を含む | `module paths use '/' separators` | 群 |
| M004 | 絶対パス | `module paths must be relative to the importing file` | 群 |
| M005 | `!import` の拡張子不一致 | `!import requires a '.tfx' module; got '<written>'` | 群 |
| M005 | `!macroimport` の拡張子不一致 | `!macroimport requires a '.tfxm' module; got '<written>'` | 群 |
| M006 | 束縛リストがインラインテキストでない | `!import binding list must be inline text` | 群 |
| M028 | ファイルが読めない | `cannot read module '<display>': <理由>` | 群 |
| M023 | `!import` に suite | `!import produces content: it does not accept a suite and cannot wrap a '>>' payload` | ノード |
| M024 / M026 | `!import` の必須群が 1 個でない | `!import requires exactly one '{path}' group` | ノード |
| M025 | `!import` に `[...]`/`<...>` | `!import does not accept '[...]' or '<...>' groups` | 群 |
| M007 | 束縛リストが空 | `!import binding list is empty; omit '(...)' instead` | 群 |
| M008 | 束縛の構文不正 | `!import bindings are written 'flag=on', 'flag=off' or 'flag=$callerFlag'; got '<chunk>'` | 部分 |
| M009 | callee が未宣言のフラグ | `imported module '<display>' does not declare build flag '<name>'; <宣言一覧>` | 名前 |
| M010 | 同じ callee フラグの二重束縛 | `build flag '<name>' is bound twice; first bound at <loc>` | 名前 |
| M011 | `$x` が caller に無い | `unknown build flag '<x>' in this module; <宣言一覧>` | 値 |
| M027 | コンテンツ import の循環 | `content import cycle: <経路>` | ノード |
| M015 | `!macroimport` に suite | `!macroimport does not accept a suite` | ノード |
| M017 | `!macroimport` の群が 1 個でない | `!macroimport requires one '{path}' group` | ノード |
| M016 | `!macroimport` に `(...)` | `!macroimport does not accept a '(...)' list` | 群 |
| M018 | `!macroimport` のパスが必須群でない | `!macroimport path must be a required '{...}' group` | 群 |
| M019 | 同一モジュールの二重取り込み | `macro module '<display>' is already imported at <loc>` | 群 |
| M020 | `!macroimport` が非トップレベル | `!macroimport is only valid at the top level` | ノード |
| M012 | `!macroimport` が `>>` セグメント | `!macroimport must be a top-level declaration and cannot be a '>>' segment` | セグメント |
| M014 | `.tfxm` のトップレベル純粋性違反 | `a .tfxm macro module may contain only !defmacro, !macroimport, comment lines and blank lines` | ノード |
| M013 | `.tfxm` 内の禁止特殊 | `'!<name>' is not allowed in a .tfxm macro module` | ノード |
| M021 | 取り込みマクロ名の衝突 | `macro '!<name>' is already available here, defined at <loc>` | `!macroimport` |
| M022 | 同梱の標準マクロモジュールが `!macroimport` を持つ（到達不能: `InternalError` に変換される） | `the bundled standard macro module imports no other module` | ノード |
| M029 | `.tfxm` の自己完結性違反 | `'!<n>' is not defined in <display> and is not available through its own !macroimport` | ノード |
| M030 | `normalize()` 直呼び | `'!<name>' requires module compilation; use texflux.compile_with_map or the texflux CLI` | ノード |

`ValidationError` として報告するもの（既存の体系に合わせる）:

| 条件 | メッセージ |
| --- | --- |
| テンプレート内の `!import` | `!import is not allowed inside a macro template` |
| テンプレート内の `!macroimport` | `!macroimport is not allowed inside a macro template`（モジュール経路では `resolve_macro_imports` が先に走るため M21 が先に出る。この分岐が実際に使われるのは `normalize()` 直呼びの経路） |
| 取り込み名とローカル定義名の衝突 | 既存の `macro '!<name>' is already defined at <loc>` |

`ParseError` として報告するもの（`HeaderScanner` 由来）:

| 条件 | メッセージ |
| --- | --- |
| `(...)` が閉じない | `unclosed binding list` |
| `(...)` の中でブレースが釣り合わない | `mismatched group delimiter` |
| `(...)` が群より前 | `a special's '(...)' list must follow its groups` |
| `(...)` が 2 個以上 | `a special accepts at most one '(...)' list` |

---

## 14. 実装チェックリスト

| ファイル | 変更内容 |
| --- | --- |
| `src/texflux/modules.py` | **新規**。`ModuleKind` `ModuleSource` `MacroImport` `MacroEnvironment` `FlagBinding` `CompilationSession` `resolve_module_path` `parse_bindings` `bind_import_flags` `resolve_macro_imports` `resolve_content_imports` `validate_macroimport_forms` `validate_macro_module_purity` |
| [ast.py](../src/texflux/ast.py) | `GroupKind.BINDING`、`_GROUP_DELIMITERS` の 4 件目、`_INLINE_KINDS`、`GROUP_OPENERS` を 3 種に限定、`BINDING_OPENER` |
| [parser.py](../src/texflux/parser.py) | `scan_group` の `(` ケース、`_segment` の末尾束縛群走査（`!` 限定）と 3 種のエラー |
| [syntax.py](../src/texflux/syntax.py) | `binding_text(argument)`（`_inline_text(argument, GroupKind.BINDING)`） |
| [errors.py](../src/texflux/errors.py) | `ModuleError` |
| [flags.py](../src/texflux/flags.py) | `_declared_flags` を `declared_flags_hint` として公開 |
| [macros.py](../src/texflux/macros.py) | `MacroDefinition.module`、`collect_macros(..., imported=, module=)`、`expand_macros(..., environments=, module=)`、`_Expander._env`、`_Frame.chain` を定義タプル化、`chain_text`、テンプレート内 `!import`/`!macroimport` 禁止 |
| [normalize.py](../src/texflux/normalize.py) | `canonicalize()` を公開、`normalize()` の末尾をそれに置換、`BUILTIN_DIRECTIVES` にガード 2 件 |
| [render.py](../src/texflux/render.py) | `LoadedSource` 追加、`CompilationResult.sources` 追加 |
| [source_map.py](../src/texflux/source_map.py) | 複数ソース直列化、シグネチャ変更 |
| [__init__.py](../src/texflux/__init__.py) | `compile_with_map` をセッション経由に、`source_bytes` 引数、`ModuleError` `LoadedSource` `CompilationSession` 等の公開と `__all__` 更新 |
| [cli.py](../src/texflux/cli.py) | `compile_with_map` / `serialize_source_map` の呼び出し更新 |
| [tests/support.py](../tests/support.py) | `compile_to_disk` を新シグネチャに追随 |

実装後に更新が必要な文書:

- `texflux_tex_first_dsl_v1_spec.md` — モジュールシステムの規範節を新設し、
  §3 のヘッダースキャナ、§10 のマクロ、§11 のフラグ、§13 のパイプライン、
  §15 のソースマップ、§16 の非目標、§17 の文法に加筆する。
  新設節は §12 とし、以降の旧 §12〜§17 を §13〜§18 に繰り下げる
  （本節の番号は繰り下げ後のものである）。
- `doc/dsl.md` — 日本語リファレンスへの反映。
- `README.md` — `.tfxm` と 2 種の import の紹介。
- `AGENTS.md` — v1 境界（`.tfxm` の存在、「捨てられたペイロードに届く規則」を 4 件に）、
  レビューチェックリストへの項目追加。

---

## 15. 検証計画

### 15.1 ゴールデンテスト

既存ハーネス（[test_golden.py:9-42](../tests/test_golden.py#L9-L42)）は
`filename` にリポジトリ相対パス（`tests/golden/<case>/input.tfx`）を渡している。
本設計では `filename` が **import の基準ディレクトリ**も決めるため、
この綴りのままだとテストがカレントディレクトリ依存になる。

ハーネスを次のように直す。`source-comments` だけは期待出力にファイル名が埋め込まれて
いるので相対の綴りを保ち、それ以外は `Path(__file__)` 由来の絶対パスを渡す。
`source_comments=` の引数がすでに同じ条件で分岐しているので、特別扱いは 1 箇所に収まる。

```python
comments = input_path.parent.name == "source-comments"
filename = (
    input_path.relative_to(root.parent.parent).as_posix()
    if comments
    else str(input_path)
)
```

絶対パスにしても、期待出力にファイル名を含むのは `source-comments` だけなので
既存ゴールデンは変わらない。以降、ケース名集合への追加だけで module ケースが動く。

ゴールデンは一括再生成されるため、既存ケースが覆っていない形だけを足す。

| ケース | 追加ファイル | 覆う形 |
| --- | --- | --- |
| `module-import` | `input.tfx` / `quiz.tfx` | 既定フラグでの import、リテラル束縛、`$caller` 転送、同一モジュールの 2 回 import、`@{} >> !import` によるグルーピング |
| `module-macros` | `input.tfx` / `style.tfxm` / `core-v1.tfxm` / `core-v2.tfxm` / `legacy.tfx` | `A.tfxm → core.tfxm` の連鎖、語彙的スコープ（呼び出し側から `core` のマクロが見えない）、core-v1/v2 の併用 |

### 15.2 `tests/test_modules.py`（新規）

§13 の診断一覧 M01〜M27 を 1 件ずつ再現し、例外型・メッセージ・`span.location` を検証する。
加えて次を検証する。

- `.tfxm` の循環 import（`A ↔ B`）が成功し、環境が両方向で同じになること。
- 条件分岐で落ちた `!import` が、存在しないファイルを指していてもコンパイルできること。
- `!import` をマクロ引数に渡し、テンプレートが `!param` を 2 回参照したとき
  2 インスタンスが生成されること。
- 別モジュールの同名マクロ `A.foo → B.foo` が再帰と誤判定されないこと。
- 被 import モジュール内のエラーが、callee の span を保ったまま
  `; imported from ...` を積み上げること。

### 15.3 既存テストの拡張

- [test_sourcemap.py](../tests/test_sourcemap.py) — 複数ソースの `.tfxmap`。
  id 0 がルートであること、`sources` にフラグメントを持つファイルだけが載ること。
- [test_remap.py](../tests/test_remap.py) — 2 ソースの map で SyncTeX を再マップし、
  新しい `Input` レコードが割り当てられること（`remap.py` 無変更の確認）。
- **回帰**: import を使わない文書の `.tfxmap` が、本変更の前後でバイト単位一致すること。

### 15.4 ベースライン

```bash
python -m unittest discover
```

`AGENTS.md` の実装ワークフロー（テストファースト、最小の失敗テスト → 最小の標準ライブラリ実装
→ 対象テスト → 全スイート）に従う。LaTeX 統合テストはツールチェーンを検出して skip する
現行方針を維持する。

### 15.5 レビュー観点（`AGENTS.md` のチェックリストに追加する項目）

- `\foo`、`@foo`、`!foo` の分類が依然としてプレフィックスのみで決まること。
- `(` が `\` / `@` セグメントと生 TeX の解釈を一切変えていないこと。
- `!import` 以外のすべての特殊が束縛群を既存コードのまま拒否すること。
- `GroupKind.BINDING` の `Argument` が正準 AST に到達しないこと。
- 被 import 正準 AST が callee の span を保持していること。
- 落ちた条件分岐ペイロード内の `!import` がファイルを開かないこと。
- `.tfxm` にフラグ・条件式・コンテンツが入り込んでいないこと。
- `(` が `\` / `@` セグメントの診断文言を変えていないこと。
- `GroupKind.BINDING` がレンダラに届いたら黙って `(...)` を出さずに失敗すること。
- マクロ取り込みが非推移的であること（呼び出し側から間接依存が見えないこと）。

---

## 16. 明示的な非対象

以下は本設計に含めない。

```text
パッケージレジストリ
セマンティックバージョン解決
lockfile
リモートパッケージ取得
public / private / export 宣言
修飾名（qualified name）
再エクスポート構文
任意のフラグ式
真偽値以外のモジュール引数
動的 import パス
マクロテンプレートから生成される !import
生 TeX マクロの自動隔離
LaTeX の意味検証
!asset
```

加えて本設計では次も対象外とする。

- **依存関係の出力コマンド**（`--deps` 相当）。ビルドシステム連携が具体的に必要になってから設計する。
- **`ModuleInstance` の正準 AST キャッシュ**（D7）。キャッシュするのはパース結果だけである。
- **`.tfxm` の変更検出**。`.tfxm` はフラグメントを持たないため `.tfxmap` の `sources` に
  載らず、ソースマップだけでは陳腐化を検出できない。これはビルドシステムの責務とする。

---

## 17. 規範的要約

```text
1 ファイル = 1 モジュール

.tfx:
    コンテンツモジュール
    ローカル !flag / ローカル !defmacro / !macroimport / !import

.tfxm:
    純粋なマクロ定義モジュール
    !defmacro / !macroimport / コメント / 空行 のみ
    フラグなし、条件式なし、コンテンツなし、!import なし

!macroimport:
    私的・非推移的・語彙的
    取り込むのは対象モジュールが自分で定義したマクロだけ
    取り込んだ依存は再輸出されない
    トップレベル宣言であり >> セグメントになれない

!import:
    独立したコンテンツモジュールのインスタンス化
    (...) による明示的フラグ束縛（on / off / $callerFlag）
    マクロ・フラグの名前空間は一切漏れない
    結果の正準 AST が import 位置に差し込まれる
    suite を取らず、>> のラッパーになれない
    マクロテンプレート内には書けない

同一コンテンツモジュールの繰り返し import:
    許可。各 import は別インスタンス

コンテンツ import の循環:
    エラー（アクティブな import スタックで判定）

マクロモジュールの循環:
    許可（.tfxm が純粋宣言的である限り）

マクロ名のシャドーイング:
    禁止。last-import-wins は存在しない

ソースマッピング:
    被 import 正準 AST は callee の span を保持する

レンダラ:
    正準 AST しか知らない。モジュール境界は到達前に消える
```
