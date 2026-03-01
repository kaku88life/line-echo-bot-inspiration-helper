import os
import re
import tempfile
import time
import threading
import base64
from flask import Flask, request, abort
from dotenv import load_dotenv
from openai import OpenAI
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from notion_client import Client as NotionClient

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
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent, ImageMessageContent

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_SOCIAL_DATABASE_ID = os.getenv("NOTION_SOCIAL_DATABASE_ID")
APIFY_API_KEY = os.getenv("APIFY_API_KEY")

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

# URL pattern for detecting links
URL_PATTERN = re.compile(
    r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.-]*(?:\?[^\s]*)?'
)

# Social media URL patterns
# Single post patterns
FACEBOOK_POST_PATTERN = re.compile(
    r'https?://(?:www\.|m\.|web\.)?facebook\.com/(?:[\w.]+/)?(?:posts|videos|photos|watch|story\.php|permalink\.php|reel|share)[\w/?=&.-]*'
)
# Facebook page/profile patterns (for multi-post scraping)
FACEBOOK_PAGE_PATTERN = re.compile(
    r'https?://(?:www\.|m\.|web\.)?facebook\.com/([\w.]+)/?(?:\?.*)?$'
)
THREADS_POST_PATTERN = re.compile(
    r'https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/post/[\w]+(?:\?.*)?'
)
THREADS_PROFILE_PATTERN = re.compile(
    r'https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/?(?:\?.*)?$'
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
    return match.group(0) if match else None


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


def format_google_maps_result(place: dict) -> str:
    """Format scraped Google Maps place data into a readable summary"""
    lines = []

    name = place.get("title") or place.get("name") or "未知地點"
    lines.append(f"📍 地點名稱：{name}")

    # Category / type
    category = place.get("categoryName") or place.get("category") or ""
    if category:
        lines.append(f"🏷️ 類型：{category}")

    # Address
    address = place.get("address") or place.get("street") or ""
    if address:
        lines.append(f"📮 地址：{address}")

    # Rating
    rating = place.get("totalScore") or place.get("rating") or place.get("stars")
    reviews_count = place.get("reviewsCount") or place.get("reviews") or 0
    if rating:
        lines.append(f"⭐ 評分：{rating}/5（{reviews_count} 則評論）")

    # Phone
    phone = place.get("phone") or place.get("phoneUnformatted") or ""
    if phone:
        lines.append(f"📞 電話：{phone}")

    # Website
    website = place.get("website") or place.get("url") or ""
    if website:
        lines.append(f"🌐 網站：{website}")

    # Price level
    price = place.get("price") or place.get("priceLevel") or ""
    if price:
        lines.append(f"💰 價位：{price}")

    # Opening hours
    hours = place.get("openingHours")
    if hours:
        if isinstance(hours, list):
            lines.append("🕐 營業時間：")
            for h in hours[:7]:
                if isinstance(h, dict):
                    day = h.get("day", "")
                    time_str = h.get("hours", "")
                    lines.append(f"  • {day}：{time_str}")
                elif isinstance(h, str):
                    lines.append(f"  • {h}")
        elif isinstance(hours, str):
            lines.append(f"🕐 營業時間：{hours}")

    # Description
    description = place.get("description") or ""
    if description:
        lines.append(f"\n📝 簡介：{description}")

    # Location coordinates
    lat = place.get("location", {}).get("lat") or place.get("latitude")
    lng = place.get("location", {}).get("lng") or place.get("longitude")
    if lat and lng:
        lines.append(f"🗺️ 座標：{lat}, {lng}")

    # Additional info
    additional = place.get("additionalInfo") or place.get("additionalCategories")
    if additional and isinstance(additional, dict):
        for key, value in list(additional.items())[:5]:
            if value:
                lines.append(f"ℹ️ {key}：{value}")

    return "\n".join(lines)


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

    if platform == "facebook":
        # Try various field names from Apify Facebook scraper
        user_dict = post_data.get("user") if isinstance(post_data.get("user"), dict) else {}
        username = (
            post_data.get("pageName") or
            post_data.get("userName") or
            user_dict.get("name") or
            post_data.get("name") or
            "未知"
        )
        text = (
            post_data.get("text") or
            post_data.get("postText") or
            post_data.get("message") or
            post_data.get("description") or
            ""
        )
        # Handle various reaction/like field names
        reactions_dict = post_data.get("reactions") if isinstance(post_data.get("reactions"), dict) else {}
        likes = (
            post_data.get("likes") or
            post_data.get("likesCount") or
            post_data.get("reactionsCount") or
            reactions_dict.get("count") or
            0
        )
        comments = (
            post_data.get("comments") or
            post_data.get("commentsCount") or
            post_data.get("commentCount") or
            0
        )
        shares = (
            post_data.get("shares") or
            post_data.get("sharesCount") or
            post_data.get("shareCount") or
            0
        )
        # Extract images from media array
        images = []
        image_text = ""
        media_list = post_data.get("media") or []
        for media_item in media_list:
            if isinstance(media_item, dict):
                # Try photo_image.uri first, then thumbnail
                photo_image = media_item.get("photo_image", {})
                img_url = photo_image.get("uri") if isinstance(photo_image, dict) else None
                if not img_url:
                    img_url = media_item.get("thumbnail") or media_item.get("url")
                if img_url:
                    images.append(img_url)
                # Collect OCR text
                ocr = media_item.get("ocrText") or ""
                if ocr:
                    image_text += ocr + "\n"
        return {
            "username": username,
            "text": text,
            "likes": int(likes) if likes else 0,
            "comments": int(comments) if comments else 0,
            "shares": int(shares) if shares else 0,
            "images": images,
            "image_text": image_text.strip(),
        }
    elif platform == "threads":
        # Support both old (ownerUsername/text) and new (authorId/content) field names
        author_id = post_data.get("authorId", "")
        # authorId often comes as "/@username", strip leading /@
        if isinstance(author_id, str):
            author_id = author_id.lstrip("/@")
        username = (
            post_data.get("ownerUsername") or
            post_data.get("author", {}).get("username") or
            post_data.get("authorName") or
            author_id or
            "未知"
        )
        text = (
            post_data.get("text") or
            post_data.get("caption") or
            post_data.get("content") or
            ""
        )
        # Extract images
        images = post_data.get("images") or []
        # Ensure images is a list of strings
        images = [img for img in images if isinstance(img, str)]
        return {
            "username": username,
            "text": text,
            "likes": int(post_data.get("likeCount") or post_data.get("likesCount") or 0),
            "comments": int(post_data.get("replyCount") or post_data.get("commentsCount") or 0),
            "shares": int(post_data.get("repostCount") or 0),
            "images": images,
            "image_text": "",
        }
    return {
        "username": "未知",
        "text": "",
        "likes": 0,
        "comments": 0,
        "shares": 0,
        "images": [],
        "image_text": "",
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
            model="gpt-4o-mini",
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


def save_social_to_notion(
    platform: str,
    username: str,
    summary: str,
    original_text: str,
    keywords: list[str],
    likes: int,
    comments: int,
    shares: int,
    source_url: str,
    post_type: str = "其他",
    user_id: str = None,
    images: list[str] = None,
    image_text: str = None
) -> bool:
    """Save social media post to Notion database

    Args:
        platform: "Facebook" | "Threads"
        username: Account name
        summary: AI-generated summary
        original_text: Original post content
        keywords: List of keywords
        likes: Like count
        comments: Comment count
        shares: Share count
        source_url: Original post URL
        post_type: Post type category
        user_id: LINE User ID
        images: List of image URLs
        image_text: OCR text from images

    Returns:
        True if saved successfully, False otherwise
    """
    if not notion_client:
        print("[DEBUG] Notion not configured, skipping save")
        return False

    # Use dedicated social database if available, otherwise use default
    database_id = NOTION_SOCIAL_DATABASE_ID or NOTION_DATABASE_ID
    if not database_id:
        print("[DEBUG] No Notion database ID configured")
        return False

    try:
        # Build title: account name + summary snippet
        title = f"[{platform}] {username}: {summary[:50]}..." if len(summary) > 50 else f"[{platform}] {username}: {summary}"

        properties = {
            "名稱": {"title": [{"text": {"content": title[:100]}}]},
            "平台": {"select": {"name": platform}},
            "帳號": {"rich_text": [{"text": {"content": username[:100]}}]},
            "內容摘要": {"rich_text": [{"text": {"content": summary[:2000]}}]},
            "原始內容": {"rich_text": [{"text": {"content": original_text[:2000] if original_text else ""}}]},
            "來源網址": {"url": source_url},
        }

        # Add keywords as multi-select
        if keywords:
            properties["關鍵字"] = {"multi_select": [{"name": kw[:100]} for kw in keywords[:10]]}

        # Add numeric fields (only if the database has these columns)
        # These will be added if the database supports them
        try:
            properties["Likes"] = {"number": likes}
            properties["留言數"] = {"number": comments}
            properties["分享數"] = {"number": shares}
        except Exception:
            pass

        # Add category/type
        if post_type:
            properties["類型"] = {"select": {"name": post_type}}

        if user_id:
            properties["LINE 用戶"] = {"rich_text": [{"text": {"content": user_id}}]}

        # Add image URLs
        if images:
            image_urls_text = "\n".join(images[:5])  # Limit to 5 images
            properties["圖片"] = {"rich_text": [{"text": {"content": image_urls_text[:2000]}}]}

        # Add image OCR text
        if image_text:
            properties["圖片文字"] = {"rich_text": [{"text": {"content": image_text[:2000]}}]}

        # Create page in database
        notion_client.pages.create(
            parent={"database_id": database_id},
            properties=properties
        )

        print(f"[DEBUG] Saved social post to Notion: {title[:50]}...")
        return True

    except Exception as e:
        print(f"[DEBUG] Notion save error: {str(e)}")
        return False


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

請用以下格式回覆（每個欄位只填一個值）：

🏷️ 分類：[只選一個：科技、AI、金融、商業、新聞、教學、運動、美食、旅遊、地圖、生活、娛樂、其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1 - 詳細說明]
• [重點2 - 詳細說明]
• [重點3 - 詳細說明]
（依內容提供3-5個重點）

🔑 關鍵字：[列出3-5個關鍵字，用頓號分隔，例如：人工智慧、程式設計、自動化]

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
            model="gpt-4o-mini",
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


def save_to_notion(
    title: str,
    content_type: str,
    category: str,
    content: str,
    source_url: str = None,
    original_text: str = None,
    keywords: list[str] = None,
    target_language: str = None,
    user_id: str = None
) -> bool:
    """Save content to Notion database

    Args:
        title: Main title/subject
        content_type: "URL摘要" | "語音轉文字" | "翻譯"
        category: Category from the predefined list
        content: Full summary/transcription/translation content
        source_url: Original URL (for URL summaries)
        original_text: Original text (for translations)
        keywords: List of keywords extracted by AI
        target_language: Target language (for translations)
        user_id: LINE User ID

    Returns:
        True if saved successfully, False otherwise
    """
    if not notion_client or not NOTION_DATABASE_ID:
        print("[DEBUG] Notion not configured, skipping save")
        return False

    try:
        # Build properties
        properties = {
            "標題": {"title": [{"text": {"content": title[:100] if title else "無標題"}}]},
            "類型": {"select": {"name": content_type}},
            "分類": {"select": {"name": category}},
            "內容": {"rich_text": [{"text": {"content": content[:2000] if content else ""}}]},
        }

        # Add optional fields
        if source_url:
            properties["來源網址"] = {"url": source_url}

        if original_text:
            properties["原始文字"] = {"rich_text": [{"text": {"content": original_text[:2000]}}]}

        if keywords:
            properties["關鍵字"] = {"multi_select": [{"name": kw[:100]} for kw in keywords[:10]]}

        if target_language:
            properties["目標語言"] = {"select": {"name": target_language}}

        if user_id:
            properties["LINE 用戶"] = {"rich_text": [{"text": {"content": user_id}}]}

        # Create page in database
        notion_client.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties
        )

        print(f"[DEBUG] Saved to Notion: {title[:50]}...")
        return True

    except Exception as e:
        print(f"[DEBUG] Notion save error: {str(e)}")
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

請用以下格式回覆（每個欄位只填一個值）：

🏷️ 分類：[只選一個：科技、AI、商業、新聞、教學、生活、娛樂、筆記、想法、其他]

📌 主題：[一句話描述核心主題]

📝 重點摘要：
• [重點1 - 詳細說明]
• [重點2 - 詳細說明]
• [重點3 - 詳細說明]
（依內容提供3-5個重點）

🔑 關鍵字：[列出3-5個關鍵字，用頓號分隔，例如：人工智慧、程式設計、自動化]

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
                save_to_notion(
                    title=f"翻譯：{text[:50]}...",
                    content_type="翻譯",
                    category="翻譯",
                    content=translated,
                    original_text=text,
                    target_language=target_language,
                    user_id=user_id
                )
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
                save_to_notion(
                    title=f"翻譯：{text_to_translate[:50]}...",
                    content_type="翻譯",
                    category="翻譯",
                    content=translated,
                    original_text=text_to_translate,
                    target_language=target_language,
                    user_id=user_id
                )
            except Exception as e:
                print(f"[DEBUG] Translation error: {str(e)}")
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=f"❌ 翻譯失敗：{str(e)}")],
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
                platform_name = "Facebook" if platform == "facebook" else "Threads"
                saved_count = 0

                for i, post_data in enumerate(posts):
                    try:
                        print(f"[DEBUG] Processing post {i+1}, raw data keys: {post_data.keys()}")
                        normalized_data = normalize_social_post_data(post_data, platform)
                        print(f"[DEBUG] Normalized: likes={normalized_data.get('likes')}, comments={normalized_data.get('comments')}")
                        summary = summarize_social_post(normalized_data, platform)
                        parsed = parse_social_summary_response(summary)

                        post_url = post_data.get("url") or post_data.get("postUrl") or url

                        if save_social_to_notion(
                            platform=platform_name,
                            username=normalized_data.get("username", "未知"),
                            summary=parsed.get("summary", ""),
                            original_text=normalized_data.get("text", ""),
                            keywords=parsed.get("keywords", []),
                            likes=normalized_data.get("likes", 0),
                            comments=normalized_data.get("comments", 0),
                            shares=normalized_data.get("shares", 0),
                            source_url=post_url,
                            post_type=parsed.get("post_type", "其他"),
                            user_id=user_id,
                            images=normalized_data.get("images", []),
                            image_text=normalized_data.get("image_text", "")
                        ):
                            saved_count += 1
                    except Exception as e:
                        print(f"[DEBUG] Error processing post {i+1}: {str(e)}")

                # Send completion message
                with ApiClient(configuration) as api_client2:
                    messaging_api2 = MessagingApi(api_client2)
                    messaging_api2.push_message(
                        PushMessageRequest(
                            to=user_id,
                            messages=[TextMessage(text=f"✅ 完成！已爬取 {len(posts)} 篇貼文，成功存入 Notion {saved_count} 篇")]
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
            platform_name = "Facebook" if platform == "facebook" else "Threads"
            saved_count = 0

            for i, post_data in enumerate(posts):
                try:
                    normalized_data = normalize_social_post_data(post_data, platform)
                    summary = summarize_social_post(normalized_data, platform)
                    parsed = parse_social_summary_response(summary)

                    # Get post URL if available
                    post_url = post_data.get("url") or post_data.get("postUrl") or url

                    # Save to Notion
                    if save_social_to_notion(
                        platform=platform_name,
                        username=normalized_data.get("username", "未知"),
                        summary=parsed.get("summary", ""),
                        original_text=normalized_data.get("text", ""),
                        keywords=parsed.get("keywords", []),
                        likes=normalized_data.get("likes", 0),
                        comments=normalized_data.get("comments", 0),
                        shares=normalized_data.get("shares", 0),
                        source_url=post_url,
                        post_type=parsed.get("post_type", "其他"),
                        user_id=user_id,
                        images=normalized_data.get("images", []),
                        image_text=normalized_data.get("image_text", "")
                    ):
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
                        messages=[TextMessage(text=f"✅ 完成！已爬取 {len(posts)} 篇貼文，成功存入 Notion {saved_count} 篇")]
                    )
                )
            return

        # Check if message contains a URL
        url = extract_url(text)
        print(f"[DEBUG] Extracted URL: {url}")

        if url:
            try:
                # Priority 1: Check if it's a social media URL (Facebook or Threads)
                platform, url_type = detect_social_platform(url)
                if platform:
                    print(f"[DEBUG] Detected {platform} {url_type} URL, scraping post...")

                    # Check if Apify is configured
                    if not apify_client:
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text="❌ 社群爬蟲功能未設定，請設定 APIFY_API_KEY")],
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
                        line_bot_api.reply_message_with_http_info(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text=f"❌ 無法爬取 {platform.title()} 貼文，可能是私人貼文或網址無效")],
                            )
                        )
                        return

                    post_data = posts[0]

                    # Normalize data
                    normalized_data = normalize_social_post_data(post_data, platform)
                    print(f"[DEBUG] Normalized data: {normalized_data}")

                    # Generate AI summary
                    summary = summarize_social_post(normalized_data, platform)
                    parsed = parse_social_summary_response(summary)

                    # Build response message
                    platform_emoji = "📘" if platform == "facebook" else "🧵"
                    platform_name = "Facebook" if platform == "facebook" else "Threads"

                    response_text = f"{platform_emoji} {platform_name} 貼文分析\n{url}\n\n{summary}"

                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=response_text)],
                        )
                    )
                    print(f"[DEBUG] Social post analysis sent successfully")

                    # Save to Notion
                    save_social_to_notion(
                        platform=platform_name,
                        username=normalized_data.get("username", "未知"),
                        summary=parsed.get("summary", ""),
                        original_text=normalized_data.get("text", ""),
                        keywords=parsed.get("keywords", []),
                        likes=normalized_data.get("likes", 0),
                        comments=normalized_data.get("comments", 0),
                        shares=normalized_data.get("shares", 0),
                        source_url=url,
                        post_type=parsed.get("post_type", "其他"),
                        user_id=user_id,
                        images=normalized_data.get("images", []),
                        image_text=normalized_data.get("image_text", "")
                    )
                    return

                # Priority 2: Check if it's a Google Maps URL
                is_google_maps = any(pattern in url.lower() for pattern in [
                    'maps.google.com', 'google.com/maps', 'goo.gl/maps',
                    'maps.app.goo.gl', '/maps/', 'maps.app'
                ])

                if is_google_maps:
                    print(f"[DEBUG] Detected Google Maps URL, trying Apify scraper first...")
                    # Resolve short URLs (e.g., maps.app.goo.gl/xxx -> full Google Maps URL)
                    resolved_url = resolve_short_url(url)
                    place_data = scrape_google_maps(resolved_url)
                    if place_data:
                        scraped_info = format_google_maps_result(place_data)
                        # Use OpenAI to enhance the scraped data with analysis
                        summary = summarize_google_maps(scraped_info, resolved_url)
                    else:
                        # Fallback to webpage scraping with resolved URL
                        print(f"[DEBUG] Apify scraper failed, falling back to webpage fetch...")
                        content = fetch_webpage_content(resolved_url)
                        print(f"[DEBUG] Maps content length: {len(content)}")
                        summary = summarize_google_maps(content, resolved_url)
                else:
                    # Priority 3: General webpage
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

                # Save to Notion
                parsed = parse_summary_response(summary)
                save_to_notion(
                    title=parsed["title"] or url[:50],
                    content_type="URL摘要",
                    category=parsed["category"],
                    content=summary,
                    source_url=url,
                    keywords=parsed["keywords"],
                    user_id=user_id
                )
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
            model="gpt-4o-mini",
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
            model="gpt-4o-mini",
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
                save_to_notion(
                    title=f"圖片翻譯：{target_language}",
                    content_type="翻譯",
                    category="翻譯",
                    content=result,
                    target_language=target_language,
                    user_id=user_id
                )
                return

            # Normal image analysis
            result = analyze_image(image_data)

            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"🖼️ 圖片分析\n\n{result}")],
                )
            )
            print(f"[DEBUG] Image analysis sent successfully")

            # Save to Notion
            parsed = parse_summary_response(result)
            save_to_notion(
                title=parsed["title"] or "圖片分析",
                content_type="圖片分析",
                category=parsed["category"],
                content=result,
                keywords=parsed["keywords"],
                user_id=user_id
            )

        except Exception as e:
            print(f"[DEBUG] Image processing error: {str(e)}")
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=f"圖片處理失敗：{str(e)}")],
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

            # Save to Notion
            user_id = event.source.user_id
            save_to_notion(
                title=f"語音轉文字：{result_text[:50]}...",
                content_type="語音轉文字",
                category="筆記",
                content=result_text,
                user_id=user_id
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


# Initialize Notion social database on startup
if notion_client and NOTION_SOCIAL_DATABASE_ID:
    try:
        setup_notion_social_database()
    except Exception as e:
        print(f"[DEBUG] Social database init error: {str(e)}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
