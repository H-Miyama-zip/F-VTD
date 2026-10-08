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
- `release.json` は更新単位ID、個別変更ID、生成入力と辞書・検索のハッシュを保存します。`public/data/latest.json` は同じ版とIDを指し、ZIPハッシュも保存します。
- 更新履歴は追加・訂正の前後・削除・内容取消し・記録訂正を表示します。導入前の復元記録、適用済み、公開確認済みを区別します。詳細の理由・根拠・確認日も表示します。
- 同日の履歴は台帳への追記順を逆にして新しい更新を先頭に表示します。IDの文字順では並べません。
- 通常検索JSONはmasterだけを使い、履歴中の旧読みや削除行を含めません。保留・下書きは履歴や配布物へ入れません。
- 公開確認記録は `data/publications.jsonl` です。ローカル生成は追記しません。公開日時不明の過去版を公開済みと推定しません。公開確認後の追記はmanifestの状態だけを変え、同じ版のimmutableな辞書・検索JSON・ZIPを変更しません。
- 開いている画面も定期確認と再表示時に公開確認の表示を更新します。同じ版の公開状態だけが変わる場合、検索データと検索処理を読み直しません。
- 同じ版で辞書のURL・ハッシュ・更新履歴などが変わったmanifestは公開状態だけの更新として受け入れません。
- `_headers` の `/files/*` のimmutable設定を維持しています。コード変更による生成内容の変化も新しい版とURLになります。

同じ確定入力・同じ圧縮ランタイムなら生成物は再現します。JSONLの意味が同じなら改行コードだけの差は版に影響しません。固定基準TSVは元のLFバイト列を保ちます。

## ホスティングについて

既存の設定は `wrangler.jsonc` です。GitHub連携はpush時にビルド・公開を実行し、main以外もプレビュー公開になり得ます。ローカル検証だけの作業ではpushやdeployを実行しないでください。今回の導入では設定の変更も公開の実行もしていません。

収録依頼フォーム等のリンクは `site/config.json` にあります。公開確認コマンドは公開そのものを実行しません。手順は [LEDGER.md](LEDGER.md) を参照してください。
