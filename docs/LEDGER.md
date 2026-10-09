# 変更台帳と更新手順

## 保存場所

| ファイル | 役割 |
| --- | --- |
| `data/master.tsv` | 現在有効な辞書。通常ビルドによる自動修正はしない |
| `data/baseline/master.tsv`・`info.json` | 台帳導入時の固定した20,050行と基準コミット・ハッシュ・復元記録の封印 |
| `data/ledger.jsonl` | 1行＝確定した更新単位。各更新に複数の個別変更。追記順に検査・再現 |
| `data/upstreams.json` | 原版の版名、原ファイル名とSHA-256。旧版を上書きせず版ごとに追加 |
| `data/publications.jsonl` | 人が公開を別途確認した版、公開時刻、URL、確認方法、変更ID、成果物ハッシュ |
| `releases/<version>/` | 公開確認済みの実ZIPとrelease.json。サイト配信対象外・Git管理対象 |
| `data/drafts/` | 編集可能な下書き（Git対象外・ビルド対象外） |

旧 `data/additions.tsv` は廃止しました。36件の追加日、URL、noteは台帳のafter行・`legacy_added_on`・根拠へ移し、旧ファイルのSHA-256と復元元コミットも記録しています。旧 `docs/upstream-hashes.json` の5ファイルのハッシュは `data/upstreams.json` に保全しました。移行スクリプト内の旧パスはGit履歴を読むための歴史的参照です。独自追加の現状一覧が必要なときはmasterから `origin=added` を抽出し、別の常設台帳を作らないでください。

## 識別・日付・検査

更新単位の `id` と個別変更の `id` は一意で、公開版にも両方を保存します。対象は9列の完全なbefore/after行です。行番号は識別子ではありません。差分候補の表示に使う行ハッシュは内容から計算され、訂正時に変わります。人物の恒久IDを推定するものではありません。

- `scope=reconstructed`：導入前の復元。固定基準に既に含まれるため再適用しない。封印済みの復元範囲を増やすことはできない。
- `scope=applied`：導入後、確定した変更。基準から順に適用する。
- `status=final`：台帳では確定済みのみ。draft/pendingがあれば検査失敗。下書きは別ファイルに置く。
- `checked_on`：資料を確認した日。`applied_on`：masterへの変更を確定・適用した日。日付は日本時間のYYYY-MM-DD。
- `added_on`：行の元の追加日。原版由来は空欄。訂正後もoriginと追加日を維持し、訂正日は更新単位の適用日に置く。
- `legacy_added_on`：旧記録の追加日をそのまま移した値。確認日・適用日と解釈し直さない。
- 復元元の `committed_at`：Gitのコミット時刻。適用日・公開日を断定しない。
- `published_at`：人が確認した公開時刻。ISO 8601、必ず `+09:00` または `Z` 等のタイムゾーン付き。公開確認日時は根拠も添える。
- 不明値はnullと `unknown` の列名で表す。過去の理由はnoteから推測せずnull、確認日・適用日・公開日は不明のまま。

台帳はハッシュ鎖を持ち、基準情報で復元部分を固定します。既にGit HEADにある基準・確定記録の先頭部分・原版ハッシュは書き換え検出を行います。新しい未コミット記録は確定後にSHA-256鎖で検査しますが、記録と独立した基準をすべて再計算する改ざんを防ぐ暗号署名基盤ではありません。

完全一致の辞書登録（reading・word・3品詞）が重複すれば失敗します。別読みや同じ読みから別の名前へ変換する行を統合しません。単なる行順変更は履歴イベントにせず、配布順はmasterに従います（成果物が変われば版のハッシュは変わる）。

基準＋台帳とmasterの全列が一致しない、beforeが現在の状態と違う、重複ID、競合・二重適用、無効なorigin/追加日がある場合は出力前に失敗します。通常ビルドは台帳を追記しません。

## 通常の追加・訂正・削除

まずmasterを編集します。追加行にはsource_urlとadded_onを記入します。訂正時はoriginとadded_onを保持します。誤登録を削除する場合はmasterから外し、旧内容を台帳へ残します。今回の実装では実データの変更を行っていません。

```powershell
python scripts/ledger.py draft --id update-20261006-01 --applied-on 2026-10-06 --output data/drafts/update-20261006-01.json
```

下書きJSONを編集し、個別変更ごとにreason、evidence.urls（複数可）、evidence.text（最小記述）、checked_onを補います。判明した項目はunknownから除きます。確認日不明ならnullとunknown内のchecked_onを維持します。根拠不明も推測で埋めずunknownへ記録します。重複・未定義の不明項目や、値とunknownの矛盾は検査で失敗します。通常変更では理由は必須です。適用日は既存の最新適用日・基準の既知の追加日より前にはできません。日付例は実際の適用・確認日に置き換えてください。

根拠URLと追加行のsource_urlは、ホスト名を持つhttp/httpsの絶対URLにします。空のホスト、不正なポート、空白や制御文字を含むURLは確定・ビルド前に拒否します。URL先の実在や資料内容の正しさを自動確認するものではありません。

差分だけで人物の同一性を推定しません。既定の差分は独立したdeleteとaddです。訂正と判断した場合は表示されたbefore/afterの行ハッシュを対応ファイルへ記入します：

```json
[{"before":"削除候補の64桁ハッシュ","after":"追加候補の64桁ハッシュ"}]
```

```powershell
python scripts/ledger.py draft --id update-20261006-01 --applied-on 2026-10-06 --pairs data/drafts/pairs.json --output data/drafts/update-20261006-01-paired.json
```

同じ対象を複数の対応へ割り当てると失敗します。同じファイルを上書きしません。before/afterを点検し、理由・根拠を補った下書きだけを確定します：

```powershell
python scripts/ledger.py confirm data/drafts/update-20261006-01-paired.json
python scripts/ledger.py check
python scripts/build.py
python scripts/build.py --check
python scripts/build_site.py --check
python scripts/build_site.py
python -m unittest discover -s tests -v
```

追加のみなら最初の下書きをconfirmします。confirmは台帳の候補全体を検査し、masterの差分を完全に説明するときだけ1更新単位を追記します。masterを編集せず、公開を記録しません。出力先を変える場合は各スクリプトに `--root`、サイトにはさらに `--output` を使えます。

## 内容取消しと台帳の記録訂正

確定済みイベントを編集・削除しません。

**辞書内容の取消し**は対象IDのbefore/afterを完全に反転する新しい `undo` です。先にmasterを対象変更のbefore状態へ手で戻します。履歴は消えません。

```powershell
python scripts/ledger.py draft --id update-20261007-undo --applied-on 2026-10-07 --undo 対象変更ID --reason "取り消した理由" --output data/drafts/undo.json
```

根拠・確認日を補いconfirmします。取消しには `related_ids` で1件の元変更を指定します。後続訂正で対象状態が変わっている場合や同じ元変更を二度取消す場合は失敗します。複数段階の訂正を戻すときは最新から逆順に判断します。復元された追加も、現在の行に一致すれば明示的に取消せます。

**理由・根拠・確認日・不明項目の記録訂正**は `annotate` です。辞書を編集しません。

```powershell
python scripts/ledger.py draft --id update-20261007-note --applied-on 2026-10-07 --annotate 対象変更ID --reason "記録を訂正する理由" --output data/drafts/annotation.json
```

`metadata_before` は現在有効な対象メタデータです。`metadata_after` のreason/evidence/checked_on/unknownを修正します（変更なしでは確定不可）。イベント自体にも記録訂正の根拠を添えます。元記録のafterや適用日・変更種別は変更しません。間違った適用日の注記は新記録の理由・根拠に明示し、元の事実記録を改変しないでください。beforeメタデータの不一致は競合として検出します。

## 公開状態の確認

ビルドだけでは公開済みになりません。現時点のpublicationsは空です。導入前の公開版が存在しても、36件それぞれの初回公開時刻は不明です。

将来、別途承認された公開を実行し、配布先の版・ZIP・release.jsonのハッシュを人が確認した後だけ、以下を実行します。このコマンドはネットワークへの送信もデプロイも行いません。

公開に使うZIPとrelease.jsonは、次のサイトビルドでpublicが置き換わる前に2ファイルを別の場所へコピーしてください。例えば `data/release-candidates/実際の版/` を使えます。この一時候補はGit管理外で、公開確認・保管が終わるまで残します。ビルドしただけの候補を公開済みとして扱いません。

```powershell
python scripts/publications.py --release data/release-candidates/版/release.json --zip data/release-candidates/版/F-VTD-版.zip --id publication-固有ID --published-at "2026-10-08T12:00:00+09:00" --url "https://配布先/files/版/release.json" --evidence "公開先の版とrelease/ZIPのSHA-256を確認した方法"
python scripts/build_site.py
```

手元にpublicの2ファイルが残っていれば、そのパスを指定しても構いません。確認が遅れ、master・生成コード・Python環境が次版へ進んでいても、保存した旧版そのものを検査できます。現在のmasterやdistとの完全一致は要求しません。固定基準、確定台帳の先頭から当該版までのID・状態・日付、版算式、ZIP内外のrelease.json、4辞書と検索データのハッシュ・登録内容、ZIPのREADME実バイトを照合します。新形式は `hashes["README.txt"]` と `readmeTemplate` に当時のテンプレートを保持し、その入力ハッシュから版の埋め込み結果まで照合します。現在のmaster・生成コード・Pythonからの再生成一致を必須にしません。行順だけの変更は記録対象外なので、配布時の並び順は保存した辞書を使います。

旧形式のrelease.json（生成後READMEハッシュがないもの）を後日確認するときは、当時の版を埋め込む前の `package/README.txt` も保存し、公開確認コマンドへ `--readme-template C:\path\to\historical-README.txt` を指定してください。descriptor内のテンプレート入力ハッシュと一致し、そこから当該版・語数を埋めたREADMEがZIP内の実バイトと一致する場合にだけ受け入れます。当時のテンプレートを確保できない旧候補は拒否します。旧release.jsonやZIPを新形式へ書き換えて同じ版を流用しません。既に保管・確認済みの成果物は台帳の生バイトハッシュで保全し、再確認を要求しません。

検査に通ると、入力の生バイトを `releases/<version>/release.json` と `releases/<version>/F-VTD-<version>.zip` に保存し、公開確認記録を追記します。既存の保管物は別内容で上書きしません。公開記録の書き込みに失敗した場合は、その操作で新規作成した保管物を取り消します。サイトの通常ビルドは保存済みの公開物と記録のハッシュを検査し、保管物の欠落・改変で失敗します。

公開先の実在・実際の配布バイトとの一致は人が証拠に記録します。旧生成コードや旧Python環境をこのコマンドで再実行するものではありません。公開記録は追記専用で、同じ版の重複確認を拒否します。通常ビルドは公開記録と保管物を増やしません。ZIP内の全内容が一致する再圧縮やrelease.jsonのJSON空白差は受け入れます。同じ版のビルドでは保存した確認済み生バイトを使い、再圧縮した別のZIPに置き換えません。READMEなどメンバー内容の不一致は確認前に拒否します。今回の検証は一時的なフィクスチャで行い、実台帳へ架空の公開記録を入れていません。

`releases/` の公開済みZIP・release.jsonと `data/publications.jsonl` は一緒にGit管理して引き継ぎます。ZIPとrelease.jsonは改行変換を禁止して生バイトを維持します。公開リポジトリへ将来pushすれば、この保管フォルダーもリポジトリから閲覧できます。導入前の版に新方式のrelease.jsonがない場合や、公開日時・配布物が不明な場合は、公開記録を推測で作らず別途調査します。

## 原版更新前の監査

今回、原版更新は実行しません。原版は不変のまま保管し、新版の版名・原ファイル名・ハッシュを `data/upstreams.json` へ別キーで追記します。古い版のハッシュを変更しません。単なる原版アップグレードをoriginの付け替えで表現しないでください。

原版が1版だけなら通常下書きはその版を使います。複数版を記録した後は `draft --upstream 原版キー` が必須です。旧版を自動選択したり、カタログの並び順から最新版を推定したりしません。異なる版を対象にした変更が混在する下書きは、各変更のupstreamを人が確認してください。取消し・記録訂正は対象記録の参照版を引き継ぐため、`--upstream` を指定しません。

未改変の新版原版を作業用コピーでmasterの9列に正規化します。3つの原版TSVを照合し、origin=upstream、source_url/note/added_onは空欄とします。削除専用ファイルは有効辞書へ混ぜません。正規化を行っても未改変原ファイルのハッシュ・版名は保管します。現在のF-VTD masterだけを見て「原版で修正済み」と判断してはいけません。

```powershell
python scripts/ledger.py audit-upstream C:\path\to\unmodified-upstream-normalized.tsv
```

監査は過去のupstream訂正・削除・取消しをすべて比較し、手動判断用JSONを返します。入力を変更したり、自動で再適用したりしません。

| 状況 | 報告と判断 |
| --- | --- |
| 旧登録が存在・復活 | old_present。過去の削除・訂正を調べ、自動復活させない |
| 原版側で変更後だけ存在 | already_corrected。未改変原版の一致を確認して二重適用しない |
| 旧・新の両方 | both_present。正当な別読みを含め独立して判断 |
| どちらもない | neither_present。別の対象へ同じ訂正を適用しない |
| 名前・品詞・由来等が違う | suspicious候補。読みか名前の一致だけでは対象を確定しない |
| 同じ対象へ複数一致 | multiple_matches。作業停止して対象を明示 |
| 過去の複数訂正が対象を共有 | related_history。競合とは断定せず、取消しも含め順序・現状態を点検 |
| 同じ訂正の二重適用 | 台帳再現のbefore不一致・重複ID・登録競合で失敗 |

名前も読みも全く変わった未知の対象を機械的に特定することはできません。監査は同一人物の推測を行わず、正当な別読み・短縮読み・同名別人を自動結合しません。将来の更新では監査結果を解決してからmasterを編集し、その差分と明示した対応関係を通常の台帳へ記録してください。汎用の自動取り込み・再適用はありません。

## 導入前履歴の復元範囲

公開main `365960717ee6bb06e8f270461fa524f8ca3bd464` のGit履歴を監査しました。masterが最初に現れる `fbcfb896deeef5c542ebb8ac1eb96262dfb5cb4d` には20,014 upstream＋36 addedがあります。`b14670e` は旧追加一覧のリネーム、`b435b994f418dcdaeae7e98edc749e4d02e992d3` はmasterへのadded_on導入です。added_on以外の全列は一致し、追跡範囲の置換型訂正・削除は0件です。

36件の追加は1更新単位・36個別IDで復元しています。noteに正しい読みを追加し原版の読みを互換性のため残したと明記された1件も、追加として保全しています。初回インポート前の編集履歴、個別の確認日・適用日・公開日、推測を要する理由は不明です。原版の削除用ファイルはF-VTDのmasterで削除した履歴とは区別します。

`migrate_ledger.py` は監査した履歴専用の一回限りのツールです。基準にあるmasterと一致しない作業データ、既存の台帳、旧一覧の情報欠落、未想定の過去の内容差分があれば停止します。通常ビルドや原版更新で実行しないでください。
