#コンテンツ内容テスト

'''import asyncio
from langchain.document_loaders import HNLoader

# 非同期でHNLoaderを使用してデータをロードし、テキストファイルに保存する
async def main():
    # HNLoaderを初期化して最新のコンテンツをロード
    loader = HNLoader("https://news.ycombinator.com/newest")
    documents = loader.load()  # 非同期処理を待つ

    # テキストファイルに保存
    with open("hn_content.txt", "w", encoding='utf-8') as file:
        for doc in documents:
            # ドキュメントの内容をそのままテキストとして書き込む
            file.write(str(doc) + "\n\n")

# asyncioを使用してメイン関数を実行
asyncio.run(main())
'''

#base64テスト
import os
import base64

'''
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')
# Base64エンコードされたGoogleクレデンシャルをデコード
creds = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
print(creds)
'''