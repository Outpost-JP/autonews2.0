import os
import json
import base64
import asyncio
import aiohttp
import openai
import traceback
from html2text import html2text
import gspread
from backoff import on_exception, expo
from datetime import datetime
from langchain.chat_models import ChatOpenAI
from langchain.chains import create_extraction_chain


# 環境変数からスプレッドシートIDとクレデンシャルを取得
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
# 適切な範囲を指定するように書き換えること
SHEET_NAME = 'SHEET2_NAME!A2'  
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

openai.api_key = OPENAI_API_KEY

# Base64エンコードされたGoogleクレデンシャルをデコードし、gspreadクライアントを認証
creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
creds = json.loads(creds_json)
gc = gspread.service_account_from_dict(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).get_worksheet(1)  # sheet2に対応するインデックスを指定

# URLからコンテンツを取得してテキストに変換する非同期関数
async def fetch_content_from_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()

# OpenAI API呼び出すための関数
def openai_api_call(model, temperature, messages):
    try:
        response = openai.ChatCompletion.create(
            model=model,
            temperature=temperature,
            messages=messages
        )
        return response.choices[0].message['content']
    except Exception as e:
        print(f"Error in OpenAI API call: {e}")
        traceback.print_exc()
        return None

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
        llm = ChatOpenAI(temperature=0, model="gpt-3.5-turbo")
        # 抽出チェーンを作成
        chain = create_extraction_chain(schema, llm)
        # チェーンを実行
        extracted_categories = chain.run(f"あなたは優秀なカテゴリ生成アシスタントです。提供された文章をもとに、カテゴリ(2個から3個)を生成してください。\n\n{content}")
        return extracted_categories
    except Exception as e:
        print(f"Error in category generation: {e}")
        traceback.print_exc()
        return "カテゴリを生成できませんでした"
    
    # 要約用の関数
def summarize_content(content):
    try:
        summary = openai_api_call(
        "gpt-3.5-turbo-16k-0613",
        0,
        [
            {"role": "system", "content": "あなたは優秀な要約アシスタントです。提供された文章をもとに、できる限り正確な内容にすることを意識して要約してください。"},
            {"role": "user", "content": content}
        ]
        )
        return summary
    except Exception as e:
        print(f"Error in summarization: {e}")
        traceback.print_exc()
        return "要約できませんでした"
    
    # 意見生成用の関数
def generate_opinion(content):
    try:
        opinion = openai_api_call(
        "gpt-4",
        0.6,
        [
            {"role": "system", "content": "あなたは優秀な意見生成アシスタントです。提供された文章をもとに、文章に関する感想や意見を生成してください。"},
            {"role": "user", "content": content}
        ]
        )
        return opinion
    except Exception as e:
        print(f"Error in opinion generation: {e}")
        traceback.print_exc()
        return "意見を生成できませんでした"
    

# リード文作成関数
def generate_lead(content):
    try:
        lead = openai_api_call(
        "gpt-3.5-turbo-0613",
        0.6,
        [
            {"role": "system", "content": "あなたは優秀なリード文生成アシスタントです。提供された文章をもとに、日本語のリード文を生成してください。"},
            {"role": "user", "content": content}
        ]
    )
        return lead
    except Exception as e:
        print(f"Error in lead generation: {e}")
        traceback.print_exc()
        return "リード文を生成できませんでした"


# カテゴリー、要約、意見、リード文を生成する関数
def generate_textual_content(content):


    #非同期で行うと要約が出来上がる前にほかの関数が走ってエラー出そうだからそこだけ修正すること。
    categories = generate_category(content)
    summary = summarize_content(content)
    opinion = generate_opinion(content)
    lead = generate_lead(content)
    return categories, summary, opinion, lead

# Function to write to the Google Sheet with exponential backoff
@on_exception(expo, gspread.exceptions.APIError, max_tries=3)
@on_exception(expo, gspread.exceptions.GSpreadException, max_tries=3)
def write_to_sheet_with_retry(row):
    try:
        sheet.insert_row(row, index=2)  # 新しいエントリを挿入する行のインデックス
    except Exception as e:
        print(f"Exception encountered: {e}")
        raise

# Function to process content and write it to the sheet
async def process_and_write_content(title, url):
    html_content = await fetch_content_from_url(url)
    text_content = html2text(html_content)
    categories, summary, opinion, lead = generate_textual_content(text_content)
    
    # 時刻
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 個々の順番変えた方が分かりやすそう
    row = [title, url, now, categories, summary, opinion, lead]

    # リトライロジック実装できてるか確認すること。
    write_to_sheet_with_retry(row)

# Main function to be called with the news data
def main(news_data):
    title = news_data.get('title')
    url = news_data.get('url')
    if title and url:
        # Run the async function to process and write content
        asyncio.run(process_and_write_content(title, url))

if __name__ == "__main__":
    # Test with dummy data
    news_data = {
        'title': 'Example Title',
        'url': 'http://example.com'
    }
    main(news_data)
