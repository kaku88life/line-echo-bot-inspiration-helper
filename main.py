import os
import re
import json
import tempfile
import time
import threading
import base64
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from flask import Flask, request, abort, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from notion_client import Client as NotionClient
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from apify_client import ApifyClient

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ImageMessage as LineImageMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent, ImageMessageContent

load_dotenv()

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LINEBOT_USAGE_DIR = os.path.join(BASE_DIR, "docs", "linebot-usage")
LINEBOT_USAGE_CARD_FILES = [
    "linebot-usage-01-overview.png",
    "linebot-usage-02-url-sources.png",
    "linebot-usage-03-commands.png",
    "linebot-usage-04-buttons.png",
]
LINEBOT_WORKFLOW_CARD_FILES = [
    "linebot-usage-05-workflow.png",
    "linebot-usage-06-agent-rhythm.png",
]
LINEBOT_CARD_FILES = LINEBOT_USAGE_CARD_FILES + LINEBOT_WORKFLOW_CARD_FILES
LINEBOT_USAGE_HELP_TEXTS = {
    "/?",
    "/？",
    "?",
    "？",
    "help",
    "/help",
    "功能",
    "功能說明",
    "使用說明",
    "使用方法",
    "按鈕",
    "指令",
}
LINEBOT_WORKFLOW_HELP_TEXTS = {
    "工作流",
    "整理流程",
    "定期整理",
    "每週整理",
    "ai整理",
    "aiagent",
    "agent",
    "workflow",
}
LINEBOT_DRIVE_DIAGNOSTIC_TEXTS = {
    "drive診斷",
    "drive測試",
    "儲存測試",
    "儲存診斷",
    "保存測試",
    "obsidian診斷",
    "obsidian測試",
    "linebot診斷",
    "linebot測試",
    "/drive-test",
    "/drive",
}


def normalize_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_SOCIAL_DATABASE_ID = os.getenv("NOTION_SOCIAL_DATABASE_ID")
APIFY_API_KEY = os.getenv("APIFY_API_KEY")
GDRIVE_CREDENTIALS_FILE = normalize_env_value(os.getenv("GDRIVE_CREDENTIALS_FILE")) or "gdrive_credentials.json"
GDRIVE_VAULT_FOLDER_ID_RAW = os.getenv("GDRIVE_VAULT_FOLDER_ID")
GDRIVE_VAULT_FOLDER_ID = normalize_env_value(GDRIVE_VAULT_FOLDER_ID_RAW)
GOOGLE_CALENDAR_IDS = [cid.strip() for cid in os.getenv("GOOGLE_CALENDAR_ID", "primary").split(",") if cid.strip()]
CRON_SECRET = os.getenv("CRON_SECRET")

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

# Notion client for saving content
notion_client = None
if NOTION_API_KEY and NOTION_DATABASE_ID:
    notion_client = NotionClient(auth=NOTION_API_KEY)
    print("[DEBUG] Notion client initialized")

# Apify client for social media scraping
apify_client = None
if APIFY_API_KEY:
    apify_client = ApifyClient(APIFY_API_KEY)
    print("[DEBUG] Apify client initialized")

# User states for translation mode (in-memory storage)
# Structure: { user_id: { "mode": "translate", "target_language": "English", "entered_at": timestamp } }
user_states = {}

# Track last saved file per user for "補充想法" feature
# Structure: { user_id: { "file_id": "...", "title": "...", "saved_at": timestamp } }
user_last_file = {}

# Translation mode timeout (5 minutes)
TRANSLATION_MODE_TIMEOUT = 5 * 60  # 5 minutes in seconds


def check_translation_timeout():
    """Background thread to check and handle translation mode timeouts"""
    while True:
        try:
            current_time = time.time()
            users_to_remove = []

            # Find users who have timed out
            for user_id, state in list(user_states.items()):
                if state.get("mode") in ["translate_waiting", "translate_select_language"]:
                    entered_at = state.get("entered_at", current_time)
                    if current_time - entered_at >= TRANSLATION_MODE_TIMEOUT:
                        users_to_remove.append(user_id)

            # Remove timed out users and send notification
            for user_id in users_to_remove:
                if user_id in user_states:
                    del user_states[user_id]
                    print(f"[DEBUG] User {user_id} translation mode timed out")

                    # Send push message to notify user
                    try:
                        with ApiClient(configuration) as api_client:
                            messaging_api = MessagingApi(api_client)
                            messaging_api.push_message(
                                PushMessageRequest(
                                    to=user_id,
                                    messages=[TextMessage(text="⏰ 翻譯模式已逾時（5分鐘），已自動退出。\n\n如需繼續翻譯，請重新輸入「翻譯」進入翻譯模式。")]
                                )
                            )
                            print(f"[DEBUG] Timeout notification sent to user {user_id}")
                    except Exception as e:
                        print(f"[DEBUG] Failed to send timeout notification: {str(e)}")

        except Exception as e:
            print(f"[DEBUG] Error in timeout checker: {str(e)}")

        # Check every 30 seconds
        time.sleep(30)


# Start background thread for timeout checking
timeout_thread = threading.Thread(target=check_translation_timeout, daemon=True)
timeout_thread.start()

# URL pattern for detecting links. Keep this permissive because mobile share
# links often contain @, !, encoded params, and platform-specific tokens.
URL_PATTERN = re.compile(
    r'https?://[^\s<>"\'\u3000]+'
)

# Social media URL patterns
# Single post patterns
FACEBOOK_POST_PATTERN = re.compile(
    r'https?://(?:(?:www\.|m\.|web\.)?facebook\.com/(?:groups/[\w.-]+/(?:posts|permalink)/?[\w/?=&%#_.:-]*|(?:[\w.]+/)?(?:posts|videos|photos|photo\.php|watch|story\.php|permalink\.php|reel|share)[\w/?=&%#_.:-]*)|fb\.watch/[\w-]+/?(?:[?#].*)?)',
    re.IGNORECASE,
)
# Facebook page/profile patterns (for multi-post scraping)
FACEBOOK_PAGE_PATTERN = re.compile(
    r'https?://(?:www\.|m\.|web\.)?facebook\.com/([\w.]+)/?(?:\?.*)?$',
    re.IGNORECASE,
)
THREADS_POST_PATTERN = re.compile(
    r'https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/post/[\w-]+(?:[/?#].*)?$',
    re.IGNORECASE,
)
THREADS_PROFILE_PATTERN = re.compile(
    r'https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/?(?:\?.*)?$',
    re.IGNORECASE,
)

# Command pattern for multi-post scraping: "爬 5 篇 [URL]" or "幫我爬 10 篇 [URL]"
SCRAPE_MULTI_PATTERN = re.compile(
    r'^(?:幫我)?爬取?\s*(\d+)\s*篇\s*(https?://\S+)',
    re.IGNORECASE
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

CAPTURE_STATUS_FULL = "full"
CAPTURE_STATUS_PARTIAL = "partial"
CAPTURE_STATUS_FAILED = "failed"

SOCIAL_EXTRACTOR_BY_PLATFORM = {
    "facebook": "facebook-apify",
    "threads": "threads-apify",
}

FAILED_CONTENT_MARKERS = [
    "無法抓取網頁內容",
    "請提供網頁內容",
    "請提供網頁內容的具體信息",
    "please provide the webpage content",
    "access denied",
    "forbidden",
    "just a moment",
    "enable javascript",
    "connection reset",
    "連接被重置",
    "error 403",
    "error 404",
    "error 500",
]


def yaml_block_value(value: str) -> str:
    """Format a possibly multiline string as a YAML block scalar."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return "''"
    lines = text.split("\n")
    return "|-\n" + "\n".join(f"  {line}" if line else "  " for line in lines)


def yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def normalize_input_light(text: str) -> str:
    """Lightly normalize user input without changing domain meaning."""
    normalized = re.sub(r'[ \t]+', ' ', text or "").strip()
    normalized = re.sub(r'\n{3,}', '\n\n', normalized)
    return normalized


def is_linebot_usage_help_request(text: str) -> bool:
    compact = re.sub(r'\s+', '', text or "").lower()
    return compact in LINEBOT_USAGE_HELP_TEXTS


def is_linebot_workflow_help_request(text: str) -> bool:
    compact = re.sub(r'\s+', '', text or "").lower()
    return compact in LINEBOT_WORKFLOW_HELP_TEXTS


def is_linebot_drive_diagnostic_request(text: str) -> bool:
    compact = re.sub(r'\s+', '', text or "").lower()
    return compact in LINEBOT_DRIVE_DIAGNOSTIC_TEXTS


def get_public_base_url() -> str:
    configured = (
        os.getenv("LINE_BOT_PUBLIC_BASE_URL") or
        os.getenv("PUBLIC_BASE_URL") or
        os.getenv("APP_BASE_URL")
    )
    if configured:
        return configured.rstrip("/")

    proto = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def build_linebot_card_image_messages(card_files: list[str]) -> list[LineImageMessage]:
    base_url = get_public_base_url()
    messages = []
    for filename in card_files:
        image_url = f"{base_url}/linebot-usage/{filename}"
        messages.append(LineImageMessage(
            originalContentUrl=image_url,
            previewImageUrl=image_url,
        ))
    return messages


def build_linebot_usage_image_messages() -> list[LineImageMessage]:
    return build_linebot_card_image_messages(LINEBOT_USAGE_CARD_FILES)


def build_linebot_workflow_image_messages() -> list[LineImageMessage]:
    return build_linebot_card_image_messages(LINEBOT_WORKFLOW_CARD_FILES)


def assess_extracted_content(content: str) -> dict:
    """Classify extracted content quality before asking AI to summarize it."""
    text = (content or "").strip()
    lower = text.lower()
    if not text:
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "empty_content",
        }
    if any(marker in lower for marker in FAILED_CONTENT_MARKERS):
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "fetch_error_marker",
        }
    compact = re.sub(r'\s+', '', text)
    if len(compact) < 80:
        return {
            "status": CAPTURE_STATUS_PARTIAL,
            "needs_review": True,
            "reason": "short_content",
        }
    if len(compact) < 180:
        return {
            "status": CAPTURE_STATUS_PARTIAL,
            "needs_review": True,
            "reason": "limited_content",
        }
    return {
        "status": CAPTURE_STATUS_FULL,
        "needs_review": False,
        "reason": "",
    }


def assess_url_capture_quality(content: str, source_type: str, extractor: str) -> dict:
    """Apply source-specific capture quality rules after extraction."""
    quality = assess_extracted_content(content)
    if quality["status"] == CAPTURE_STATUS_FAILED:
        return quality

    if source_type == "youtube":
        if extractor != "youtube-transcript":
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "youtube_metadata_only",
            }
    if source_type == "ptt" and extractor == "ptt-html":
        body_match = re.search(r"## 本文\s*(.+?)(?:\n\n## 推文統計|\Z)", content or "", re.DOTALL)
        body_text = body_match.group(1).strip() if body_match else ""
        if not body_text or body_text == "（未抓到本文內容）":
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "ptt_body_missing",
            }
    if source_type == "104":
        if extractor != "104-ajax":
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "104_fallback_extractor",
            }
        job_name = re.search(r"^職缺：(.+)$", content or "", re.MULTILINE)
        company_name = re.search(r"^公司：(.+)$", content or "", re.MULTILINE)
        desc_match = re.search(r"## 工作內容\s*(.+?)(?:\n\n## 條件要求|\Z)", content or "", re.DOTALL)
        desc_text = desc_match.group(1).strip() if desc_match else ""
        if not job_name or not job_name.group(1).strip() or not company_name or not company_name.group(1).strip():
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "104_title_or_company_missing",
            }
        if not desc_text or desc_text == "（未抓到工作內容）":
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "104_job_description_missing",
            }

    return quality


def build_capture_status_note(
    url: str,
    raw_input: str,
    source_type: str,
    extractor: str,
    status: str,
    reason: str,
    extracted_content: str = "",
) -> str:
    """Build a safe note body when extraction is partial or failed."""
    lines = [
        "## 捕捉狀態",
        f"- source_type: {source_type}",
        f"- capture_status: {status}",
        f"- extractor: {extractor}",
        f"- reason: {reason or 'unknown'}",
        "",
        "## 原始輸入",
        raw_input or url,
        "",
        "## 來源",
        url,
    ]
    if extracted_content:
        lines.extend(["", "## 抓取到的內容", extracted_content])
    if status == CAPTURE_STATUS_FAILED:
        lines.extend([
            "",
            "## 待補充",
            "這筆資料沒有抓到足夠內容，請補一句保存理由或手動貼上重點。",
        ])
    return "\n".join(lines)


def source_type_from_url(url: str) -> str:
    lower = (url or "").lower()
    if "threads.net" in lower or "threads.com" in lower:
        return "threads"
    if "facebook.com" in lower or "fb.watch" in lower:
        return "facebook"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if any(pattern in lower for pattern in [
        "maps.google.com", "google.com/maps", "goo.gl/maps",
        "maps.app.goo.gl", "/maps/", "maps.app"
    ]):
        return "google_maps"
    if "104.com.tw/job/" in lower:
        return "104"
    if "ptt.cc" in lower:
        return "ptt"
    return "webpage"


def should_save_status_note_only(source_type: str, status: str) -> bool:
    return status == CAPTURE_STATUS_FAILED or (
        source_type in ["youtube", "google_maps", "ptt", "104"] and status == CAPTURE_STATUS_PARTIAL
    )


def build_url_capture_push_message(file_id: str | None, status: str, source_type: str, title: str | None) -> str:
    if file_id:
        return (
            f"網址已保存\n"
            f"抓取狀態：{status}\n"
            f"來源類型：{source_type}\n\n"
            f"{title or '完整內容已存入 Obsidian'}"
        )
    return (
        "網址已讀取，但保存到 Obsidian 失敗\n"
        f"抓取狀態：{status}\n"
        f"來源類型：{source_type}\n\n"
        "請檢查 GDRIVE_VAULT_FOLDER_ID、Google Drive 權限或部署環境變數。"
    )


def get_gdrive_service_account_email() -> str:
    try:
        credentials_json = os.getenv("GDRIVE_CREDENTIALS_JSON")
        if credentials_json:
            return json.loads(credentials_json).get("client_email", "")
        if GDRIVE_CREDENTIALS_FILE and os.path.exists(GDRIVE_CREDENTIALS_FILE):
            with open(GDRIVE_CREDENTIALS_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh).get("client_email", "")
    except Exception:
        return ""
    return ""


def save_gdrive_diagnostic_note(service, now: datetime, user_id: str | None) -> dict:
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    month_str = now.strftime("%Y-%m")
    sources_id = get_or_create_folder(service, "Sources", GDRIVE_VAULT_FOLDER_ID)
    month_id = get_or_create_folder(service, month_str, sources_id)
    filename = f"{date_str}-{time_str}-系統診斷-LineBot Drive 診斷.md"
    content = "\n".join([
        "---",
        f"date: {date_str}",
        "type: 系統診斷",
        "category: 系統",
        "source_type: text",
        "capture_status: full",
        "extractor: linebot-drive-diagnostic",
        "needs_review: false",
        "tags: [系統, linebot, drive, diagnostic]",
        "---",
        "",
        "# LineBot Drive 診斷",
        "",
        "這是一筆 Line Bot production 環境的 Google Drive 寫入測試。",
        "",
        f"- Created at: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- User id present: {bool(user_id)}",
        "- 如果這筆 note 出現在桌面 Obsidian vault，代表 Google Drive API 寫入與桌面同步都正常。",
    ])
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
    metadata = {"name": filename, "parents": [month_id]}
    return service.files().create(body=metadata, media_body=media, fields="id,name").execute()


def run_gdrive_diagnostic(user_id: str | None = None) -> dict:
    now = datetime.now()
    result = {
        "vault_configured": bool(GDRIVE_VAULT_FOLDER_ID),
        "vault_id_trimmed": bool(GDRIVE_VAULT_FOLDER_ID_RAW and GDRIVE_VAULT_FOLDER_ID_RAW != GDRIVE_VAULT_FOLDER_ID),
        "credentials_source": "env_json" if os.getenv("GDRIVE_CREDENTIALS_JSON") else "file",
        "service_account_email": get_gdrive_service_account_email(),
        "root_name": "",
        "root_accessible": False,
        "child_folders": {},
        "write_success": False,
        "file_name": "",
        "relative_folder": f"Sources/{now.strftime('%Y-%m')}",
        "write_stage": "",
        "error": "",
    }
    if not GDRIVE_VAULT_FOLDER_ID:
        result["error"] = "GDRIVE_VAULT_FOLDER_ID is not configured"
        return result
    try:
        service = get_gdrive_service()
        root = service.files().get(
            fileId=GDRIVE_VAULT_FOLDER_ID,
            fields="name,mimeType,trashed",
        ).execute()
        result["root_name"] = root.get("name", "")
        result["root_accessible"] = (
            root.get("mimeType") == "application/vnd.google-apps.folder"
            and not root.get("trashed", False)
        )
        for folder_name in ["Sources", "Meetings", "Wiki", "90_System"]:
            safe_name = folder_name.replace("'", "\\'")
            query = (
                f"name='{safe_name}' and '{GDRIVE_VAULT_FOLDER_ID}' in parents "
                "and mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            files = service.files().list(q=query, fields="files(id,name)").execute().get("files", [])
            result["child_folders"][folder_name] = bool(files)

        result["write_stage"] = "create Sources/YYYY-MM folder and upload diagnostic note"
        created = save_gdrive_diagnostic_note(service, now, user_id)
        result["write_success"] = bool(created.get("id"))
        result["file_name"] = created.get("name", "")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
    return result


def build_gdrive_diagnostic_message(result: dict) -> str:
    child_folders = result.get("child_folders") or {}
    folder_status = ", ".join(
        f"{name}={'OK' if child_folders.get(name) else 'missing'}"
        for name in ["Sources", "Meetings", "Wiki", "90_System"]
    )
    lines = [
        "Line Bot Drive 診斷結果",
        f"- Vault 設定：{'已設定' if result.get('vault_configured') else '未設定'}",
        f"- Folder ID 空白修正：{'有，已自動移除' if result.get('vault_id_trimmed') else '無'}",
        f"- 憑證來源：{result.get('credentials_source') or 'unknown'}",
        f"- Service Account：{result.get('service_account_email') or '無法讀取'}",
        f"- Drive 根資料夾：{result.get('root_name') or '無法讀取'}",
        f"- 根資料夾可讀：{'是' if result.get('root_accessible') else '否'}",
        f"- 必要資料夾：{folder_status or '無法檢查'}",
        f"- 測試寫入：{'成功' if result.get('write_success') else '失敗'}",
    ]
    if result.get("write_stage"):
        lines.append(f"- 寫入階段：{result['write_stage']}")
    if result.get("file_name"):
        lines.append(f"- 測試檔案：{result['file_name']}")
        lines.append(f"- 預期同步位置：{result.get('relative_folder')}")
    if result.get("error"):
        lines.append(f"- 錯誤：{result['error']}")
    lines.append("")
    lines.append("判讀：如果測試寫入成功但電腦 Obsidian 沒看到，請檢查 Google Drive for Desktop 同步。")
    lines.append("如果測試寫入失敗，請檢查 Zeabur 的 Drive 環境變數與服務帳號權限。")
    return "\n".join(lines)


def social_extractor_name(platform: str) -> str:
    return SOCIAL_EXTRACTOR_BY_PLATFORM.get((platform or "").lower(), "apify")


def platform_display_name(platform: str) -> str:
    platform = (platform or "").lower()
    if platform == "facebook":
        return "Facebook"
    if platform == "threads":
        return "Threads"
    return platform.title() if platform else "Social"


def clean_social_value(value) -> str:
    """Normalize scraped social values into compact human-readable text."""
    if value is None or value is False:
        return ""
    if isinstance(value, bool):
        return "是" if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return re.sub(r'\s+', ' ', value).strip()
    if isinstance(value, (list, tuple, set)):
        parts = [clean_social_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ["text", "content", "message", "description", "body", "caption", "title", "name", "username", "value"]:
            cleaned = clean_social_value(value.get(key))
            if cleaned:
                return cleaned
    return ""


def pick_social_value(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return ""


def coerce_social_count(value) -> int:
    if value is None or value is False:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in ["count", "total", "totalCount", "value", "text"]:
            count = coerce_social_count(value.get(key))
            if count:
                return count
        return 0
    if isinstance(value, list):
        return len(value)

    text = clean_social_value(value).replace(",", "")
    if not text:
        return 0
    match = re.search(r'(\d+(?:\.\d+)?)\s*([kKmMbB萬千]?)', text)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2).lower()
    multiplier = {
        "k": 1000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "萬": 10_000,
        "千": 1000,
    }.get(suffix, 1)
    return int(number * multiplier)


def collect_social_images(post_data: dict) -> list[str]:
    images = []

    def add_image(value):
        if not value:
            return
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("http") and cleaned not in images:
                images.append(cleaned)
            return
        if isinstance(value, list):
            for item in value:
                add_image(item)
            return
        if isinstance(value, dict):
            nested_keys = ["photo_image", "image", "images", "media", "thumbnailImage"]
            has_nested_image = any(value.get(key) for key in nested_keys)
            for key in nested_keys:
                add_image(value.get(key))
            if has_nested_image:
                return
            for key in ["uri", "url", "src", "thumbnail", "displayUrl", "display_url", "imageUrl", "image_url", "fullUrl", "full_url"]:
                add_image(value.get(key))

    for key in ["images", "image", "imageUrl", "imageUrls", "displayUrl", "displayUrls", "thumbnail"]:
        add_image(post_data.get(key))
    for key in ["media", "attachments", "mediaAttachments", "attachmentsData"]:
        add_image(post_data.get(key))

    return images


def collect_social_image_text(post_data: dict) -> str:
    parts = []

    def add_text(value):
        cleaned = clean_social_value(value)
        if cleaned:
            parts.append(cleaned)

    for key in ["image_text", "imageText", "ocrText", "ocr", "alt", "accessibilityCaption"]:
        add_text(post_data.get(key))
    for media_key in ["media", "attachments", "mediaAttachments", "attachmentsData"]:
        media_value = post_data.get(media_key) or []
        if isinstance(media_value, dict):
            media_value = [media_value]
        if isinstance(media_value, list):
            for item in media_value:
                if isinstance(item, dict):
                    for key in ["ocrText", "imageText", "alt", "accessibilityCaption", "description"]:
                        add_text(item.get(key))

    deduped = []
    for part in parts:
        if part not in deduped:
            deduped.append(part)
    return "\n".join(deduped).strip()


def assess_social_post_content(post_data: dict) -> dict:
    """Classify normalized social post quality before AI summarization."""
    text = "\n".join(
        part.strip()
        for part in [
            str(post_data.get("text") or ""),
            str(post_data.get("image_text") or ""),
        ]
        if str(part or "").strip()
    )
    lower = text.lower()
    images = [img for img in (post_data.get("images") or []) if isinstance(img, str) and img.strip()]

    if any(marker in lower for marker in FAILED_CONTENT_MARKERS):
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "fetch_error_marker",
        }

    compact = re.sub(r'\s+', '', text)
    if not compact:
        if images:
            return {
                "status": CAPTURE_STATUS_PARTIAL,
                "needs_review": True,
                "reason": "media_only",
            }
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "empty_social_content",
        }
    if len(compact) < 24:
        return {
            "status": CAPTURE_STATUS_PARTIAL,
            "needs_review": True,
            "reason": "short_social_content",
        }
    return {
        "status": CAPTURE_STATUS_FULL,
        "needs_review": False,
        "reason": "",
    }


def format_social_extracted_content(post_data: dict, source_url: str = "") -> str:
    """Format normalized social post fields as raw capture content."""
    post_url = post_data.get("post_url") or ""
    published_at = post_data.get("published_at") or ""
    content_type = post_data.get("content_type") or ""
    lines = [
        f"帳號：{post_data.get('username') or '未知'}",
        f"來源：{source_url or post_url}",
        f"互動數據：{post_data.get('likes', 0)} 讚 | {post_data.get('comments', 0)} 留言 | {post_data.get('shares', 0)} 分享",
    ]
    if post_url and post_url != source_url:
        lines.append(f"貼文網址：{post_url}")
    if published_at:
        lines.append(f"發布時間：{published_at}")
    if content_type:
        lines.append(f"內容類型：{content_type}")
    lines.extend(["", "貼文文字：", post_data.get("text") or "（未抓取到文字）"])
    image_text = post_data.get("image_text") or ""
    if image_text:
        lines.extend(["", "圖片文字：", image_text])
    images = [img for img in (post_data.get("images") or []) if isinstance(img, str) and img.strip()]
    if images:
        lines.extend(["", "圖片連結："])
        lines.extend(f"- {img}" for img in images[:5])
    return "\n".join(lines)


def resolve_short_url(url: str) -> str:
    """Resolve a shortened URL to its final destination URL.
    Returns the original URL if resolution fails."""
    try:
        response = requests.head(url, allow_redirects=True, timeout=10)
        final_url = response.url
        if final_url and final_url != url:
            print(f"[DEBUG] Resolved short URL: {url} -> {final_url}")
            return final_url
    except Exception as e:
        print(f"[DEBUG] Failed to resolve short URL: {str(e)}")
    return url


def extract_url(text: str) -> str | None:
    """Extract the first URL from text"""
    match = URL_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,，。;；:：!?！？)]}）】」'")


def detect_social_platform(url: str) -> tuple[str | None, str]:
    """Detect social media platform type and URL type

    Returns:
        Tuple of (platform, url_type) where url_type is "post" or "page"
    """
    if FACEBOOK_POST_PATTERN.match(url):
        return ("facebook", "post")
    if FACEBOOK_PAGE_PATTERN.match(url):
        return ("facebook", "page")
    if THREADS_POST_PATTERN.match(url):
        return ("threads", "post")
    if THREADS_PROFILE_PATTERN.match(url):
        return ("threads", "page")
    return (None, "")


def scrape_facebook_post(url: str, max_posts: int = 1) -> list[dict]:
    """Scrape Facebook post(s) using Apify

    Args:
        url: Facebook URL (post or page)
        max_posts: Maximum number of posts to scrape (default 1)

    Returns:
        List of post data dictionaries
    """
    if not apify_client:
        print("[DEBUG] Apify client not configured")
        return []

    try:
        print(f"[DEBUG] Scraping Facebook URL: {url}, max_posts: {max_posts}")
        run_input = {
            "startUrls": [{"url": url}],
            "resultsLimit": max_posts,
        }
        run = apify_client.actor("apify/facebook-posts-scraper").call(run_input=run_input)
        items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
        if items:
            print(f"[DEBUG] Facebook scrape successful, got {len(items)} posts")
            return items
        print("[DEBUG] No items returned from Facebook scraper")
        return []
    except Exception as e:
        print(f"[DEBUG] Facebook scrape error: {str(e)}")
        return []


def scrape_threads_post(url: str, max_posts: int = 1) -> list[dict]:
    """Scrape Threads post(s) using Apify

    Args:
        url: Threads URL
        max_posts: Maximum number of posts to scrape (default 1)

    Returns:
        List of post data dictionaries
    """
    if not apify_client:
        print("[DEBUG] Apify client not configured")
        return []

    try:
        print(f"[DEBUG] Scraping Threads post: {url}")
        run_input = {
            "url": url,
        }
        run = apify_client.actor("sinam7/threads-post-scraper").call(run_input=run_input)
        items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
        if items:
            print(f"[DEBUG] Threads scrape successful, got {len(items)} posts")
            return items
        print("[DEBUG] No items returned from Threads scraper")
        return []
    except Exception as e:
        print(f"[DEBUG] Threads scrape error: {str(e)}")
        return []


def scrape_google_maps(url: str) -> dict | None:
    """Scrape Google Maps place data using Apify (compass/crawler-google-places)

    Args:
        url: Google Maps URL (resolved, not shortened)

    Returns:
        Place data dictionary or None
    """
    if not apify_client:
        print("[DEBUG] Apify client not configured for Google Maps scraping")
        return None

    try:
        print(f"[DEBUG] Scraping Google Maps URL: {url}")
        run_input = {
            "startUrls": [{"url": url}],
            "maxCrawledPlacesPerSearch": 1,
            "language": "zh-TW",
        }
        run = apify_client.actor("compass/crawler-google-places").call(run_input=run_input)
        items = list(apify_client.dataset(run["defaultDatasetId"]).iterate_items())
        if items:
            print(f"[DEBUG] Google Maps scrape successful, got {len(items)} places")
            return items[0]
        print("[DEBUG] No items returned from Google Maps scraper")
        return None
    except Exception as e:
        print(f"[DEBUG] Google Maps scrape error: {str(e)}")
        return None


def clean_google_maps_value(value) -> str:
    """Normalize Google Maps scraped values without exposing raw nested noise."""
    if value is None or value is False:
        return ""
    if isinstance(value, bool):
        return "是" if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return re.sub(r'\s+', ' ', value).strip()
    if isinstance(value, (list, tuple, set)):
        parts = [clean_google_maps_value(item) for item in value]
        return "、".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ["text", "name", "title", "simpleText", "label", "value"]:
            cleaned = clean_google_maps_value(value.get(key))
            if cleaned:
                return cleaned
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def get_google_maps_review_count(place: dict):
    for key in ["reviewsCount", "reviewCount", "numberOfReviews"]:
        value = place.get(key)
        if value:
            return value
    reviews = place.get("reviews")
    if isinstance(reviews, list):
        return len(reviews)
    if isinstance(reviews, (int, float, str)) and reviews:
        return reviews
    return ""


def format_google_maps_result(place: dict) -> str:
    """Format scraped Google Maps place data into a readable summary."""
    place = place or {}
    lines = []

    name = clean_google_maps_value(place.get("title") or place.get("name")) or "未知地點"
    lines.append(f"地點名稱：{name}")

    category = clean_google_maps_value(place.get("categoryName") or place.get("category"))
    if category:
        lines.append(f"類型：{category}")

    status_flags = []
    if place.get("temporarilyClosed"):
        status_flags.append("暫停營業")
    if place.get("permanentlyClosed"):
        status_flags.append("永久停業")
    if place.get("closed") is True:
        status_flags.append("已停業")
    if status_flags:
        lines.append(f"營業狀態：{'、'.join(status_flags)}")

    address = clean_google_maps_value(place.get("address") or place.get("street"))
    if address:
        lines.append(f"地址：{address}")

    rating = clean_google_maps_value(place.get("totalScore") or place.get("rating") or place.get("stars"))
    reviews_count = clean_google_maps_value(get_google_maps_review_count(place))
    if rating:
        review_suffix = f"（{reviews_count} 則評論）" if reviews_count else ""
        lines.append(f"評分：{rating}/5{review_suffix}")

    phone = clean_google_maps_value(place.get("phone") or place.get("phoneUnformatted"))
    if phone:
        lines.append(f"電話：{phone}")

    website = clean_google_maps_value(place.get("website"))
    if website:
        lines.append(f"網站：{website}")

    maps_url = clean_google_maps_value(
        place.get("placeUrl") or place.get("googleMapsUrl") or place.get("url") or place.get("searchPageUrl")
    )
    if maps_url:
        lines.append(f"Google Maps URL：{maps_url}")

    price = clean_google_maps_value(place.get("price") or place.get("priceLevel"))
    if price:
        lines.append(f"價位：{price}")

    hours = place.get("openingHours") or place.get("hours")
    if hours:
        if isinstance(hours, list):
            lines.append("營業時間：")
            for item in hours[:7]:
                if isinstance(item, dict):
                    day = clean_google_maps_value(item.get("day") or item.get("name"))
                    time_str = clean_google_maps_value(item.get("hours") or item.get("value") or item.get("time"))
                    if day and time_str:
                        lines.append(f"  - {day}：{time_str}")
                    elif day or time_str:
                        lines.append(f"  - {day or time_str}")
                else:
                    cleaned = clean_google_maps_value(item)
                    if cleaned:
                        lines.append(f"  - {cleaned}")
        else:
            cleaned_hours = clean_google_maps_value(hours)
            if cleaned_hours:
                lines.append(f"營業時間：{cleaned_hours}")

    description = clean_google_maps_value(place.get("description"))
    if description:
        lines.extend(["", f"簡介：{description}"])

    plus_code = clean_google_maps_value(place.get("plusCode"))
    if plus_code:
        lines.append(f"Plus Code：{plus_code}")

    location = place.get("location") if isinstance(place.get("location"), dict) else {}
    lat = location.get("lat") or place.get("latitude")
    lng = location.get("lng") or place.get("longitude")
    if lat and lng:
        lines.append(f"座標：{lat}, {lng}")

    distribution = place.get("reviewsDistribution")
    if isinstance(distribution, dict) and distribution:
        lines.append("評論分布：")
        for key, value in list(distribution.items())[:5]:
            cleaned = clean_google_maps_value(value)
            if cleaned:
                lines.append(f"  - {key}：{cleaned}")

    reviews = place.get("reviews")
    if isinstance(reviews, list) and reviews:
        lines.append("評論摘錄：")
        for review in reviews[:3]:
            if not isinstance(review, dict):
                cleaned = clean_google_maps_value(review)
                if cleaned:
                    lines.append(f"  - {cleaned[:300]}")
                continue
            author = clean_google_maps_value(review.get("name") or review.get("authorName") or review.get("reviewerName"))
            stars = clean_google_maps_value(review.get("stars") or review.get("rating"))
            date = clean_google_maps_value(review.get("publishedAtDate") or review.get("date") or review.get("publishedAt"))
            text = clean_google_maps_value(review.get("text") or review.get("reviewText") or review.get("snippet"))
            pieces = []
            if author:
                pieces.append(author)
            if stars:
                pieces.append(f"{stars}/5")
            if date:
                pieces.append(date)
            prefix = "，".join(pieces)
            if text:
                lines.append(f"  - {prefix}：{text[:300]}" if prefix else f"  - {text[:300]}")
            elif prefix:
                lines.append(f"  - {prefix}")

    additional = place.get("additionalInfo") or place.get("additionalCategories")
    if isinstance(additional, dict):
        for key, value in list(additional.items())[:5]:
            cleaned = clean_google_maps_value(value)
            if cleaned:
                lines.append(f"{key}：{cleaned}")
    elif isinstance(additional, list):
        cleaned = clean_google_maps_value(additional)
        if cleaned:
            lines.append(f"補充資訊：{cleaned}")

    return "\n".join(lines)


def assess_google_maps_place_data(place: dict | None) -> dict:
    """Classify Google Maps structured data before asking AI to analyze it."""
    if not isinstance(place, dict) or not place:
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "empty_place_data",
        }

    name = place.get("title") or place.get("name")
    category = place.get("categoryName") or place.get("category")
    address = place.get("address") or place.get("street")
    rating = place.get("totalScore") or place.get("rating") or place.get("stars")
    phone = place.get("phone") or place.get("phoneUnformatted")
    website = place.get("website")
    maps_url = place.get("placeUrl") or place.get("googleMapsUrl") or place.get("url") or place.get("searchPageUrl")
    price = place.get("price") or place.get("priceLevel")
    hours = place.get("openingHours")
    description = place.get("description")
    reviews_count = get_google_maps_review_count(place)
    plus_code = place.get("plusCode")
    location = place.get("location") if isinstance(place.get("location"), dict) else {}
    has_coords = bool(
        (location.get("lat") and location.get("lng")) or
        (place.get("latitude") and place.get("longitude"))
    )
    detail_score = sum(bool(value) for value in [
        category,
        address,
        rating,
        phone,
        website,
        price,
        hours,
        description,
        has_coords,
        reviews_count,
        maps_url,
        plus_code,
    ])

    if not name and detail_score < 2:
        return {
            "status": CAPTURE_STATUS_FAILED,
            "needs_review": True,
            "reason": "insufficient_place_identity",
        }
    if detail_score < 2:
        return {
            "status": CAPTURE_STATUS_PARTIAL,
            "needs_review": True,
            "reason": "limited_place_fields",
        }
    return {
        "status": CAPTURE_STATUS_FULL,
        "needs_review": False,
        "reason": "",
    }


def setup_notion_social_database():
    """Initialize Notion social database with required properties"""
    if not notion_client or not NOTION_SOCIAL_DATABASE_ID:
        print("[DEBUG] Notion not configured for social database setup")
        return False

    try:
        # Update database with required properties
        notion_client.databases.update(
            database_id=NOTION_SOCIAL_DATABASE_ID,
            title=[{"text": {"content": "社群分析"}}],
            properties={
                "名稱": {"title": {}},
                "平台": {
                    "select": {
                        "options": [
                            {"name": "Facebook", "color": "blue"},
                            {"name": "Threads", "color": "purple"},
                        ]
                    }
                },
                "帳號": {"rich_text": {}},
                "內容摘要": {"rich_text": {}},
                "原始內容": {"rich_text": {}},
                "關鍵字": {"multi_select": {"options": []}},
                "Likes": {"number": {"format": "number"}},
                "留言數": {"number": {"format": "number"}},
                "分享數": {"number": {"format": "number"}},
                "來源網址": {"url": {}},
                "類型": {
                    "select": {
                        "options": [
                            {"name": "資訊分享", "color": "blue"},
                            {"name": "個人心得", "color": "green"},
                            {"name": "產品推廣", "color": "orange"},
                            {"name": "新聞報導", "color": "red"},
                            {"name": "教學內容", "color": "yellow"},
                            {"name": "娛樂內容", "color": "pink"},
                            {"name": "活動宣傳", "color": "purple"},
                            {"name": "其他", "color": "gray"},
                        ]
                    }
                },
                "LINE 用戶": {"rich_text": {}},
                "圖片": {"rich_text": {}},
                "圖片文字": {"rich_text": {}},
                "建立時間": {"created_time": {}},
            }
        )
        print("[DEBUG] Notion social database setup completed")
        return True
    except Exception as e:
        print(f"[DEBUG] Notion database setup error: {str(e)}")
        return False


def normalize_social_post_data(post_data: dict, platform: str) -> dict:
    """Normalize post data from different platforms to a common format"""
    print(f"[DEBUG] Raw post data: {post_data}")
    post_data = post_data or {}
    platform = (platform or "").lower()

    if platform == "facebook":
        user_dict = post_data.get("user") if isinstance(post_data.get("user"), dict) else {}
        author_dict = post_data.get("author") if isinstance(post_data.get("author"), dict) else {}
        username = (
            clean_social_value(pick_social_value(post_data, "pageName", "userName", "accountName", "profileName")) or
            clean_social_value(user_dict.get("name") or user_dict.get("username")) or
            clean_social_value(author_dict.get("name") or author_dict.get("username")) or
            clean_social_value(post_data.get("name")) or
            "未知"
        )
        text = (
            clean_social_value(pick_social_value(
                post_data,
                "text",
                "postText",
                "message",
                "description",
                "content",
                "caption",
                "body",
            ))
        )
        reactions_dict = post_data.get("reactions") if isinstance(post_data.get("reactions"), dict) else {}
        statistics_dict = post_data.get("statistics") if isinstance(post_data.get("statistics"), dict) else {}
        likes = (
            post_data.get("likes") or
            post_data.get("likesCount") or
            post_data.get("reactionsCount") or
            post_data.get("reactionCount") or
            statistics_dict.get("likes") or
            statistics_dict.get("reactions") or
            reactions_dict.get("count") or
            reactions_dict.get("total") or
            0
        )
        comments = (
            post_data.get("comments") or
            post_data.get("commentsCount") or
            post_data.get("commentCount") or
            statistics_dict.get("comments") or
            0
        )
        shares = (
            post_data.get("shares") or
            post_data.get("sharesCount") or
            post_data.get("shareCount") or
            statistics_dict.get("shares") or
            0
        )
        return {
            "username": username,
            "text": text,
            "likes": coerce_social_count(likes),
            "comments": coerce_social_count(comments),
            "shares": coerce_social_count(shares),
            "images": collect_social_images(post_data),
            "image_text": collect_social_image_text(post_data),
            "post_url": clean_social_value(pick_social_value(
                post_data,
                "url",
                "postUrl",
                "facebookUrl",
                "permalinkUrl",
                "link",
            )),
            "published_at": clean_social_value(pick_social_value(
                post_data,
                "time",
                "timestamp",
                "date",
                "createdTime",
                "creationTime",
            )),
            "content_type": clean_social_value(pick_social_value(post_data, "type", "__typename", "postType")),
        }
    elif platform == "threads":
        author_dict = post_data.get("author") if isinstance(post_data.get("author"), dict) else {}
        user_dict = post_data.get("user") if isinstance(post_data.get("user"), dict) else {}
        author_id = post_data.get("authorId", "")
        if isinstance(author_id, str):
            author_id = re.sub(r'^/?@', '', author_id.strip())
        username = (
            clean_social_value(pick_social_value(post_data, "ownerUsername", "username")) or
            clean_social_value(author_dict.get("username") or author_dict.get("name")) or
            clean_social_value(user_dict.get("username") or user_dict.get("name")) or
            clean_social_value(post_data.get("authorName")) or
            author_id or
            "未知"
        )
        text = (
            clean_social_value(pick_social_value(
                post_data,
                "text",
                "caption",
                "content",
                "postText",
                "description",
                "body",
            ))
        )
        metrics = post_data.get("metrics") if isinstance(post_data.get("metrics"), dict) else {}
        return {
            "username": username,
            "text": text,
            "likes": coerce_social_count(
                post_data.get("likeCount") or post_data.get("likesCount") or post_data.get("likes") or metrics.get("likes")
            ),
            "comments": coerce_social_count(
                post_data.get("replyCount") or post_data.get("commentsCount") or post_data.get("comments") or metrics.get("replies")
            ),
            "shares": coerce_social_count(
                post_data.get("repostCount") or post_data.get("shareCount") or post_data.get("shares") or metrics.get("reposts")
            ),
            "images": collect_social_images(post_data),
            "image_text": collect_social_image_text(post_data),
            "post_url": clean_social_value(pick_social_value(post_data, "postUrl", "url", "threadUrl", "permalink")),
            "published_at": clean_social_value(pick_social_value(post_data, "timestamp", "time", "takenAt", "date", "createdAt")),
            "content_type": clean_social_value(pick_social_value(post_data, "type", "mediaType", "__typename")),
        }
    return {
        "username": "未知",
        "text": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "images": [],
        "image_text": "",
        "post_url": "",
        "published_at": "",
        "content_type": "",
    }


def summarize_social_post(post_data: dict, platform: str) -> str:
    """Use AI to analyze social media post"""
    if not openai_client:
        return "社群分析功能未設定，請設定 OPENAI_API_KEY"

    platform_name = "Facebook" if platform == "facebook" else "Threads"

    try:
        # Build image context if available
        image_context = ""
        image_text = post_data.get('image_text', '')
        images = post_data.get('images', [])
        if image_text:
            image_context += f"\n圖片中的文字（OCR）：{image_text}"
        if images:
            image_context += f"\n附圖數量：{len(images)} 張"

        prompt = f"""分析以下 {platform_name} 貼文：
帳號：{post_data.get('username', '未知')}
內容：{post_data.get('text', '')}{image_context}
互動數據：{post_data.get('likes', 0)} 讚、{post_data.get('comments', 0)} 留言、{post_data.get('shares', 0)} 分享

請用以下格式回覆（繁體中文）：

📌 帳號：{post_data.get('username', '未知')}

📝 摘要：[用2-3句話摘要貼文內容的重點]

🔑 關鍵字：[3-5個關鍵字，用頓號分隔]

📊 互動數據：{post_data.get('likes', 0)} 讚 | {post_data.get('comments', 0)} 留言 | {post_data.get('shares', 0)} 分享

🎯 貼文類型：[只選一個：資訊分享、個人心得、產品推廣、新聞報導、教學內容、娛樂內容、活動宣傳、其他]
"""
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是一個專業的社群媒體分析助手，擅長分析貼文內容並提取關鍵資訊。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"社群分析失敗：{str(e)}"


def parse_social_summary_response(response: str) -> dict:
    """Parse summary and keywords from social post AI response"""
    result = {
        "summary": "",
        "keywords": [],
        "post_type": "其他",
    }

    # Parse 📝 摘要：xxx
    summary_match = re.search(r'📝\s*摘要[：:]\s*(.+?)(?:\n\n|🔑|$)', response, re.DOTALL)
    if summary_match:
        result["summary"] = summary_match.group(1).strip()

    # Parse 🔑 關鍵字：xxx
    keywords_match = re.search(r'🔑\s*關鍵字[：:]\s*(.+?)(?:\n\n|📊|$)', response, re.DOTALL)
    if keywords_match:
        keywords_text = keywords_match.group(1).strip()
        keywords = re.split(r'[、,，]', keywords_text)
        result["keywords"] = [kw.strip() for kw in keywords if kw.strip() and len(kw.strip()) < 50]

    # Parse 🎯 貼文類型：xxx
    type_match = re.search(r'🎯\s*貼文類型[：:]\s*(.+?)(?:\n|$)', response)
    if type_match:
        result["post_type"] = type_match.group(1).strip()

    return result


def fetch_webpage_content(url: str) -> str:
    """Fetch webpage content via Jina AI Reader (handles JS rendering, returns clean markdown)"""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            'Accept': 'text/plain',
            'X-Return-Format': 'markdown',
        }
        response = requests.get(jina_url, headers=headers, timeout=15)
        response.raise_for_status()
        content = response.text
        if len(content) > 3000:
            content = content[:3000] + "..."
        return content
    except Exception as e:
        print(f"[DEBUG] Jina AI fetch failed: {str(e)}, falling back to direct fetch")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=5)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
                element.decompose()
            content = soup.get_text(separator='\n', strip=True)
            lines = [line.strip() for line in content.split('\n') if len(line.strip()) > 20]
            content = '\n'.join(lines)
            if len(content) > 2000:
                content = content[:2000] + "..."
            return content
        except Exception as e2:
            return f"無法抓取網頁內容：{str(e2)}"

    except Exception as e:
        return f"無法抓取網頁內容：{str(e)}"


def extract_youtube_video_id(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        return re.sub(r'[^A-Za-z0-9_-]', '', path_parts[0])
    if "youtube.com" not in host:
        return ""
    query = parse_qs(parsed.query)
    if query.get("v"):
        return re.sub(r'[^A-Za-z0-9_-]', '', query["v"][0])
    if len(path_parts) >= 2 and path_parts[0] in ["embed", "shorts", "live"]:
        return re.sub(r'[^A-Za-z0-9_-]', '', path_parts[1])
    return ""


def extract_balanced_json(text: str, start_index: int) -> str:
    if start_index < 0 or start_index >= len(text) or text[start_index] != "{":
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return ""


def extract_yt_initial_player_response(html_text: str) -> dict:
    marker_match = re.search(
        r'(?:ytInitialPlayerResponse|window\["ytInitialPlayerResponse"\]|window\[\'ytInitialPlayerResponse\'\])\s*=',
        html_text or "",
    )
    if not marker_match:
        return {}
    json_start = (html_text or "").find("{", marker_match.end())
    json_text = extract_balanced_json(html_text or "", json_start)
    if not json_text:
        return {}
    try:
        return json.loads(json_text)
    except Exception as e:
        print(f"[DEBUG] YouTube player response parse failed: {str(e)}")
        return {}


def extract_youtube_text(value) -> str:
    """Read text from common YouTube renderer payload shapes."""
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r'\s+', ' ', value).strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [extract_youtube_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ["simpleText", "text", "content", "label"]:
            text = extract_youtube_text(value.get(key))
            if text:
                return text
        runs = value.get("runs")
        if isinstance(runs, list):
            return extract_youtube_text([run.get("text") for run in runs if isinstance(run, dict)])
        accessibility = value.get("accessibilityData")
        if isinstance(accessibility, dict):
            return extract_youtube_text(accessibility.get("label"))
    return ""


def format_youtube_duration(seconds_value) -> str:
    try:
        seconds = int(float(seconds_value))
    except (TypeError, ValueError):
        return extract_youtube_text(seconds_value)
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def choose_youtube_caption_track(player_response: dict) -> dict:
    tracks = (
        player_response.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if not isinstance(tracks, list) or not tracks:
        return {}

    preferred_langs = ["zh-Hant", "zh-TW", "zh-Hans", "zh", "en"]

    def track_score(track: dict) -> tuple[int, int]:
        lang = track.get("languageCode") or ""
        try:
            lang_index = preferred_langs.index(lang)
        except ValueError:
            lang_index = len(preferred_langs)
        is_auto = 1 if track.get("kind") == "asr" else 0
        return (lang_index, is_auto)

    valid_tracks = [track for track in tracks if isinstance(track, dict) and track.get("baseUrl")]
    if not valid_tracks:
        return {}
    return sorted(valid_tracks, key=track_score)[0]


def fetch_youtube_transcript(caption_url: str) -> str:
    if not caption_url:
        return ""
    try:
        transcript_url = caption_url
        if "fmt=" not in transcript_url:
            separator = "&" if "?" in transcript_url else "?"
            transcript_url = f"{transcript_url}{separator}fmt=json3"
        response = requests.get(transcript_url, timeout=10)
        response.raise_for_status()
        raw_text = response.text
        lines = []
        try:
            payload = response.json()
            for event in payload.get("events", []):
                seg_text = "".join(seg.get("utf8", "") for seg in event.get("segs", []) if isinstance(seg, dict))
                seg_text = re.sub(r'\s+', ' ', seg_text).strip()
                if seg_text:
                    lines.append(seg_text)
        except Exception:
            soup = BeautifulSoup(raw_text, "html.parser")
            for item in soup.find_all("text"):
                seg_text = re.sub(r'\s+', ' ', item.get_text(" ", strip=True)).strip()
                if seg_text:
                    lines.append(seg_text)
        transcript = "\n".join(lines)
        if len(transcript) > 6000:
            transcript = transcript[:6000] + "..."
        return transcript
    except Exception as e:
        print(f"[DEBUG] YouTube transcript fetch failed: {str(e)}")
        return ""


def fetch_youtube_page_data(url: str) -> tuple[dict, str]:
    video_id = extract_youtube_video_id(url)
    watch_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
    try:
        response = requests.get(
            watch_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        response.raise_for_status()
        return extract_yt_initial_player_response(response.text), response.url
    except Exception as e:
        print(f"[DEBUG] YouTube watch page fetch failed: {str(e)}")
        return {}, watch_url


def fetch_youtube_content(url: str) -> tuple[str, str]:
    """Fetch YouTube metadata and transcript when public captions are available."""
    metadata = {
        "video_id": extract_youtube_video_id(url),
        "title": "",
        "author_name": "",
        "author_url": "",
        "description": "",
        "publish_date": "",
        "upload_date": "",
        "length_seconds": "",
        "view_count": "",
        "category": "",
        "channel_id": "",
        "is_live": "",
    }
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        metadata.update({
            "title": data.get("title", ""),
            "author_name": data.get("author_name", ""),
            "author_url": data.get("author_url", ""),
        })
    except Exception as e:
        print(f"[DEBUG] YouTube metadata fetch failed: {str(e)}")

    player_response, canonical_url = fetch_youtube_page_data(url)
    video_details = player_response.get("videoDetails", {}) if isinstance(player_response.get("videoDetails"), dict) else {}
    microformat = (
        player_response.get("microformat", {})
        .get("playerMicroformatRenderer", {})
        if isinstance(player_response.get("microformat"), dict)
        else {}
    )
    metadata.update({
        "video_id": metadata["video_id"] or video_details.get("videoId", ""),
        "title": metadata["title"] or video_details.get("title", ""),
        "author_name": metadata["author_name"] or video_details.get("author", "") or microformat.get("ownerChannelName", ""),
        "author_url": metadata["author_url"] or microformat.get("ownerProfileUrl", ""),
        "description": video_details.get("shortDescription", "") or extract_youtube_text(microformat.get("description")),
        "publish_date": microformat.get("publishDate", ""),
        "upload_date": microformat.get("uploadDate", ""),
        "length_seconds": video_details.get("lengthSeconds", ""),
        "view_count": video_details.get("viewCount", "") or extract_youtube_text(microformat.get("viewCount")),
        "category": microformat.get("category", ""),
        "channel_id": video_details.get("channelId", "") or microformat.get("externalChannelId", ""),
        "is_live": video_details.get("isLiveContent", ""),
    })

    transcript = ""
    caption_track = choose_youtube_caption_track(player_response)
    if caption_track:
        transcript = fetch_youtube_transcript(caption_track.get("baseUrl", ""))

    lines = []
    if metadata["video_id"]:
        lines.append(f"影片 ID：{metadata['video_id']}")
    lines.extend([
        f"標題：{metadata['title']}",
        f"頻道：{metadata['author_name']}",
        f"頻道網址：{metadata['author_url']}",
        f"影片網址：{canonical_url or url}",
    ])
    if metadata["channel_id"]:
        lines.append(f"頻道 ID：{metadata['channel_id']}")
    if metadata["publish_date"]:
        lines.append(f"發布日期：{metadata['publish_date']}")
    if metadata["upload_date"] and metadata["upload_date"] != metadata["publish_date"]:
        lines.append(f"上傳日期：{metadata['upload_date']}")
    if metadata["length_seconds"]:
        duration = format_youtube_duration(metadata["length_seconds"])
        length_text = f"{duration}（{metadata['length_seconds']} 秒）" if duration else metadata["length_seconds"]
        lines.append(f"影片長度：{length_text}")
    if metadata["view_count"]:
        lines.append(f"觀看次數：{metadata['view_count']}")
    if metadata["category"]:
        lines.append(f"分類：{metadata['category']}")
    if metadata["is_live"] is True:
        lines.append("直播狀態：直播內容")
    if metadata["description"]:
        lines.extend(["", "影片描述：", metadata["description"][:1500]])

    if transcript:
        lines.extend(["", "逐字稿：", transcript])
        return "\n".join(line for line in lines if line is not None), "youtube-transcript"

    lines.extend(["", "字幕：尚未抓取逐字稿，先保存影片 metadata。"])
    content = "\n".join(line for line in lines if line is not None)
    if metadata["title"] or metadata["author_name"]:
        return content, "youtube-oembed"
    fallback = fetch_webpage_content(url)
    return fallback, "jina"


def parse_ptt_article_html(html_text: str, url: str = "") -> dict:
    """Parse a PTT article page into metadata, body, and push comments."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    main_content = soup.select_one("#main-content")
    if not main_content:
        return {}

    metadata = {}
    for metaline in main_content.select(".article-metaline"):
        tag = metaline.select_one(".article-meta-tag")
        value = metaline.select_one(".article-meta-value")
        if tag and value:
            metadata[tag.get_text(strip=True)] = value.get_text(" ", strip=True)

    parsed_url = urlparse(url or "")
    path_parts = [part for part in parsed_url.path.split("/") if part]
    board = path_parts[1] if len(path_parts) >= 2 and path_parts[0].lower() == "bbs" else ""
    article_id = path_parts[2] if len(path_parts) >= 3 else ""

    pushes = []
    push_counts = {"推": 0, "噓": 0, "→": 0}
    for push in main_content.select(".push"):
        tag = push.select_one(".push-tag")
        user = push.select_one(".push-userid")
        content = push.select_one(".push-content")
        datetime_text = push.select_one(".push-ipdatetime")
        tag_text = (tag.get_text(strip=True) if tag else "").strip()
        if tag_text.startswith("推"):
            tag_key = "推"
        elif tag_text.startswith("噓"):
            tag_key = "噓"
        else:
            tag_key = "→"
        push_counts[tag_key] += 1
        content_text = content.get_text(" ", strip=True).lstrip(":").strip() if content else ""
        pushes.append({
            "tag": tag_key,
            "user": user.get_text(strip=True) if user else "",
            "content": content_text,
            "datetime": datetime_text.get_text(" ", strip=True) if datetime_text else "",
        })

    for element in main_content.select(".article-metaline, .article-metaline-right, .push, script, style"):
        element.decompose()

    lines = []
    for raw_line in main_content.get_text("\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("※ 發信站:") or line.startswith("※ 文章網址:"):
            break
        if line == "--":
            break
        if line.startswith("※") or line.startswith("◆"):
            continue
        lines.append(line)

    title = metadata.get("標題") or ""
    if not title:
        og_title = soup.select_one("meta[property='og:title']")
        title = og_title.get("content", "").strip() if og_title else ""

    return {
        "board": board,
        "article_id": article_id,
        "author": metadata.get("作者", ""),
        "title": title,
        "date": metadata.get("時間", ""),
        "body": "\n".join(lines).strip(),
        "push_counts": push_counts,
        "pushes": pushes,
        "url": url,
    }


def format_ptt_article(article: dict) -> str:
    """Format parsed PTT data as stable plain text for summarization."""
    if not article:
        return ""

    lines = [
        f"看板：{article.get('board', '')}",
        f"文章 ID：{article.get('article_id', '')}",
        f"標題：{article.get('title', '')}",
        f"作者：{article.get('author', '')}",
        f"時間：{article.get('date', '')}",
        f"來源：{article.get('url', '')}",
        "",
        "## 本文",
        article.get("body") or "（未抓到本文內容）",
        "",
        "## 推文統計",
    ]
    push_counts = article.get("push_counts") or {}
    lines.extend([
        f"- 推：{push_counts.get('推', 0)}",
        f"- 噓：{push_counts.get('噓', 0)}",
        f"- →：{push_counts.get('→', 0)}",
    ])

    pushes = article.get("pushes") or []
    if pushes:
        lines.extend(["", "## 推文節錄"])
        for push in pushes[:30]:
            user = push.get("user") or "unknown"
            content = push.get("content") or ""
            datetime_text = push.get("datetime") or ""
            suffix = f" ({datetime_text})" if datetime_text else ""
            lines.append(f"- {push.get('tag', '→')} {user}: {content}{suffix}")
        if len(pushes) > 30:
            lines.append(f"- 另有 {len(pushes) - 30} 則推文未列出")

    return "\n".join(lines).strip()


def fetch_ptt_content(url: str) -> tuple[str, str]:
    """Fetch PTT article content with over18 cookie."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, cookies={"over18": "1"}, timeout=10)
        response.raise_for_status()
        article = parse_ptt_article_html(response.text, url)
        content = format_ptt_article(article)
        if not content:
            return fetch_webpage_content(url), "jina"
        return content[:5000], "ptt-html"
    except Exception as e:
        print(f"[DEBUG] PTT fetch failed: {str(e)}")
        return fetch_webpage_content(url), "jina"


def extract_104_job_id(url: str) -> str:
    match = re.search(r'104\.com\.tw/job/([0-9a-zA-Z]+)', url or "")
    return match.group(1) if match else ""


def clean_104_text(value) -> str:
    """Normalize 104 API values while preserving human-readable meaning."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [clean_104_text(item) for item in value]
        return "、".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ["description", "desc", "name", "value", "text", "label"]:
            if value.get(key):
                return clean_104_text(value.get(key))
        parts = [clean_104_text(item) for item in value.values()]
        return "、".join(part for part in parts if part)
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text("\n", strip=True)
    text = re.sub(r'<([^<>\n]{1,120})>', r'（\1）', text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def format_104_job_type(value) -> str:
    mapping = {
        "1": "全職",
        "2": "兼職",
        "3": "高階",
        "4": "派遣",
        "5": "接案",
        "6": "實習",
    }
    text = clean_104_text(value)
    return mapping.get(text, text)


def pick_104_field(data: dict, *paths) -> str:
    for path in paths:
        current = data
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        value = clean_104_text(current)
        if value:
            return value
    return ""


def normalize_104_job_payload(payload: dict, url: str = "") -> dict:
    """Convert 104 ajax payload variants into a stable job dict."""
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    header = data.get("header") if isinstance(data.get("header"), dict) else {}
    job_detail = data.get("jobDetail") if isinstance(data.get("jobDetail"), dict) else {}
    condition = data.get("condition") if isinstance(data.get("condition"), dict) else {}
    welfare = data.get("welfare") if isinstance(data.get("welfare"), dict) else {}
    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}

    address = " ".join(
        part for part in [
            clean_104_text(job_detail.get("addressRegion")),
            clean_104_text(job_detail.get("addressDetail")),
            clean_104_text(job_detail.get("landmark")),
        ]
        if part
    )
    skill_parts = [
        clean_104_text(condition.get("specialty")),
        clean_104_text(condition.get("skill")),
    ]
    welfare_parts = [
        clean_104_text(welfare.get("welfare")),
        clean_104_text(welfare.get("tag")),
        clean_104_text(welfare.get("legalTag")),
    ]

    return {
        "job_id": extract_104_job_id(url),
        "source_url": url,
        "job_name": pick_104_field(data, ("header", "jobName"), ("jobName",)),
        "company_name": pick_104_field(data, ("header", "custName"), ("custName",)),
        "company_url": clean_104_text(header.get("custUrl")),
        "industry": clean_104_text(data.get("industry")),
        "employees": clean_104_text(data.get("employees")),
        "appear_date": clean_104_text(header.get("appearDate")),
        "apply_count": clean_104_text(header.get("userApplyCount")),
        "address": address,
        "salary": clean_104_text(job_detail.get("salary")),
        "job_type": format_104_job_type(job_detail.get("jobType")),
        "work_type": clean_104_text(job_detail.get("workType")),
        "work_period": clean_104_text(job_detail.get("workPeriod") or job_detail.get("workPeriodTags")),
        "vacation_policy": clean_104_text(job_detail.get("vacationPolicy")),
        "start_working_day": clean_104_text(job_detail.get("startWorkingDay")),
        "remote_work": clean_104_text(job_detail.get("remoteWork")),
        "business_trip": clean_104_text(job_detail.get("businessTrip")),
        "manage_resp": clean_104_text(job_detail.get("manageResp")),
        "need_emp": clean_104_text(job_detail.get("needEmp")),
        "job_category": clean_104_text(job_detail.get("jobCategory")),
        "job_description": clean_104_text(job_detail.get("jobDescription")),
        "work_exp": clean_104_text(condition.get("workExp")),
        "education": clean_104_text(condition.get("edu")),
        "major": clean_104_text(condition.get("major")),
        "language": clean_104_text(condition.get("language") or condition.get("localLanguage")),
        "skills": "、".join(part for part in skill_parts if part),
        "certificates": clean_104_text(condition.get("certificate")),
        "driver_license": clean_104_text(condition.get("driverLicense")),
        "accept_role": clean_104_text(condition.get("acceptRole")),
        "condition_other": clean_104_text(condition.get("other")),
        "welfare": "\n".join(part for part in welfare_parts if part),
        "contact": "、".join(
            part for part in [
                clean_104_text(contact.get("hrName")),
                clean_104_text(contact.get("phone")),
                clean_104_text(contact.get("email")),
                clean_104_text(contact.get("other")),
            ]
            if part
        ),
    }


def append_104_line(lines: list[str], label: str, value: str) -> None:
    if value:
        lines.append(f"- {label}：{value}")


def format_104_job(job: dict) -> str:
    """Format normalized 104 job data for safe capture and summarization."""
    if not job or not any(job.get(key) for key in ["job_name", "company_name", "job_description"]):
        return ""

    lines = [
        f"職缺：{job.get('job_name', '')}",
        f"公司：{job.get('company_name', '')}",
        f"職缺 ID：{job.get('job_id', '')}",
        f"來源：{job.get('source_url', '')}",
    ]
    if job.get("appear_date"):
        lines.append(f"刊登日期：{job['appear_date']}")
    if job.get("apply_count"):
        lines.append(f"應徵人數：{job['apply_count']}")

    lines.extend(["", "## 工作資訊"])
    append_104_line(lines, "地點", job.get("address", ""))
    append_104_line(lines, "薪資", job.get("salary", ""))
    append_104_line(lines, "職務類別", job.get("job_category", ""))
    append_104_line(lines, "工作性質", job.get("job_type", ""))
    append_104_line(lines, "工作型態", job.get("work_type", ""))
    append_104_line(lines, "上班時段", job.get("work_period", ""))
    append_104_line(lines, "休假制度", job.get("vacation_policy", ""))
    append_104_line(lines, "可上班日", job.get("start_working_day", ""))
    append_104_line(lines, "遠端工作", job.get("remote_work", ""))
    append_104_line(lines, "出差外派", job.get("business_trip", ""))
    append_104_line(lines, "管理責任", job.get("manage_resp", ""))
    append_104_line(lines, "需求人數", job.get("need_emp", ""))

    lines.extend(["", "## 工作內容", job.get("job_description") or "（未抓到工作內容）"])

    lines.extend(["", "## 條件要求"])
    append_104_line(lines, "工作經歷", job.get("work_exp", ""))
    append_104_line(lines, "學歷要求", job.get("education", ""))
    append_104_line(lines, "科系要求", job.get("major", ""))
    append_104_line(lines, "語文條件", job.get("language", ""))
    append_104_line(lines, "技能工具", job.get("skills", ""))
    append_104_line(lines, "證照", job.get("certificates", ""))
    append_104_line(lines, "駕照", job.get("driver_license", ""))
    append_104_line(lines, "接受身份", job.get("accept_role", ""))
    append_104_line(lines, "其他條件", job.get("condition_other", ""))

    if job.get("welfare"):
        lines.extend(["", "## 福利制度", job["welfare"]])

    lines.extend(["", "## 公司資訊"])
    append_104_line(lines, "產業", job.get("industry", ""))
    append_104_line(lines, "員工人數", job.get("employees", ""))
    append_104_line(lines, "公司頁", job.get("company_url", ""))
    append_104_line(lines, "聯絡資訊", job.get("contact", ""))

    return "\n".join(lines).strip()


def fetch_104_content(url: str) -> tuple[str, str]:
    """Fetch 104 job detail from its public ajax endpoint when possible."""
    job_id = extract_104_job_id(url)
    if not job_id:
        return fetch_webpage_content(url), "jina"
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'https://www.104.com.tw/job/{job_id}',
        }
        response = requests.get(f"https://www.104.com.tw/job/ajax/content/{job_id}", headers=headers, timeout=10)
        response.raise_for_status()
        payload = response.json()
        job = normalize_104_job_payload(payload, url)
        content = format_104_job(job)
        if not content:
            return fetch_webpage_content(url), "jina"
        return content[:6000], "104-ajax"
    except Exception as e:
        print(f"[DEBUG] 104 fetch failed: {str(e)}")
        return fetch_webpage_content(url), "jina"


def fetch_content_by_source_type(url: str, source_type: str) -> tuple[str, str]:
    """Fetch URL content with source-specific extractors where available."""
    if source_type == "youtube":
        return fetch_youtube_content(url)
    if source_type == "ptt":
        return fetch_ptt_content(url)
    if source_type == "104":
        return fetch_104_content(url)
    return fetch_webpage_content(url), "jina"


def summarize_webpage(content: str) -> str:
    """Use OpenAI to summarize webpage content"""
    if not openai_client:
        return "網頁摘要功能未設定，請設定 OPENAI_API_KEY"

    try:
        prompt = f"""請分析以下網頁內容，用繁體中文提供完整摘要：

{content}

請用以下格式回覆：

🏷️ 分類：[只選一個：科技、AI、金融、商業、新聞、教學、運動、美食、旅遊、地圖、電影、書籍、投資、生活、娛樂、其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1]
• [重點2]
• [重點3]

🔑 關鍵字：[3-5個關鍵字，用頓號分隔]

🎯 一句話總結：[核心價值或啟發]

💭 建議思考：[這個資訊對你有什麼用？可以如何應用？]
"""
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是一個幫助用戶建立個人知識庫的助手，擅長提取網頁重點，並引導用戶思考如何應用這些資訊，用繁體中文清晰呈現。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.7
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"摘要生成失敗：{str(e)}"


def summarize_google_maps(content: str, url: str) -> str:
    """Use OpenAI to analyze Google Maps location"""
    if not openai_client:
        return "地圖分析功能未設定，請設定 OPENAI_API_KEY"

    try:
        prompt = f"""請分析以下 Google 地圖的地點資訊，用繁體中文提供分類和摘要：

網址：{url}
頁面內容：{content}

請用以下格式回覆：

🏷️ 分類：地圖

📍 地區：[國家/城市，例如：日本東京、臺灣台北、美國紐約]

🍽️ 類型：[如果是餐廳，請分類：日式、義式、美式、法式、中式、韓式、泰式、越南、印度、墨西哥、歐式、咖啡廳、酒吧、甜點、其他]
[如果不是餐廳，請說明是什麼類型的地點：景點、飯店、商店、公司、住宅、其他]

📌 地點名稱：[店名或地點名稱]

📝 重點資訊：
• [營業時間、評分、價位等資訊，如果有的話]
• [特色或推薦項目]
• [地址或交通方式]

🔑 關鍵字：[列出3-5個關鍵字，用頓號分隔，例如：日本料理、拉麵、東京]

🎯 一句話總結：[簡短描述這個地點]

注意：如果無法從內容判斷某些資訊，請標註「無法判斷」而非猜測。
"""
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是一個專業的地點分析助手，擅長從 Google 地圖資訊中提取地點類型、地區和詳細資訊。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.5
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"地圖分析失敗：{str(e)}"


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


def parse_summary_response(response: str) -> dict:
    """Parse category and keywords from AI summary response"""
    result = {
        "category": "其他",
        "keywords": [],
        "title": ""
    }

    # Parse 🏷️ 分類：xxx
    category_match = re.search(r'🏷️\s*分類[：:]\s*(.+?)(?:\n|$)', response)
    if category_match:
        category = category_match.group(1).strip()
        # If category contains slash, take the first one
        if '/' in category:
            category = category.split('/')[0].strip()
        result["category"] = category

    # Parse 📌 主題：xxx or 📌 地點名稱：xxx
    title_match = re.search(r'📌\s*(?:主題|地點名稱)[：:]\s*(.+?)(?:\n|$)', response)
    if title_match:
        result["title"] = title_match.group(1).strip()

    # Parse 🔑 關鍵字 or 💡 關鍵字 or 💡 關鍵資訊
    keywords_match = re.search(r'[🔑💡]\s*關鍵(?:字|資訊)[：:]\s*(.+?)(?:\n\n|🎯|$)', response, re.DOTALL)
    if keywords_match:
        keywords_text = keywords_match.group(1).strip()
        # Remove any newlines and clean up
        keywords_text = keywords_text.replace('\n', '、')
        # Split by common separators: 、,，
        keywords = re.split(r'[、,，]', keywords_text)
        # Clean up each keyword and filter empty ones
        result["keywords"] = [kw.strip() for kw in keywords if kw.strip() and len(kw.strip()) < 50]

    return result


def get_gdrive_service():
    """Initialize Google Drive service.
    Uses GDRIVE_CREDENTIALS_JSON env var (for Zeabur) or falls back to local file."""
    import json as _json
    scopes = ['https://www.googleapis.com/auth/drive']
    credentials_json = os.getenv("GDRIVE_CREDENTIALS_JSON")
    if credentials_json:
        info = _json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials = service_account.Credentials.from_service_account_file(
            GDRIVE_CREDENTIALS_FILE, scopes=scopes
        )
    return build('drive', 'v3', credentials=credentials)


def get_calendar_service():
    """Initialize Google Calendar service using service account credentials"""
    import json as _json
    scopes = ['https://www.googleapis.com/auth/calendar']
    credentials_json = os.getenv("GDRIVE_CREDENTIALS_JSON")
    if credentials_json:
        info = _json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials = service_account.Credentials.from_service_account_file(
            GDRIVE_CREDENTIALS_FILE, scopes=scopes
        )
    return build('calendar', 'v3', credentials=credentials)


def parse_event_from_text(text: str) -> dict | None:
    """Use AI to parse natural language into structured calendar event"""
    if not openai_client:
        return None
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    weekday_map = {"Monday": "週一", "Tuesday": "週二", "Wednesday": "週三",
                   "Thursday": "週四", "Friday": "週五", "Saturday": "週六", "Sunday": "週日"}
    weekday_str = weekday_map.get(today.strftime("%A"), today.strftime("%A"))
    try:
        import json as _json
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{
                "role": "user",
                "content": f"""今天是 {today_str}（{weekday_str}）。
請從以下文字提取行事曆事件，用 JSON 格式回覆（只回 JSON，不要加說明）：

文字：{text}

格式：
{{
  "title": "事件標題",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "location": "地點或空字串",
  "description": "備註或空字串"
}}

規則：
- 「明天」= {(today + timedelta(days=1)).strftime("%Y-%m-%d")}
- 「後天」= {(today + timedelta(days=2)).strftime("%Y-%m-%d")}
- 「週X」= 換算成最近那天的日期
- 只說時間沒說結束時間 → 預設 1 小時
- 全天行程：start_time 和 end_time 都填 "00:00"
"""
            }],
            max_tokens=250,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE)
        return _json.loads(raw)
    except Exception as e:
        print(f"[DEBUG] Parse event error: {str(e)}")
        return None


def create_calendar_event(title: str, date: str, start_time: str, end_time: str,
                          location: str = "", description: str = "") -> int:
    """Create event on ALL configured calendars. Returns count of successes."""
    try:
        service = get_calendar_service()
        if start_time == "00:00" and end_time == "00:00":
            event_body = {
                'summary': title,
                'location': location,
                'description': description,
                'start': {'date': date, 'timeZone': 'Asia/Taipei'},
                'end': {'date': date, 'timeZone': 'Asia/Taipei'},
                'reminders': {'useDefault': True},
            }
        else:
            event_body = {
                'summary': title,
                'location': location,
                'description': description,
                'start': {'dateTime': f"{date}T{start_time}:00+08:00", 'timeZone': 'Asia/Taipei'},
                'end': {'dateTime': f"{date}T{end_time}:00+08:00", 'timeZone': 'Asia/Taipei'},
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 30},
                        {'method': 'email', 'minutes': 60},
                    ],
                },
            }
        success = 0
        for cal_id in GOOGLE_CALENDAR_IDS:
            try:
                service.events().insert(calendarId=cal_id, body=event_body).execute()
                print(f"[DEBUG] Created event '{title}' in calendar: {cal_id}")
                success += 1
            except Exception as e:
                print(f"[DEBUG] Failed to create event in {cal_id}: {str(e)}")
        return success
    except Exception as e:
        print(f"[DEBUG] Create event error: {str(e)}")
        return 0


def list_upcoming_events(days: int = 7) -> list:
    """List upcoming events across ALL configured calendars, deduped by title+time"""
    try:
        service = get_calendar_service()
        now = datetime.utcnow().isoformat() + 'Z'
        end = (datetime.utcnow() + timedelta(days=days)).isoformat() + 'Z'
        seen = set()
        all_events = []
        for cal_id in GOOGLE_CALENDAR_IDS:
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=now, timeMax=end,
                    maxResults=20, singleEvents=True, orderBy='startTime'
                ).execute()
                for ev in result.get('items', []):
                    key = (ev.get('summary', ''), ev['start'].get('dateTime', ev['start'].get('date', '')))
                    if key not in seen:
                        seen.add(key)
                        all_events.append(ev)
            except Exception as e:
                print(f"[DEBUG] List events error for {cal_id}: {str(e)}")
        all_events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
        return all_events
    except Exception as e:
        print(f"[DEBUG] List events error: {str(e)}")
        return []


def format_event_list(events: list, label: str = "") -> str:
    """Format events list for LINE message"""
    if not events:
        return f"{label}沒有任何行程" if label else "沒有任何行程"
    lines = [f"📅 {label}行程（共 {len(events)} 個）\n"] if label else [f"📅 行程（共 {len(events)} 個）\n"]
    current_date = ""
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date', ''))
        if 'T' in start:
            dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            date_str = dt.strftime("%-m/%-d（%a）").replace(
                "Mon", "週一").replace("Tue", "週二").replace("Wed", "週三").replace(
                "Thu", "週四").replace("Fri", "週五").replace("Sat", "週六").replace("Sun", "週日")
            time_str = dt.strftime("%H:%M")
        else:
            date_str = start[5:].replace('-', '/')
            time_str = "全天"
        if date_str != current_date:
            current_date = date_str
            lines.append(f"\n{date_str}")
        title = event.get('summary', '（無標題）')
        loc = event.get('location', '')
        loc_str = f" ｜ {loc}" if loc else ""
        lines.append(f"  • {time_str} {title}{loc_str}")
    return "\n".join(lines)


def get_today_events() -> list:
    """List today's events across ALL configured calendars, deduped"""
    try:
        service = get_calendar_service()
        today = datetime.now().strftime("%Y-%m-%d")
        start = f"{today}T00:00:00+08:00"
        end = f"{today}T23:59:59+08:00"
        seen = set()
        all_events = []
        for cal_id in GOOGLE_CALENDAR_IDS:
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=start, timeMax=end,
                    maxResults=20, singleEvents=True, orderBy='startTime'
                ).execute()
                for ev in result.get('items', []):
                    key = (ev.get('summary', ''), ev['start'].get('dateTime', ev['start'].get('date', '')))
                    if key not in seen:
                        seen.add(key)
                        all_events.append(ev)
            except Exception as e:
                print(f"[DEBUG] Get today events error for {cal_id}: {str(e)}")
        all_events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
        return all_events
    except Exception as e:
        print(f"[DEBUG] Get today events error: {str(e)}")
        return []


def parse_contact_from_text(text: str) -> dict | None:
    """Use AI to parse natural language into structured contact info"""
    if not openai_client:
        return None
    try:
        import json as _json
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{
                "role": "user",
                "content": f"""請從以下文字提取人脈聯絡人資訊，用 JSON 格式回覆（只回 JSON，不要加說明）：

文字：{text}

格式：
{{
  "name": "姓名（必填，若無法識別則填 null）",
  "relation": "關係，例如：朋友/同事/客戶/家人/合作夥伴/其他",
  "company": "公司或單位",
  "role": "職位",
  "phone": "電話",
  "email": "Email",
  "line_id": "LINE ID",
  "notes": "認識方式、興趣、特徵或其他備註",
  "tags": ["標籤1", "標籤2"]
}}

規則：
- 找不到的欄位填空字串 ""（tags 填 []）
- name 是必填，若文字中沒有明顯的人名，name 填 null
- tags 從文字推斷出 1-4 個關鍵字（例如：投資、AI、台北、創業）
"""
            }],
            max_tokens=400,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE)
        data = _json.loads(raw)
        if not data.get("name"):
            return None
        return data
    except Exception as e:
        print(f"[DEBUG] Parse contact error: {str(e)}")
        return None


def save_contact_to_wiki(contact: dict) -> str | None:
    """Save contact info as a Wiki/People/{name}.md page"""
    if not GDRIVE_VAULT_FOLDER_ID:
        print("[DEBUG] GDRIVE_VAULT_FOLDER_ID not set, skipping contact save")
        return None
    name = contact.get("name", "").strip()
    if not name:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    tags = ["人脈"]
    if contact.get("relation"):
        tags.append(contact["relation"])
    if contact.get("tags"):
        tags.extend([t for t in contact["tags"] if t])

    frontmatter_lines = [
        "---",
        f"date: {today}",
        "type: 人脈",
        f"name: {name}",
    ]
    if contact.get("relation"):
        frontmatter_lines.append(f"relation: {contact['relation']}")
    if contact.get("company"):
        frontmatter_lines.append(f"company: {contact['company']}")
    frontmatter_lines.append(f"tags: [{', '.join(tags)}]")
    frontmatter_lines.append("---\n")
    frontmatter = "\n".join(frontmatter_lines)

    body_lines = [f"# {name}\n"]
    info_pairs = [
        ("關係", contact.get("relation")),
        ("公司", contact.get("company")),
        ("職位", contact.get("role")),
        ("電話", contact.get("phone")),
        ("Email", contact.get("email")),
        ("LINE ID", contact.get("line_id")),
    ]
    info_lines = [f"- **{label}**：{value}" for label, value in info_pairs if value]
    if info_lines:
        body_lines.append("## 基本資料\n")
        body_lines.extend(info_lines)
        body_lines.append("")

    if contact.get("notes"):
        body_lines.append("## 備註\n")
        body_lines.append(contact["notes"])
        body_lines.append("")

    body_lines.append("## 互動記錄\n")
    body_lines.append(f"- {today}：建立聯絡人")

    full_content = frontmatter + "\n" + "\n".join(body_lines) + "\n"

    # Reuse save_wiki_page (saves under Wiki/People/) – it handles update vs create
    return save_wiki_page(name, full_content, subfolder="People")


def get_or_create_folder(service, folder_name: str, parent_id: str) -> str:
    """Get existing folder or create it if not found"""
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"name='{safe_name}' and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = service.files().list(q=query, fields='files(id)').execute()
    files = results.get('files', [])
    if files:
        return files[0]['id']
    metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = service.files().create(body=metadata, fields='id').execute()
    return folder['id']


def save_to_gdrive(
    title: str,
    content_type: str,
    category: str,
    content: str,
    source_url: str = None,
    original_text: str = None,
    keywords: list = None,
    target_language: str = None,
    user_id: str = None,
    source_type: str = None,
    capture_status: str = CAPTURE_STATUS_FULL,
    extractor: str = None,
    needs_review: bool = False,
    raw_input: str = None,
    normalized_input: str = None,
) -> bool:
    """Save content as .md file to ObsidianVault in Google Drive"""
    if not GDRIVE_VAULT_FOLDER_ID:
        print("[DEBUG] GDRIVE_VAULT_FOLDER_ID not set, skipping save")
        return False
    try:
        service = get_gdrive_service()
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M%S")
        month_str = now.strftime("%Y-%m")

        sources_id = get_or_create_folder(service, "Sources", GDRIVE_VAULT_FOLDER_ID)
        month_id = get_or_create_folder(service, month_str, sources_id)

        tags = [category]
        if keywords:
            tags.extend(keywords[:5])

        frontmatter = f"---\ndate: {date_str}\ntype: {content_type}\ncategory: {category}\n"
        if source_type:
            frontmatter += f"source_type: {source_type}\n"
        frontmatter += f"capture_status: {capture_status}\n"
        if extractor:
            frontmatter += f"extractor: {extractor}\n"
        frontmatter += f"needs_review: {yaml_bool(needs_review)}\n"
        frontmatter += f"tags: [{', '.join(tags)}]\n"
        if source_url:
            frontmatter += f"source: \"{source_url}\"\n"
        if target_language:
            frontmatter += f"language: {target_language}\n"
        if raw_input:
            frontmatter += f"raw_input: {yaml_block_value(raw_input)}\n"
        if normalized_input:
            frontmatter += f"normalized_input: {yaml_block_value(normalized_input)}\n"
        frontmatter += "---\n\n"

        body = f"# {title}\n\n{content}\n"
        if raw_input:
            body += f"\n## 原始輸入\n{raw_input}\n"
        if normalized_input and normalized_input != raw_input:
            body += f"\n## 修正版輸入\n{normalized_input}\n"
        if original_text:
            body += f"\n## 原始文字\n{original_text}\n"

        full_content = frontmatter + body
        safe_title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title[:30]).strip()
        filename = f"{date_str}-{time_str}-{content_type}-{safe_title}.md"

        media = MediaInMemoryUpload(full_content.encode('utf-8'), mimetype='text/plain')
        file_metadata = {'name': filename, 'parents': [month_id]}
        result = service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        print(f"[DEBUG] Saved to Google Drive: {filename}")
        return result.get('id')
    except Exception as e:
        print(f"[DEBUG] Google Drive save error: {str(e)}")
        return None


def append_to_gdrive_file(file_id: str, extra_content: str) -> bool:
    """Append additional thoughts to an existing Google Drive file"""
    try:
        service = get_gdrive_service()
        existing = service.files().get_media(fileId=file_id).execute()
        current_content = existing.decode('utf-8') if isinstance(existing, bytes) else existing
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_content = current_content + f"\n\n## 補充想法（{timestamp}）\n{extra_content}\n"
        media = MediaInMemoryUpload(new_content.encode('utf-8'), mimetype='text/plain')
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"[DEBUG] Appended to file: {file_id}")
        return True
    except Exception as e:
        print(f"[DEBUG] Append error: {str(e)}")
        return False


def get_today_files() -> list:
    """Get all files saved today from ObsidianVault"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return []
    try:
        service = get_gdrive_service()
        today = datetime.now().strftime("%Y-%m-%d")
        month_str = datetime.now().strftime("%Y-%m")
        sources_query = f"name='Sources' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        sources_results = service.files().list(q=sources_query, fields='files(id)').execute()
        sources_files = sources_results.get('files', [])
        if not sources_files:
            return []
        sources_id = sources_files[0]['id']
        month_query = f"name='{month_str}' and '{sources_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        month_results = service.files().list(q=month_query, fields='files(id)').execute()
        month_files = month_results.get('files', [])
        if not month_files:
            return []
        month_id = month_files[0]['id']
        files_query = f"name contains '{today}' and '{month_id}' in parents and trashed=false"
        results = service.files().list(q=files_query, fields='files(id, name)', orderBy='createdTime desc').execute()
        return results.get('files', [])
    except Exception as e:
        print(f"[DEBUG] Get today files error: {str(e)}")
        return []


def get_latest_today_file() -> dict | None:
    """Return the latest source note from today, if available."""
    files = get_today_files()
    return files[0] if files else None


def read_gdrive_file(file_id: str) -> str:
    """Read content of a Google Drive file by ID"""
    try:
        service = get_gdrive_service()
        content = service.files().get_media(fileId=file_id).execute()
        return content.decode('utf-8') if isinstance(content, bytes) else str(content)
    except Exception as e:
        print(f"[DEBUG] Read file error: {str(e)}")
        return ""


def list_sources_files_by_month(month_str: str = None, limit: int = 100) -> list[dict]:
    """List .md files in Sources/YYYY-MM/, default to current month"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return []
    try:
        service = get_gdrive_service()
        if not month_str:
            month_str = datetime.now().strftime("%Y-%m")
        sources_query = f"name='Sources' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        sources_id = service.files().list(q=sources_query, fields='files(id)').execute().get('files', [{}])[0].get('id')
        if not sources_id:
            return []
        month_query = f"name='{month_str}' and '{sources_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        month_files = service.files().list(q=month_query, fields='files(id)').execute().get('files', [])
        if not month_files:
            return []
        month_id = month_files[0]['id']
        results = service.files().list(
            q=f"'{month_id}' in parents and name contains '.md' and trashed=false",
            fields='files(id, name)',
            orderBy='createdTime desc',
            pageSize=limit
        ).execute()
        return results.get('files', [])
    except Exception as e:
        print(f"[DEBUG] List sources error: {str(e)}")
        return []


def list_recent_source_notes(days: int = 7, limit: int = 120) -> list[dict]:
    """Read recent Source notes and return lightweight metadata."""
    start_date = datetime.now() - timedelta(days=days - 1)
    month_keys = {datetime.now().strftime("%Y-%m"), start_date.strftime("%Y-%m")}
    candidates = []
    for month_key in sorted(month_keys, reverse=True):
        candidates.extend(list_sources_files_by_month(month_key, limit=limit))

    notes = []
    seen_ids = set()
    for file_info in candidates:
        if file_info.get("id") in seen_ids:
            continue
        seen_ids.add(file_info.get("id"))
        name = file_info.get("name", "")
        date_match = re.match(r'(\d{4}-\d{2}-\d{2})', name)
        if not date_match:
            continue
        try:
            note_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        if note_date < start_date.replace(hour=0, minute=0, second=0, microsecond=0):
            continue
        content = read_gdrive_file(file_info["id"])
        notes.append({
            "id": file_info["id"],
            "name": name,
            "date": date_match.group(1),
            "category": extract_frontmatter_category(content),
            "source_type": extract_frontmatter_field(content, "source_type", "unknown"),
            "capture_status": extract_frontmatter_field(content, "capture_status", "unknown"),
            "needs_review": extract_frontmatter_field(content, "needs_review", "false"),
            "content": content,
        })
        if len(notes) >= limit:
            break
    return notes


def summarize_capture_notes(notes: list[dict]) -> dict:
    """Aggregate capture metadata for weekly review commands."""
    summary = {
        "total": len(notes),
        "by_status": {},
        "by_type": {},
        "needs_review": [],
    }
    for note in notes:
        status = note.get("capture_status") or "unknown"
        source_type = note.get("source_type") or "unknown"
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        summary["by_type"][source_type] = summary["by_type"].get(source_type, 0) + 1
        if str(note.get("needs_review", "")).lower() == "true" or status in [CAPTURE_STATUS_FAILED, CAPTURE_STATUS_PARTIAL]:
            summary["needs_review"].append(note)
    return summary


def format_weekly_review(notes: list[dict], title: str = "本週回顧") -> str:
    summary = summarize_capture_notes(notes)
    lines = [f"{title}（近 7 天）", ""]
    lines.append(f"新增筆記：{summary['total']} 筆")
    if summary["by_status"]:
        status_text = " / ".join(f"{k}: {v}" for k, v in sorted(summary["by_status"].items()))
        lines.append(f"抓取狀態：{status_text}")
    if summary["by_type"]:
        type_text = " / ".join(f"{k}: {v}" for k, v in sorted(summary["by_type"].items()))
        lines.append(f"來源類型：{type_text}")
    if notes:
        lines.append("")
        lines.append("最近筆記：")
        for note in notes[:8]:
            short_name = note["name"].replace(".md", "")
            lines.append(f"- {short_name} [{note.get('capture_status', 'unknown')}]")
    if summary["needs_review"]:
        lines.append("")
        lines.append(f"需要確認：{len(summary['needs_review'])} 筆")
        for note in summary["needs_review"][:5]:
            lines.append(f"- {note['name'].replace('.md', '')}")
    return "\n".join(lines)


def save_weekly_digest(notes: list[dict]) -> str | None:
    """Save a weekly digest under 40_Outputs/weekly-digests in Google Drive."""
    if not GDRIVE_VAULT_FOLDER_ID:
        return None
    try:
        service = get_gdrive_service()
        outputs_id = get_or_create_folder(service, "40_Outputs", GDRIVE_VAULT_FOLDER_ID)
        digests_id = get_or_create_folder(service, "weekly-digests", outputs_id)
        week_key = datetime.now().strftime("%G-W%V")
        filename = f"{week_key}.md"
        content = "# Weekly Digest\n\n" + format_weekly_review(notes, title="本週知識消化")
        content += "\n\n## 建議下一步\n"
        content += "- 檢查 failed 與 partial 筆記，補上保存理由或原文。\n"
        content += "- 將穩定主題整理進 Wiki。\n"
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
        existing = service.files().list(
            q=f"name='{filename}' and '{digests_id}' in parents and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        if existing:
            result = service.files().update(fileId=existing[0]['id'], media_body=media, fields='id').execute()
        else:
            result = service.files().create(
                body={'name': filename, 'parents': [digests_id]},
                media_body=media,
                fields='id'
            ).execute()
        log_id = find_vault_file("log.md")
        if log_id:
            log_content = read_gdrive_file(log_id) or ""
            log_entry = f"\n\n## {datetime.now().strftime('%Y-%m-%d')} — 週度消化\n\n"
            log_entry += f"- 近 7 天 Sources：{len(notes)} 筆\n"
            log_entry += f"- 週報：[[40_Outputs/weekly-digests/{week_key}]]\n"
            insert_pos = log_content.find('\n\n---')
            new_log = log_content[:insert_pos] + log_entry + log_content[insert_pos:] if insert_pos >= 0 else log_content + log_entry
            update_gdrive_file_content(log_id, new_log)
        return result.get('id')
    except Exception as e:
        print(f"[DEBUG] Save weekly digest error: {str(e)}")
        return None


def search_sources(keyword: str, limit: int = 8) -> list[dict]:
    """Search Sources across all months for files matching keyword"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return []
    try:
        service = get_gdrive_service()
        sources_query = f"name='Sources' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        sources_files = service.files().list(q=sources_query, fields='files(id)').execute().get('files', [])
        if not sources_files:
            return []
        sources_id = sources_files[0]['id']
        months_results = service.files().list(
            q=f"'{sources_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id, name)',
            orderBy='name desc'
        ).execute().get('files', [])
        safe_kw = keyword.replace("'", "\\'")
        all_matches = []
        for month_folder in months_results[:6]:
            results = service.files().list(
                q=f"'{month_folder['id']}' in parents and name contains '.md' and trashed=false and (fullText contains '{safe_kw}' or name contains '{safe_kw}')",
                fields='files(id, name)',
                orderBy='createdTime desc'
            ).execute().get('files', [])
            all_matches.extend(results)
            if len(all_matches) >= limit:
                break
        return all_matches[:limit]
    except Exception as e:
        print(f"[DEBUG] Search sources error: {str(e)}")
        return []


def find_vault_file(filename: str) -> str | None:
    """Find a file by name in vault root, return file_id"""
    try:
        service = get_gdrive_service()
        safe_name = filename.replace("'", "\\'")
        results = service.files().list(
            q=f"name='{safe_name}' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        return results[0]['id'] if results else None
    except Exception as e:
        print(f"[DEBUG] Find vault file error: {str(e)}")
        return None


def update_gdrive_file_content(file_id: str, new_content: str) -> bool:
    """Overwrite content of an existing Google Drive file"""
    try:
        service = get_gdrive_service()
        media = MediaInMemoryUpload(new_content.encode('utf-8'), mimetype='text/plain')
        service.files().update(fileId=file_id, media_body=media).execute()
        return True
    except Exception as e:
        print(f"[DEBUG] Update file content error: {str(e)}")
        return False


def save_wiki_page(title: str, content: str, subfolder: str = None) -> str | None:
    """Save/update a wiki page to Wiki/ folder, return file_id"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return None
    try:
        service = get_gdrive_service()
        wiki_id = get_or_create_folder(service, "Wiki", GDRIVE_VAULT_FOLDER_ID)
        parent_id = get_or_create_folder(service, subfolder, wiki_id) if subfolder else wiki_id
        safe_title = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', title[:40]).strip()
        filename = f"{safe_title}.md"
        safe_name = filename.replace("'", "\\'")
        existing = service.files().list(
            q=f"name='{safe_name}' and '{parent_id}' in parents and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        media = MediaInMemoryUpload(content.encode('utf-8'), mimetype='text/plain')
        if existing:
            result = service.files().update(fileId=existing[0]['id'], media_body=media, fields='id').execute()
            print(f"[DEBUG] Updated wiki page: {filename}")
        else:
            result = service.files().create(
                body={'name': filename, 'parents': [parent_id]},
                media_body=media, fields='id'
            ).execute()
            print(f"[DEBUG] Created wiki page: {filename}")
        return result.get('id')
    except Exception as e:
        print(f"[DEBUG] Save wiki page error: {str(e)}")
        return None


def extract_frontmatter_category(content: str) -> str:
    """Extract category from markdown frontmatter"""
    match = re.search(r'^category:\s*(.+)$', content, re.MULTILINE)
    return match.group(1).strip() if match else "其他"


def extract_frontmatter_field(content: str, field: str, default: str = "") -> str:
    """Extract a one-line frontmatter field."""
    match = re.search(rf'^{re.escape(field)}:\s*(.+)$', content or "", re.MULTILINE)
    if not match:
        return default
    return match.group(1).strip().strip('"')


def consolidate_sources_to_wiki(topic: str, sources_texts: list[str]) -> str:
    """AI: synthesize multiple source files into a structured wiki page"""
    if not openai_client:
        return ""
    combined = "\n\n---\n\n".join(f"[來源 {i+1}]\n{text[:1500]}" for i, text in enumerate(sources_texts))
    prompt = f"""你是 Kaku 的個人知識庫助手。請根據以下 {len(sources_texts)} 篇關於「{topic}」的原始筆記，整合成一篇結構化的 Wiki 頁面。

原始筆記：
{combined}

請用以下格式輸出（Markdown，繁體中文）：

# {topic}

## 核心概念
[2-3句話說明這個主題的核心]

## 重點整理
[用 bullet points 整合所有重點，去除重複，按重要性排序]

## Kaku 的觀察與想法
[從筆記中提取 Kaku 個人的觀點、疑問或行動建議]

## 相關主題
[列出 2-5 個相關的 [[Wiki連結]]，例如 [[投資]]、[[AI工具]] 等]

## 來源統計
- 共 {len(sources_texts)} 篇原始筆記
- 最後整合：{datetime.now().strftime("%Y-%m-%d")}

---
_由 AI 自動整合 | [[index]] | [[log]]_
"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是幫助 Kaku 建立個人知識庫的 AI，擅長整合多篇筆記、提取核心洞見，用繁體中文清晰呈現。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.6
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[DEBUG] Consolidate error: {str(e)}")
        return ""


def summarize_search_results(keyword: str, files_content: list[tuple[str, str]]) -> str:
    """AI: summarize search results for a keyword query"""
    if not openai_client or not files_content:
        return ""
    combined = "\n\n---\n\n".join(f"[筆記 {i+1}：{name}]\n{content[:800]}" for i, (name, content) in enumerate(files_content))
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是 Kaku 的個人知識庫助手，幫助他快速回顧自己存過的相關筆記。用繁體中文，簡潔清晰。"},
                {"role": "user", "content": f"Kaku 想查詢關於「{keyword}」的筆記。以下是找到的 {len(files_content)} 篇相關筆記，請整理重點給他：\n\n{combined}\n\n請用以下格式：\n\n📋 共找到 {len(files_content)} 筆相關記錄\n\n🔍 重點摘要：\n[整合所有筆記的核心重點，bullet points]\n\n💡 建議延伸：\n[基於這些筆記，建議 Kaku 可以深入思考或行動的方向]"}
            ],
            max_tokens=1000,
            temperature=0.6
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[DEBUG] Search summarize error: {str(e)}")
        return ""


def list_wiki_files() -> list[dict]:
    """List all .md files under Wiki/ and its subfolders"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return []
    try:
        service = get_gdrive_service()
        wiki_folders = service.files().list(
            q=f"name='Wiki' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        if not wiki_folders:
            return []
        wiki_id = wiki_folders[0]['id']
        subfolders = service.files().list(
            q=f"'{wiki_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        all_files = []
        for parent_id in [wiki_id] + [sf['id'] for sf in subfolders]:
            files = service.files().list(
                q=f"'{parent_id}' in parents and name contains '.md' and trashed=false",
                fields='files(id, name)'
            ).execute().get('files', [])
            all_files.extend(files)
        return all_files
    except Exception as e:
        print(f"[DEBUG] List wiki files error: {str(e)}")
        return []


def search_wiki_pages(keyword: str) -> list[dict]:
    """Search Wiki pages for files matching keyword in name or content"""
    if not GDRIVE_VAULT_FOLDER_ID:
        return []
    try:
        service = get_gdrive_service()
        wiki_folders = service.files().list(
            q=f"name='Wiki' and '{GDRIVE_VAULT_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        if not wiki_folders:
            return []
        wiki_id = wiki_folders[0]['id']
        subfolders = service.files().list(
            q=f"'{wiki_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields='files(id)'
        ).execute().get('files', [])
        safe_kw = keyword.replace("'", "\\'")
        results = []
        for parent_id in [wiki_id] + [sf['id'] for sf in subfolders]:
            found = service.files().list(
                q=f"'{parent_id}' in parents and name contains '.md' and trashed=false and (fullText contains '{safe_kw}' or name contains '{safe_kw}')",
                fields='files(id, name)'
            ).execute().get('files', [])
            results.extend(found)
        return results
    except Exception as e:
        print(f"[DEBUG] Search wiki error: {str(e)}")
        return []


def answer_from_knowledge_base(question: str, wiki_docs: list[tuple], source_docs: list[tuple]) -> str:
    """AI: answer a question grounded in the user's personal knowledge base"""
    if not openai_client:
        return ""
    context_parts = []
    if wiki_docs:
        context_parts.append("=== Wiki 知識頁面（整合後的知識）===")
        for name, content in wiki_docs:
            context_parts.append(f"[{name}]\n{content[:2000]}")
    if source_docs:
        context_parts.append("=== 原始筆記 ===")
        for name, content in source_docs:
            context_parts.append(f"[{name}]\n{content[:800]}")
    context = "\n\n---\n\n".join(context_parts)
    if not context.strip():
        return f"知識庫中還沒有關於這個主題的筆記。\n\n💡 建議先用語音或文字記錄相關想法，存幾篇之後再來問。"
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是 Kaku 的個人 AI 助手，專門根據他的個人知識庫來回答問題。只能根據知識庫的內容回答，不要加入知識庫以外的資訊。知識庫沒有提到的事情，明確說明需要 Kaku 自己補充。用繁體中文，簡潔有力。"
                },
                {
                    "role": "user",
                    "content": f"問題：{question}\n\n知識庫內容：\n{context}\n\n請用以下格式回答：\n\n💡 回答：\n[基於知識庫的回答，2-4句話]\n\n📚 依據：\n[列出主要參考了哪些筆記]\n\n🔍 知識缺口：\n[哪些資訊不足，建議補充什麼]"
                }
            ],
            max_tokens=1200,
            temperature=0.5
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"[DEBUG] Answer from KB error: {str(e)}")
        return ""


def run_consolidate_sources(month_str: str = None) -> dict:
    """Core logic for consolidating Sources into Wiki pages.
    Used by both 整理筆記 command and weekly cron job. Returns summary dict."""
    if not month_str:
        month_str = datetime.now().strftime("%Y-%m")

    files = list_sources_files_by_month(month_str, limit=80)
    if not files:
        return {"month": month_str, "total": 0, "consolidated": [], "skipped": []}

    category_files: dict[str, list[tuple[str, str]]] = {}
    for f in files:
        content = read_gdrive_file(f['id'])
        if not content:
            continue
        cat = extract_frontmatter_category(content)
        if cat not in category_files:
            category_files[cat] = []
        category_files[cat].append((f['name'], content))

    consolidated = []
    skipped = []
    for cat, cat_files in sorted(category_files.items(), key=lambda x: -len(x[1])):
        if len(cat_files) >= 3:
            texts = [c for _, c in cat_files]
            wiki_content = consolidate_sources_to_wiki(cat, texts)
            if wiki_content:
                save_wiki_page(cat, wiki_content)
                consolidated.append(f"{cat}（{len(cat_files)} 篇）")
        else:
            skipped.append(f"{cat}（{len(cat_files)} 篇）")

    log_id = find_vault_file("log.md")
    if log_id:
        log_content = read_gdrive_file(log_id) or ""
        log_entry = f"\n\n## {datetime.now().strftime('%Y-%m-%d')} — 月度整理\n\n"
        log_entry += f"- 本月 Sources：{len(files)} 篇\n"
        for line in consolidated:
            log_entry += f"- 整合 Wiki：✅ {line}\n"
        if skipped:
            log_entry += f"- 待累積：{', '.join(skipped)}\n"
        insert_pos = log_content.find('\n\n---')
        if insert_pos >= 0:
            new_log = log_content[:insert_pos] + log_entry + log_content[insert_pos:]
        else:
            new_log = log_content + log_entry
        update_gdrive_file_content(log_id, new_log)

    return {
        "month": month_str,
        "total": len(files),
        "consolidated": consolidated,
        "skipped": skipped,
    }


def save_social_to_gdrive(
    platform: str,
    username: str,
    summary: str,
    original_text: str,
    keywords: list,
    likes: int,
    comments: int,
    shares: int,
    source_url: str,
    post_type: str = "其他",
    user_id: str = None,
    images: list = None,
    image_text: str = None,
    source_type: str = None,
    capture_status: str = CAPTURE_STATUS_FULL,
    extractor: str = "apify",
    needs_review: bool = False,
    raw_input: str = None,
    normalized_input: str = None,
) -> bool:
    """Save social media post as .md file to Google Drive"""
    content = f"{summary}\n\n**互動數據：** {likes} 讚 | {comments} 留言 | {shares} 分享"
    if image_text:
        content += f"\n\n**圖片文字：**\n{image_text}"
    if images:
        content += f"\n\n**圖片連結：**\n" + "\n".join(f"- {img}" for img in images[:5])

    return save_to_gdrive(
        title=f"{platform}-{username}",
        content_type="社群分析",
        category=platform,
        content=content,
        source_url=source_url,
        original_text=original_text,
        keywords=keywords,
        user_id=user_id,
        source_type=source_type or platform.lower(),
        capture_status=capture_status,
        extractor=extractor,
        needs_review=needs_review,
        raw_input=raw_input or source_url,
        normalized_input=normalized_input or normalize_input_light(raw_input or source_url),
    )


def save_normalized_social_post(
    platform: str,
    normalized_data: dict,
    source_url: str,
    raw_input: str = None,
    user_id: str = None,
) -> tuple[str | bool | None, dict]:
    """Save a normalized social post, summarizing only when extraction is full."""
    quality = assess_social_post_content(normalized_data)
    platform_name = platform_display_name(platform)
    extractor = social_extractor_name(platform)
    raw_value = raw_input or source_url
    normalized_value = normalize_input_light(raw_value)

    if quality["status"] != CAPTURE_STATUS_FULL:
        extracted_content = format_social_extracted_content(normalized_data, source_url)
        note = build_capture_status_note(
            url=source_url,
            raw_input=raw_value,
            source_type=platform,
            extractor=extractor,
            status=quality["status"],
            reason=quality["reason"],
            extracted_content=extracted_content,
        )
        fid = save_to_gdrive(
            title=f"{platform_name} 貼文待確認",
            content_type="社群分析",
            category=platform_name,
            content=note,
            source_url=source_url,
            keywords=[platform, "待確認"],
            user_id=user_id,
            source_type=platform,
            capture_status=quality["status"],
            extractor=extractor,
            needs_review=True,
            raw_input=raw_value,
            normalized_input=normalized_value,
        )
        return fid, {
            "quality": quality,
            "summary": "貼文內容不足，已存成待確認筆記。",
            "title": f"{platform_name} 貼文待確認",
        }

    summary = summarize_social_post(normalized_data, platform)
    parsed = parse_social_summary_response(summary)
    summary_text = parsed.get("summary") or summary[:300]

    fid = save_social_to_gdrive(
        platform=platform_name,
        username=normalized_data.get("username", "未知"),
        summary=summary_text,
        original_text=normalized_data.get("text", ""),
        keywords=parsed.get("keywords", []),
        likes=normalized_data.get("likes", 0),
        comments=normalized_data.get("comments", 0),
        shares=normalized_data.get("shares", 0),
        source_url=source_url,
        post_type=parsed.get("post_type", "其他"),
        user_id=user_id,
        images=normalized_data.get("images", []),
        image_text=normalized_data.get("image_text", ""),
        source_type=platform,
        capture_status=quality["status"],
        extractor=extractor,
        needs_review=quality["needs_review"],
        raw_input=raw_value,
        normalized_input=normalized_value,
    )
    return fid, {
        "quality": quality,
        "summary": summary_text,
        "title": f"{platform_name}-{normalized_data.get('username', '未知')}",
    }


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@app.route("/healthz", methods=["GET"])
def healthz():
    """Health check endpoint for Zeabur"""
    return {"status": "ok", "ts": datetime.now().isoformat()}, 200


@app.route("/linebot-usage/<path:filename>", methods=["GET"])
def linebot_usage_asset(filename):
    """Serve LINE Bot usage cards for LINE image messages."""
    if filename not in LINEBOT_CARD_FILES:
        abort(404)
    return send_from_directory(
        LINEBOT_USAGE_DIR,
        filename,
        mimetype="image/png",
        max_age=86400,
    )


@app.route("/cron/weekly", methods=["POST", "GET"])
def cron_weekly():
    """Weekly cron job: consolidate Sources → Wiki pages.
    Authenticated via X-Cron-Secret header or ?secret= query param.
    Set CRON_SECRET env var on Zeabur and configure a Cron Job to hit this URL weekly."""
    if not CRON_SECRET:
        return {"error": "CRON_SECRET not configured"}, 503

    provided = request.headers.get("X-Cron-Secret") or request.args.get("secret", "")
    if provided != CRON_SECRET:
        return {"error": "unauthorized"}, 401

    try:
        result = run_consolidate_sources()
        print(f"[CRON] weekly consolidation done: {result}")
        return {
            "status": "ok",
            "month": result["month"],
            "total_sources": result["total"],
            "wiki_pages_created": len(result["consolidated"]),
            "consolidated": result["consolidated"],
            "skipped": result["skipped"],
        }, 200
    except Exception as e:
        print(f"[CRON] weekly consolidation error: {str(e)}")
        return {"status": "error", "message": str(e)}, 500


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
            model="gpt-4.1-mini",
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

🏷️ 分類：[只選一個：科技、AI、商業、新聞、教學、旅遊、美食、電影、書籍、投資、生活、娛樂、筆記、想法、其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1]
• [重點2]
• [重點3]

🔑 關鍵字：[3-5個關鍵字，用頓號分隔]

🎯 一句話總結：[核心價值或啟發]

💭 建議思考：[根據內容，提一個值得深思的問題或行動建議]
"""
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "你是一個幫助用戶建立個人知識庫的助手，擅長提取重點、分類內容，並引導用戶深度思考，用繁體中文清晰呈現。"},
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

        if is_linebot_usage_help_request(text):
            usage_intro = (
                "LINE Bot 功能說明\n\n"
                "平常直接傳文字、網址、圖片或語音即可保存。"
                "需要查詢或整理時，再輸入圖卡中的指令。"
                "\n\n輸入「工作流」可以查看每天捕捉與定期整理節奏。"
            )
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=usage_intro), *build_linebot_usage_image_messages()],
                )
            )
            return

        if is_linebot_workflow_help_request(text):
            workflow_intro = (
                "LINE Bot 工作流\n\n"
                "每天先把素材丟進來，定期再請 AI Agent 整理成 Wiki、週報或行動清單。"
            )
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=workflow_intro), *build_linebot_workflow_image_messages()],
                )
            )
            return

        if is_linebot_drive_diagnostic_request(text):
            diagnostic = run_gdrive_diagnostic(user_id=user_id)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=build_gdrive_diagnostic_message(diagnostic))],
                )
            )
            return

        # 今日回顧指令
        if text in ["今日回顧", "今天存了什麼", "回顧"]:
            files = get_today_files()
            if not files:
                reply_text = "今天還沒有任何記錄，快去捕捉些什麼吧！"
            else:
                names = "\n".join(f"• {f['name'].replace('.md','')}" for f in files[:10])
                reply_text = f"📚 今日記錄（共 {len(files)} 筆）\n\n{names}"
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(
                    text=reply_text,
                )])
            )
            return

        # 本週回顧 / 消化狀態指令
        if text in ["本週回顧", "這週回顧", "消化狀態"]:
            notes = list_recent_source_notes(days=7)
            title = "消化狀態" if text == "消化狀態" else "本週回顧"
            reply_text = format_weekly_review(notes, title=title)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(
                    text=reply_text,
                )])
            )
            return

        # 整理本週：產生 weekly digest，不直接改 Wiki
        if text in ["整理本週", "週整理", "本週整理"]:
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="開始整理近 7 天捕捉內容，完成後會推送週報摘要。")]
                )
            )

            def _weekly_digest_async(uid):
                try:
                    notes = list_recent_source_notes(days=7)
                    digest_id = save_weekly_digest(notes)
                    result_text = format_weekly_review(notes, title="本週知識消化")
                    if digest_id:
                        result_text += "\n\n已寫入 Obsidian weekly-digests。"
                    else:
                        result_text += "\n\n週報寫入失敗，請稍後再試。"
                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(text=result_text)]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Weekly digest async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="整理本週失敗，請稍後再試。")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_weekly_digest_async, args=(user_id,), daemon=True).start()
            return

        # 補充想法指令
        if text.startswith("補充想法：") or text.startswith("補充想法:"):
            extra = text.split("：", 1)[-1].split(":", 1)[-1].strip()
            last = user_last_file.get(user_id)
            if not last:
                latest = get_latest_today_file()
                if latest:
                    last = {"file_id": latest["id"], "title": latest["name"].replace(".md", ""), "saved_at": time.time()}
                    user_last_file[user_id] = last
            if last and extra:
                success = append_to_gdrive_file(last["file_id"], extra)
                if success:
                    reply_msg = TextMessage(text=f"✅ 已補充到「{last['title']}」\n\n💭 {extra}")
                else:
                    reply_msg = TextMessage(text="❌ 補充失敗，請稍後再試")
            else:
                reply_msg = TextMessage(text="找不到最近的筆記，請重新傳送一則訊息後再補充。")
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[reply_msg])
            )
            return

        # 查詢指令：查 投資 / 搜尋 AI / 找 日本
        query_match = re.match(r'^(?:查|搜尋|找)\s+(.+)$', text.strip())
        if query_match:
            keyword = query_match.group(1).strip()
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"🔍 正在搜尋「{keyword}」的相關筆記...")]
                )
            )

            def _search_async(uid, kw):
                try:
                    matched_files = search_sources(kw, limit=8)
                    if not matched_files:
                        result_text = f"找不到關於「{kw}」的筆記\n\n💡 試試其他關鍵字，或先存一些相關內容"
                    else:
                        files_content = []
                        for f in matched_files[:5]:
                            content = read_gdrive_file(f['id'])
                            if content:
                                files_content.append((f['name'], content))
                        if files_content:
                            result_text = summarize_search_results(kw, files_content)
                        else:
                            names = "\n".join(f"• {f['name'].replace('.md','')}" for f in matched_files[:8])
                            result_text = f"🔍 找到 {len(matched_files)} 筆關於「{kw}」的記錄：\n\n{names}"

                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(text=result_text)]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Search async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="❌ 搜尋失敗，請稍後再試")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_search_async, args=(user_id, keyword), daemon=True).start()
            return

        # 整理筆記指令：讀取 Sources，整合成 Wiki 頁面
        if text in ["整理筆記", "整理", "wiki整理", "Wiki整理"]:
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="📚 開始整理本月筆記...\n\n找出主題超過 3 篇的筆記，自動生成 Wiki 頁面。\n（通常需要 1-3 分鐘）")]
                )
            )

            def _consolidate_async(uid):
                try:
                    result = run_consolidate_sources()
                    month_str = result["month"]
                    if result["total"] == 0:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text=f"本月（{month_str}）還沒有任何筆記")]
                            ))
                        return

                    summary_lines = [f"📚 整理完成（{month_str}）\n"]
                    summary_lines.append(f"共 {result['total']} 篇筆記 → {len(result['consolidated'])} 個 Wiki 頁面\n")
                    if result["consolidated"]:
                        summary_lines.append("已生成 Wiki：")
                        summary_lines.extend(f"✅ {line}" for line in result["consolidated"])
                    if result["skipped"]:
                        summary_lines.append("\n待累積（未達 3 篇）：")
                        summary_lines.extend(f"⏳ {line}" for line in result["skipped"])
                    result_text = "\n".join(summary_lines)

                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(text=result_text)]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Consolidate async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="❌ 整理失敗，請稍後再試")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_consolidate_async, args=(user_id,), daemon=True).start()
            return

        # 查行程指令
        schedule_query = re.match(r'^(?:查行程|行程|今天行程|明天行程|這週行程|下週行程|本週行程)$', text.strip())
        if schedule_query:
            keyword = text.strip()
            if "今天" in keyword:
                events = get_today_events()
                reply = format_event_list(events, "今天的")
            elif "明天" in keyword:
                try:
                    service = get_calendar_service()
                    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                    start = f"{tomorrow}T00:00:00+08:00"
                    end = f"{tomorrow}T23:59:59+08:00"
                    events = []
                    seen_keys = set()
                    for cal_id in GOOGLE_CALENDAR_IDS:
                        try:
                            result = service.events().list(
                                calendarId=cal_id, timeMin=start, timeMax=end,
                                maxResults=10, singleEvents=True, orderBy='startTime'
                            ).execute()
                            for evt in result.get('items', []):
                                key = (evt.get('summary', ''), evt.get('start', {}).get('dateTime', evt.get('start', {}).get('date', '')))
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    events.append(evt)
                        except Exception as ce:
                            print(f"[DEBUG] List tomorrow events error in {cal_id}: {str(ce)}")
                    events.sort(key=lambda e: e['start'].get('dateTime', e['start'].get('date', '')))
                    reply = format_event_list(events, "明天的")
                except Exception:
                    reply = "❌ 無法取得行程，請確認行事曆已設定"
            else:
                days = 14 if "下週" in keyword else 7
                events = list_upcoming_events(days=days)
                label = "這週" if days == 7 else "近兩週"
                reply = format_event_list(events, label)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(
                    text=reply,
                    quick_reply=QuickReply(items=[
                        QuickReplyItem(action=MessageAction(label="今天行程", text="今天行程")),
                        QuickReplyItem(action=MessageAction(label="這週行程", text="這週行程")),
                        QuickReplyItem(action=MessageAction(label="加行程", text="加行程：")),
                    ])
                )])
            )
            return

        # 加行程指令
        add_event_match = re.match(r'^(?:加行程|新增行程|加入行程|記行程)[：:]\s*(.+)$', text.strip())
        if add_event_match:
            event_text = add_event_match.group(1).strip()
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token,
                    messages=[TextMessage(text=f"📅 正在新增行程...\n「{event_text}」")])
            )

            def _add_event_async(uid, evt_text):
                try:
                    parsed = parse_event_from_text(evt_text)
                    if not parsed or not parsed.get('title') or not parsed.get('date'):
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="❌ 無法解析行程內容\n\n試試這個格式：\n加行程：週五下午3點 跟 Jason 開會 地點：台北")]
                            ))
                        return
                    result = create_calendar_event(
                        title=parsed['title'],
                        date=parsed['date'],
                        start_time=parsed.get('start_time', '09:00'),
                        end_time=parsed.get('end_time', '10:00'),
                        location=parsed.get('location', ''),
                        description=parsed.get('description', '')
                    )
                    if result > 0:
                        time_display = "全天" if parsed.get('start_time') == "00:00" else f"{parsed.get('start_time')} - {parsed.get('end_time')}"
                        loc_str = f"\n📍 {parsed['location']}" if parsed.get('location') else ""
                        note_str = f"\n📝 {parsed['description']}" if parsed.get('description') else ""
                        cal_str = f"\n🗂 已同步 {result} 個行事曆" if len(GOOGLE_CALENDAR_IDS) > 1 else ""
                        reply_text = (
                            f"✅ 已加入行事曆\n\n"
                            f"📌 {parsed['title']}\n"
                            f"📅 {parsed['date']} {time_display}"
                            f"{loc_str}{note_str}{cal_str}"
                        )
                    else:
                        reply_text = "❌ 行程新增失敗，請確認 Calendar API 已啟用並把行事曆共用給 Service Account"
                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(
                                text=reply_text,
                                quick_reply=QuickReply(items=[
                                    QuickReplyItem(action=MessageAction(label="查行程", text="這週行程")),
                                    QuickReplyItem(action=MessageAction(label="再加一個", text="加行程：")),
                                ])
                            )]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Add event async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid, messages=[TextMessage(text="❌ 新增行程失敗，請稍後再試")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_add_event_async, args=(user_id, event_text), daemon=True).start()
            return

        # 加聯絡人指令：解析自然語言 → 存到 Wiki/People/
        add_contact_match = re.match(r'^(?:加聯絡人|新增聯絡人|記聯絡人|加人脈)[：:]\s*(.+)$', text.strip())
        if add_contact_match:
            contact_text = add_contact_match.group(1).strip()
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(reply_token=event.reply_token,
                    messages=[TextMessage(text=f"👤 正在新增聯絡人...\n「{contact_text[:60]}」")])
            )

            def _add_contact_async(uid, ct_text):
                try:
                    parsed = parse_contact_from_text(ct_text)
                    if not parsed:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="❌ 無法解析聯絡人資訊\n\n試試這個格式：\n加聯絡人：Jason 同事 ABC 公司工程師 0912345678 在 AWS 大會認識")]
                            ))
                        return

                    file_id = save_contact_to_wiki(parsed)
                    if not file_id:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid, messages=[TextMessage(text="❌ 聯絡人儲存失敗，請稍後再試")]
                            ))
                        return

                    info_lines = [f"✅ 已加入人脈資料庫\n", f"👤 {parsed['name']}"]
                    if parsed.get("relation"):
                        info_lines.append(f"🤝 {parsed['relation']}")
                    if parsed.get("company"):
                        company = parsed["company"]
                        if parsed.get("role"):
                            company += f"・{parsed['role']}"
                        info_lines.append(f"🏢 {company}")
                    if parsed.get("phone"):
                        info_lines.append(f"📞 {parsed['phone']}")
                    if parsed.get("email"):
                        info_lines.append(f"✉️ {parsed['email']}")
                    if parsed.get("notes"):
                        info_lines.append(f"📝 {parsed['notes'][:80]}")

                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(
                                text="\n".join(info_lines),
                                quick_reply=QuickReply(items=[
                                    QuickReplyItem(action=MessageAction(label="再加一位", text="加聯絡人：")),
                                    QuickReplyItem(action=MessageAction(label="🔍 搜尋人脈", text="查 ")),
                                ])
                            )]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Add contact async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid, messages=[TextMessage(text="❌ 新增聯絡人失敗，請稍後再試")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_add_contact_async, args=(user_id, contact_text), daemon=True).start()
            return

        # 問 XXX 指令：根據個人知識庫回答問題
        ask_match = re.match(r'^(?:問|請問)\s+(.+)$', text.strip())
        if ask_match:
            question = ask_match.group(1).strip()
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"🧠 正在查詢你的知識庫...\n\n問題：{question}")]
                )
            )

            def _answer_async(uid, q):
                try:
                    wiki_matches = search_wiki_pages(q)
                    source_matches = search_sources(q, limit=5)

                    wiki_docs = []
                    for f in wiki_matches[:4]:
                        content = read_gdrive_file(f['id'])
                        if content:
                            wiki_docs.append((f['name'], content))

                    source_docs = []
                    for f in source_matches[:4]:
                        content = read_gdrive_file(f['id'])
                        if content:
                            source_docs.append((f['name'], content))

                    result = answer_from_knowledge_base(q, wiki_docs, source_docs)
                    if not result:
                        result = "❌ 回答生成失敗，請稍後再試"

                    with ApiClient(configuration) as push_client:
                        MessagingApi(push_client).push_message(PushMessageRequest(
                            to=uid,
                            messages=[TextMessage(text=f"🧠 根據你的知識庫\n\n{result}")]
                        ))
                except Exception as ex:
                    print(f"[DEBUG] Answer async error: {str(ex)}")
                    try:
                        with ApiClient(configuration) as push_client:
                            MessagingApi(push_client).push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text="❌ 查詢失敗，請稍後再試")]
                            ))
                    except Exception:
                        pass

            threading.Thread(target=_answer_async, args=(user_id, question), daemon=True).start()
            return

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

            # Check if user wants to switch language
            if text in ["翻譯", "翻譯模式", "換語言", "切換語言"]:
                user_states[user_id] = {"mode": "translate_select_language", "entered_at": time.time()}
                quick_reply_items = [
                    QuickReplyItem(action=MessageAction(label=label, text=label))
                    for label, _ in QUICK_REPLY_LANGUAGES
                ]
                quick_reply_items.append(
                    QuickReplyItem(action=MessageAction(label="❌ 取消", text="取消"))
                )
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text="🌐 切換語言\n\n請選擇要翻譯成的語言：\n\n💡 也可以直接輸入語言名稱（如：韓文、馬來文）",
                            quick_reply=QuickReply(items=quick_reply_items)
                        )],
                    )
                )
                return

            # Translate the content
            try:
                translated = translate_text(text, target_language)
                # Keep user in translation mode for continuous translation
                # Reset timeout on each translation
                user_states[user_id]["entered_at"] = time.time()
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text=f"🌐 翻譯結果（{target_language}）\n\n{translated}\n\n─────────\n💡 繼續輸入文字可持續翻譯\n輸入「取消」離開翻譯模式",
                            quick_reply=QuickReply(items=[
                                QuickReplyItem(action=MessageAction(label="🚪 離開翻譯模式", text="取消")),
                                QuickReplyItem(action=MessageAction(label="🔄 切換語言", text="切換語言")),
                            ])
                        )],
                    )
                )
                print(f"[DEBUG] Translation in mode sent successfully")

                # Save to Notion
                save_to_gdrive(
                    title=f"翻譯：{text[:50]}...",
                    content_type="翻譯",
                    category="翻譯",
                    content=translated,
                    original_text=text,
                    target_language=target_language,
                    user_id=user_id,
                    source_type="text",
                    capture_status=CAPTURE_STATUS_FULL,
                    extractor="line-translation",
                    raw_input=text,
                    normalized_input=normalize_input_light(text),
                )
            except Exception as e:
                print(f"[DEBUG] Translation error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 翻譯失敗，請稍後再試")],
                    )
                )
            return

        # Check if user selected a language from Quick Reply
        if user_id in user_states and user_states[user_id].get("mode") == "translate_select_language":
            # Check if the input matches a language
            selected_language = LANGUAGE_MAP.get(text)
            if selected_language:
                user_states[user_id] = {"mode": "translate_waiting", "target_language": selected_language, "entered_at": time.time()}
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
            if text in ["取消", "離開", "結束", "exit", "cancel"]:
                del user_states[user_id]
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="已離開翻譯模式 👋")],
                    )
                )
                return

            # Check if it's a valid language name not in our quick reply but in the map
            for lang_name, lang_code in LANGUAGE_MAP.items():
                if text == lang_name:
                    user_states[user_id] = {"mode": "translate_waiting", "target_language": lang_code, "entered_at": time.time()}
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=f"✅ 已選擇翻譯成【{text}】\n\n請輸入要翻譯的內容：\n\n💡 輸入「取消」可離開翻譯模式")],
                        )
                    )
                    return

            # No matching language found - show error and re-display language selection
            quick_reply_items = [
                QuickReplyItem(action=MessageAction(label=label, text=label))
                for label, _ in QUICK_REPLY_LANGUAGES
            ]
            quick_reply_items.append(
                QuickReplyItem(action=MessageAction(label="❌ 取消", text="取消"))
            )
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(
                        text=f"❌ 找不到「{text}」這個語言\n\n請從下方選擇，或直接輸入語言名稱（如：韓文、馬來文）：",
                        quick_reply=QuickReply(items=quick_reply_items)
                    )],
                )
            )
            return

        # Check if user wants to enter translation mode (just "翻譯" or "翻譯模式")
        if text in ["翻譯", "翻譯模式"]:
            user_states[user_id] = {"mode": "translate_select_language", "entered_at": time.time()}
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

                # Save to Notion
                save_to_gdrive(
                    title=f"翻譯：{text_to_translate[:50]}...",
                    content_type="翻譯",
                    category="翻譯",
                    content=translated,
                    original_text=text_to_translate,
                    target_language=target_language,
                    user_id=user_id,
                    source_type="text",
                    capture_status=CAPTURE_STATUS_FULL,
                    extractor="line-translation",
                    raw_input=text_to_translate,
                    normalized_input=normalize_input_light(text_to_translate),
                )
            except Exception as e:
                print(f"[DEBUG] Translation error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 翻譯失敗，請稍後再試")],
                    )
                )
            return

        # Check if user is in scrape_waiting_count mode (waiting for post count)
        if user_id in user_states and user_states[user_id].get("mode") == "scrape_waiting_count":
            state = user_states[user_id]
            url = state.get("url")
            platform = state.get("platform")

            # Check for cancel
            if text in ["取消", "離開", "結束", "exit", "cancel"]:
                del user_states[user_id]
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="已取消爬取 👋")],
                    )
                )
                return

            # Check if input is a number
            if text.isdigit():
                max_posts = min(int(text), 20)  # Cap at 20
                del user_states[user_id]  # Clear state

                if not apify_client:
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="❌ 社群爬蟲功能未設定，請設定 APIFY_API_KEY")],
                        )
                    )
                    return

                # Send initial response with clear wait time expectation
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"🔄 開始爬取 {max_posts} 篇貼文\n\n⏱️ 預計需要 2-5 分鐘\n📱 完成後會自動通知你\n\n請耐心等候，不需要重複發送...")],
                    )
                )

                # Scrape multiple posts
                posts = scrape_facebook_post(url, max_posts) if platform == "facebook" else scrape_threads_post(url, max_posts)

                if not posts:
                    with ApiClient(configuration) as api_client2:
                        messaging_api2 = MessagingApi(api_client2)
                        messaging_api2.push_message(
                            PushMessageRequest(
                                to=user_id,
                                messages=[TextMessage(text=f"❌ 無法爬取貼文，可能是私人帳號或網址無效")]
                            )
                        )
                    return

                # Process each post
                saved_count = 0

                for i, post_data in enumerate(posts):
                    try:
                        print(f"[DEBUG] Processing post {i+1}, raw data keys: {post_data.keys()}")
                        normalized_data = normalize_social_post_data(post_data, platform)
                        print(f"[DEBUG] Normalized: likes={normalized_data.get('likes')}, comments={normalized_data.get('comments')}")

                        post_url = post_data.get("url") or post_data.get("postUrl") or url

                        fid, _capture = save_normalized_social_post(
                            platform=platform,
                            normalized_data=normalized_data,
                            source_url=post_url,
                            raw_input=url,
                            user_id=user_id,
                        )
                        if fid:
                            saved_count += 1
                    except Exception as e:
                        print(f"[DEBUG] Error processing post {i+1}: {str(e)}")

                # Send completion message
                with ApiClient(configuration) as api_client2:
                    messaging_api2 = MessagingApi(api_client2)
                    messaging_api2.push_message(
                        PushMessageRequest(
                            to=user_id,
                                messages=[TextMessage(text=f"✅ 完成！已爬取 {len(posts)} 篇貼文，成功存入 Obsidian {saved_count} 篇")]
                        )
                    )
                return

        # Check for multi-post scraping command: "爬 5 篇 [URL]"
        multi_match = SCRAPE_MULTI_PATTERN.match(text)
        if multi_match:
            max_posts = min(int(multi_match.group(1)), 20)  # Cap at 20 posts
            url = multi_match.group(2)
            print(f"[DEBUG] Multi-post scraping: {max_posts} posts from {url}")

            if not apify_client:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 社群爬蟲功能未設定，請設定 APIFY_API_KEY")],
                    )
                )
                return

            platform, url_type = detect_social_platform(url)
            if not platform:
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 不支援的網址格式，請提供 Facebook 或 Threads 網址")],
                    )
                )
                return

            # Send initial response
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"🔄 開始爬取 {max_posts} 篇貼文\n\n⏱️ 預計需要 2-5 分鐘\n📱 完成後會自動通知你\n\n請耐心等候，不需要重複發送...")],
                )
            )

            # Scrape multiple posts
            if platform == "facebook":
                posts = scrape_facebook_post(url, max_posts)
            else:
                posts = scrape_threads_post(url, max_posts)

            if not posts:
                # Use push message since we already replied
                with ApiClient(configuration) as api_client2:
                    messaging_api2 = MessagingApi(api_client2)
                    messaging_api2.push_message(
                        PushMessageRequest(
                            to=user_id,
                            messages=[TextMessage(text=f"❌ 無法爬取貼文，可能是私人帳號或網址無效")]
                        )
                    )
                return

            # Process each post
            saved_count = 0

            for i, post_data in enumerate(posts):
                try:
                    normalized_data = normalize_social_post_data(post_data, platform)

                    # Get post URL if available
                    post_url = post_data.get("url") or post_data.get("postUrl") or url

                    fid, _capture = save_normalized_social_post(
                        platform=platform,
                        normalized_data=normalized_data,
                        source_url=post_url,
                        raw_input=text,
                        user_id=user_id,
                    )
                    if fid:
                        saved_count += 1
                        print(f"[DEBUG] Saved post {i+1}/{len(posts)}")
                except Exception as e:
                    print(f"[DEBUG] Error processing post {i+1}: {str(e)}")

            # Send completion message
            with ApiClient(configuration) as api_client2:
                messaging_api2 = MessagingApi(api_client2)
                messaging_api2.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=[TextMessage(text=f"✅ 完成！已爬取 {len(posts)} 篇貼文，成功存入 Obsidian {saved_count} 篇")]
                    )
                )
            return

        # Check if message contains a URL
        url = extract_url(text)
        print(f"[DEBUG] Extracted URL: {url}")

        if url:
            try:
                source_type = source_type_from_url(url)
                # Priority 1: Check if it's a social media URL (Facebook or Threads)
                platform, url_type = detect_social_platform(url)
                if platform:
                    print(f"[DEBUG] Detected {platform} {url_type} URL, scraping post...")
                    extractor = social_extractor_name(platform)

                    # Check if Apify is configured
                    if not apify_client:
                        note = build_capture_status_note(
                            url=url,
                            raw_input=text,
                            source_type=platform,
                            extractor=extractor,
                            status=CAPTURE_STATUS_FAILED,
                            reason="apify_not_configured",
                        )
                        save_to_gdrive(
                            title=f"{platform} 貼文抓取失敗",
                            content_type="URL摘要",
                            category="其他",
                            content=note,
                            source_url=url,
                            keywords=[platform, "抓取失敗"],
                            user_id=user_id,
                            source_type=platform,
                            capture_status=CAPTURE_STATUS_FAILED,
                            extractor=extractor,
                            needs_review=True,
                            raw_input=text,
                            normalized_input=normalize_input_light(text),
                        )
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="社群抓取尚未設定，已先把網址存成待確認筆記。")],
                            )
                        )
                        return

                    # If it's a page URL, ask user how many posts to scrape
                    if url_type == "page":
                        # Store state for waiting scrape count
                        user_states[user_id] = {
                            "mode": "scrape_waiting_count",
                            "url": url,
                            "platform": platform,
                            "entered_at": time.time()
                        }
                        platform_emoji = "📘" if platform == "facebook" else "🧵"
                        platform_label = "Facebook 粉專/個人頁面" if platform == "facebook" else "Threads 個人頁面"
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(
                                    text=f"{platform_emoji} 偵測到 {platform_label}\n\n請選擇要爬取幾篇貼文：",
                                    quick_reply=QuickReply(items=[
                                        QuickReplyItem(action=MessageAction(label="3 篇", text="3")),
                                        QuickReplyItem(action=MessageAction(label="5 篇", text="5")),
                                        QuickReplyItem(action=MessageAction(label="10 篇", text="10")),
                                        QuickReplyItem(action=MessageAction(label="20 篇", text="20")),
                                        QuickReplyItem(action=MessageAction(label="❌ 取消", text="取消")),
                                    ])
                                )],
                            )
                        )
                        return

                    # Single post - scrape and analyze
                    posts = scrape_facebook_post(url, 1) if platform == "facebook" else scrape_threads_post(url, 1)

                    if not posts:
                        note = build_capture_status_note(
                            url=url,
                            raw_input=text,
                            source_type=platform,
                            extractor=extractor,
                            status=CAPTURE_STATUS_FAILED,
                            reason="no_posts_returned",
                        )
                        fid = save_to_gdrive(
                            title=f"{platform} 貼文抓取失敗",
                            content_type="URL摘要",
                            category="其他",
                            content=note,
                            source_url=url,
                            keywords=[platform, "抓取失敗"],
                            user_id=user_id,
                            source_type=platform,
                            capture_status=CAPTURE_STATUS_FAILED,
                            extractor=extractor,
                            needs_review=True,
                            raw_input=text,
                            normalized_input=normalize_input_light(text),
                        )
                        if fid:
                            user_last_file[user_id] = {"file_id": fid, "title": f"{platform} 貼文抓取失敗", "saved_at": time.time()}
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text=f"無法爬取 {platform.title()} 貼文，已先存成待確認筆記。")],
                            )
                        )
                        return

                    post_data = posts[0]

                    # Normalize data
                    normalized_data = normalize_social_post_data(post_data, platform)
                    print(f"[DEBUG] Normalized data: {normalized_data}")

                    # Build response message
                    platform_emoji = "📘" if platform == "facebook" else "🧵"
                    platform_name = platform_display_name(platform)
                    fid, capture = save_normalized_social_post(
                        platform=platform,
                        normalized_data=normalized_data,
                        source_url=url,
                        raw_input=text,
                        user_id=user_id,
                    )
                    quality = capture["quality"]

                    response_text = f"{platform_emoji} {platform_name} 貼文已保存\n抓取狀態：{quality['status']}\n\n{capture['summary']}"

                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=response_text)],
                        )
                    )
                    print(f"[DEBUG] Social post analysis sent successfully")

                    if fid:
                        user_last_file[user_id] = {"file_id": fid, "title": capture["title"], "saved_at": time.time()}
                    return

                # Priority 2+3: Google Maps or general webpage
                is_google_maps = source_type == "google_maps"

                # Send immediate waiting message (Jina AI + GPT can take 10-20s)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"🔗 正在讀取網頁摘要...\n（通常需要 10-20 秒）")]
                    )
                )

                def _process_url_async(uid, u, maps):
                    try:
                        if maps:
                            print(f"[DEBUG] Detected Google Maps URL, trying Apify scraper first...")
                            resolved_url = resolve_short_url(u)
                            place_data = scrape_google_maps(resolved_url)
                            if place_data:
                                scraped_info = format_google_maps_result(place_data)
                                extractor = "google-maps-apify"
                                quality = assess_google_maps_place_data(place_data)
                                if should_save_status_note_only("google_maps", quality["status"]):
                                    page_summary = build_capture_status_note(
                                        url=resolved_url,
                                        raw_input=text,
                                        source_type="google_maps",
                                        extractor=extractor,
                                        status=quality["status"],
                                        reason=quality["reason"],
                                        extracted_content=scraped_info,
                                    )
                                else:
                                    page_summary = summarize_google_maps(scraped_info, resolved_url)
                            else:
                                print(f"[DEBUG] Apify scraper failed, falling back to webpage fetch...")
                                page_content = fetch_webpage_content(resolved_url)
                                extractor = "jina"
                                quality = assess_extracted_content(page_content)
                                if should_save_status_note_only("google_maps", quality["status"]):
                                    page_summary = build_capture_status_note(
                                        url=resolved_url,
                                        raw_input=text,
                                        source_type="google_maps",
                                        extractor=extractor,
                                        status=quality["status"],
                                        reason=quality["reason"],
                                        extracted_content=page_content,
                                    )
                                else:
                                    page_summary = summarize_google_maps(page_content, resolved_url)
                        else:
                            print(f"[DEBUG] Fetching webpage content...")
                            source_type_inner = source_type_from_url(u)
                            page_content, extractor = fetch_content_by_source_type(u, source_type_inner)
                            print(f"[DEBUG] Content length: {len(page_content)}")
                            quality = assess_url_capture_quality(page_content, source_type_inner, extractor)
                            if should_save_status_note_only(source_type_inner, quality["status"]):
                                page_summary = build_capture_status_note(
                                    url=u,
                                    raw_input=text,
                                    source_type=source_type_inner,
                                    extractor=extractor,
                                    status=quality["status"],
                                    reason=quality["reason"],
                                    extracted_content=page_content,
                                )
                            else:
                                page_summary = summarize_webpage(page_content)

                        print(f"[DEBUG] Summary: {page_summary[:100]}...")
                        parsed_url = parse_summary_response(page_summary)
                        title = parsed_url["title"] or u[:50]
                        url_source_type = source_type_from_url(u)
                        fid = save_to_gdrive(
                            title=title,
                            content_type="URL摘要",
                            category=parsed_url["category"],
                            content=page_summary,
                            source_url=u,
                            keywords=parsed_url["keywords"],
                            user_id=uid,
                            source_type=url_source_type,
                            capture_status=quality["status"],
                            extractor=extractor,
                            needs_review=quality["needs_review"],
                            raw_input=text,
                            normalized_input=normalize_input_light(text),
                        )
                        if fid:
                            user_last_file[uid] = {"file_id": fid, "title": title, "saved_at": time.time()}

                        with ApiClient(configuration) as push_client:
                            push_api = MessagingApi(push_client)
                            push_api.push_message(PushMessageRequest(
                                to=uid,
                                messages=[TextMessage(text=build_url_capture_push_message(
                                    fid,
                                    quality["status"],
                                    url_source_type,
                                    parsed_url["title"],
                                ))]
                            ))
                        print(f"[DEBUG] URL summary pushed successfully")
                    except Exception as ex:
                        print(f"[DEBUG] Async URL error: {str(ex)}")
                        try:
                            with ApiClient(configuration) as push_client:
                                push_api = MessagingApi(push_client)
                                push_api.push_message(PushMessageRequest(
                                    to=uid,
                                    messages=[TextMessage(text="❌ 無法讀取網頁，請確認網址是否正確")]
                                ))
                        except Exception:
                            pass

                threading.Thread(target=_process_url_async, args=(user_id, url, is_google_maps), daemon=True).start()
            except Exception as e:
                print(f"[DEBUG] Error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 處理失敗，請稍後再試")],
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
                parsed = parse_summary_response(summary)
                title = parsed["title"] or text[:30]
                file_id = save_to_gdrive(
                    title=title,
                    content_type="文字筆記",
                    category=parsed["category"],
                    content=f"{summary}\n\n## 原始輸入\n{text}",
                    keywords=parsed["keywords"],
                    user_id=user_id,
                    source_type="text",
                    capture_status=CAPTURE_STATUS_FULL,
                    extractor="line-text",
                    needs_review=False,
                    raw_input=text,
                    normalized_input=normalize_input_light(text),
                )
                if file_id:
                    user_last_file[user_id] = {"file_id": file_id, "title": title, "saved_at": time.time()}
                print(f"[DEBUG] Text summary sent successfully")
            except Exception as e:
                print(f"[DEBUG] Error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="❌ 摘要失敗，請稍後再試")],
                    )
                )


def analyze_image(image_data: bytes) -> str:
    """Use OpenAI GPT-4o-mini Vision to analyze an image

    Args:
        image_data: Raw image bytes

    Returns:
        Analysis result text
    """
    if not openai_client:
        return "圖片分析功能未設定，請設定 OPENAI_API_KEY"

    try:
        # Encode image to base64
        base64_image = base64.b64encode(image_data).decode("utf-8")

        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是一個專業的圖片分析助手，擅長描述圖片內容、辨識文字（OCR）、分類圖片類型。請用繁體中文回覆。"
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """請分析這張圖片，用以下格式回覆：

🏷️ 分類：[只選一個：截圖、照片、美食、風景、人物、商品、文件、地圖、其他]

📝 圖片描述：[2-3句話描述圖片內容]

📖 圖片中的文字：[如果有文字，請完整列出；如果沒有文字，請寫「無」]

🔑 關鍵字：[3-5個關鍵字，用頓號分隔]

🎯 一句話總結：[用一句話總結圖片內容]"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "auto"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.5
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"圖片分析失敗：{str(e)}"


def translate_image_text(image_data: bytes, target_language: str) -> str:
    """Use OpenAI GPT-4o-mini Vision to extract and translate text from an image

    Args:
        image_data: Raw image bytes
        target_language: Target language for translation

    Returns:
        Translation result text
    """
    if not openai_client:
        return "圖片翻譯功能未設定，請設定 OPENAI_API_KEY"

    try:
        base64_image = base64.b64encode(image_data).decode("utf-8")

        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"你是一個專業的翻譯助手。請辨識圖片中的文字，並翻譯成{target_language}。"
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"請辨識這張圖片中的所有文字，並翻譯成{target_language}。請用以下格式回覆：\n\n📖 原始文字：\n[圖片中的原始文字]\n\n🌐 翻譯結果（{target_language}）：\n[翻譯後的文字]"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "auto"
                            }
                        }
                    ]
                }
            ],
            max_tokens=2000,
            temperature=0.3
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"圖片翻譯失敗：{str(e)}"


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    """Handle image messages - analyze with OpenAI Vision or translate text in image"""
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        blob_api = MessagingApiBlob(api_client)

        user_id = event.source.user_id
        print(f"[DEBUG] Received image message from user: {user_id}")

        # Check if OpenAI is configured
        if not openai_client:
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="圖片分析功能未設定，請設定 OPENAI_API_KEY")],
                )
            )
            return

        try:
            # Download image content from LINE
            image_content = blob_api.get_message_content(event.message.id)

            # Read image data
            if hasattr(image_content, 'read'):
                image_data = image_content.read()
            elif hasattr(image_content, '__iter__') and not isinstance(image_content, bytes):
                image_data = b''.join(chunk for chunk in image_content)
            else:
                image_data = image_content

            # Check if user is in translation mode
            if user_id in user_states and user_states[user_id].get("mode") == "translate_waiting":
                target_language = user_states[user_id].get("target_language")
                print(f"[DEBUG] User in translation mode, translating image text to: {target_language}")

                # Reset timeout
                user_states[user_id]["entered_at"] = time.time()

                result = translate_image_text(image_data, target_language)

                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(
                            text=f"🖼️ 圖片翻譯\n\n{result}\n\n─────────\n💡 繼續傳送圖片或文字可持續翻譯\n輸入「取消」離開翻譯模式",
                            quick_reply=QuickReply(items=[
                                QuickReplyItem(action=MessageAction(label="🚪 離開翻譯模式", text="取消")),
                                QuickReplyItem(action=MessageAction(label="🔄 切換語言", text="切換語言")),
                            ])
                        )],
                    )
                )

                # Save to Notion
                save_to_gdrive(
                    title=f"圖片翻譯：{target_language}",
                    content_type="翻譯",
                    category="翻譯",
                    content=result,
                    target_language=target_language,
                    user_id=user_id,
                    source_type="image",
                    capture_status=CAPTURE_STATUS_FULL,
                    extractor="line-image-vision",
                )
                return

            # Normal image analysis
            result = analyze_image(image_data)
            parsed = parse_summary_response(result)
            title = parsed["title"] or "圖片分析"

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"🖼️ 圖片分析\n\n{result}")],
                )
            )
            print(f"[DEBUG] Image analysis sent successfully")

            fid = save_to_gdrive(
                title=title,
                content_type="圖片分析",
                category=parsed["category"],
                content=result,
                keywords=parsed["keywords"],
                user_id=user_id,
                source_type="image",
                capture_status=CAPTURE_STATUS_FULL,
                extractor="line-image-vision",
                needs_review=False,
            )
            if fid:
                user_last_file[user_id] = {"file_id": fid, "title": title, "saved_at": time.time()}

        except Exception as e:
            print(f"[DEBUG] Image processing error: {str(e)}")
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="❌ 圖片分析失敗，請稍後再試")],
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

            # Auto-analyze transcription (same pipeline as text input)
            summary = summarize_text(result_text)

            reply_text = f"🎙️ 語音筆記\n\n{summary}\n\n─────────\n原始語音：{result_text[:100]}{'...' if len(result_text) > 100 else ''}"

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

            user_id = event.source.user_id
            parsed = parse_summary_response(summary)
            title = parsed["title"] or f"語音筆記：{result_text[:30]}"
            fid = save_to_gdrive(
                title=title,
                content_type="語音筆記",
                category=parsed["category"],
                content=f"{summary}\n\n## 原始語音\n{result_text}",
                keywords=parsed["keywords"],
                user_id=user_id,
                source_type="audio",
                capture_status=CAPTURE_STATUS_FULL,
                extractor="line-audio-whisper",
                needs_review=False,
                raw_input=result_text,
                normalized_input=normalize_input_light(result_text),
            )
            if fid:
                user_last_file[user_id] = {"file_id": fid, "title": title, "saved_at": time.time()}

        except Exception as e:
            # Clean up temp file if exists
            if 'tmp_file_path' in locals():
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass

            print(f"[DEBUG] Audio processing error: {str(e)}")
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="❌ 語音處理失敗，請稍後再試")],
                )
            )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
