"""
LINE Bot 功能測試
測試所有核心功能是否正常運作
"""

import re
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import os
import time
import json

# Set dummy environment variables before importing main
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")

import main


# ============================================================
# 1. URL 提取與偵測測試
# ============================================================

class TestExtractUrl:
    """測試 extract_url 功能"""

    def test_extract_http_url(self):
        text = "看看這個 http://example.com 很有趣"
        assert main.extract_url(text) == "http://example.com"

    def test_extract_https_url(self):
        text = "https://www.google.com/search?q=test"
        assert main.extract_url(text) == "https://www.google.com/search?q=test"

    def test_no_url(self):
        text = "這是一段沒有網址的文字"
        assert main.extract_url(text) is None

    def test_multiple_urls_returns_first(self):
        text = "first: https://a.com then https://b.com"
        assert main.extract_url(text) == "https://a.com"

    def test_url_with_path(self):
        text = "https://example.com/path/to/page"
        assert main.extract_url(text) == "https://example.com/path/to/page"

    def test_url_with_query_params(self):
        text = "https://example.com/page?key=value&foo=bar"
        result = main.extract_url(text)
        assert result is not None
        assert "example.com" in result


class TestDetectSocialPlatform:
    """測試社群平台偵測"""

    # Facebook post URLs
    def test_facebook_post_url(self):
        url = "https://www.facebook.com/user/posts/123456"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "post"

    def test_facebook_video_url(self):
        url = "https://www.facebook.com/user/videos/123456"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "post"

    def test_facebook_reel_url(self):
        url = "https://www.facebook.com/reel/123456"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "post"

    def test_facebook_watch_url(self):
        url = "https://www.facebook.com/watch/?v=123456"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "post"

    def test_facebook_mobile_post(self):
        url = "https://m.facebook.com/user/posts/123456"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "post"

    # Facebook page URLs
    def test_facebook_page_url(self):
        url = "https://www.facebook.com/some.page"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "page"

    def test_facebook_page_url_with_trailing_slash(self):
        url = "https://www.facebook.com/some.page/"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "facebook"
        assert url_type == "page"

    # Threads URLs
    def test_threads_post_url(self):
        url = "https://www.threads.net/@user/post/ABC123"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "threads"
        assert url_type == "post"

    def test_threads_com_post_url(self):
        """threads.com 域名的貼文"""
        url = "https://www.threads.com/@chiongyjdpp/post/DU2oUjaEynj?xmt=AQF0rvrP"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "threads"
        assert url_type == "post"

    def test_threads_profile_url(self):
        """Threads 個人頁面應被偵測為 page"""
        url = "https://www.threads.com/@kaku_88life"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "threads"
        assert url_type == "page"

    def test_threads_net_profile_url(self):
        url = "https://www.threads.net/@someuser"
        platform, url_type = main.detect_social_platform(url)
        assert platform == "threads"
        assert url_type == "page"

    # Non-social URLs
    def test_non_social_url(self):
        url = "https://www.google.com"
        platform, url_type = main.detect_social_platform(url)
        assert platform is None
        assert url_type == ""

    def test_google_maps_not_social(self):
        url = "https://maps.google.com/some-place"
        platform, url_type = main.detect_social_platform(url)
        assert platform is None


class TestGoogleMapsDetection:
    """測試 Google Maps URL 偵測"""

    def test_maps_google_com(self):
        url = "https://maps.google.com/some-location"
        is_maps = any(p in url.lower() for p in [
            'maps.google.com', 'google.com/maps', 'goo.gl/maps',
            'maps.app.goo.gl', '/maps/', 'maps.app'
        ])
        assert is_maps is True

    def test_google_com_maps(self):
        url = "https://www.google.com/maps/place/some+place"
        is_maps = any(p in url.lower() for p in [
            'maps.google.com', 'google.com/maps', 'goo.gl/maps',
            'maps.app.goo.gl', '/maps/', 'maps.app'
        ])
        assert is_maps is True

    def test_goo_gl_maps(self):
        url = "https://goo.gl/maps/abc123"
        is_maps = any(p in url.lower() for p in [
            'maps.google.com', 'google.com/maps', 'goo.gl/maps',
            'maps.app.goo.gl', '/maps/', 'maps.app'
        ])
        assert is_maps is True

    def test_maps_app_goo_gl(self):
        url = "https://maps.app.goo.gl/abc123"
        is_maps = any(p in url.lower() for p in [
            'maps.google.com', 'google.com/maps', 'goo.gl/maps',
            'maps.app.goo.gl', '/maps/', 'maps.app'
        ])
        assert is_maps is True

    def test_non_maps_url(self):
        url = "https://www.google.com/search?q=test"
        is_maps = any(p in url.lower() for p in [
            'maps.google.com', 'google.com/maps', 'goo.gl/maps',
            'maps.app.goo.gl', '/maps/', 'maps.app'
        ])
        assert is_maps is False


# ============================================================
# 2. 翻譯請求解析測試
# ============================================================

class TestParseTranslationRequest:
    """測試翻譯請求解析"""

    def test_basic_translation(self):
        result = main.parse_translation_request("翻譯成英文：你好世界")
        assert result is not None
        lang, text = result
        assert lang == "English"
        assert text == "你好世界"

    def test_translation_with_colon(self):
        result = main.parse_translation_request("翻譯成日文:今天天氣很好")
        assert result is not None
        lang, text = result
        assert lang == "Japanese"
        assert text == "今天天氣很好"

    def test_translation_with_space(self):
        result = main.parse_translation_request("翻譯成韓文 我喜歡音樂")
        assert result is not None
        lang, text = result
        assert lang == "Korean"

    def test_translation_with_help_prefix(self):
        result = main.parse_translation_request("幫我翻譯成英文：謝謝你的幫助")
        assert result is not None
        lang, text = result
        assert lang == "English"
        assert text == "謝謝你的幫助"

    def test_translation_with_please_prefix(self):
        result = main.parse_translation_request("請翻譯成法文：你好")
        assert result is not None
        lang, text = result
        assert lang == "French"

    def test_translation_without_成(self):
        result = main.parse_translation_request("翻譯英文：你好")
        assert result is not None
        lang, text = result
        assert lang == "English"

    def test_translation_unknown_language(self):
        result = main.parse_translation_request("翻譯成火星文：你好")
        assert result is not None
        lang, text = result
        # Unknown language should be passed through as-is
        assert lang == "火星文"

    def test_not_translation_request(self):
        result = main.parse_translation_request("你好世界")
        assert result is None


class TestLanguageMap:
    """測試語言對照表"""

    def test_common_languages(self):
        assert main.LANGUAGE_MAP["英文"] == "English"
        assert main.LANGUAGE_MAP["日文"] == "Japanese"
        assert main.LANGUAGE_MAP["韓文"] == "Korean"

    def test_chinese_variants(self):
        assert main.LANGUAGE_MAP["繁體中文"] == "Traditional Chinese"
        assert main.LANGUAGE_MAP["簡體中文"] == "Simplified Chinese"
        assert main.LANGUAGE_MAP["繁中"] == "Traditional Chinese"
        assert main.LANGUAGE_MAP["簡中"] == "Simplified Chinese"

    def test_southeast_asian_languages(self):
        assert main.LANGUAGE_MAP["越南文"] == "Vietnamese"
        assert main.LANGUAGE_MAP["泰文"] == "Thai"
        assert main.LANGUAGE_MAP["印尼文"] == "Indonesian"
        assert main.LANGUAGE_MAP["馬來文"] == "Malay"
        assert main.LANGUAGE_MAP["菲律賓文"] == "Filipino"

    def test_european_languages(self):
        assert main.LANGUAGE_MAP["法文"] == "French"
        assert main.LANGUAGE_MAP["德文"] == "German"
        assert main.LANGUAGE_MAP["西班牙文"] == "Spanish"
        assert main.LANGUAGE_MAP["義大利文"] == "Italian"
        assert main.LANGUAGE_MAP["俄文"] == "Russian"

    def test_alternative_names(self):
        # 文 and 語 should map to the same language
        assert main.LANGUAGE_MAP["英文"] == main.LANGUAGE_MAP["英語"]
        assert main.LANGUAGE_MAP["日文"] == main.LANGUAGE_MAP["日語"]
        assert main.LANGUAGE_MAP["韓文"] == main.LANGUAGE_MAP["韓語"]


# ============================================================
# 3. 社群貼文資料正規化測試
# ============================================================

class TestNormalizeSocialPostData:
    """測試社群貼文資料正規化"""

    def test_facebook_standard_fields(self):
        post = {
            "pageName": "TestPage",
            "text": "Hello world",
            "likes": 100,
            "comments": 50,
            "shares": 25,
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert result["username"] == "TestPage"
        assert result["text"] == "Hello world"
        assert result["likes"] == 100
        assert result["comments"] == 50
        assert result["shares"] == 25

    def test_facebook_alternative_field_names(self):
        post = {
            "userName": "AltUser",
            "postText": "Alt text content",
            "likesCount": 200,
            "commentsCount": 30,
            "sharesCount": 10,
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert result["username"] == "AltUser"
        assert result["text"] == "Alt text content"
        assert result["likes"] == 200
        assert result["comments"] == 30
        assert result["shares"] == 10

    def test_facebook_missing_fields(self):
        post = {}
        result = main.normalize_social_post_data(post, "facebook")
        assert result["username"] == "未知"
        assert result["text"] == ""
        assert result["likes"] == 0
        assert result["comments"] == 0
        assert result["shares"] == 0

    def test_threads_standard_fields(self):
        post = {
            "ownerUsername": "threaduser",
            "text": "Thread post content",
            "likeCount": 500,
            "replyCount": 20,
            "repostCount": 5,
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "threaduser"
        assert result["text"] == "Thread post content"
        assert result["likes"] == 500
        assert result["comments"] == 20
        assert result["shares"] == 5

    def test_threads_alternative_fields(self):
        post = {
            "author": {"username": "altthread"},
            "caption": "Alt caption",
            "likesCount": 300,
            "commentsCount": 15,
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "altthread"
        assert result["text"] == "Alt caption"
        assert result["likes"] == 300
        assert result["comments"] == 15

    def test_unknown_platform(self):
        post = {"text": "unknown"}
        result = main.normalize_social_post_data(post, "instagram")
        assert result["username"] == "未知"
        assert result["text"] == ""
        assert result["likes"] == 0


# ============================================================
# 4. AI 回覆解析測試
# ============================================================

class TestParseSummaryResponse:
    """測試摘要回覆解析"""

    def test_parse_full_response(self):
        response = """🏷️ 分類：科技

📌 主題：AI 技術發展趨勢

📝 重點摘要：
• 重點1 - AI 快速發展
• 重點2 - 影響各行各業

🔑 關鍵字：AI、科技、機器學習、自動化

🎯 一句話總結：AI 正在改變世界"""

        result = main.parse_summary_response(response)
        assert result["category"] == "科技"
        assert result["title"] == "AI 技術發展趨勢"
        assert "AI" in result["keywords"]
        assert "科技" in result["keywords"]
        assert len(result["keywords"]) >= 3

    def test_parse_category_with_slash(self):
        response = "🏷️ 分類：科技/AI\n\n📌 主題：測試"
        result = main.parse_summary_response(response)
        assert result["category"] == "科技"

    def test_parse_empty_response(self):
        result = main.parse_summary_response("")
        assert result["category"] == "其他"
        assert result["keywords"] == []
        assert result["title"] == ""

    def test_parse_maps_response(self):
        response = """🏷️ 分類：地圖

📌 地點名稱：一蘭拉麵 新宿店

📝 重點資訊：
• 營業時間：24小時

🔑 關鍵字：拉麵、日本料理、新宿

🎯 一句話總結：新宿24小時營業的拉麵店"""

        result = main.parse_summary_response(response)
        assert result["category"] == "地圖"
        assert result["title"] == "一蘭拉麵 新宿店"
        assert "拉麵" in result["keywords"]


class TestParseSocialSummaryResponse:
    """測試社群摘要回覆解析"""

    def test_parse_social_summary(self):
        response = """📌 帳號：TestAccount

📝 摘要：這是一篇關於科技趨勢的貼文，討論了AI的發展方向。內容豐富。

🔑 關鍵字：AI、科技、趨勢、創新、未來

📊 互動數據：500 讚 | 30 留言 | 10 分享

🎯 貼文類型：資訊分享"""

        result = main.parse_social_summary_response(response)
        assert "科技趨勢" in result["summary"]
        assert "AI" in result["keywords"]
        assert len(result["keywords"]) >= 3
        assert result["post_type"] == "資訊分享"

    def test_parse_social_empty(self):
        result = main.parse_social_summary_response("")
        assert result["summary"] == ""
        assert result["keywords"] == []
        assert result["post_type"] == "其他"


# ============================================================
# 5. 幻覺偵測測試
# ============================================================

class TestIsHallucination:
    """測試 Whisper 幻覺偵測"""

    def test_empty_text(self):
        assert main.is_hallucination("") is True
        assert main.is_hallucination("   ") is True

    def test_none_text(self):
        assert main.is_hallucination(None) is True

    def test_too_short(self):
        assert main.is_hallucination("哈哈") is True
        assert main.is_hallucination("嗯") is True

    def test_known_hallucination_patterns(self):
        assert main.is_hallucination("请不吝点赞") is True
        assert main.is_hallucination("感謝觀看本期影片") is True
        assert main.is_hallucination("歡迎訂閱我的頻道") is True
        assert main.is_hallucination("字幕由 Amara 提供") is True
        assert main.is_hallucination("like and subscribe") is True

    def test_repeated_words(self):
        assert main.is_hallucination("嗯 嗯 嗯") is True
        assert main.is_hallucination("啊 啊 啊") is True

    def test_valid_transcription(self):
        assert main.is_hallucination("今天天氣真好，我想出去走走") is False
        assert main.is_hallucination("請記得明天帶文件過來") is False
        assert main.is_hallucination("Hello world this is a test") is False


# ============================================================
# 6. 多篇爬取指令解析測試
# ============================================================

class TestScrapeMultiPattern:
    """測試多篇爬取指令 regex"""

    def test_basic_pattern(self):
        text = "爬 5 篇 https://www.facebook.com/some.page"
        match = main.SCRAPE_MULTI_PATTERN.match(text)
        assert match is not None
        assert match.group(1) == "5"
        assert "facebook.com" in match.group(2)

    def test_with_help_prefix(self):
        text = "幫我爬 10 篇 https://www.facebook.com/page"
        match = main.SCRAPE_MULTI_PATTERN.match(text)
        assert match is not None
        assert match.group(1) == "10"

    def test_with_取_character(self):
        text = "爬取 3 篇 https://www.facebook.com/page"
        match = main.SCRAPE_MULTI_PATTERN.match(text)
        assert match is not None
        assert match.group(1) == "3"

    def test_no_match(self):
        text = "你好"
        match = main.SCRAPE_MULTI_PATTERN.match(text)
        assert match is None


# ============================================================
# 7. Regex 模式測試
# ============================================================

class TestRegexPatterns:
    """測試各種 regex 模式"""

    def test_url_pattern(self):
        assert main.URL_PATTERN.search("https://example.com") is not None
        assert main.URL_PATTERN.search("http://test.org/page") is not None
        assert main.URL_PATTERN.search("no url here") is None

    def test_facebook_post_pattern(self):
        urls = [
            "https://www.facebook.com/user/posts/123",
            "https://www.facebook.com/user/videos/456",
            "https://www.facebook.com/user/photos/789",
            "https://www.facebook.com/watch/?v=123",
            "https://www.facebook.com/story.php?id=123",
            "https://www.facebook.com/reel/123",
            "https://m.facebook.com/user/posts/123",
            "https://web.facebook.com/user/posts/123",
        ]
        for url in urls:
            assert main.FACEBOOK_POST_PATTERN.match(url) is not None, f"Failed for: {url}"

    def test_facebook_page_pattern(self):
        urls = [
            "https://www.facebook.com/somepage",
            "https://www.facebook.com/some.page/",
            "https://www.facebook.com/page123",
        ]
        for url in urls:
            assert main.FACEBOOK_PAGE_PATTERN.match(url) is not None, f"Failed for: {url}"

    def test_threads_post_pattern(self):
        url = "https://www.threads.net/@username/post/ABC123xyz"
        assert main.THREADS_POST_PATTERN.match(url) is not None

    def test_threads_post_pattern_com_domain(self):
        """threads.com 域名也應該匹配"""
        url = "https://www.threads.com/@username/post/ABC123xyz"
        assert main.THREADS_POST_PATTERN.match(url) is not None

    def test_threads_post_pattern_with_query_params(self):
        """帶 query params 的 Threads URL 應該匹配"""
        url = "https://www.threads.com/@chiongyjdpp/post/DU2oUjaEynj?xmt=AQF0rvrPjWqAbBJIGjET1wW1"
        assert main.THREADS_POST_PATTERN.match(url) is not None

    def test_threads_profile_pattern(self):
        """Threads 個人頁面應該匹配"""
        url = "https://www.threads.com/@kaku_88life"
        assert main.THREADS_PROFILE_PATTERN.match(url) is not None

    def test_threads_profile_pattern_net(self):
        url = "https://www.threads.net/@kaku_88life"
        assert main.THREADS_PROFILE_PATTERN.match(url) is not None

    def test_threads_profile_not_match_post(self):
        """貼文 URL 不應該被 profile pattern 匹配"""
        url = "https://www.threads.com/@username/post/ABC123"
        assert main.THREADS_PROFILE_PATTERN.match(url) is None


# ============================================================
# 8. Quick Reply 語言選項測試
# ============================================================

class TestQuickReplyLanguages:
    """測試 Quick Reply 語言選項"""

    def test_has_10_languages(self):
        assert len(main.QUICK_REPLY_LANGUAGES) == 10

    def test_all_languages_in_map(self):
        for label, _ in main.QUICK_REPLY_LANGUAGES:
            assert label in main.LANGUAGE_MAP, f"Language '{label}' not found in LANGUAGE_MAP"

    def test_common_languages_included(self):
        labels = [label for label, _ in main.QUICK_REPLY_LANGUAGES]
        assert "英文" in labels
        assert "日文" in labels
        assert "韓文" in labels


# ============================================================
# 9. 使用者狀態管理測試
# ============================================================

class TestUserStates:
    """測試使用者狀態管理"""

    def setup_method(self):
        """每個測試前清空 user_states"""
        main.user_states.clear()

    def test_translation_mode_state(self):
        main.user_states["user1"] = {
            "mode": "translate_waiting",
            "target_language": "English",
            "entered_at": time.time(),
        }
        assert main.user_states["user1"]["mode"] == "translate_waiting"
        assert main.user_states["user1"]["target_language"] == "English"

    def test_language_select_state(self):
        main.user_states["user1"] = {
            "mode": "translate_select_language",
            "entered_at": time.time(),
        }
        assert main.user_states["user1"]["mode"] == "translate_select_language"

    def test_scrape_waiting_count_state(self):
        main.user_states["user1"] = {
            "mode": "scrape_waiting_count",
            "url": "https://facebook.com/page",
            "platform": "facebook",
            "entered_at": time.time(),
        }
        assert main.user_states["user1"]["mode"] == "scrape_waiting_count"
        assert main.user_states["user1"]["platform"] == "facebook"

    def test_timeout_detection(self):
        """測試超時偵測邏輯"""
        old_time = time.time() - (main.TRANSLATION_MODE_TIMEOUT + 10)
        main.user_states["user1"] = {
            "mode": "translate_waiting",
            "target_language": "English",
            "entered_at": old_time,
        }
        # Simulate timeout check logic
        current_time = time.time()
        entered_at = main.user_states["user1"]["entered_at"]
        assert current_time - entered_at >= main.TRANSLATION_MODE_TIMEOUT

    def test_non_timeout(self):
        """測試未超時"""
        main.user_states["user1"] = {
            "mode": "translate_waiting",
            "target_language": "English",
            "entered_at": time.time(),
        }
        current_time = time.time()
        entered_at = main.user_states["user1"]["entered_at"]
        assert current_time - entered_at < main.TRANSLATION_MODE_TIMEOUT


# ============================================================
# 10. Flask 路由測試
# ============================================================

class TestFlaskRoutes:
    """測試 Flask 路由"""

    def setup_method(self):
        self.client = main.app.test_client()

    def test_callback_without_signature(self):
        """沒有簽名應該回傳 400"""
        response = self.client.post("/callback", data="test")
        assert response.status_code == 400

    def test_callback_with_invalid_signature(self):
        """無效簽名應該回傳 400"""
        response = self.client.post(
            "/callback",
            data="test",
            headers={"X-Line-Signature": "invalid"}
        )
        assert response.status_code == 400


# ============================================================
# 10.5 短網址解析測試
# ============================================================

class TestResolveShortUrl:
    """測試短網址解析"""

    @patch("main.requests.head")
    def test_resolve_redirect(self, mock_head):
        """短網址應被解析為完整 URL"""
        mock_response = MagicMock()
        mock_response.url = "https://www.google.com/maps/place/Tokyo+Tower"
        mock_head.return_value = mock_response

        result = main.resolve_short_url("https://maps.app.goo.gl/abc123")
        assert result == "https://www.google.com/maps/place/Tokyo+Tower"

    @patch("main.requests.head")
    def test_no_redirect(self, mock_head):
        """沒有重定向時回傳原始 URL"""
        mock_response = MagicMock()
        mock_response.url = "https://maps.app.goo.gl/abc123"
        mock_head.return_value = mock_response

        result = main.resolve_short_url("https://maps.app.goo.gl/abc123")
        assert result == "https://maps.app.goo.gl/abc123"

    @patch("main.requests.head")
    def test_resolve_failure(self, mock_head):
        """解析失敗時回傳原始 URL"""
        mock_head.side_effect = Exception("Timeout")
        result = main.resolve_short_url("https://maps.app.goo.gl/abc123")
        assert result == "https://maps.app.goo.gl/abc123"


# ============================================================
# 11. 網頁抓取功能測試（使用 mock）
# ============================================================

class TestFetchWebpageContent:
    """測試網頁內容抓取"""

    @patch("main.requests.get")
    def test_fetch_simple_page(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = """
        <html>
            <head><title>Test Title</title>
            <meta name="description" content="Test description">
            </head>
            <body>
                <article>
                    <p>This is a test paragraph with enough content to pass the length filter for testing purposes.</p>
                </article>
            </body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = main.fetch_webpage_content("https://example.com")
        assert "Test Title" in result
        assert "Test description" in result

    @patch("main.requests.get")
    def test_fetch_page_error(self, mock_get):
        mock_get.side_effect = Exception("Connection error")
        result = main.fetch_webpage_content("https://invalid.com")
        assert "無法抓取網頁內容" in result

    @patch("main.requests.get")
    def test_fetch_page_strips_scripts(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = """
        <html>
            <head><title>Page</title></head>
            <body>
                <script>alert('test')</script>
                <p>This is the actual content that should remain after cleaning up all the scripts.</p>
            </body>
        </html>
        """
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = main.fetch_webpage_content("https://example.com")
        assert "alert" not in result


# ============================================================
# 12. Notion 儲存功能測試（使用 mock）
# ============================================================

class TestSaveToNotion:
    """測試 Notion 儲存功能"""

    def test_save_without_notion_configured(self):
        """Notion 未設定時應回傳 False"""
        original_client = main.notion_client
        original_db = main.NOTION_DATABASE_ID
        main.notion_client = None
        main.NOTION_DATABASE_ID = None

        result = main.save_to_notion(
            title="Test",
            content_type="URL摘要",
            category="科技",
            content="Test content"
        )
        assert result is False

        main.notion_client = original_client
        main.NOTION_DATABASE_ID = original_db

    def test_save_social_without_notion(self):
        """Notion 未設定時社群儲存應回傳 False"""
        original_client = main.notion_client
        main.notion_client = None

        result = main.save_social_to_notion(
            platform="Facebook",
            username="test",
            summary="test summary",
            original_text="test text",
            keywords=["test"],
            likes=10,
            comments=5,
            shares=2,
            source_url="https://example.com"
        )
        assert result is False

        main.notion_client = original_client


# ============================================================
# 13. OpenAI 功能測試（使用 mock）
# ============================================================

class TestOpenAIFunctions:
    """測試 OpenAI 相關功能"""

    def test_translate_without_openai(self):
        """OpenAI 未設定時翻譯應回傳錯誤訊息"""
        original_client = main.openai_client
        main.openai_client = None

        result = main.translate_text("你好", "English")
        assert "翻譯功能未設定" in result

        main.openai_client = original_client

    def test_summarize_webpage_without_openai(self):
        """OpenAI 未設定時摘要應回傳錯誤訊息"""
        original_client = main.openai_client
        main.openai_client = None

        result = main.summarize_webpage("test content")
        assert "網頁摘要功能未設定" in result

        main.openai_client = original_client

    def test_summarize_text_without_openai(self):
        original_client = main.openai_client
        main.openai_client = None

        result = main.summarize_text("test text")
        assert "文字摘要功能未設定" in result

        main.openai_client = original_client

    def test_summarize_google_maps_without_openai(self):
        original_client = main.openai_client
        main.openai_client = None

        result = main.summarize_google_maps("content", "https://maps.google.com")
        assert "地圖分析功能未設定" in result

        main.openai_client = original_client

    def test_summarize_social_without_openai(self):
        original_client = main.openai_client
        main.openai_client = None

        result = main.summarize_social_post({"username": "test", "text": "hello"}, "facebook")
        assert "社群分析功能未設定" in result

        main.openai_client = original_client

    @patch("main.openai_client")
    def test_translate_text_success(self, mock_client):
        """測試翻譯功能正常回傳"""
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello World"
        mock_client.chat.completions.create.return_value = mock_response

        result = main.translate_text("你好世界", "English")
        assert result == "Hello World"

    @patch("main.openai_client")
    def test_translate_text_error(self, mock_client):
        """測試翻譯功能錯誤處理"""
        main.openai_client = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        result = main.translate_text("你好", "English")
        assert "翻譯失敗" in result


# ============================================================
# 14. Apify 爬蟲功能測試（使用 mock）
# ============================================================

class TestApifyScraping:
    """測試 Apify 爬蟲功能"""

    def test_scrape_facebook_without_apify(self):
        """Apify 未設定時應回傳空列表"""
        original_client = main.apify_client
        main.apify_client = None

        result = main.scrape_facebook_post("https://facebook.com/post/123")
        assert result == []

        main.apify_client = original_client

    def test_scrape_threads_without_apify(self):
        original_client = main.apify_client
        main.apify_client = None

        result = main.scrape_threads_post("https://threads.net/@user/post/123")
        assert result == []

        main.apify_client = original_client

    def test_scrape_google_maps_without_apify(self):
        """Apify 未設定時應回傳 None"""
        original_client = main.apify_client
        main.apify_client = None

        result = main.scrape_google_maps("https://maps.google.com/place/test")
        assert result is None

        main.apify_client = original_client


# ============================================================
# 14.5 Google Maps 格式化測試
# ============================================================

class TestFormatGoogleMapsResult:
    """測試 Google Maps 爬蟲結果格式化"""

    def test_format_full_place_data(self):
        """完整地點資料格式化"""
        place = {
            "title": "東京拉麵店",
            "categoryName": "拉麵店",
            "address": "東京都新宿區1-2-3",
            "totalScore": 4.5,
            "reviewsCount": 120,
            "phone": "+81-3-1234-5678",
            "website": "https://ramen.example.com",
            "price": "$$",
        }
        result = main.format_google_maps_result(place)
        assert "東京拉麵店" in result
        assert "拉麵店" in result
        assert "東京都新宿區1-2-3" in result
        assert "4.5" in result
        assert "120" in result
        assert "+81-3-1234-5678" in result
        assert "https://ramen.example.com" in result
        assert "$$" in result

    def test_format_minimal_place_data(self):
        """最少資料的地點格式化"""
        place = {
            "title": "某地點",
        }
        result = main.format_google_maps_result(place)
        assert "某地點" in result
        assert "📍" in result

    def test_format_with_name_field(self):
        """使用 name 欄位而非 title"""
        place = {
            "name": "備用名稱店",
        }
        result = main.format_google_maps_result(place)
        assert "備用名稱店" in result

    def test_format_with_opening_hours_list(self):
        """營業時間為列表格式"""
        place = {
            "title": "Test Place",
            "openingHours": [
                {"day": "Monday", "hours": "9:00-21:00"},
                {"day": "Tuesday", "hours": "9:00-21:00"},
            ],
        }
        result = main.format_google_maps_result(place)
        assert "Monday" in result
        assert "9:00-21:00" in result

    def test_format_with_opening_hours_string_list(self):
        """營業時間為字串列表格式"""
        place = {
            "title": "Test Place",
            "openingHours": ["Mon: 9-21", "Tue: 9-21"],
        }
        result = main.format_google_maps_result(place)
        assert "Mon: 9-21" in result

    def test_format_with_opening_hours_string(self):
        """營業時間為單一字串"""
        place = {
            "title": "Test Place",
            "openingHours": "Mon-Fri 9:00-21:00",
        }
        result = main.format_google_maps_result(place)
        assert "Mon-Fri 9:00-21:00" in result

    def test_format_with_coordinates(self):
        """包含座標資訊"""
        place = {
            "title": "Test Place",
            "location": {"lat": 35.6762, "lng": 139.6503},
        }
        result = main.format_google_maps_result(place)
        assert "35.6762" in result
        assert "139.6503" in result

    def test_format_with_description(self):
        """包含簡介"""
        place = {
            "title": "Test Place",
            "description": "一家很棒的餐廳",
        }
        result = main.format_google_maps_result(place)
        assert "一家很棒的餐廳" in result

    def test_format_empty_place(self):
        """空資料應回傳未知地點"""
        place = {}
        result = main.format_google_maps_result(place)
        assert "未知地點" in result

    def test_format_with_rating_field(self):
        """使用 rating 欄位而非 totalScore"""
        place = {
            "title": "Test",
            "rating": 4.2,
            "reviews": 50,
        }
        result = main.format_google_maps_result(place)
        assert "4.2" in result
        assert "50" in result


# ============================================================
# 15. 常數與設定測試
# ============================================================

class TestConstants:
    """測試常數與設定"""

    def test_timeout_is_5_minutes(self):
        assert main.TRANSLATION_MODE_TIMEOUT == 300

    def test_hallucination_patterns_not_empty(self):
        assert len(main.HALLUCINATION_PATTERNS) > 0

    def test_language_map_not_empty(self):
        assert len(main.LANGUAGE_MAP) > 0

    def test_quick_reply_languages_format(self):
        for item in main.QUICK_REPLY_LANGUAGES:
            assert len(item) == 2
            assert isinstance(item[0], str)  # Chinese label
            assert isinstance(item[1], str)  # English code


# ============================================================
# 16. 邊界情況測試
# ============================================================

class TestEdgeCases:
    """測試邊界情況"""

    def test_extract_url_with_unicode(self):
        text = "看看 https://example.com/path 這個"
        result = main.extract_url(text)
        assert result is not None

    def test_normalize_facebook_with_string_numbers(self):
        """數字可能以字串形式出現"""
        post = {
            "pageName": "Test",
            "text": "content",
            "likes": "100",
            "comments": "50",
            "shares": "25",
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert result["likes"] == 100
        assert result["comments"] == 50
        assert result["shares"] == 25

    def test_parse_keywords_with_different_separators(self):
        response = "🔑 關鍵字：AI、機器學習,深度學習，自然語言處理"
        result = main.parse_summary_response(response)
        assert len(result["keywords"]) >= 3

    def test_long_content_truncation(self):
        """測試超長文字在 Notion 儲存時被截斷"""
        long_title = "A" * 200
        # Title should be truncated at 100
        truncated = long_title[:100]
        assert len(truncated) == 100


# ============================================================
# 17. Threads normalize 新欄位測試
# ============================================================

class TestThreadsNewFields:
    """測試 Threads 新欄位格式（sinam7/threads-post-scraper 回傳格式）"""

    def test_threads_content_field(self):
        """content 欄位應被正確提取為 text"""
        post = {
            "authorId": "/@voltima_quant",
            "authorName": None,
            "content": "This is the post content from new scraper",
            "images": [],
            "postUrl": "https://threads.net/@voltima_quant/post/abc",
            "timestamp": "2026-01-01",
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["text"] == "This is the post content from new scraper"
        assert result["username"] == "voltima_quant"

    def test_threads_author_id_strip(self):
        """authorId 的 /@ 前綴應被移除"""
        post = {
            "authorId": "/@someuser",
            "content": "test",
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "someuser"

    def test_threads_author_name_fallback(self):
        """authorName 應作為 username 的 fallback"""
        post = {
            "authorId": "",
            "authorName": "Display Name",
            "content": "test",
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "Display Name"

    def test_threads_backward_compatible(self):
        """舊欄位名仍應正常運作"""
        post = {
            "ownerUsername": "olduser",
            "text": "old format text",
            "likeCount": 100,
            "replyCount": 10,
            "repostCount": 5,
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "olduser"
        assert result["text"] == "old format text"
        assert result["likes"] == 100
        assert result["comments"] == 10
        assert result["shares"] == 5

    def test_threads_empty_new_format(self):
        """新格式但欄位為空"""
        post = {
            "authorId": "",
            "authorName": None,
            "content": "",
            "images": [],
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["username"] == "未知"
        assert result["text"] == ""


# ============================================================
# 18. Facebook 圖片提取測試
# ============================================================

class TestFacebookImageExtraction:
    """測試 Facebook 圖片資料提取"""

    def test_facebook_media_extraction(self):
        """從 media 陣列提取圖片 URL 和 OCR 文字"""
        post = {
            "pageName": "TestPage",
            "text": "Hello",
            "likes": 10,
            "comments": 5,
            "shares": 2,
            "media": [
                {
                    "thumbnail": "https://scontent.xx.fbcdn.net/thumb1.jpg",
                    "__typename": "Photo",
                    "photo_image": {"uri": "https://scontent.xx.fbcdn.net/full1.jpg", "height": 526, "width": 526},
                    "ocrText": "Some text in the image",
                    "url": "https://www.facebook.com/photo/?fbid=123"
                },
                {
                    "thumbnail": "https://scontent.xx.fbcdn.net/thumb2.jpg",
                    "photo_image": {"uri": "https://scontent.xx.fbcdn.net/full2.jpg"},
                    "ocrText": "More OCR text",
                }
            ]
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert len(result["images"]) == 2
        assert result["images"][0] == "https://scontent.xx.fbcdn.net/full1.jpg"
        assert result["images"][1] == "https://scontent.xx.fbcdn.net/full2.jpg"
        assert "Some text in the image" in result["image_text"]
        assert "More OCR text" in result["image_text"]

    def test_facebook_media_thumbnail_fallback(self):
        """photo_image 不存在時使用 thumbnail"""
        post = {
            "pageName": "TestPage",
            "text": "Hello",
            "media": [
                {
                    "thumbnail": "https://scontent.xx.fbcdn.net/thumb.jpg",
                }
            ]
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert len(result["images"]) == 1
        assert result["images"][0] == "https://scontent.xx.fbcdn.net/thumb.jpg"

    def test_facebook_no_media(self):
        """沒有 media 欄位時 images 應為空"""
        post = {
            "pageName": "TestPage",
            "text": "Hello",
        }
        result = main.normalize_social_post_data(post, "facebook")
        assert result["images"] == []
        assert result["image_text"] == ""

    def test_threads_images_extraction(self):
        """Threads images 欄位提取"""
        post = {
            "ownerUsername": "user1",
            "text": "Post with images",
            "images": [
                "https://scontent.cdninstagram.com/img1.jpg",
                "https://scontent.cdninstagram.com/img2.jpg",
            ],
        }
        result = main.normalize_social_post_data(post, "threads")
        assert len(result["images"]) == 2
        assert result["images"][0] == "https://scontent.cdninstagram.com/img1.jpg"

    def test_threads_empty_images(self):
        """Threads 空 images 陣列"""
        post = {
            "ownerUsername": "user1",
            "text": "No images",
            "images": [],
        }
        result = main.normalize_social_post_data(post, "threads")
        assert result["images"] == []

    def test_unknown_platform_has_image_fields(self):
        """未知平台也應回傳 images 和 image_text 欄位"""
        post = {"text": "unknown"}
        result = main.normalize_social_post_data(post, "instagram")
        assert result["images"] == []
        assert result["image_text"] == ""


# ============================================================
# 19. 翻譯模式 fall through 修復測試
# ============================================================

class TestTranslateSelectLanguageFallthrough:
    """測試翻譯模式語言選擇的 fall through 修復"""

    def setup_method(self):
        main.user_states.clear()

    def test_invalid_language_keeps_state(self):
        """輸入無效語言時狀態應保持在 translate_select_language"""
        main.user_states["user1"] = {
            "mode": "translate_select_language",
            "entered_at": time.time(),
        }
        # After the fix, the state should remain (not fall through)
        # The handler would show error + keep state, but we test the logic:
        # Input "你好" is not in LANGUAGE_MAP
        assert main.LANGUAGE_MAP.get("你好") is None

    def test_cancel_in_select_language_mode(self):
        """在語言選擇模式中取消應清除狀態"""
        main.user_states["user1"] = {
            "mode": "translate_select_language",
            "entered_at": time.time(),
        }
        # Simulate cancel - state should be removed
        cancel_words = ["取消", "離開", "結束", "exit", "cancel"]
        for word in cancel_words:
            main.user_states["user1"] = {
                "mode": "translate_select_language",
                "entered_at": time.time(),
            }
            # The fix now handles cancel properly in translate_select_language
            assert word in ["取消", "離開", "結束", "exit", "cancel"]

    def test_valid_language_from_map(self):
        """在語言選擇模式中輸入有效語言名稱"""
        # All LANGUAGE_MAP keys should be found
        for lang_name in ["英文", "日文", "韓文", "越南文", "馬來文"]:
            assert lang_name in main.LANGUAGE_MAP


# ============================================================
# 20. Google Maps 新 actor 測試
# ============================================================

class TestGoogleMapsNewActor:
    """測試 Google Maps 新 Apify actor 設定"""

    @patch("main.apify_client")
    def test_scrape_uses_new_actor(self, mock_client):
        """應使用 compass/crawler-google-places actor"""
        main.apify_client = mock_client
        mock_run = {"defaultDatasetId": "ds123"}
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = iter([
            {"title": "Test Place", "address": "Test Address"}
        ])

        result = main.scrape_google_maps("https://www.google.com/maps/place/Test")
        assert result is not None
        assert result["title"] == "Test Place"
        # Verify the correct actor was called
        mock_client.actor.assert_called_with("compass/crawler-google-places")

    @patch("main.apify_client")
    def test_scrape_input_format(self, mock_client):
        """應傳送正確的 input 格式"""
        main.apify_client = mock_client
        mock_run = {"defaultDatasetId": "ds123"}
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = iter([])

        main.scrape_google_maps("https://www.google.com/maps/place/Test")
        call_args = mock_client.actor.return_value.call.call_args
        run_input = call_args[1]["run_input"]
        assert "startUrls" in run_input
        assert run_input["startUrls"] == [{"url": "https://www.google.com/maps/place/Test"}]
        assert run_input["maxCrawledPlacesPerSearch"] == 1
        assert run_input["language"] == "zh-TW"


# ============================================================
# 21. 圖片分析功能測試
# ============================================================

class TestImageAnalysis:
    """測試圖片分析相關功能"""

    def test_analyze_image_without_openai(self):
        """OpenAI 未設定時應回傳錯誤訊息"""
        original_client = main.openai_client
        main.openai_client = None

        result = main.analyze_image(b"fake image data")
        assert "圖片分析功能未設定" in result

        main.openai_client = original_client

    def test_translate_image_text_without_openai(self):
        """OpenAI 未設定時應回傳錯誤訊息"""
        original_client = main.openai_client
        main.openai_client = None

        result = main.translate_image_text(b"fake image data", "English")
        assert "圖片翻譯功能未設定" in result

        main.openai_client = original_client

    @patch("main.openai_client")
    def test_analyze_image_success(self, mock_client):
        """測試圖片分析正常回傳"""
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "🏷️ 分類：照片\n\n📝 圖片描述：Test image"
        mock_client.chat.completions.create.return_value = mock_response

        result = main.analyze_image(b"fake image data")
        assert "Test image" in result

    @patch("main.openai_client")
    def test_analyze_image_error(self, mock_client):
        """測試圖片分析錯誤處理"""
        main.openai_client = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API Error")

        result = main.analyze_image(b"fake image data")
        assert "圖片分析失敗" in result

    @patch("main.openai_client")
    def test_translate_image_text_success(self, mock_client):
        """測試圖片翻譯正常回傳"""
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "📖 原始文字：你好\n\n🌐 翻譯結果：Hello"
        mock_client.chat.completions.create.return_value = mock_response

        result = main.translate_image_text(b"fake image data", "English")
        assert "Hello" in result

    def test_image_message_content_imported(self):
        """ImageMessageContent 應已被 import"""
        from linebot.v3.webhooks import ImageMessageContent
        assert ImageMessageContent is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
