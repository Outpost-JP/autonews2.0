# autonews2.0

## 簡単な説明
hackernewsから最新の記事を取得します。まだ未完成なのでここから書き加えれる人のみ使用してください。

## main.py
完成済み。Google Cloud Functionにデプロイして環境変数を入力と、関数を５分に1回起動するようにCloud Schedulerでリクエストを送ると、hackernewsの更新内容を書き出してくれます。日付、タイトル、URL、ID（hackernewsの内部の一意のID）を取得します。

**注意**

https://hacker-news.firebaseio.com/v0/maxitem.json?print=pretty にアクセスして最新のニュースIDをD列の２行目以降のどこかに書き込んだ後使用してください。

**さもないと3000万回くらいhackernewsAPIにリクエストを送る可能性があります。ほんとに気を付けてください(要修正)**

## content_fetcher.py

Google Cloud Functionにデプロイして環境変数を入力し、main.pyのpublishをトリガーとしてmain関数を起動するようにしてください。
langchainのスキーマ定義がGPT4-previewを使うと微妙っぽくてエラーが頻発するので修正しておくこと。ここのところはChatGPTのJSONモードを使用するのがよさそう
また、一部の非同期関数がうまくいっていないせいでリード文生成が宇なくいかないことが多いのでそこの修正が必要そうです。
明日頑張って直す（つもり）