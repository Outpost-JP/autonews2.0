import threading
from flask import escape
import openai
from openai import AsyncOpenAI
import asyncio
import requests
import logging
import json
import os
import re
from urllib.parse import urlparse
import time
from backoff import expo, on_exception
from bs4 import BeautifulSoup
import gspread
import base64
import traceback
import langchain
from langchain.prompts import PromptTemplate
from langchain.chat_models import ChatOpenAI
from langchain.chains.summarize import load_summarize_chain
from langchain.text_splitter import CharacterTextSplitter
from langchain.docstore.document import Document


def summarize_content(content):
    try:
        # テキストを分割するためのスプリッターを設定
        text_splitter = CharacterTextSplitter(
            chunk_size=3000,  # 分割するチャンクのサイズ
            chunk_overlap=100,  # チャンク間のオーバーラップ
            separator="\n"    # 文章を分割するためのセパレータ
        )
        texts = text_splitter.create_documents([content])

        # 要約チェーンを実行
        result = refine_chain({"input_documents": texts}, return_only_outputs=True)

        # 要約されたテキストを結合して返す
        return result["output_text"]
    except Exception as e:
        logging.error(f"要約処理中にエラーが発生しました: {e}")
        traceback.print_exc()
        return ""

# パラメーターを書き出す
parameter = '''
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
                {"role": "system", "content": f'あなたは優秀な先進技術メディアのキュレーターです。信頼性,最新性,重要性,革新性,影響力,関連性,包括性,教育的価値,時事性,倫理性をもとに、"""{summary}"""を10点満点でスコアリングして、JSON形式で返します。平均点は5点でスコアを付けるようにしてください。"""{parameter}"""のJSON形式で返してください。'},
                {"role": "user", "content": summary}
            ],
            4000,
            { "type":"json_object" }
            )
        score_json = json.loads(score)
        # 応答を整形して返す
        formatted_score = json.dumps(score_json, indent=2, ensure_ascii=False)
        return formatted_score
    except Exception as e:
        logging.warning(f"スコア測定時にエラーが発生しました。: {e}")
        traceback.print_exc()
        return ""


# 定数
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')  
GOOGLE_CREDENTIALS_BASE64 = os.getenv('CREDENTIALS_BASE64')   
OPENAI_api_key = os.getenv('OPENAI_API_KEY')
EXCLUDED_DOMAINS = ['github.com', 'youtube.com', 'wikipedia.org', 'twitter.com', 'www.youtube.com']

# プロンプトテンプレートの定義
refine_first_template = """以下の文章は、長い記事をチャンクで分割したものの冒頭の文章です。それを留意し、次の文章の内容と結合することを留意したうえで以下の文章をテーマ毎にまとめて下さい。
------
{text}
------
"""

refine_template = """下記の文章は、長い記事をチャンクで分割したものの一部です。また、「{existing_answer}」の内容はこれまでの内容の要約である。そして、「{text}」はそれらに続く文章です。それを留意し、次の文章の内容と結合することを留意したうえで以下の文章をテーマ毎にまとめて下さい。できる限り多くの情報を残しながら日本語で要約して出力してください。
------
{existing_answer}
{text}
------
"""
refine_first_prompt = PromptTemplate(input_variables=["text"],template=refine_first_template)
refine_prompt = PromptTemplate(input_variables=["existing_answer", "text"],template=refine_template)
llm = ChatOpenAI(temperature=0, model_name="gpt-3.5-turbo-16k")
# 要約チェーンの初期化
refine_chain = load_summarize_chain(
    llm=llm,
    chain_type="refine",
    question_prompt=refine_first_prompt,
    refine_prompt=refine_prompt
)

# gspread初期化
def init_gspread():

    # Base64デコード
    creds_json = base64.b64decode(GOOGLE_CREDENTIALS_BASE64).decode('utf-8')

    # JSONパース  
    creds = json.loads(creds_json)

    # gspread認証
    gc = gspread.service_account_from_dict(creds)  

    # スプレッドシートオープン
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)

    # 2枚目のシート取得
    worksheet = spreadsheet.get_worksheet(1)
    return worksheet


SHEET_CLIENT = init_gspread()

# OpenAI API呼び出し関数
async def openai_api_call(model, temperature, messages, max_tokens, response_format):
    client = AsyncOpenAI(api_key=OPENAI_api_key)  # 非同期クライアントのインスタンス化
    try:
        # OpenAI API呼び出しを行う
        response = await client.chat.completions.create(model=model, temperature=temperature, messages=messages, max_tokens=max_tokens, response_format=response_format)
        return response.choices[0].message.content  # 辞書型アクセスから属性アクセスへ変更
    except Exception as e:
        logging.error(f"OpenAI API呼び出し中にエラーが発生しました: {e}")
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



# メインのタスクの部分
def heavy_task(article_title, article_url):
    try:
        # URLからコンテンツを取得し、パースする
        content = fetch_content_from_url(article_url)
        if not content:
            logging.warning(f"コンテンツが見つからない: {article_url}")
            return

        parsed_content = parse_content(content)
        if not parsed_content:
            logging.warning(f"コンテンツのパースに失敗: {article_url}")
            return

        # 初期要約を生成
        preliminary_summary = summarize_content(parsed_content)
        if not preliminary_summary:
            logging.warning(f"コンテンツの要約に失敗: {article_url}")
            return

        # OpenAIを使用してさらに要約を洗練
        final_summary = openai_api_call(
            "gpt-4-1106-preview",
            0,
            [
                {"role": "system", "content": f"以下のテキストを要約してください: {preliminary_summary}"},
                {"role": "user", "content": preliminary_summary}
            ],
            4000,
            {"type": "text"}
        )
        if not final_summary:
            logging.warning(f"要約の洗練に失敗: {article_url}")
            return

        # 要約のスコアを生成
        score = generate_score(final_summary)
        if not score:
            logging.warning(f"スコアの生成に失敗: {article_url}")
            return

        # スプレッドシートに書き込む
        write_to_spreadsheet([article_title, article_url, final_summary, score])
        logging.info(f"処理完了: {article_url}")

    except Exception as e:
        logging.error(f"{article_url} の処理中にエラーが発生: {e}")
        traceback.print_exc()
    pass

def process_inoreader_update(request):
    request_json = request.get_json()

    if request_json and 'title' in request_json and 'url' in request_json:
        article_title = escape(request_json['title'])
        article_url = escape(request_json['url'])

        # 重い処理を非同期で実行するために別のスレッドを起動
        thread = threading.Thread(target=heavy_task, args=(article_title, article_url))
        thread.start()

        # メインスレッドでは即座に応答を返す
        return '記事の更新を受け取りました'

    else:
        return '適切なデータがリクエストに含まれていません'
