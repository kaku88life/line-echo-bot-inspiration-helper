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

# URL pattern for detecting links
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.-]*(?:\?[^\s]*)?'
)


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
        prompt = f"""用繁體中文總結以下網頁的3-5個重點：

{content}

格式：
📌 主題：[一句話]
📝 重點：
• 重點1
• 重點2
• 重點3
"""
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "你是一個網頁摘要助手，用繁體中文回覆。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
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


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    """Handle text messages - check for URL or echo back"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        text = event.message.text.strip()
        print(f"[DEBUG] Received text: {text}")

        # Check if message contains a URL
        url = extract_url(text)
        print(f"[DEBUG] Extracted URL: {url}")

        if url:
            try:
                print(f"[DEBUG] Fetching webpage content...")
                content = fetch_webpage_content(url)
                print(f"[DEBUG] Content length: {len(content)}")

                print(f"[DEBUG] Generating summary...")
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
            print(f"[DEBUG] No URL found, echoing text")
            # Echo back the text
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=text)],
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
