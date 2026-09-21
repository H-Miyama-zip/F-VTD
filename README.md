# F-VTD

VTuber変換辞書 ver1_2（とぷんろぷ氏、2026-09-03）を基にした非公式の改変版です。原作者・各VTuberの公式配布物ではありません。

## 今回の変更

2026-09-21：原版20,014語に36語を追加し、合計20,050語。

追加内容・確認元は [data/additions.tsv](data/additions.tsv) を参照してください。

## ダウンロード・導入

[dist](dist) から利用するIMEに対応するファイルを1つダウンロードしてください。GitHubのファイル表示画面にある「Download raw file」を利用できます。

| 環境 | ファイル | 文字コード |
| --- | --- | --- |
| Microsoft IME | [MicrosoftIME.tsv](dist/VTuber変換辞書_MicrosoftIME.tsv) | UTF-16LE（BOM付き） |
| Google 日本語入力 | [Google日本語入力.tsv](dist/VTuber変換辞書_Google日本語入力.tsv) | UTF-16LE（BOM付き） |
| ATOK | [ATOK.tsv](dist/VTuber変換辞書_ATOK.tsv) | UTF-16LE（BOM付き） |
| macOS | [macOS.plist](dist/VTuber変換辞書_macOS.plist) | UTF-8 |

各IMEのユーザー辞書のインポート機能で読み込んでください。複数形式を同じ辞書へ登録すると重複する場合があります。原版ver1.0／ver1.1由来の誤登録の削除については、原配布物の更新案内を参照してください。本版には原版の削除専用ファイルを同梱していません。

## 検索サイト

<https://f-vtd.tomaranaina.workers.dev> で、名前・読みの検索と辞書のダウンロードができます。サイトの構成・更新手順は [docs/SITE.md](docs/SITE.md) を参照してください。

## 出典・利用条件

[NOTICE.md](NOTICE.md) を参照してください。原配布物そのものはGit管理から除外しています。
