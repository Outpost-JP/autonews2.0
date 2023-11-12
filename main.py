import os
import requests
import base64
import json
from datetime import datetime, timezone, timedelta
import gspread
from backoff import on_exception, expo
from requests.exceptions import RequestException
import time
from google.cloud import pubsub_v1
import logging

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 環境変数からスプレッドシートIDとクレデンシャルを取得
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')

# エラーハンドリング用の最大リトライ回数
MAX_RETRIES = 3

# Hacker News APIの基本URL
HN_API_BASE = 'https://hacker-news.firebaseio.com/v0'

# Base64エンコードされたGoogleクレデンシャルをデコード
creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
creds = json.loads(creds_json)

# gspreadクライアントを認証
gc = gspread.service_account_from_dict(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).sheet1

# API呼び出しのリトライ用デコレータ
@on_exception(expo, RequestException, max_tries=MAX_RETRIES)
# Hacker News APIを呼び出す関数
def fetch_hn_api(endpoint):
    try:
        url = f'{HN_API_BASE}/{endpoint}.json'
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except RequestException as e:
        logging.error(f"Hacker News APIの呼び出し中にエラーが発生しました: {e}")
        raise

def get_last_checked_id():
    # F1セルから最後にチェックしたIDを取得
    cell = 'F1'
    value = sheet.acell(cell).value
    if not value:
        raise ValueError(f"{cell}セルに最新の記事IDが存在しません。")
    return int(value)

# ニュース取得関数
def update_news_on_sheet(last_checked_id):
    maxitem = fetch_hn_api('maxitem')
    new_news_ids = fetch_hn_api('newstories')
    
    new_news_ids = [news_id for news_id in new_news_ids if last_checked_id < news_id <= maxitem]
    
    for count, news_id in enumerate(new_news_ids):
        try:
            news_data = fetch_hn_api(f'item/{news_id}')
            if news_data and not news_data.get('dead'):
                row = write_news_to_sheet(news_data)
                publish_to_topic(row)
        except Exception as e:
            logging.error(f"Non-fatal exception caught: {e}")
        
        time.sleep(1)
        if count % 10 == 9:
            time.sleep(5)

# スプレッドシートに書き込む関数
def write_news_to_sheet(news_data):
    datetime_jst = datetime.utcfromtimestamp(news_data['time']) + timedelta(hours=9)
    row = [
        datetime_jst.strftime('%Y-%m-%d %H:%M:%S'),
        news_data.get('title'),
        news_data.get('url'),
        news_data.get('id')
    ]
    try:
        write_to_sheet_with_retry(row)
        logging.info(f"ニュース {news_data.get('id')} をスプレッドシートに書き込みました。")
    except Exception as e:
        logging.error(f"ニュース {news_data.get('id')} のスプレッドシートへの書き込みに失敗しました: {e}")
    time.sleep(1)

@on_exception(expo, (gspread.exceptions.APIError, gspread.exceptions.GSpreadException), max_tries=MAX_RETRIES)
def write_to_sheet_with_retry(row):
    try:
        index = 2  # 新しいエントリを挿入する行のインデックス
        sheet.insert_row(row, index=index)
    except Exception as e:
        logging.error(f"Googleスプレッドシートへの書き込み中にエラーが発生しました: {e}")
        raise

def publish_to_topic(row):
    try:
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path('あなたのGCPプロジェクトID', 'あなたのトピック名')
        data = json.dumps(row).encode("utf-8")
        publisher.publish(topic_path, data)
        logging.info("Data published to Pub/Sub")
    except Exception as e:
        logging.error(f"Pub/Subへのパブリッシュ中にエラーが発生しました: {e}")
        raise

def check_and_update_new_hn_content(event, context):
    try:
        logging.info("Starting update process...")
        last_checked_id = get_last_checked_id()
        update_news_on_sheet(last_checked_id)
        logging.info("Update process completed.")
    except Exception as e:
        logging.error(f"An error occurred during the update process: {e}")
        raise

if __name__ == "__main__":
    check_and_update_new_hn_content(None, None)