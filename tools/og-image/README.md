# リンクカード用画像

X などでサイトのURLを共有したときに表示される画像（`site/og.png`、1200×630）の元データです。`og.html` を編集したら、Microsoft Edge（または Chrome）の画面なしモードで書き出し直します。リポジトリ直下で実行します（Windows・Git Bash の例）。

```sh
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu --hide-scrollbars \
  --user-data-dir="$TEMP/edge-og" --window-size=1200,630 \
  --screenshot="$(cygpath -w "$PWD/site/og.png")" "file:///$(cygpath -m "$PWD/tools/og-image/og.html")"
```

語数は追加のたびに変わるため、画像には「20,000語以上」と書いています。X はカード画像をしばらくキャッシュするので、差し替えてもすぐには反映されないことがあります。
