import asyncio
import json
import logging
import os
from datetime import datetime
import aiohttp
import base64
from backoff import expo, on_exception
from google.cloud import pubsub_v1
from gspread import service_account_from_dict  
from html2text import html2text
from openai import OpenAI
from urllib.parse import urlparse
import gspread
import openai
import time
import traceback
import requests
from bs4 import BeautifulSoup


#　こちらは新しく書き直してリファクタリングしたもの。

# 定数
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')  
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')   
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
EXCLUDED_DOMAINS = ['github.com', 'youtube.com', 'wikipedia.org', 'twitter.com']



# gspread初期化
def init_gspread():
  try:
    creds = json.loads(base64.b64decode(GOOGLE_CREDENTIALS_BASE64))
    return gspread.service_account_from_dict(creds).open_by_key(SPREADSHEET_ID).get_worksheet(1) 
  except Exception as e:
        logging.error(f"gspread初期化中にエラーが発生しました: {e}")
        raise   
  
SHEET_CLIENT = init_gspread()

# OpenAIの非同期クライアント初期化  
def init_openai():
  return OpenAI(api_key=OPENAI_API_KEY)

# OpenAI API呼び出し関数
def openai_api_call(model, temperature, messages, max_tokens, response_format):
    client = init_openai() 
    try:
        # OpenAI API呼び出しを行う非同期関数
        response = client.chat.completions.create(model=model, temperature=temperature, messages=messages, max_tokens=max_tokens, response_format=response_format)
        return response.choices[0].message.content  # 辞書型アクセスから属性アクセスへ変更
    except Exception as e:
        logging.error(f"OpenAI API呼び出し中にエラーが発生しました: {e}")
        raise


#　要約関数を書き出す。
def summarize_content(content):
    try:
        summary = openai_api_call(
        "gpt-4-1106-preview",
        0,
        [
            {"role": "system", "content": f'あなたは優秀な要約アシスタントです。"""{content}"""の内容をできる限り多くの情報を残しながら日本語で要約して出力してください。'},
            {"role": "user", "content": content}
        ],
        2800,
        # タイプ指定をサボらない
        { "type": "text" }
        )
        return summary
    except Exception as e:
        logging.info(f"要約時にエラーが発生しました。: {e}")
        traceback.print_exc()
        raise
    
# パラメーターを書き出す
paramater = '''
{
    "properties": {
        "importance": {
            "type": "integer",
            "description": "How impactful the topic of the article is. Scale: 0-10."
        },
        "timeliness": {
            "type": "integer",
            "description": "How relevant the information is to current events or trends. Scale: 0-10."
        },
        "objectivity": {
            "type": "integer",
            "description": "Whether the information is presented without bias or subjective opinion. Scale: 0-10."
        },
        "originality": {
            "type": "integer",
            "description": "The novelty or uniqueness of the content. Scale: 0-10."
        },
        "target_audience": {
            "type": "integer",
            "description": "How well the content is adjusted for a specific audience. Scale: 0-10."
        },
        "diversity": {
            "type": "integer",
            "description": "Reflection of different perspectives or cultures. Scale: 0-10."
        },
        "relation_to_advertising": {
            "type": "integer",
            "description": "If the content is biased due to advertising. Scale: 0-10."
        },
        "security_issues": {
            "type": "integer",
            "description": "Potential for raising security concerns. Scale: 0-10."
        },
        "social_responsibility": {
            "type": "integer",
            "description": "How socially responsible the content presentation is. Scale: 0-10."
        },
        "social_significance": {
            "type": "integer",
            "description": "The social impact of the content. Scale: 0-10."
        }
        "reason": {
        "type": "string",
        "description": "the basis for each numerical score. Output in 1-sentence Japanese with respect to all parameters"
        }
    },
    "required": ["importance", "timeliness", "objectivity", "originality", "target_audience", "diversity", "relation_to_advertising", "security_issues", "social_responsibility", "social_significance", "reason"]
}
'''

# スコアを書き出す
def generate_score(summary):
    try:
        score = openai_api_call(
            "gpt-3.5-turbo-1106",
            0,
            [
                {"role": "system", "content": f'あなたは優秀な先進技術メディアのキュレーターです。信頼性,最新性,重要性,革新性,影響力,関連性,包括性,教育的価値,時事性,倫理性をもとに、"""{summary}"""を10点満点でスコアリングして、JSON形式で返します。平均点は5点でスコアを付けるようにしてください。"""{paramater}"""のJSON形式で返してください。'},
                {"role": "user", "content": summary}
            ],
            4000,
            { "type":"json_object" }
            )
        return score
    except Exception as e:
        logging.warning(f"スコア測定時にエラーが発生しました。: {e}")
        traceback.print_exc()
        return ""
    
# スプレッドシートに書き出す
@on_exception(expo, gspread.exceptions.APIError, max_tries=3)
@on_exception(expo, gspread.exceptions.GSpreadException, max_tries=3)
def write_to_spreadsheet(row):
    if not SHEET_CLIENT:
        logging.error("スプレッドシートのクライアントが初期化されていません。")
        return False
    time.sleep(1)  # 1秒スリープを追加
    try:
        logging.info(f"スプレッドシートへの書き込みを開始: {row}")
        # スプレッドシートの初期化
        worksheet = SHEET_CLIENT

        # スプレッドシートに書き込み
        worksheet.append_row(row)

        logging.info(f"スプレッドシートへの書き込みが成功: {row}")

    except gspread.exceptions.APIError as e:
        logging.warning(f"一時的なエラー、リトライ可能: {e}")
        raise 

    except gspread.exceptions.GSpreadException as e:
        logging.error(f"致命的なエラー: {e}")
        raise

    
# URLからコンテンツを取得する関数
def fetch_content_from_url(url):
    try:
        logging.info(f"URLからコンテンツの取得を開始: {url}")

        # ユーザーエージェントを設定
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        response = requests.get(url, headers=headers, timeout=100)
        content = response.text

        logging.info(f"URLからコンテンツの取得が成功: {url}")
        return content

    except Exception as e:
        logging.warning(f"URLからのコンテンツ取得中にエラーが発生しました: {e}")
        raise

#　コンテンツをパースする関数 
def parse_content(content):
    try:
        # HTMLコンテンツをBeautiful Soupでパース
        soup = BeautifulSoup(content, 'html.parser')

        # ヘッダーとフッターを削除（もし存在する場合）
        header = soup.find('header')
        if header:
            header.decompose()

        footer = soup.find('footer')
        if footer:
            footer.decompose()

        # JavaScriptとCSSを削除
        for script in soup(["script", "style"]):
            script.decompose()

        # HTMLタグを削除してテキストのみを取得
        text = soup.get_text()

        # 改行をスペースに置き換え
        parsed_text = ' '.join(text.split())

        # パースされたテキストの文字数を出力
        print(f"パースされたテキストの文字数: {len(parsed_text)}")

        return parsed_text

    except Exception as e:
        logging.warning(f"コンテンツのパース中にエラーが発生しました: {e}")
        return ""

# メイン関数
def main(event, context):
    
    try:
        news_data = json.loads(base64.b64decode(event['data']).decode('utf-8'))
        title = news_data.get('title')
        url = news_data.get('url')
        # URLの確認
        if title and url:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
        # 特定のドメインをチェックしてスキップ
            if domain in EXCLUDED_DOMAINS:
                logging.info(f"スキップするドメインです。: {domain}")
                return
        else:
            logging.warning(f"タイトルまたはURLがありません。: {news_data}")
            return
        # コンテンツを取得
        content = fetch_content_from_url(url)
        if not content:
            logging.warning(f"コンテンツがありません。: {url}")
            return

        # コンテンツをパース
        parsed_content = parse_content(content)
        if not parsed_content:
            logging.warning(f"コンテンツのパースに失敗しました。: {url}")
            return
        # 要約
        summary = summarize_content(parsed_content)
        if not summary:
            logging.warning(f"要約に失敗しました。: {url}")
            return
        # スコアを生成
        score = generate_score(summary)
        if not score:
            logging.warning(f"スコアの生成に失敗しました。: {url}")
            return
        # スプレッドシートに書き込み
        write_to_spreadsheet([title, url, summary, score])
        # ログを出力
        logging.info(f"コンテンツの処理が完了: {url}")
    except Exception as e:
        logging.error(f"コンテンツの処理中にエラーが発生しました: {e}")
        raise
