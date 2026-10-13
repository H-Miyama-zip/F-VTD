# F-VTD

VTuber変換辞書 ver1_2（とぷんろぷ氏、2026-09-03）を基にした非公式の改変版です。原作者・各VTuberの公式配布物ではありません。

## 収録状況

2026-10-13：追加候補から103語を追加しました。原版20,014語に合計209語を追加し、収録数は20,223語です。「さつ☆まい」はいったん保留とし、収録から除外しました。

配布4形式・検索データを再生成し、`data/master.tsv` の20,223語と一致することを確認しました。ダウンロードZIP内の辞書も `dist/` と一致しています。

2026-10-09：追加候補から37語を追加しました。

2026-09-22：福岡・福島にゆかりのあるVTuber 12名の23語、ユニバースプロダクション所属VTuber 3名の6語、RIOT MUSIC所属VTuber 2名の4語を追加しました。

追加・訂正・削除の記録と確認元は [data/ledger.jsonl](data/ledger.jsonl)、更新・検査の手順は [docs/LEDGER.md](docs/LEDGER.md) を参照してください。現在の有効な登録は `data/master.tsv` です。

変更台帳方式へ統合し、追加候補からの173変換を台帳へ記録しました。既存の変更履歴と辞書の内容・元の追加日を保持し、旧 `data/additions.tsv` は変更台帳へ統合しています。

2026-10-14：作業ブランチをmainへ反映し、Cloudflareの自動公開を確認しました。公開版は `20261013-273ea18bcb504452`（20,223語）です。公開先の配布4形式・検索データ・ZIP・release.jsonを照合し、公開確認記録と実配布物を保存しました。詳細は [公開確認記録](data/publications.jsonl) と [検証報告](docs/IMPLEMENTATION_REPORT.md) を参照してください。

## ダウンロード・導入

[dist](dist) から利用するIMEに対応するファイルを1つダウンロードしてください。GitHubのファイル表示画面にある「Download raw file」を利用できます。

| 環境 | ファイル | 文字コード |
| --- | --- | --- |
| Microsoft IME | [MicrosoftIME.tsv](dist/VTuber変換辞書_MicrosoftIME.tsv) | UTF-16LE（BOM付き） |
| Google 日本語入力 | [Google日本語入力.tsv](dist/VTuber変換辞書_Google日本語入力.tsv) | UTF-16LE（BOM付き） |
| ATOK | [ATOK.tsv](dist/VTuber変換辞書_ATOK.tsv) | UTF-16LE（BOM付き） |
| macOS | [macOS.plist](dist/VTuber変換辞書_macOS.plist) | UTF-8 |

Microsoft IME・Google日本語入力・ATOKはユーザー辞書へインポートしてください。macOSのplistは「システム設定」→「キーボード」→「ユーザ辞書」（テキスト置換）の一覧へドラッグします。複数形式を同じ辞書へ登録すると重複する場合があります。原版ver1.0／ver1.1由来の誤登録の削除については、原配布物の更新案内を参照してください。本版には原版の削除専用ファイルを同梱していません。

## 検索サイト

<https://f-vtd.tomaranaina.workers.dev> で、名前・読みの検索と辞書のダウンロードができます。サイトの構成・更新手順は [docs/SITE.md](docs/SITE.md) を参照してください。

作業ブランチでの生成・コミットとWeb公開は別の操作です。公開する場合は、本番ブランチへの反映と公開結果を確認します。

## 収録の依頼・誤りのお知らせ

未収録のVTuberの追加、読みの追加、名前や読みの誤りの訂正は、[収録依頼フォーム](https://docs.google.com/forms/d/e/1FAIpQLSfPnkf9U3Ut1OrpjrpTryg-yYFmgK8Ct03w9phtO1GAHIQIGQ/viewform)で受け付けています。検索サイトで見つからなかった名前は、検索結果のボタンからそのまま依頼できます。GitHubのIssueでも受け付けます。

F-VTDについてのご連絡を、原作者の窓口へ送らないでください。

## 追加候補の編集

「辞書追加アプリ.cmd」で、名前・読み・別の変換を確認し、追加予定を候補DBに保存できます。「追加する」は追加予定の選択で、「候補DBに保存する」で候補DBへ保存します。辞書への追加は、保存した追加予定をもとにCodexへ依頼します。

保存済みの追加予定には、辞書へ登録済みの候補も残ります。辞書へ反映する前に、全名と指定された別の変換を「読み・名前」の組み合わせごとに `data/master.tsv` と照合し、未登録の組み合わせだけを追加します。

画面操作は [アプリの使い方](tools/candidate-editor/アプリの使い方.txt)、辞書追加・公開までの流れは [追加候補の保存と辞書への反映](docs/CANDIDATES.md) を参照してください。

## 出典・利用条件

[NOTICE.md](NOTICE.md) を参照してください。原配布物そのものはGit管理から除外しています。
