# 検索サイト（Cloudflare Pages）

F-VTD の検索・ダウンロード用サイトは、このリポジトリから Cloudflare Pages の無料プランで公開します。サーバー側の処理はなく、検索はすべて閲覧者のブラウザー内で行います。

## しくみ

| パス | 役割 |
| --- | --- |
| `site/` | 画面のファイル（HTML・CSS・JS）と `config.json`（GitHub・フォームへのリンク） |
| `data/changelog.tsv` | サイトの「更新履歴」の元データ。1行が1語の追加または削除 |
| `scripts/build_site.py` | `dist/` を再生成・検査し、検索用データとZIPを作って `public/` にまとめる |
| `public/` | 公開されるファイル一式。生成物なのでGit管理しない |

`main` に push すると、Cloudflare が `python scripts/build_site.py` を実行し、`public/` を公開します。版の名前は「更新履歴の最新の日付＋`master.tsv` のハッシュ」です（例：`20260921-dc11b032`）。

ビルドは次の場合に失敗し、公開中のサイトは前の版のまま残ります。

- `master.tsv` に空欄・重複がある
- コミットされた `dist/` が `master.tsv` から生成したものと一致しない（`python scripts/build.py` の実行忘れ）
- `changelog.tsv` の「追加」の語が `master.tsv` にない、または「削除」の語が残っている

## 辞書を更新するとき

1. `data/master.tsv` を編集し、追加分の確認元を追加記録に書く。
2. `data/changelog.tsv` に `日付<TAB>追加<TAB>読み<TAB>名前` の行を足す（削除は「削除」）。
3. `python scripts/build.py` で `dist/` を作り直す。
4. `python scripts/build_site.py` でサイトを作り、必要なら手元で確認する：`python -m http.server 8788 --directory public` → <http://localhost:8788>
5. コミットして push する。数分でサイトに反映されます。

収録依頼フォームのURLは `site/config.json` の `formUrl` に書くと、「収録候補・修正の連絡」にボタンが出ます。

## 初回の設定

1. <https://dash.cloudflare.com/sign-up> で無料アカウントを作ります。
2. ダッシュボードの「Workers & Pages」→「作成」→「Pages」→「Git に接続」を選びます。
3. GitHub を連携し、リポジトリ `H-Miyama-zip/F-VTD` へのアクセスを許可します（このリポジトリだけを選べば十分です）。
4. ビルドの設定：
   - プロジェクト名：`f-vtd`（公開URLが `https://f-vtd.pages.dev` になります。使用済みなら別の名前にします）
   - 本番ブランチ：`main`
   - フレームワーク プリセット：なし
   - ビルド コマンド：`python scripts/build_site.py`
   - ビルド出力ディレクトリ：`public`
5. 「保存してデプロイ」を押します。ログに `Built site ...: 20050 entries` と出て、公開URLが表示されれば完了です。

Python のバージョンが原因でビルドが失敗する場合は、プロジェクトの「設定」→「環境変数」に `PYTHON_VERSION` = `3.12` を追加して再実行してください。

## 無料プランの範囲

Cloudflare Pages の無料プランでは、ビルドは月500回まで、1サイトのファイルは2万個まで、1ファイルは25MiBまでです。静的ファイルの配信は、回数・転送量とも無制限です。現在のサイトは9ファイルで、最大のファイルは検索用データ（約1.3MB）です。push のたびにビルドが1回使われます。
