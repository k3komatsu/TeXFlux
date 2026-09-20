# 開発引き継ぎ

このリポジトリの実装は `source/texflux/` の D/LDC 1.43+ 版だけです。規範仕様は
`texflux_tex_first_dsl_v1_spec.md`、利用者向けの文法は `doc/dsl.md`、個別機能の設計は
`doc/` の設計書を参照してください。実装の局所的な規約は `AGENTS.md` にあります。

## 必須検証

```bash
source ~/dlang/ldc-1.43.0/activate
dub test
dub build
dub build -c library
dub build --build=release
dub build -c update-regression
```

`tests/regression/v1.jsonl` は通常変更しません。公開出力を意図的に変更するときだけ、差分を
確認したうえで次を実行します。

```bash
dub run -c update-regression -- --accept-current
```

## v0.4.0 LSP・リリース完了記録（2026-09-20）

- `512de7e` でLSP本体、analysis API、workspace、UTF-8/16/32位置変換、診断公開、
  reverse dependency invalidation、watcher通知を実装し、`ebe1ab1` でWindows向けURI
  テストを修正した。
- PR #6 の必須4チェックを通過して `db288c3` として `main` にmerge済みである。
- `v0.4.0` tagを新規作成し、5 platform archive、`SHA256SUMS`、artifact attestationを
  含むGitHub Releaseを公開した。全archiveのchecksumとattestationを検証済みである。
- `k3komatsu/homebrew-tap` のFormula PR #3（reviewed head SHA
  `4039ebd4e728ceb41ec7662106b760ca1d2ff4f6`）について、macOS/Linuxの`brew test-bot`
  を通過させ、`brew pr-pull`（run `35487043603`）でFormulaとBottleをmainへ反映した。
  macOS arm64でBottleをpoured後、`brew test k3komatsu/tap/texflux` と
  `texflux --version`（`texflux 0.4.0`）を確認済みである。

## 保留事項

- 依存グラフ専用の出力、モジュール instance cache、compiler-levelの複数診断収集
- LSP v0.4.1のcross-file module visibility、`.tfxm` rename、parser recovery、
  background analysisは未実装。詳細は`plan/lsp-v0.4.1.md`の境界と残差を参照する。
- CLIからのimport先overlay、未保存`.tfxb`/asset bytes overlay、registry公開、自動配布
- 非同期解析へ移行する場合のrevision/version commit guard
- `library` configuration の registry 公開

## v0.4.1 LSP editor intelligence 実装記録（2026-09-20、完了）

- `source/texflux/lsp/features.d`にstrict parse AST由来のsymbols、folds、occurrences、
  semantic token indexを追加した。
- `documentSymbol`、`foldingRange`、completion、hover、definition、semantic tokens、
  document-local references/rename、safe empty codeAction endpointを実装した。
- `didChange`の複数content changeを順序どおりtransactionalに適用し、UTF-8/16/32境界、
  line-end clamp、明示的completion `textEdit`を追加した。
- renameは予約名と`.tfxm`を拒否し、module可視性を推測するcross-file編集を行わない。
- 初回Primary Engineerレビューの3 blockerを修正し、同一セッションの再レビューは
  `PASS_WITH_RISK`（blockerなし）。フォーカス済み`dub test --force`は32 modules passed。
- `e09e283`をPR #7としてrequired 4 checks成功後に`main`へmergeし、merge commit
  `2df1335`へ`v0.4.1` tagを新規作成した。
- Release workflow run `35493463099`で5 platform archive、`SHA256SUMS`、artifact
  attestation、GitHub Releaseを公開し、全checksumと5 archiveのattestationを検証した。
- `k3komatsu/homebrew-tap`のFormula PR #4（reviewed head SHA
  `72d5490202bec6e0d34f620f1ca4c5dfdfc7a41d`）はmacOS/Linuxのtest-botを通過した。
  publish workflow run `35494057593`でFormulaと`arm64_tahoe`/`x86_64_linux` Bottleを
  mainへ反映し、macOS arm64でBottleをpouredした。
- Homebrew upgrade後に`brew test k3komatsu/tap/texflux`と`texflux --version`
  （`texflux 0.4.1`）を確認した。

## 既知の残差

- Bundle v1 は `doc/bundle.md` の設計に沿って実装済み。`.tfxb` は
  source / asset / nested Bundle snapshot、manifest edge、frame index を保持し、
  `!bundleimport` は保存済み flags と archive-local resolver で再コンパイルする。
- Bundle cache は検証済み archive の materialized source を保持する。cache 清掃、署名、
  remote registry、nested selector、archive flatten は v1 の非スコープである。

- 大文字小文字を区別しない volume でも、ケース違いの path は別 source として扱われる。
- ルート filename の NUL は span のない `ValueError` になる。
- 壊れた `.tfxmap` の後置メッセージは JSON reader 実装に依存する。
- SyncTeX の link tag は 32-bit 整数範囲だけを受け付ける。
- M029 の固定メッセージには回帰 fixture 互換の旧 API 表記が残る。
- LDC が出す `SumType.toHash` の既知 warning は許容する。新しい warning は受け入れない。

## CI/CD・バイナリ配布の引き継ぎ（2026-09-20）

### 完了

CI/CD・バイナリ配布の初回実装は完了している。詳細な設計と判断理由は
`plan/ci-cd-binary-distribution.md` を参照する。

- `be2a6c4`: `ci.yml`、`release.yml`、0BSD、version CLI、README、Dependabotを追加。
- `100e407`: Claude Opusレビュー後のworkflow修正。
  - tag形式のarchive名
  - `GH_REPO` の明示
  - 公開済みReleaseの上書き防止
  - attestation用 `artifact-metadata: write`
  - CIでのDUB設定明示
- `380dde4`: Dependabotによる`actions/attest` 4.2.1 → 4.2.2更新。
- `v0.3.0`: `100e407`を指すtagとしてpush済み。
- [v0.3.0 Release](https://github.com/k3komatsu/TeXFlux/releases/tag/v0.3.0) は公開済みで、Release workflowは成功済み。
- `v0.3.1`: `cc51116`を指すtagとしてpush済み。
- [v0.3.1 Release](https://github.com/k3komatsu/TeXFlux/releases/tag/v0.3.1) は公開済みで、Release workflowの5 platform build、checksum、attestation、asset公開が成功済み。
- 5 platform archiveと`SHA256SUMS`を公開済み。
- 公開URLから全archiveを取得し、`SHA256SUMS`を検証済み。
- v0.3.0のmacOS arm64 binaryは`texflux 0.3.0`を出力し、Windows ZIPは`texflux.exe`、`LICENSE`、`README.md`を含む。
- v0.3.1のmacOS arm64 release binaryも`texflux 0.3.1`を出力することを確認済み。
- `v0.4.0`: `db288c3`（LSP実装PR #6のmerge commit）を指すtagとしてpush済み。
- [v0.4.0 Release](https://github.com/k3komatsu/TeXFlux/releases/tag/v0.4.0) は公開済みで、
  5 platform build、checksum、attestation、asset公開が成功済みである。
- v0.4.0の全archiveを公開URLから取得し、`SHA256SUMS`を検証済みである。
- `v0.4.1`: `2df1335`（LSP editor intelligence実装PR #7のmerge commit）を指すtagとして
  push済み。
- [v0.4.1 Release](https://github.com/k3komatsu/TeXFlux/releases/tag/v0.4.1) は公開済みで、
  5 platform build、checksum、attestation、asset公開が成功済みである。
- v0.4.1の全archiveを公開URLから取得し、`SHA256SUMS`と5 archiveのattestationを検証済みである。
- `k3komatsu/homebrew-tap` を別repositoryとして作成・push済み。
  - `Formula/texflux.rb` は現在 `v0.4.1` source archiveを使うsource Formulaである。
  - `7fffa60`: Formula testを`version`参照に変更し、将来のversion bumpへ追従可能にした。
  - `ldc` / `dub`によるsource build、`brew test`、linkage検査をローカルで確認済み。
  - Homebrew標準の `autobump.yml`、`tests.yml`、`publish.yml` を配置済み。
  - 初回 `brew test-bot` はmacOS/Linuxとも成功済み。
  - Formula PR #2のhead SHA `a3ccf99`を固定して`publish.yml` / `brew pr-pull`を実行し、v0.3.1のBottleを公開済み。
  - Bottle publish run `35449736881`は成功し、`arm64_tahoe`と`x86_64_linux`のBottle stanzaをmainへ反映した。
  - macOS arm64でBottleをpoured後、`brew test texflux`、`brew linkage --test texflux`、`texflux --version`（`texflux 0.3.1`）を確認済み。
- v0.4.0のFormula PR #3はhead SHA `4039ebd4e728ceb41ec7662106b760ca1d2ff4f6`でレビューし、
  macOS/Linuxの`brew test-bot`を通過した。`publish.yml` / `brew pr-pull` run
  `35487043603`でv0.4.0のFormulaとBottleをmainへ反映し、macOS arm64でBottleをpoured、
  `brew test k3komatsu/tap/texflux`と`texflux --version`（`texflux 0.4.0`）を確認済み。
- v0.4.1のFormula PR #4はhead SHA `72d5490202bec6e0d34f620f1ca4c5dfdfc7a41d`でレビューし、
  macOS/Linuxの`brew test-bot`を通過した。`publish.yml` run `35494057593`でv0.4.1の
  FormulaとBottleをmainへ反映し、macOS arm64でBottleをpoured、`brew upgrade`、
  `brew test k3komatsu/tap/texflux`、`texflux --version`（`texflux 0.4.1`）を確認済み。
- READMEの標準install導線をHomebrewにし、WindowsのRelease ZIP/PowerShell手順とmacOS/Linuxのarchive手順を記載した。

### 次回以降のrelease手順

```text
source/texflux/package.d の texfluxVersion 更新
→ 必須Dテスト/build
→ READMEのinstall手順・asset naming・固定version記述を確認
→ PR/CI
→ mainへmerge
→ vX.Y.Z tag push
→ Release asset、checksum、attestation確認
→ Homebrew tapのautobumpによるFormula更新PR
→ tapのFormula CI成功確認
→ reviewed head SHAを指定してpublish.yml / brew pr-pullを実行
→ Bottle、Formula、brew install/upgrade、--versionを確認
```

version番号を別ファイルへ手動同期しない。`texfluxVersion`、tag、`--version`出力を一致させる。

### GitHub repository設定（2026-09-19時点で設定済み）

GitHub UIで次の設定を反映し、Rulesets一覧と各設定画面で確認済みである。

- Activeな`main protection` rulesetをdefault branch `main`へ適用。
  - Pull request必須。
  - Required approvalsは0。
  - Required checksは次の4つ。
    - `Linux minimum`
    - `Platform smoke (macOS arm64)`
    - `Platform smoke (Windows x86_64)`
    - `Linux latest LDC`
  - `main`の削除とforce pushは禁止。
- Activeな`version tag protection` rulesetを`v*`へ適用。
  - 新しいversion tagの作成は許可。
  - 既存`v*` tagの更新・削除・force updateは禁止。
- Repository SettingsのRelease immutabilityを有効化。
  - 公開後のRelease tagとassetを変更しない。
  - 設定有効化前に公開済みの`v0.3.0`へ遡及適用されることは前提にしない。
- Actions policyを次のように制限。
  - GitHub作成Actionsを許可。
  - `dlang-community/setup-dlang@*`だけを個別allowlistへ追加。
  - Marketplace verified creatorsの一括許可は無効。
  - Actionはfull-length commit SHAでpinすることを必須化。
  - Default `GITHUB_TOKEN` permissionはread-only。
  - ActionsによるPull Request作成・approveは無効。

この設定により、通常の変更は次の順序で行う。

```text
version変更（texfluxVersionのみ）
→ 作業branchへpush
→ Pull Request
→ 4 required checks成功
→ mainへmerge
→ 新しいvX.Y.Z tagをpush（既存tagは再利用しない）
→ release.ymlのtest/build/package/checksum/attestation/release
→ 公開asset、SHA256SUMS、--version、attestationを確認
```

`main`へ直接pushせず、tagのforce update・削除も行わない。新しいActionを追加する場合は、先にGitHub UIのallowlistへrepositoryを追加し、workflowではfull commit SHAを使う。

### 継続して守るrelease方針

- LinuxはUbuntu 22.04をglibc baselineとする。
- macOSは`MACOSX_DEPLOYMENT_TARGET=13.0`でbuildするが、runtime test済みはmacOS 15だけである。
- macOS codesign/notarization、Windows MSI、Universal Binary、musl、AppImage、SBOM、cosignは現段階で追加しない。
- Homebrew tapは別repository `k3komatsu/homebrew-tap`で管理する。
- 現在の方式はsource Formula + Homebrew標準Bottle workflowとする。TeXFlux GitHub Releaseのpre-built binaryをFormulaから直接配布する方式、Caskは採用しない。
- Formula更新時は、source tag URLとSHA-256を確認する。Formula testのversion期待値は`version`から導出するため、手動同期しない。
- Bottleが必要なFormula PRは、GitHubの単純なAuto-mergeを先に行わず、reviewed head SHAを固定してtapの`publish.yml` / `brew pr-pull`で処理する。
- TeXFlux本体releaseからtapへ直接通知するcross-repository PATは追加しない。tapのscheduled autobumpを通常経路とする。
- `tests/regression/v1.jsonl`をreleaseやCIから更新しない。
