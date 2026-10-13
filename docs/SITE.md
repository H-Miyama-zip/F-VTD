# 検索サイト（Cloudflare Workers）

公開URL：<https://f-vtd.tomaranaina.workers.dev>

F-VTD の検索・ダウンロード用サイトは、このリポジトリから Cloudflare Workers の無料プランで公開しています。Worker のプログラムは持たず、静的ファイルを配信するだけです。検索はすべて閲覧者のブラウザー内で行います。

## しくみ

| パス | 役割 |
| --- | --- |
| `site/` | 画面のファイル（HTML・CSS・JS）、`config.json`（GitHub・フォームへのリンク）、`_headers`（キャッシュ設定） |
| `outputs/vtuberpost_private/` | ローカルの追加候補DB。アプリの保存先で、サイトのビルド元には含まれない。Git管理から除外する |
| `data/master.tsv` の `added_on` 列 | サイトの「更新履歴」の元データ。追加した語の追加日 |
| `package/README.txt` | ZIPに入れる利用者向けの案内。`{version}` と `{count}` はビルド時に埋め込む |
| `scripts/build_site.py` | `dist/` を再生成・検査し、検索用データとZIPを作って `public/` にまとめる |
| `wrangler.jsonc` | Workers の設定。`public/` を静的ファイルとして配信する |
| `public/` | 公開されるファイル一式。生成物なのでGit管理しない |

`main` に push すると、Cloudflare が `python scripts/build_site.py` で `public/` を作り、`npx wrangler deploy` で公開します。版の名前は「更新履歴の最新の日付＋ハッシュ」です（例：`20260921-1a2b3c4d`）。ハッシュは `master.tsv`・`package/README.txt`・`NOTICE.md` から計算するため、ZIPに入るファイルが変わると版の名前とURLも変わります。検索用データとZIPは版ごとのURLで配信し、長期間キャッシュさせています。

ビルドは次の場合に失敗し、公開中のサイトは前の版のまま残ります。

- `master.tsv` に空欄・重複がある
- コミットされた `dist/` が `master.tsv` から生成したものと一致しない（`python scripts/build.py` の実行忘れ）
- 追加した行（`origin` が `added`）に追加日（`added_on`）か確認元URL（`source_url`）がない
- コミットされた `data/additions.tsv` が `master.tsv` から生成したものと一致しない

`main` 以外のブランチを push すると、本番とは別のプレビュー用URLに公開されます。Cloudflare のダッシュボードでビルドのログから確認できます。

## 辞書を更新するとき

ブラウザアプリの「候補DBに保存する」は追加予定の保存です。この操作では `data/master.tsv`、配布ファイル、`public/`、公開サイトは更新されません。追加予定の確認と辞書への引き継ぎは [追加候補の保存と辞書への反映](CANDIDATES.md) を参照してください。

1. 作業用ブランチで、候補DBに保存した追加予定の名前・読み・出典と別の変換を確認し、各「読み・名前」を辞書と照合します。
2. 未登録の組み合わせを `data/master.tsv` に追加する。`origin` は `added`、`added_on` に辞書への追加日、`source_url` に確認元URL、必要なら `note` に注記を書く。辞書データの編集元はこのファイルです。候補DBの保存日と辞書の追加日は別です。
3. `python scripts/build.py` で `dist/` と `data/additions.tsv` を作り直す。
4. `python scripts/build_site.py` でサイトを作り、配布4形式・検索データ・ZIPの一致を確認する。必要なら手元で確認する：`python -m http.server 8788 --directory public` → <http://localhost:8788>
5. READMEの追加内容・収録数を更新し、辞書関係の変更を作業ブランチにコミットする。候補DBや作業記録はGit管理から除外されます。
6. 公開する場合は本番ブランチへ反映・pushし、Cloudflareのビルドと公開結果を確認する。作業ブランチへのコミットやローカルビルドだけでは本番の公開サイトは更新されません。

各IMEへのインポートも別操作です。サイトの公開結果やプロジェクトの収録数は、利用者のIMEに取り込まれた語数を示すものではありません。

語の削除を更新履歴に載せる仕組みは、まだありません。削除や改名の方針を決めるときに用意します。

収録依頼フォームのURLは `site/config.json` の `formUrl` に書くと、「収録候補・修正の連絡」にボタンが出ます。

## 現在の Cloudflare の設定

作り直すときの参考として記録します。

- 種類：Workers（GitHub の `H-Miyama-zip/F-VTD` と連携）
- Worker 名：`f-vtd`（`wrangler.jsonc` の `name` と一致させる）
- 本番ブランチ：`main`
- ビルド コマンド：`python scripts/build_site.py`
- デプロイ コマンド：`npx wrangler deploy`

作り直す場合は、ダッシュボードの「Workers & Pages」→「作成」→「Workers」→「リポジトリをインポート」から、上の設定で作成します。

## 無料プランの範囲

- 静的ファイルの配信：回数・転送量とも無制限。Worker のプログラムが動かないため、無料プランの「1日10万リクエスト」の上限にも数えられません。
- ファイル：1バージョンあたり2万個まで、1ファイル25MiBまで。現在は9ファイルで、最大は検索用データ（約1.3MB）。
- ビルド：月3,000分まで（1回40秒ほど）。push のたびに1回使われます。

未収録の検索語を記録するなど、Worker のプログラムを足す場合は、その部分のリクエストが1日10万回の上限に数えられます。
