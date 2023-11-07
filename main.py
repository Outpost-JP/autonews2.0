import os
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
import gspread
from backoff import on_exception, expo
from requests.exceptions import RequestException

# 環境変数からスプレッドシートIDとクレデンシャルを取得
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
GOOGLE_CREDENTIALS_BASE64 = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_BASE64')

# エラーハンドリング用の最大リトライ回数
MAX_RETRIES = 5

# エラー発生時のバックオフ指数的戦略と最大再試行回数
@on_exception(expo, RequestException, max_tries=MAX_RETRIES)
def fetch_hn_api(endpoint):
    url = f'{HN_API_BASE}/{endpoint}.json'
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

# Base64エンコードされたGoogleクレデンシャルをデコード
creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
creds = json.loads(creds_json)

# gspreadクライアントを認証
gc = gspread.service_account_from_dict(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

# Hacker News APIの基本URL
HN_API_BASE = 'https://hacker-news.firebaseio.com/v0'

def get_last_checked_id():
    value = sheet.acell('B1').value
    if not value:
        raise ValueError("B1セルに最新の記事IDが存在しません。")
    return int(value)

def update_news_on_sheet(last_checked_id):
    maxitem = fetch_hn_api('maxitem')
    if maxitem <= last_checked_id:
        print(f"No new items to check. {maxitem} <= {last_checked_id}")
        return

    # /v0/newstories エンドポイントから最新50件の記事IDを取得
    latest_news_ids = fetch_hn_api('newstories')

    for news_id in reversed(latest_news_ids):
        if news_id > last_checked_id:
            news_data = fetch_hn_api(f'item/{news_id}')
            if news_data and not news_data.get('dead'):
                datetime_jst = datetime.utcfromtimestamp(news_data['time']) + timedelta(hours=9)
                row = [
                    datetime_jst.strftime('%Y-%m-%d %H:%M:%S'),
                    news_data.get('title'),
                    news_data.get('url'),
                    news_id
                ]
                write_to_sheet_with_retry(row)
    sheet.update('B1', maxitem)

@on_exception(expo, (gspread.exceptions.APIError, gspread.exceptions.GSpreadException), max_tries=MAX_RETRIES)
def write_to_sheet_with_retry(row):
    index = 2  # 新しいエントリを挿入する行のインデックス
    sheet.insert_row(row, index=index)

def check_and_update_new_hn_content(event, context):
    try:
        last_checked_id = get_last_checked_id()
        update_news_on_sheet(last_checked_id)
    except Exception as e:
        print(f"An error occurred: {e}")
        raise  # 最新記事ID取得失敗など、致命的な問題が発生した場合はスクリプトを停止

if __name__ == "__main__":
    check_and_update_new_hn_content(None, None)