# 検索サイト

F-VTDの検索・配布用静的サイトです。画面は `site/`、生成先は `public/`、現役の辞書は `data/master.tsv`、更新履歴は `data/ledger.jsonl` です。更新方法・日付・取消し・原版更新の手順は [LEDGER.md](LEDGER.md) を参照してください。

## ローカル確認

```powershell
python scripts/ledger.py check
python scripts/build.py --check
python scripts/build_site.py --check
python scripts/build_site.py
python -m http.server 8788 --directory public
```

回帰テストは `python -m unittest discover -s tests -v` と `node --test tests/site.test.cjs` です。Nodeは画面処理のテストにだけ使い、辞書・サイト生成はPython標準ライブラリで動きます。画面テストは実際のapp.jsをDOM・検索Workerのスタブで実行し、実ブラウザーや実際の公開先への通信は行いません。

`--check` は読み取り専用です。サイトビルドは `dist/` と台帳を書き換えず、固定基準＋台帳の再現結果、現役master、4形式の整合性、公開確認記録を検査します。staleなdistは失敗します。辞書生成は `python scripts/build.py` で行います。検査失敗時は既存のdist/publicを変更しません。サイトは一時ディレクトリで完成させてから置換します。ディスク障害等によるdistの4ファイル置換全体の原子性は保証していません。

`public/` はGit管理外です。既存ディレクトリの置換には `.fvtd-generated` 印が必要です。旧ビルドで生成した印のないpublicがある場合は内容を確認して別の場所に保管するか、別の新規出力先を指定してください：

```powershell
python scripts/build_site.py --output C:\path\to\fresh-preview
```

## 配布版と履歴

- 版は「最新適用日＋SHA-256の先頭16桁」です。導入前しか記録がない場合は旧記録の追加日、さらに不明なら原版の日付を使い、その意味もmanifestに保存します。
- `updatedOn` はその日付、`dateMeaning` は日付の意味です。既存画面との互換用 `updatedAt` の日本時間00:00は表示のための正規化で、実際の適用時刻や公開時刻を表しません。`release.json` の `appliedThrough` は導入後の適用日がない場合nullです。
- 版のハッシュ入力は、実際の4辞書と検索JSON、版を埋め込む前のZIP用README、NOTICE、台帳、基準情報、原版ハッシュ、生成コード、Python・圧縮ランタイムです。版を含むZIPやrelease.jsonを入力に戻しません。
- `public/files/<version>/` に4辞書、通常検索JSON、ZIP、`release.json` を保存します。ZIPは4辞書・README.txt・NOTICE.md・release.jsonを含みます。
- サイトの検索・ダウンロードは現在の版だけを案内します。次版の生成時に旧版のpublicファイルは置き換わり、旧版URLでの継続配布は終了します。旧URLを最新版の内容で上書きして再利用しません。
- 公開を確認した版の実ZIPとrelease.jsonは、公開確認コマンドが `releases/<version>/` に保管します。サイト配信対象のpublicへはコピーしません。Git管理対象なので、保管物と公開記録を一緒に引き継げます。ローカルビルドだけでは保管物も公開記録も増えません。
- `release.json` は更新単位ID、個別変更ID、生成入力と辞書・検索・生成後READMEのハッシュを保存します。版を計算した後にREADMEを生成してハッシュを記録するため、版算式へは戻しません。版を含まない当時のREADMEテンプレートも `readmeTemplate` に保存し、入力ハッシュと照合して実READMEを検証します。`public/data/latest.json` は同じ版とIDを指し、ZIPハッシュも保存します。
- 新画面はmanifestの `ledgerChanges` で追加・訂正の前後・削除・内容取消し・記録訂正を表示します。行の出典URL・注記の差も、イベント自体の理由・根拠とは分けて表示します。旧main画面が既に開いている場合は、同じmanifestの `changes` に追加のafter行・削除のbefore行を直接渡します。訂正・取消し・記録訂正を追加や削除へ偽装せず、該当更新のタイトルにページ再読み込み案内を付けます。生成HTMLはapp.jsとstyle.cssの内容ハッシュをクエリーへ付け、再読み込み後もキャッシュ中の旧クライアントが残らないようにします。日付不明の履歴は旧画面のタイトルで明示し、その表示日を版の基準日と説明します（変更日を補完しません）。旧画面の検索・ダウンロードは新しい版へ切り替えられますが、5種類の詳細、日付の意味、公開確認の表示、plist案内やフォーカス修正には再読み込みが必要です。導入前の復元記録、適用済み、公開確認済みを区別します。詳細の理由・根拠・確認日も表示します。
- 同日の履歴は台帳への追記順を逆にして新しい更新を先頭に表示します。IDの文字順では並べません。
- 通常検索JSONはmasterだけを使い、履歴中の旧読みや削除行を含めません。保留・下書きは履歴や配布物へ入れません。
- 公開確認記録は `data/publications.jsonl` です。ローカル生成は追記しません。公開日時不明の過去版を公開済みと推定しません。公開確認後の追記はmanifestの状態だけを変え、同じ版のimmutableな辞書・検索JSON・ZIPを変更しません。
- 開いている画面も定期確認と再表示時に公開確認の表示を更新します。同じ版の公開状態だけが変わる場合、検索データと検索処理を読み直しません。
- 同じ版で辞書のURL・ハッシュ・更新履歴などが変わったmanifestは公開状態だけの更新として受け入れません。
- `_headers` の `/files/*` のimmutable設定を維持しています。コード変更による生成内容の変化も新しい版とURLになります。

同じ確定入力・同じ圧縮ランタイムなら生成物は再現します。公開確認では、内容が一致する再圧縮ZIPやrelease.jsonのJSON空白差も受け入れます。同じ版の確認後ビルドはdescriptorと全メンバーの内容を照合し、確認済みZIP・release.jsonの保管した生バイトを採用します。新しく再圧縮したバイトでimmutable URLを上書きしません。JSONLの意味が同じなら改行コードだけの差は版に影響しません。固定基準TSVは元のLFバイト列を保ちます。

## ホスティングについて

設定は `wrangler.jsonc` です。本番のGitHub連携は `main` のpushで `python scripts/build_site.py` と `npx wrangler deploy` を実行します。作業ブランチへのpushは、本番の更新ではありません。

`main`へ統合してpushする場合も、本番の自動ビルド・デプロイの対象になります。統合やデプロイの成功だけでは「公開確認済み」と扱いません。公開先の版・ZIP・release.jsonのハッシュを人が照合してから、公開確認コマンドで成果物の保管と台帳への記録を行います。詳しい手順は [LEDGER.md](LEDGER.md) を参照してください。

CloudflareのWorker Previewsでは、作業ブランチを `npx wrangler preview` で別環境へ公開します。設定の `previews: {}` はこのコマンドに必要です。ブランチ用URLは `<preview-name>-f-vtd.tomaranaina.workers.dev`、個別デプロイ用URLは `<deployment-id>-f-vtd.tomaranaina.workers.dev` です。URLが有効な間は、知っている人が誰でもアクセスできます。

通常はCloudflareの「プレビューブランチのビルド」と「プレビュー URL」をオフにし、リポジトリでも `preview_urls: false` を維持します。自動ビルドだけを止めても、既に公開したURLは閉じません。ダッシュボードでURLだけを止めても、設定が `true` のままだと後のデプロイで再び有効になる可能性があります。

一時公開による検証は、公開するブランチと終了後の停止を確認してから行います。必要な間だけ自動プレビュー生成とプレビューURLを有効にし、検証後は両方をオフにして保存します。設定ファイルも `preview_urls: false` に戻し、自動ビルド停止後に作業ブランチへpushします。本番Worker URLはオンのままにします。最後にブランチ用URL、個別デプロイ用URL、両方のZIP URLがアクセス不可で、本番の版と配布URLが変わっていないことを確認します。

2026-10-09には `codex/change-ledger` を一時公開し、検索・変更履歴・ZIPを検証してからプレビューを停止しました。このテストを本番の公開確認記録へ追記していません。今後のローカル検証では公開設定を変更する必要はありません。

収録依頼フォーム等のリンクは `site/config.json` にあります。公開確認コマンドは公開そのものを実行しません。手順は [LEDGER.md](LEDGER.md) を参照してください。
