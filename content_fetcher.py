import os
import json
import base64
import asyncio
import aiohttp
import openai
from openai import OpenAI, AsyncOpenAI
import traceback
from html2text import html2text
import gspread
from backoff import on_exception, expo
from datetime import datetime
from langchain.chat_models import ChatOpenAI
from langchain.chains import create_extraction_chain
from google.cloud import pubsub_v1
from urllib.parse import urlparse
import logging
import time


# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 環境変数からスプレッドシートIDとクレデンシャルを取得
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID') 
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY

# Base64エンコードされたGoogleクレデンシャルをデコードし、gspreadクライアントを認証
try:
    creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
    creds = json.loads(creds_json)
    gc = gspread.service_account_from_dict(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).get_worksheet(1)  # sheet2に対応するインデックスを指定
except Exception as e:
    logging.error(f"gspreadクライアントの認証中にエラーが発生しました: {e}")
    raise

# OpenAIのクライアントを初期化
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# 非同期用のOpenAIクライアント
async_client = AsyncOpenAI()

async def fetch_content_from_url(url):
    try:
        logging.info(f"URLからコンテンツの取得を開始: {url}")

        # ユーザーエージェントを設定
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as response:
                content = await response.text()

            logging.info(f"URLからコンテンツの取得が成功: {url}")
            return content

    except Exception as e:
        logging.error(f"URLからのコンテンツ取得中にエラーが発生しました: {e}")
        raise

async def openai_api_call(model, temperature, messages):
    try:
        # OpenAI API呼び出しを行う非同期関数
        response = await async_client.chat.completions.create(model=model, temperature=temperature, messages=messages)
        return response.choices[0].message.content  # 辞書型アクセスから属性アクセスへ変更
    except Exception as e:
        logging.error(f"OpenAI API呼び出し中にエラーが発生しました: {e}")
        raise

# スキーマを定義
schema = {
    "properties": {
        "category1": {"type": "string"},
        "category2": {"type": "string"},
        "category3": {"type": "string"},
    },
    "required": ["category1"]
}

def generate_category(content):
    try:
        # LLM (Language Model) を初期化
        llm = ChatOpenAI(temperature=0, model="gpt-4-1106-preview")
        # 抽出チェーンを作成
        chain = create_extraction_chain(schema, llm)
        extracted_categories = chain.run(f"あなたは優秀なカテゴリ生成アシスタントです。提供された文章をもとに、カテゴリ(2個から3個)を生成してください。なお、入力内容の有無にかかわらず、日本語で出力を行ってください。 \n\n{content}")
        return extracted_categories
    except Exception as e:
        print(f"Error in category generation: {e}")
        traceback.print_exc()
        return []

async def summarize_content(content):
    return await openai_api_call(
        "gpt-4-1106-preview",
        0,
        [
            {"role": "system", "content": "あなたは優秀な要約アシスタントです。提供された文章をもとに、できる限り正確な内容にすることを意識して要約してください。なお、入力内容の有無にかかわらず、日本語で出力を行ってください。"},
            {"role": "user", "content": content}
        ]
    )

async def generate_opinion(content):
    return await openai_api_call(
        "gpt-4-1106-preview",
        0.6,
        [
            {"role": "system", "content": "あなたは優秀な意見生成アシスタントです。提供された文章をもとに、文章に関する感想や意見を生成してください。なお、入力内容の有無にかかわらず、日本語で出力を行ってください。"},
            {"role": "user", "content": content}
        ]
    )

def generate_lead(content):
    try:
        lead = openai_api_call(
            "gpt-4",
            0.6,
            [
                {"role": "system", "content": "あなたは優秀なリード文生成アシスタントです。提供された文章をもとに、日本語のリード文を生成してください。なお、入力内容の有無にかかわらず、日本語で出力を行ってください。"},
                {"role": "user", "content": content}
            ]
        )
        return lead
    except Exception as e:
        print(f"Error in lead generation: {e}")
        traceback.print_exc()
        return ""

# カテゴリー、要約、意見、リード文を生成する非同期関数
async def generate_textual_content(content):
    # 要約と意見を非同期で生成
    summary, opinion = await asyncio.gather(summarize_content(content), generate_opinion(content))

    # 要約を基にカテゴリとリード文を生成
    categories = generate_category(summary)
    lead = generate_lead(summary)

    return categories, summary, opinion, lead

# Function to write to the Google Sheet with exponential backoff
@on_exception(expo, gspread.exceptions.APIError, max_tries=3)
@on_exception(expo, gspread.exceptions.GSpreadException, max_tries=3)
def write_to_sheet_with_retry(row):
    time.sleep(1)  # 1秒スリープを追加
    try:
        logging.info("Googleスプレッドシートへの書き込みを開始")
        sheet.insert_row(row, index=2)
        logging.info("Googleスプレッドシートへの書き込みが成功")
    except Exception as e:
        logging.error(f"Googleスプレッドシートへの書き込み中にエラーが発生しました: {e}")
        raise

# Function to process content and write it to the sheet
async def process_and_write_content(title, url):
    # URLからドメインを解析
    parsed_url = urlparse(url)
    domain = parsed_url.netloc

    # 特定のドメインをチェックしてスキップ
    if any(excluded_domain in domain for excluded_domain in ['github.com', 'youtube.com', 'wikipedia.org']):
        logging.info(f"処理をスキップ: {title} ({url}) は除外されたドメインに属しています。")
        return

    logging.info(f"コンテンツ処理が開始されました: タイトル={title}, URL={url}")
    html_content = await fetch_content_from_url(url)
    text_content = html2text(html_content)
    categories, summary, opinion, lead = await generate_textual_content(text_content)
    # 時刻
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [title, url, now, categories, summary, opinion, lead]
    write_to_sheet_with_retry(row)

# Main function to be called with the news data
def main(event, context):
    try:
        news_data = json.loads(base64.b64decode(event['data']).decode('utf-8'))
        title = news_data.get('title')
        url = news_data.get('url')
        if title and url:
            asyncio.run(process_and_write_content(title, url))
    except Exception as e:
        logging.error(f"メイン処理中にエラーが発生しました: {e}")

