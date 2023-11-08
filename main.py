import os
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
import gspread
from backoff import on_exception, expo
from requests.exceptions import RequestException
import time

# 環境変数からスプレッドシートIDとクレデンシャルを取得
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')

# エラーハンドリング用の最大リトライ回数
MAX_RETRIES = 3

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
    # F1セルから最後にチェックしたIDを取得します。
    cell = 'F1'
    value = sheet.acell(cell).value
    if not value:
        raise ValueError(f"{cell}セルに最新の記事IDが存在しません。")
    return int(value)

def update_news_on_sheet(last_checked_id):
    maxitem = fetch_hn_api('maxitem')
    print(maxitem)
    # 最新の500件の記事IDを取得
    new_news_ids = fetch_hn_api('newstories')
    
    # last_checked_idとmaxitemの間にある新しい記事IDを取得
    new_news_ids = [news_id for news_id in new_news_ids if last_checked_id < news_id <= maxitem]
    
    for count, news_id in enumerate(new_news_ids):
        try:
            news_data = fetch_hn_api(f'item/{news_id}')
            if news_data and not news_data.get('dead'):
                write_news_to_sheet(news_data)
        except Exception as e:
            print(f"Non-fatal exception caught: {e}")
        
        time.sleep(1)  # news_idごとに1秒間隔
        if count % 10 == 9:
            time.sleep(5)  # 10個ごとに追加で5秒間隔


def write_news_to_sheet(news_data):
    datetime_jst = datetime.utcfromtimestamp(news_data['time']) + timedelta(hours=9)
    row = [
        datetime_jst.strftime('%Y-%m-%d %H:%M:%S'),
        news_data.get('title'),
        news_data.get('url'),
        news_data.get('id')
    ]
    # Insert the news data row into the spreadsheet, waiting 1 second before each write operation
    write_to_sheet_with_retry(row)
    time.sleep(1)
    '''
    title = news_data.get('title')
    url = news_data.get('url')
    if title and url:
        
    '''

@on_exception(expo, (gspread.exceptions.APIError, gspread.exceptions.GSpreadException), max_tries=MAX_RETRIES)
def write_to_sheet_with_retry(row):
    try:
        index = 2  # 新しいエントリを挿入する行のインデックス
        sheet.insert_row(row, index=index)
    except gspread.exceptions.APIError as e:
        print(f"APIError encountered: {e}")
    except gspread.exceptions.GSpreadException as e:
        print(f"GSpreadException encountered: {e}")

def check_and_update_new_hn_content(event, context):
    try:
        print("Starting update process...")
        last_checked_id = get_last_checked_id()
        update_news_on_sheet(last_checked_id)
        print("Update process completed.")
    except Exception as e:
        print(f"An error occurred: {e}")
        raise

if __name__ == "__main__":
    check_and_update_new_hn_content(None, None)
