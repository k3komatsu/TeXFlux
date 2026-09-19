# Bundle v1

TeXFlux Bundle は、実際にコンパイルで使われた `.tfx` / `.tfxm`、明示的な
`!asset`、参照した `.tfxb` を source bytes のまま ZIP に保存する snapshot です。
manifest の dependency edge が解決先を指定するため、Bundle の再利用時に利用側の
プロジェクトを検索しません。format と manifest version はともに v1 です。

## 資産参照

`!asset{PATH}` は SpecialInvocation ではなく、既存の text field の中だけで認識されます。

```text
\includegraphics{!asset{figures/system.pdf}}
```

通常の filesystem compile では `figures/system.pdf` をそのまま TeX に出力します。
PATH は空でない相対 POSIX path とし、NUL、バックスラッシュ、absolute / drive / UNC
path を拒否します。`!asset` を書いた module のディレクトリを基準に regular file を
読みます。`.` と `..` は許可されます。

Macro template でも利用できます。PATH 内で許可される補間は `!text{...}` だけで、
挿入された値は再走査されません。path 内の `!` は marker の開始として予約されるため、
literal の `!` は v1 では許可しません。`!!asset{...}`、raw mode、`!|` は従来どおり
literal / verbatim です。行頭の `!asset{...}` は resource declaration ではなく、
未知の special として扱われます。

## Bundle の作成と検査

```text
texflux bundle INPUT.tfx -o OUTPUT.tfxb [--flag NAME[=on|off]]
texflux bundle list INPUT.tfxb
texflux bundle list INPUT.tfxb --json
```

writer は固定順序、固定 metadata、LF、STORE 圧縮で deterministic archive を作り、
`manifest.json` と `payload/` を格納します。`bundle list` は source を再コンパイルせず、
archive、manifest、payload size、SHA-256、dependency closure を検証してから selector と
title を表示します。タイトルがない frame はテキスト出力では `<untitled>`、JSON では
`null` です。

公開 API は `buildBundle`、`readBundleManifest`、`readBundleIndex`、
`BundleBuildOptions`、`BundleLimits` です。reader は STORE / DEFLATE を受け入れますが、
writer は STORE のみを使います。既定の archive / entry / 展開量 / manifest / nested
depth limits は `BundleLimits` で引き下げまたは変更できます。

## Bundle の再利用

```text
!bundleimport{relative/path.tfxb}{frame:3}
```

required inline group が2つ必要で、suite、binding list、`!text` interpolation は
許可されません。`frame:N` は Bundle 作成時の root canonical document の直接の子である
`@frame` を、source order で 1-based に数えた selector です。macro 展開と active な
`!import` の結果は数えますが、nested frame、dropped payload、frame body 内の frame は
数えません。

再利用時は保存された flags だけで新しい `CompilationSession` を動かし、`!import`、
`!macroimport`、`!asset`、nested `!bundleimport` は manifest edge と archive payload
だけで解決します。callee の flags / macro namespace は caller に漏れません。manifest
index と再コンパイルした frame 数、selector、title、source、span が一致しなければ
BundleError になります。

source identity は `tfxb:<bundle-sha256>!/<logicalPath>`、cache の physical path、
archive member path を分離します。cache は検証済み archive を materialize した後も残る
ため、生成済み source-map や SyncTeX の入力 path として使えます。

## セキュリティ境界

ZIP は central directory と各 member を展開前に検査します。duplicate / traversal /
absolute / drive / UNC path、NUL、directory、symlink / device、暗号化、ZIP64、multi-disk、
未対応 compression method、宣言 size と limits の違反を拒否します。展開後には CRC と
manifest の size / SHA-256 を検証します。manifest は署名形式ではなく、v1 は authenticity
を提供しません。

詳細な field 契約は
[`schemas/texflux-bundle-manifest-v1.schema.json`](../schemas/texflux-bundle-manifest-v1.schema.json)、
index 契約は
[`schemas/texflux-bundle-index-v1.schema.json`](../schemas/texflux-bundle-index-v1.schema.json)、
言語上の位置づけは normative spec の Bundle section を参照してください。
