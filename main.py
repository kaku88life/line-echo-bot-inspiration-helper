import os
import re
import tempfile
from flask import Flask, request, abort
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("Please set LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET in .env file")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# OpenAI client for Whisper
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Gemini client for text processing
gemini_model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')

# User states for translation mode (in-memory storage)
# Structure: { user_id: { "mode": "translate", "target_language": "English" } }
user_states = {}

# URL pattern for detecting links
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.-]*(?:\?[^\s]*)?'
)

# Translation pattern - matches various formats:
# 翻譯成英文：你好 / 翻譯成英文:你好 / 翻譯成英文 你好 / 翻譯英文：你好
# 幫我翻譯成英文：你好 / 請翻譯成日文：你好 / 幫我翻譯成越南文 你好
TRANSLATE_PATTERN = re.compile(
    r'^(?:幫我|請|請幫我)?翻譯成?\s*(.+?)\s*[：:\s]\s*(.+)$',
    re.DOTALL
)

# Quick Reply language options for translation mode
QUICK_REPLY_LANGUAGES = [
    ("英文", "English"),
    ("日文", "Japanese"),
    ("韓文", "Korean"),
    ("越南文", "Vietnamese"),
    ("泰文", "Thai"),
    ("印尼文", "Indonesian"),
    ("簡體中文", "Simplified Chinese"),
    ("法文", "French"),
    ("西班牙文", "Spanish"),
    ("德文", "German"),
]

# Language name mapping (Chinese name -> language code for OpenAI)
LANGUAGE_MAP = {
    # 常用語言
    "英文": "English",
    "英語": "English",
    "日文": "Japanese",
    "日語": "Japanese",
    "韓文": "Korean",
    "韓語": "Korean",
    "中文": "Traditional Chinese",
    "繁體中文": "Traditional Chinese",
    "繁中": "Traditional Chinese",
    "簡體中文": "Simplified Chinese",
    "簡中": "Simplified Chinese",
    # 東南亞語言
    "越南文": "Vietnamese",
    "越南語": "Vietnamese",
    "泰文": "Thai",
    "泰語": "Thai",
    "印尼文": "Indonesian",
    "印尼語": "Indonesian",
    "馬來文": "Malay",
    "馬來語": "Malay",
    "菲律賓文": "Filipino",
    "菲律賓語": "Filipino",
    "緬甸文": "Burmese",
    "緬甸語": "Burmese",
    "柬埔寨文": "Khmer",
    "柬埔寨語": "Khmer",
    "高棉文": "Khmer",
    "寮文": "Lao",
    "寮語": "Lao",
    "寮國文": "Lao",
    # 歐洲語言
    "法文": "French",
    "法語": "French",
    "德文": "German",
    "德語": "German",
    "西班牙文": "Spanish",
    "西班牙語": "Spanish",
    "葡萄牙文": "Portuguese",
    "葡萄牙語": "Portuguese",
    "義大利文": "Italian",
    "義大利語": "Italian",
    "俄文": "Russian",
    "俄語": "Russian",
    "荷蘭文": "Dutch",
    "荷蘭語": "Dutch",
    # 其他語言
    "阿拉伯文": "Arabic",
    "阿拉伯語": "Arabic",
    "印度文": "Hindi",
    "印地語": "Hindi",
    "土耳其文": "Turkish",
    "土耳其語": "Turkish",
    "波蘭文": "Polish",
    "波蘭語": "Polish",
    "瑞典文": "Swedish",
    "瑞典語": "Swedish",
    "希臘文": "Greek",
    "希臘語": "Greek",
}


def extract_url(text: str) -> str | None:
    """Extract the first URL from text"""
    match = URL_PATTERN.search(text)
    return match.group(0) if match else None


def fetch_webpage_content(url: str) -> str:
    """Fetch and extract key content from a webpage"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Get title
        title = ""
        if soup.title:
            title = soup.title.string or ""

        # Get meta description
        description = ""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            description = meta_desc.get('content', '')

        # Get og:description as fallback
        if not description:
            og_desc = soup.find('meta', attrs={'property': 'og:description'})
            if og_desc:
                description = og_desc.get('content', '')

        # Remove unnecessary elements
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
            element.decompose()

        # Get article content
        article = soup.find('article') or soup.find('main') or soup.find('div', class_=lambda x: x and 'content' in x.lower() if x else False)

        if article:
            content = article.get_text(separator='\n', strip=True)
        else:
            content = soup.get_text(separator='\n', strip=True)

        # Clean up - remove extra whitespace and short lines
        lines = [line.strip() for line in content.split('\n') if len(line.strip()) > 20]
        content = '\n'.join(lines)

        # Limit content length
        if len(content) > 2000:
            content = content[:2000] + "..."

        return f"標題：{title}\n\n描述：{description}\n\n內文：\n{content}"

    except Exception as e:
        return f"無法抓取網頁內容：{str(e)}"


def summarize_webpage(content: str) -> str:
    """Use OpenAI to summarize webpage content"""
    if not openai_client:
        return "網頁摘要功能未設定，請設定 OPENAI_API_KEY"

    try:
        prompt = f"""請分析以下網頁內容，用繁體中文提供完整摘要：

{content}

請用以下格式回覆：

🏷️ 分類：[從以下選擇：科技/商業/新聞/教學/生活/娛樂/其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1 - 詳細說明]
• [重點2 - 詳細說明]
• [重點3 - 詳細說明]
（依內容提供3-5個重點）

💡 關鍵資訊：
[列出重要的數據、日期、人名、專有名詞等]

🎯 一句話總結：[用一句話總結整篇文章的核心價值]
"""
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "你是一個專業的網頁摘要助手，擅長提取重點並用繁體中文清晰呈現。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"摘要生成失敗：{str(e)}"

# Known Whisper hallucination patterns
HALLUCINATION_PATTERNS = [
    "请不吝点赞",
    "點贊訂閱",
    "订阅转发",
    "訂閱轉發",
    "打赏支持",
    "打賞支持",
    "明镜与点点",
    "明鏡與點點",
    "感谢观看",
    "感謝觀看",
    "谢谢收看",
    "謝謝收看",
    "欢迎订阅",
    "歡迎訂閱",
    "like and subscribe",
    "thanks for watching",
    "字幕由",
    "字幕提供",
    "subtitles by",
    "amara.org",
]


def is_hallucination(text: str) -> bool:
    """Check if the transcription is likely a hallucination"""
    if not text or len(text.strip()) == 0:
        return True

    text_lower = text.lower().strip()

    # Check against known hallucination patterns
    for pattern in HALLUCINATION_PATTERNS:
        if pattern.lower() in text_lower:
            return True

    # Check if text is too short and repetitive
    if len(text_lower) < 5:
        return True

    # Check if text is just repeated characters/words
    words = text_lower.split()
    if len(words) > 2 and len(set(words)) == 1:
        return True

    return False


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


def translate_text(text: str, target_language: str) -> str:
    """Use OpenAI to translate text to target language"""
    if not openai_client:
        return "翻譯功能未設定，請設定 OPENAI_API_KEY"

    try:
        prompt = f"""請將以下文字翻譯成{target_language}：

{text}

注意事項：
1. 只需要輸出翻譯結果，不要加任何解釋或說明
2. 保持原文的語氣和風格
3. 如果有專有名詞，請使用當地常用的翻譯方式
"""
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"你是一個專業的翻譯助手，擅長將各種語言翻譯成{target_language}。只輸出翻譯結果，不加任何額外說明。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.3
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"翻譯失敗：{str(e)}"


def parse_translation_request(text: str) -> tuple[str, str] | None:
    """Parse translation request and return (target_language, text_to_translate)"""
    match = TRANSLATE_PATTERN.match(text.strip())
    if not match:
        return None

    language_input = match.group(1).strip()
    text_to_translate = match.group(2).strip()

    # Look up the target language
    target_language = LANGUAGE_MAP.get(language_input)

    # If not found in map, use the input directly (let OpenAI handle it)
    if not target_language:
        target_language = language_input

    return (target_language, text_to_translate)


def summarize_text(text: str) -> str:
    """Use OpenAI to summarize text content"""
    if not openai_client:
        return "文字摘要功能未設定，請設定 OPENAI_API_KEY"

    try:
        prompt = f"""請分析以下文字內容，用繁體中文提供完整摘要：

{text}

請用以下格式回覆：

🏷️ 分類：[從以下選擇：科技/商業/新聞/教學/生活/娛樂/筆記/想法/其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1 - 詳細說明]
• [重點2 - 詳細說明]
• [重點3 - 詳細說明]
（依內容提供3-5個重點）

💡 關鍵字：[列出3-5個關鍵字，用逗號分隔]

🎯 一句話總結：[用一句話總結整段文字的核心內容]
"""
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "你是一個專業的文字摘要助手，擅長提取重點、分類內容，並用繁體中文清晰呈現。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"摘要生成失敗：{str(e)}"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """Handle text messages - translation, URL summary, or text summary"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        text = event.message.text.strip()
        user_id = event.source.user_id
        print(f"[DEBUG] Received text: {text}, user_id: {user_id}")

        # Check if user is in translation mode (waiting for content to translate)
        if user_id in user_states and user_states[user_id].get("mode") == "translate_waiting":
            target_language = user_states[user_id].get("target_language")
            print(f"[DEBUG] User in translation mode, translating to: {target_language}")

            # Check if user wants to exit translation mode
            if text in ["取消", "離開", "結束", "exit", "cancel"]:
                del user_states[user_id]
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="已離開翻譯模式 👋")],
                    )
                )
                return

            # Translate the content
            try:
                translated = translate_text(text, target_language)
                # Keep user in translation mode for continuous translation
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text=f"🌐 翻譯結果（{target_language}）\n\n{translated}\n\n─────────\n💡 繼續輸入文字可持續翻譯\n輸入「取消」離開翻譯模式",
                            quick_reply=QuickReply(items=[
                                QuickReplyItem(action=MessageAction(label="🚪 離開翻譯模式", text="取消")),
                                QuickReplyItem(action=MessageAction(label="🔄 換語言", text="翻譯")),
                            ])
                        )],
                    )
                )
                print(f"[DEBUG] Translation in mode sent successfully")
            except Exception as e:
                print(f"[DEBUG] Translation error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"❌ 翻譯失敗：{str(e)}")],
                    )
                )
            return

        # Check if user selected a language from Quick Reply
        if user_id in user_states and user_states[user_id].get("mode") == "translate_select_language":
            # Check if the input matches a language
            selected_language = LANGUAGE_MAP.get(text)
            if selected_language:
                user_states[user_id] = {"mode": "translate_waiting", "target_language": selected_language}
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"✅ 已選擇翻譯成【{text}】\n\n請輸入要翻譯的內容：\n\n💡 輸入「取消」可離開翻譯模式")],
                    )
                )
                print(f"[DEBUG] Language selected: {selected_language}")
                return
            # If input doesn't match a language, treat it as content to translate with default
            # Or show error - let's show the language selection again
            if text not in ["取消", "離開", "結束", "exit", "cancel"]:
                # Check if it's a valid language name not in our quick reply but in the map
                for lang_name, lang_code in LANGUAGE_MAP.items():
                    if text == lang_name:
                        user_states[user_id] = {"mode": "translate_waiting", "target_language": lang_code}
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text=f"✅ 已選擇翻譯成【{text}】\n\n請輸入要翻譯的內容：\n\n💡 輸入「取消」可離開翻譯模式")],
                            )
                        )
                        return

        # Check if user wants to enter translation mode (just "翻譯" or "翻譯模式")
        if text in ["翻譯", "翻譯模式"]:
            user_states[user_id] = {"mode": "translate_select_language"}
            quick_reply_items = [
                QuickReplyItem(action=MessageAction(label=label, text=label))
                for label, _ in QUICK_REPLY_LANGUAGES
            ]
            # Add cancel option
            quick_reply_items.append(
                QuickReplyItem(action=MessageAction(label="❌ 取消", text="取消"))
            )

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(
                        text="🌐 翻譯模式\n\n請選擇要翻譯成的語言：\n\n💡 也可以直接輸入語言名稱（如：韓文、馬來文）",
                        quick_reply=QuickReply(items=quick_reply_items)
                    )],
                )
            )
            print(f"[DEBUG] Entered translation mode, showing language selection")
            return

        # Check if user wants to cancel (outside of translation mode)
        if text in ["取消", "離開", "結束", "exit", "cancel"]:
            if user_id in user_states:
                del user_states[user_id]
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="已取消 👋")],
                )
            )
            return

        # Check if message is a direct translation request (翻譯成英文：你好)
        translation_request = parse_translation_request(text)
        if translation_request:
            target_language, text_to_translate = translation_request
            print(f"[DEBUG] Translation request - Language: {target_language}, Text: {text_to_translate[:50]}...")

            try:
                translated = translate_text(text_to_translate, target_language)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"🌐 翻譯結果（{target_language}）\n\n{translated}")],
                    )
                )
                print(f"[DEBUG] Translation sent successfully")
            except Exception as e:
                print(f"[DEBUG] Translation error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"❌ 翻譯失敗：{str(e)}")],
                    )
                )
            return

        # Check if message contains a URL
        url = extract_url(text)
        print(f"[DEBUG] Extracted URL: {url}")

        if url:
            try:
                print(f"[DEBUG] Fetching webpage content...")
                content = fetch_webpage_content(url)
                print(f"[DEBUG] Content length: {len(content)}")

                print(f"[DEBUG] Generating webpage summary...")
                summary = summarize_webpage(content)
                print(f"[DEBUG] Summary: {summary[:100]}...")

                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"🔗 網頁摘要\n{url}\n\n{summary}")],
                    )
                )
                print(f"[DEBUG] Reply sent successfully")
            except Exception as e:
                print(f"[DEBUG] Error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"❌ 網頁摘要失敗：{str(e)}")],
                    )
                )
        else:
            # Summarize the text
            print(f"[DEBUG] Generating text summary...")
            try:
                summary = summarize_text(text)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"📝 文字摘要\n\n{summary}")],
                    )
                )
                print(f"[DEBUG] Text summary sent successfully")
            except Exception as e:
                print(f"[DEBUG] Error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"❌ 文字摘要失敗：{str(e)}")],
                    )
                )


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    """Handle audio messages - transcribe and reply with text"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        blob_api = MessagingApiBlob(api_client)

        # Check if OpenAI is configured
        if not openai_client:
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="語音轉文字功能未設定，請設定 OPENAI_API_KEY")],
                )
            )
            return

        try:
            # Download audio content from LINE
            audio_content = blob_api.get_message_content(event.message.id)

            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp_file:
                # Handle both bytes and iterator response
                if hasattr(audio_content, 'read'):
                    tmp_file.write(audio_content.read())
                elif hasattr(audio_content, '__iter__') and not isinstance(audio_content, bytes):
                    for chunk in audio_content:
                        tmp_file.write(chunk)
                else:
                    tmp_file.write(audio_content)
                tmp_file_path = tmp_file.name

            # Transcribe using OpenAI Whisper
            with open(tmp_file_path, "rb") as audio_file:
                transcription = openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="zh",  # Chinese, change if needed
                )

            # Clean up temp file
            os.unlink(tmp_file_path)

            # Check for hallucination
            result_text = transcription.text if transcription.text else ""

            if is_hallucination(result_text):
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="⚠️ 無法辨識語音內容\n\n可能原因：\n• 語音太短或太模糊\n• 背景噪音太大\n• 沒有錄到聲音\n\n請重新錄製語音訊息。")],
                    )
                )
                return

            # Reply with transcription
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"📝 語音轉文字：\n\n{result_text}")],
                )
            )

        except Exception as e:
            # Clean up temp file if exists
            if 'tmp_file_path' in locals():
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"語音轉文字失敗：{str(e)}")],
                )
            )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
