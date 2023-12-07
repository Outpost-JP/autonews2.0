import functions_framework
import threading
import openai
import flask
from markupsafe import escape
from openai import OpenAI
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
import random
from concurrent.futures import ThreadPoolExecutor, as_completed



def summarize_content(content):
    try:
        # テキストを分割するためのスプリッターを設定
        text_splitter = CharacterTextSplitter(
            chunk_size=5000,  # 分割するチャンクのサイズ
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
        return None

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
    worksheet = spreadsheet.get_worksheet(0)
    return worksheet


SHEET_CLIENT = init_gspread()

# OpenAI API呼び出し関数
def openai_api_call(model, temperature, messages, max_tokens, response_format):
    client = OpenAI(api_key=OPENAI_api_key)  # 非同期クライアントのインスタンス化
    try:
        # OpenAI API呼び出しを行う
        response = client.chat.completions.create(model=model, temperature=temperature, messages=messages, max_tokens=max_tokens, response_format=response_format)
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
        return None

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
        return None

# ランダムペルソナを選択する関数
def select_random_persona():
    # 定義されたペルソナの辞書
    personas = {
        1: "佐藤ユウキ - 職業: 大学院生、AI研究者, 性格: 好奇心旺盛、論理的、やや内向的, 思想: テクノロジーの民主化を信じており、オープンソース運動を支持, 宗教: 特定の宗教には属していないが、宇宙論的パンティズムに共感を覚える, 人種/民族: 日本人, バックグラウンド: 東京の大学でコンピュータサイエンスを専攻し、AIに魅了された。特に機械学習と人間の認知の関連性に興味があり、学際的な研究を志向している。",
        2: "Amina Hussein - 職業: ソフトウェアエンジニア、AIスタートアップの共同創業者, 性格: 外向的、決断力があり、リーダーシップに富む, 思想: 社会起業家精神を持ち、技術を利用して途上国の問題を解決したいと考えている, 宗教: イスラム教徒, 人種/民族: ソマリア系アメリカ人, バックグラウンド: アメリカで育ち、シリコンバレーの名門大学でコンピュータサイエンスを学んだ。彼女のスタートアップは、AIを使って教育と健康の分野で革新をもたらすことを目指している。",
        3: "Carlos García - 職業: AI倫理学者、大学教授,性格: 深い思考家、倫理的な問題に対して熱心、公正を重んじる,思想: テクノロジーと倫理の交差点において、社会的責任を重要視する,宗教: カトリック,人種/民族: ヒスパニック（メキシコ系アメリカ人）,バックグラウンド: メキシコで生まれ育ち、アメリカの大学で哲学とコンピュータサイエンスの両方を学んだ。現在、AIの社会的影響に関する論文を多数発表している。",
        4: "Priya Singh - 職業: データサイエンティスト、医療AIの専門家,性格: 細部に注意を払い、慎重、共感的,思想: 科学とデータの力を信じ、医療分野でのAIの可能性に情熱を持っている,宗教: ヒンドゥー教,人種/民族: インド系カナダ人,バックグラウンド: トロントでコンピュータサイエンスの学位を取得後、医療技術に特化した。彼女はAIを用いて病気の早期発見と治療法の改善に貢献している。",
        5: "David Okafor - 職業: AIアプリケーション開発者、フリーランサー,性格: 創造的、柔軟性があり、協調性がある,思想: デジタルノマドとしてのライフスタイルを享受し、仕事と旅を組み合わせることで多様な文化を経験している,宗教: 宗教には無関心,人種/民族: ナイジェリア系イギリス人,バックグラウンド: ロンドンの大学でソフトウェアエンジニアリングを学んだ後、世界中を旅しながらリモートでAIプロジェクトに取り組んでいる。彼はAIの民主化とアクセスの改善を目指しており、開発途上国のための技術支援に関心がある。",
        6: "Susan Whitaker - 職業: 中学校の歴史教師, 性格: 伝統的、保守的、慎重, 思想: 新しいテクノロジーに懐疑的で、子供たちが基本的な思考力と人間関係を育むことを重視, 宗教: キリスト教プロテスタント, 人種/民族: 白人アメリカ人, バックグラウンド: ミシシッピ州の小さな町で育ち、地元の大学で教育学を学んだ後、地域社会の学校で教えている。テクノロジー特にAIが子供たちの教育に及ぼす影響に懸念を抱いている。",
        7: "Raj Patel - 職業: 小規模農家, 性格: 勤勉、献身的、地域社会志向, 思想: 持続可能な農業と伝統的な農法を支持, 宗教: ヒンドゥー教, 人種/民族: インド系アメリカ人, バックグラウンド: カリフォルニアの農業コミュニティで育ち、家族経営の農場を継承。AIと自動化が農業コミュニティの雇用を奪うと信じており、地元の労働者の生計を守ることに尽力している。",
        8: "Emma Larson - 職業: 書店経営者, 性格: 文学愛好家、内向的、思慮深い, 思想: デジタル化に対する抵抗感があり、紙の本と店頭での対話を大切にしている, 宗教: アグノスティック, 人種/民族: スウェーデン系アメリカ人, バックグラウンド: ニューヨークで独立系の書店を経営しており、AIによる推薦システムではなく、人間のキュレーションを重んじる。デジタル化が読書体験を劣化させると考えている。",
        9: "Carlos Ramirez - 職業: 自動車整備工, 性格: 実用主義者、堅実、家族を大切にする, 思想: 手に職を持つことの重要性を信じており、学徒制度と職人精神を尊重, 宗教: カトリック, 人種/民族: メキシコ系アメリカ人, バックグラウンド: テキサスの小さな町で育ち、地元のコミュニティカレッジで自動車整備を学んだ。AIと自動運転車の台頭による職業の未来について不安を抱えている。",
        10: "Aisha Al-Farsi - 職業: 社会活動家, 性格: 激情的、説得力がある、正義感が強い, 思想: 人間の尊厳と労働権を擁護し、テクノロジーが社会不平等を拡大させることに反対, 宗教: イスラム教, 人種/民族: アラブ系アメリカ人, バックグラウンド: ニュージャージー州で生まれ育ち、人権に関する法学を学んだ後、労働者の権利と社会正義のために戦っている。AIによる監視とプライバシーの侵害に警鐘を鳴らしている。"
    }

    # 1から5までのランダムな整数を生成
    random_number = random.randint(1, 10)

    # 生成された整数に対応するペルソナを選択
    selected_persona = personas[random_number]
    # ペルソナの名前を抽出
    persona_name = selected_persona.split(" - ")[0]
    return selected_persona, persona_name

# 意見を生成する関数 
def generate_opinion(content):
    full_persona, persona_name = select_random_persona()
    opinion = openai_api_call(
        "gpt-3.5-turbo-1106",
        0.6,
        [
            {"role": "system", "content": f'あなたは"""{full_persona}"""です。提供された文章の内容に対し日本語で意見を生成してください。'},
            {"role": "user", "content": content}
            
        ],
        2000,
        {"type": "text"}
    )
    opinion_with_name = f'{persona_name}: {opinion}'
    return opinion_with_name

# 意見を生成する関数(2)
def generate_opinion2(content):
    full_persona, persona_name = select_random_persona()
    opinion = openai_api_call(
        "gpt-3.5-turbo-1106",
        0.6,
        [
            {"role": "system", "content": f'あなたは"""{full_persona}"""です。提供された文章の内容に対し日本語で意見を生成してください。'},
            {"role": "user", "content": content}
        ],
        2000,
        {"type": "text"}
    )
    opinion_with_name2 = f'{persona_name}: {opinion}'
    return opinion_with_name2

# 意見を生成する関数(3)
def generate_opinion3(content):
    full_persona, persona_name = select_random_persona()
    opinion = openai_api_call(
        "gpt-3.5-turbo-1106",
        0.6,
        [
            {"role": "system", "content": f'あなたは"""{full_persona}"""です。提供された文章の内容に対し日本語で意見を生成してください。'},
            {"role": "user", "content": content}
        ],
        2000,
        {"type": "text"}
    )
    opinion_with_name3 = f'{persona_name}: {opinion}'
    return opinion_with_name3



    # スプレッドシートに書き出す
@on_exception(expo(base=4), gspread.exceptions.APIError, max_tries=2)
@on_exception(expo(base=4), gspread.exceptions.GSpreadException, max_tries=2)
def write_to_spreadsheet(row):
    if not SHEET_CLIENT:
        logging.error("スプレッドシートのクライアントが初期化されていません。")
        return False
    try:
        logging.info(f"スプレッドシートへの書き込みを開始: {row}")
        # スプレッドシートの初期化
        worksheet = SHEET_CLIENT

        # スプレッドシートに指定行に挿入
        worksheet.insert_row(row, 2)  # A2からD2に行を挿入

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
        if content is None:
            logging.warning(f"コンテンツが見つからない: {article_url}")
            return

        parsed_content = parse_content(content)
        if parsed_content is None:
            logging.warning(f"コンテンツのパースに失敗: {article_url}")
            return

        # parsed_contentが10000文字以下なら直接OpenAIに渡す
        if len(parsed_content) <= 10000:
            final_summary = openai_api_call(
                "gpt-4-1106-preview",
                0,
                [
                    {"role": "system", "content": "あなたは優秀な要約アシスタントです。提供された文章の内容を出来る限り残しつつ、日本語で要約してください。"},
                    {"role": "user", "content": parsed_content}
                ],
                4000,
                {"type": "text"}
            )
        else:
            # 初期要約を生成
            preliminary_summary = summarize_content(parsed_content)
            if preliminary_summary is None:
                logging.warning(f"コンテンツの要約に失敗: {article_url}")
                return

            # OpenAIを使用してさらに要約を洗練
            final_summary = openai_api_call(
                "gpt-4-1106-preview",
                0,
                [
                    {"role": "system", "content": "あなたは優秀な要約アシスタントです。提供された文章の内容を出来る限り残しつつ、日本語で要約してください。"},
                    {"role": "user", "content": preliminary_summary}
                ],
                4000,
                {"type": "text"}
            )

        if not final_summary:
            logging.warning(f"要約の洗練に失敗: {article_url}")
            return None
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_opinion = {
                executor.submit(generate_opinion, final_summary): 'opinion1',
                executor.submit(generate_opinion2, final_summary): 'opinion2',
                executor.submit(generate_opinion3, final_summary): 'opinion3',
        }
        opinions = []
        for future in as_completed(future_to_opinion):
            try:
                opinions.append(future.result())
            except Exception as e:
                logging.error(f"{article_url} の意見生成中にエラーが発生: {e}")

        
        # スプレッドシートに書き込む準備
        spreadsheet_content = [article_title, article_url, final_summary] + opinions

        # スプレッドシートに書き込む
        write_to_spreadsheet(spreadsheet_content)
        logging.info(f"処理完了: {article_url}")

    except Exception as e:
        logging.error(f"{article_url} の処理中にエラーが発生: {e}")
        traceback.print_exc()

@functions_framework.http
def process_inoreader_update(request):
    request_json = request.get_json()

    if request_json and 'items' in request_json:
        for item in request_json['items']:
            article_title = escape(item.get('title', ''))
            article_href = escape(item['canonical'][0]['href']) if 'canonical' in item and item['canonical'] else ''


            # news.google.comを含むURLをスキップする
            if 'news.google.com' in article_href:
                logging.info(f"news.google.comのURLはスキップされます: {article_href}")
                continue

            if article_title and article_href:
                # 重い処理を非同期で実行するために別のスレッドを起動
                thread = threading.Thread(target=heavy_task, args=(article_title, article_href))
                thread.start()
        # メインスレッドでは即座に応答を返す
        return '記事の更新を受け取りました', 200
    else:
        return '適切なデータがリクエストに含まれていません', 400
        
         

