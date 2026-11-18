# F-VTD 作業指示書

このファイルは、F-VTDを作業する人とCodex向けの共通手順です。F-VTDは「VTuber変換辞書 ver1_2」を土台に、VTuberの名前と読みを継続的に追加・訂正して配布する非公式の改変版です。原版の利用条件と配布時の表記は [NOTICE.md](NOTICE.md) を参照してください。

## 作業を始める前に

1. `git status --short --branch` で現在のブランチ、未コミット変更、ステージ済みの差分を確認する。編集に着手する前に `git fetch origin` でGitHub側の更新を取得し、手元との差分を確認する。GitHub上で直接更新されたREADMEなども取り込む際に保持する。
2. 変更は `codex/` で始まる作業ブランチで進める。ブランチ名の指定があればそれに従う。既存の変更が今回の作業と重なる場合は、元の作業を保持して別の作業ツリーを使うなど、差分が混ざらない形で進める。
3. 依頼の対象を確認する。候補DBへの保存、配布辞書への反映、本番公開、利用者のIMEへの取り込みは、それぞれ別の操作として扱う。

## 候補の確認と辞書の更新

1. 調査資料や収録依頼から候補を追加するときは、候補DBの主一覧・別管理候補と `data/master.tsv` を照合する。本人や運営の情報などを優先して名前・読み・本人の対応を確認し、確認元URLを残す。根拠が足りない候補は保留する。[候補DBの手順](docs/CANDIDATES.md)
2. 候補DBから辞書に反映するときは、保存済みの採用分を対象にする。全名読みと指定された別の変換を「読み・名前」の組み合わせごとに照合し、未登録の組み合わせを追加する。既存の変換を訂正・削除するときは、その変更の根拠と対象を確認する。
3. 辞書の編集元は `data/master.tsv`。変更理由・根拠・確認日・適用日を [変更台帳の手順](docs/LEDGER.md) に従って `data/ledger.jsonl` に追記する。既存の確定記録は保持し、確認できない情報は不明として記録する。配布ファイルと検索データはスクリプトで再生成する。
4. 収録数や変更内容に応じて [README.md](README.md) を更新する。原配布物、候補DB、作業記録、依頼者の連絡先は手元で管理する。`outputs/` と `public/` はGit管理外。

## 検証

辞書を更新したら、台帳への記録を確定した後、リポジトリ直下で検証と生成を行う。

```powershell
python scripts/ledger.py check
python scripts/build.py
python scripts/build.py --check
python scripts/build_site.py --check
python scripts/build_site.py
python -m unittest discover -s tests -v
```

検索サイトを変更したときは `node --test tests/site.test.cjs` で検索と画面の動作を検証する。候補編集アプリを変更したときは、[候補編集アプリの検証手順](tools/candidate-editor/README.txt) に従い、変更に対応するPython・Nodeのテストを実行する。

## コミット

- 作業ブランチで、ひとつの目的を説明できる単位でコミットする。辞書更新では、対応する `data/master.tsv`、`data/ledger.jsonl`、再生成した `dist/`、更新したREADMEを同じコミットに含める。
- `git diff` で内容を確認し、今回の変更だけを `git add -- 対象パス` でステージする。同じファイルに別の作業が混ざっている場合は、対象の差分だけを選ぶ。ステージ後は `git diff --cached` と `git diff --cached --check` で確認してからコミットする。
- コミットメッセージには対象と変更内容を書く。例は `git commit -m "Add 82 planned dictionary conversions"`。コミットの作者名はリポジトリのGit設定に従う。

## 本番公開

1. 公開が依頼に含まれる場合は、照合に使うZIPと `release.json` を次のサイトビルドで `public/` が置き換わる前に退避する。検証済みの作業を `main` に統合してpushすると、Cloudflare Workersの本番ビルドとデプロイが始まる。[ホスティングの手順](docs/SITE.md#ホスティングについて)
2. デプロイ完了後、本番の配布版、ZIPと `release.json` のハッシュを照合する。照合が済んでから `data/publications.jsonl` に公開確認を記録し、実際の配布物を `releases/<version>/` に保管する。公開確認記録と保管物は一緒にコミットする。[公開確認と保管の手順](docs/LEDGER.md#公開状態の確認)
3. 作業結果には、実施した検証、作成したコミット、依頼範囲内で確認した公開版を記載する。
