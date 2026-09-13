# 作業メモ — モジュールシステム

最終更新: 2026-09-13。モジュールシステムは `b3e8e33` で `main` に統合済み。
実装・仕様・ドキュメント・テストは揃っている。本書に残すのは、
**意図的に見送った項目**と**承知の上で放置している残件**だけである。

## 参照先

| 知りたいこと | 読む場所 |
| --- | --- |
| 言語としての規範 | `texflux_tex_first_dsl_v1_spec.md` §12 |
| 日本語の利用者向け説明 | `doc/dsl.md` 14 章 |
| 実装の設計と診断一覧（M01〜M27） | `doc/module-system.md` |
| 守るべき不変条件 | `AGENTS.md`（v1 境界とレビューチェックリスト） |
| 動く実例 | `examples/modules.tfx` と `examples/modules/` |

## 検証

```bash
python3 -W error::ResourceWarning -m unittest discover   # 273 tests, OK
PYTHONPATH=src python3 -m texflux compile examples/modules.tfx -o /tmp/m.tex
diff -u examples/modules.tex /tmp/m.tex                  # 差分なし
```

## `AGENTS.md` はローカルのみ

`AGENTS.md` は `.gitignore` 済みなので、モジュールシステムに関する加筆は
**コミットされていない**。clone し直すか別マシンへ移ると失われるため、
その際は再度加筆が要る。

## 意図的に見送った項目

`doc/module-system.md` §16 に全件ある。実運用で欲しくなりそうなのは次の 3 つ。

- **依存関係の出力**（`texflux compile --deps` 相当）。Makefile で `.tfx` / `.tfxm`
  の依存を書くには要る。`session.loaded()` が全ソースを持っているので実装は軽い。
  ただし条件分岐で依存グラフが変わる点をどう扱うかは要設計。
- **`.tfxm` の陳腐化検出**。`.tfxm` はフラグメントを生まないため `.tfxmap` の
  `sources` に載らない。今はビルドシステムの責務としている。
- **`ModuleInstance` のキャッシュ**。現状はパース結果のみキャッシュなので、
  同じモジュールを同じフラグで 2 回 import すると 2 回コンパイルする。
  `(path, frozenset(flags.items()))` をキーにすれば安全に効かせられる。

## 承知の上で放置している残件

- `_check_self_contained` は `_build_environments` のたびにロード済みの全マクロ
  モジュールを走査する（O(コンテンツ数 × マクロモジュール数)）。実測で
  40×40×20 マクロ 0.09 秒。純粋かつ冪等なので害はない。問題になったら
  「検査済み集合」を持てばよい。
- 大文字小文字を区別しないボリュームでは `paths.normalized_path` の `normcase`
  により `b.tfx` と `B.tfx` が同一視される。
- ルートの `filename` に NUL が入ると `compile_with_map` は span のない
  `ValueError` を返す（文書中のパスではなく呼び出し側の引数なので、`FlagError`
  と同じ扱いという判断）。CLI は捕捉するのでクラッシュはしない。
