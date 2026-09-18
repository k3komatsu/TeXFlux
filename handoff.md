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

## 保留事項

- 依存グラフ専用の出力、モジュール instance cache、複数診断の収集
- LSP サーバー本体、CLI からの import 先 overlay、registry 公開、自動配布
- `library` configuration の registry 公開

## 既知の残差

- 大文字小文字を区別しない volume でも、ケース違いの path は別 source として扱われる。
- ルート filename の NUL は span のない `ValueError` になる。
- 壊れた `.tfxmap` の後置メッセージは JSON reader 実装に依存する。
- SyncTeX の link tag は 32-bit 整数範囲だけを受け付ける。
- M029 の固定メッセージには回帰 fixture 互換の旧 API 表記が残る。
- LDC が出す `SumType.toHash` の既知 warning は許容する。新しい warning は受け入れない。
