# TeXFlux LSP v0.4.1 実装計画

## 1. 目的

v0.4.0で追加したstdio LSP、診断、open-document overlay、reverse dependency
invalidationの上に、strict parse ASTを再利用したeditor intelligenceを追加する。
v0.4.1の対象は次のとおりである。

- `textDocument/documentSymbol`
- `textDocument/foldingRange`
- `textDocument/completion`
- `textDocument/hover`
- `textDocument/definition`
- `textDocument/semanticTokens/full`
- `textDocument/references`
- `textDocument/rename`
- `textDocument/codeAction` のAPI応答
- range付き`textDocument/didChange`によるincremental sync

TeXFluxはTeXの意味やpackage registryを知らないため、候補と定義はTeXの推測ではなく、
TeXFlux自身の構文と既存のmacro/flag/import記法だけから作る。compilerが安全な自動修正を
定義していない診断について、codeActionは空配列を返し、根拠のない書き換えを生成しない。

## 2. 現在の境界

- v0.4.0のworkspaceはopen中の`.tfx`/`.tfxm`だけをrootとして保持する。
- strict parseが成功した文書だけsymbols、folding、semantic token、reference indexを作る。
- parse途中の文書でもcompletionは現在行の軽量なprefix判定で返す。
- macro/flagの定義と参照、相対`!import`/`!macroimport`/`!bundleimport` pathだけを解決する。
- completion候補、references、renameは要求されたopen document内の宣言だけを語彙範囲とする。
  他moduleのmacroを推測して編集しない。macro module（`.tfxm`）のrenameは安全性のため
  `null`を返す。definitionはopen documentの宣言を候補にするが、TeXFluxのmodule可視性を
  推測しない。
- unknown TeX command/environmentの意味は推測しない。
- feature indexはopen documentごとに保持し、`didChange`で再構築する。
- 解析とfeature requestはsingle-threaded event loopで同期実行する。background worker、
  parser recovery、incremental compiler passは追加しない。

## 3. Editor index

`source/texflux/lsp/features.d`を追加し、次のeditor-independentな情報を保持する。

- declaration symbol: macro、flag、structural environment
- occurrence: macro、flag、module path
- fold range: suiteとmulti-line structural region
- semantic token: environment、structural command、special、macro、flag（`variable`型）、module path

各項目は既存の`SourceSpan`を持ち、LSP boundaryで現在の`LineIndex`とposition encodingへ
変換する。header/groupの名前範囲は、parserが保持するgroup spanと既存のcode-point規則から
計算する。文字列やgroup内部を再スキャンしてTeXFlux構文を推測しない。

index作成手順:

1. `parse()`でsyntax ASTを作る。
2. rootからblock、suite、groupを再帰的に歩く。
3. top-level `!defmacro`と`!flag`をdeclarationとして記録する。
4. `@environment`をsymbol、構造的な`\command`をsemantic tokenとして記録する。
5. macro/flag/module pathのoccurrenceを記録する。
6. parse error時はstrict indexを空にし、completionだけはraw prefixから返す。

## 4. LSP request contract

initialize capabilityに次を追加する。

- `documentSymbolProvider`
- `foldingRangeProvider`
- `completionProvider`
- `hoverProvider`
- `definitionProvider`
- `referencesProvider`
- `renameProvider`
- `semanticTokensProvider.legend` と`full`

codeActionは安全な変換規則がまだないためinitialize capabilityには広告しない。ただし明示的に
requestされた`textDocument/codeAction`には空配列を返し、将来のAPI接続点とする。

requestの結果:

- symbolsは`SymbolInformation[]`としてflatに返し、`containerName`でenvironment nestingを
  表す。
- foldingはsuiteのheader lineからbodyの最終lineまでを返す。
- completionはTeXFlux special、要求されたopen documentで宣言済みのmacro/flagを返し、明示的な
  `textEdit`範囲を付ける。
- hoverはmacro/flag/module pathの構文上の説明とdefinition位置を返す。
- definitionはmacro/flag declaration、または解決可能なmodule fileの先頭位置を返す。
- referencesは要求されたdocumentに宣言がある場合だけ、そのdocument内の
  `declaration/reference`を返す。
- renameはmacroまたはflagだけを対象とし、要求されたdocument内の`WorkspaceEdit`を返す。
  `.tfxm`宣言、module path、予約名へのrenameは返さない。
- semantic tokensは`data`のdelta encodingで返す。
- codeActionは該当する安全な変換規則がない場合`[]`を返す。

対象外の識別子、unknown special、raw TeX、TeX package名にはdefinitionやrenameを返さない。

## 5. Incremental sync

`didChange`は従来のfull changeに加えて、一つ以上のrange changeを受理する。

- rangeは現在のposition encodingからbyte offsetへ変換する。
- UTF-8/16/32のcode-unit境界途中はinvalid paramsとする。
- `rangeLength`はdeprecatedな補助情報として無視し、編集範囲はrangeから計算する。
- 一つのnotification内の複数changeは、LSPの順序どおり直前の結果へ順番に適用する。
  full changeが列中に現れた場合も、その後のrangeは更新後のtextへ適用する。空配列はno-opとする。
- version逆行は既存どおり拒否する。
- line末尾を越えるcharacterはline末尾へclampし、code pointの途中だけは拒否する。
- 変更後のtext、LineIndex、feature index、diagnosticsを同じeventで更新する。

結果は同期処理のためstaleにならない。将来background analysisを導入するときは、
revision/versionをcommit前に検査する。

## 6. テストと完了条件

- symbols: macro、flag、nested environment、parse failure
- folding: block/sequence suite、multi-line境界、empty suite
- completion: special prefix、macro/flag prefix、parse途中
- hover/definition: declaration、reference、module path、unknown name
- semantic tokens: token type、UTF-16 delta、raw TeXの非走査
- references/rename: document-local declarationと全参照、module/予約名の安全な拒否、複数open document
- codeAction: diagnosticsに対して不正なeditを返さない
- incremental sync: UTF-8/16/32、range境界、CRLF、version、再診断
- 既存のv0.4.0 transport/lifecycle/diagnosticsテストの非回帰

実装後に以下を実行する。

```bash
source ~/dlang/ldc-1.43.0/activate
dub test
dub build
dub build -c library
dub build --build=release
dub build -c update-regression
```

続いてPrimary Engineerのレビュー・修正・re-reviewを行い、PR必須check、v0.4.1 Release、
Homebrew Formula testとBottle publishを検証する。`tests/regression/v1.jsonl`は変更しない。

## 7. 実装状況

2026-09-20時点で実装と統合テストを完了した。`features.d`のstrict AST indexを
workspaceへ接続し、上記requestを実装した。incremental changeはtransactionalな順序適用で、
rename/referencesは安全側のdocument-local境界に限定している。空のcodeActionはendpointとして
残すが、capabilityは広告していない。

Primary Engineerの初回レビューで3件のblockerを検出したが、すべて修正した。同一Claude
セッションの再レビューは`PASS_WITH_RISK`（blockerなし）で、残るリスクはmodule可視性を
推測しないため`.tfxm`のcross-file renameを未対応とする設計上の制約である。フォーカス済み
`dub test --force`は32 modules passed。全必須D検証、PR/CI、tag、Release、Homebrew Bottle
検証も完了した。

## 8. Release完了記録

- `e09e283`をPR #7としてrequired 4 checks成功後に`main`へmergeし、merge commit
  `2df1335`に`v0.4.1` tagを作成した。
- Release workflow run `35493463099`が成功し、5 platform archive、`SHA256SUMS`、artifact
  attestation、GitHub Releaseを公開した。公開archiveのchecksumとattestationを検証済みである。
- Homebrew Formula PR #4のreviewed head SHAは
  `72d5490202bec6e0d34f620f1ca4c5dfdfc7a41d`。test-bot成功後、publish workflow run
  `35494057593`でFormulaとBottleをmainへ反映した。
- macOS arm64でv0.4.1 Bottleをpouredし、`brew upgrade`、`brew test
  k3komatsu/tap/texflux`、`texflux --version`（`texflux 0.4.1`）を確認済みである。
