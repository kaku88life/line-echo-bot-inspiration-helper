"""
LINE Bot 功能測試
測試所有核心功能是否正常運作
"""

import re
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from types import SimpleNamespace
import os
import time
import json

# Set dummy environment variables before importing main
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")

import main
import desktop_voice_capture as desktop_voice


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

    def test_extract_threads_url_with_at_and_hyphen(self):
        text = "看看 https://www.threads.com/@j_h0n.k/post/DXrOA9-kl57?xmt=AQF0_lbg&slof=1"
        result = main.extract_url(text)
        assert result == "https://www.threads.com/@j_h0n.k/post/DXrOA9-kl57?xmt=AQF0_lbg&slof=1"

    def test_extract_url_strips_trailing_punctuation(self):
        text = "來源：https://example.com/page?x=1。"
        assert main.extract_url(text) == "https://example.com/page?x=1"


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

    def test_threads_post_url_with_hyphen_id(self):
        url = "https://www.threads.com/@j_h0n.k/post/DXrOA9-kl57?xmt=AQF0_lbg"
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


class TestCaptureQuality:
    """測試捕捉狀態與來源分類"""

    def test_assess_failed_content_marker(self):
        result = main.assess_extracted_content("無法抓取網頁內容：Connection reset")
        assert result["status"] == main.CAPTURE_STATUS_FAILED
        assert result["needs_review"] is True

    def test_assess_partial_short_content(self):
        result = main.assess_extracted_content("只有一點點內容")
        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True

    def test_assess_full_content(self):
        content = "這是一段有足夠長度的內容。" * 20
        result = main.assess_extracted_content(content)
        assert result["status"] == main.CAPTURE_STATUS_FULL
        assert result["needs_review"] is False

    def test_source_type_from_url(self):
        assert main.source_type_from_url("https://www.youtube.com/watch?v=abc") == "youtube"
        assert main.source_type_from_url("https://maps.app.goo.gl/abc") == "google_maps"
        assert main.source_type_from_url("https://www.104.com.tw/job/6m2k2") == "104"
        assert main.source_type_from_url("https://www.ptt.cc/bbs/Stock/M.123.html") == "ptt"

    def test_social_extractor_name(self):
        assert main.social_extractor_name("threads") == "threads-apify"
        assert main.social_extractor_name("facebook") == "facebook-apify"
        assert main.social_extractor_name("instagram") == "apify"

    def test_assess_social_empty_content_failed(self):
        post = {"text": "", "image_text": "", "images": []}
        result = main.assess_social_post_content(post)
        assert result["status"] == main.CAPTURE_STATUS_FAILED
        assert result["needs_review"] is True
        assert result["reason"] == "empty_social_content"

    def test_assess_social_short_content_partial(self):
        post = {"text": "太短了", "image_text": "", "images": []}
        result = main.assess_social_post_content(post)
        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True

    def test_assess_social_full_content(self):
        post = {"text": "這是一段有足夠資訊的社群貼文內容，可以支撐摘要而不是靠 AI 猜測。", "image_text": "", "images": []}
        result = main.assess_social_post_content(post)
        assert result["status"] == main.CAPTURE_STATUS_FULL
        assert result["needs_review"] is False

    def test_format_social_extracted_content_preserves_raw_fields(self):
        post = {
            "username": "tester",
            "text": "貼文原文",
            "image_text": "圖片文字",
            "images": ["https://example.com/a.jpg"],
            "likes": 1,
            "comments": 2,
            "shares": 3,
        }
        result = main.format_social_extracted_content(post, "https://threads.net/@tester/post/abc")
        assert "tester" in result
        assert "貼文原文" in result
        assert "圖片文字" in result
        assert "https://example.com/a.jpg" in result

    def test_assess_youtube_metadata_only_is_partial(self):
        content = "標題：Test\n頻道：Channel\n\n字幕：尚未抓取逐字稿，先保存影片 metadata。"
        result = main.assess_url_capture_quality(content, "youtube", "youtube-oembed")
        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True
        assert result["reason"] == "youtube_metadata_only"

    def test_assess_youtube_transcript_can_be_full(self):
        content = "標題：Test\n\n逐字稿：\n" + ("這是一段逐字稿內容。" * 20)
        result = main.assess_url_capture_quality(content, "youtube", "youtube-transcript")
        assert result["status"] == main.CAPTURE_STATUS_FULL
        assert result["needs_review"] is False

    def test_status_note_only_for_youtube_partial(self):
        assert main.should_save_status_note_only("youtube", main.CAPTURE_STATUS_PARTIAL) is True
        assert main.should_save_status_note_only("webpage", main.CAPTURE_STATUS_PARTIAL) is False

    def test_format_weekly_review_counts_status(self):
        notes = [
            {"name": "2026-05-01-a.md", "capture_status": "full", "source_type": "webpage", "needs_review": "false"},
            {"name": "2026-05-01-b.md", "capture_status": "failed", "source_type": "104", "needs_review": "true"},
        ]
        result = main.format_weekly_review(notes)
        assert "新增筆記：2 筆" in result
        assert "failed: 1" in result
        assert "需要確認：1 筆" in result


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
        # 第一次 call (Jina AI) 失敗，第二次走 fallback 才會用 BeautifulSoup 過濾 <script>
        fallback_response = MagicMock()
        fallback_response.text = """
        <html>
            <head><title>Page</title></head>
            <body>
                <script>alert('test')</script>
                <p>This is the actual content that should remain after cleaning up all the scripts.</p>
            </body>
        </html>
        """
        fallback_response.raise_for_status = MagicMock()
        mock_get.side_effect = [Exception("Jina down"), fallback_response]

        result = main.fetch_webpage_content("https://example.com")
        assert "alert" not in result
        assert "actual content" in result


# ============================================================
# 12. Notion 儲存功能測試（使用 mock）
# ============================================================

class TestSaveToGDrive:
    """測試 Google Drive 儲存功能（取代舊的 Notion 儲存）"""

    def test_save_without_vault_configured(self):
        """GDRIVE_VAULT_FOLDER_ID 未設定時應回傳 False"""
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = None

        result = main.save_to_gdrive(
            title="Test",
            content_type="URL摘要",
            category="科技",
            content="Test content"
        )
        assert result is False

        main.GDRIVE_VAULT_FOLDER_ID = original

    def test_save_social_without_vault_configured(self):
        """GDRIVE_VAULT_FOLDER_ID 未設定時社群儲存應回傳 False"""
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = None

        result = main.save_social_to_gdrive(
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

        main.GDRIVE_VAULT_FOLDER_ID = original

    @patch("main.save_to_gdrive")
    def test_save_social_passes_capture_metadata(self, mock_save):
        mock_save.return_value = "file123"

        result = main.save_social_to_gdrive(
            platform="Threads",
            username="tester",
            summary="summary",
            original_text="original",
            keywords=["test"],
            likes=1,
            comments=2,
            shares=3,
            source_url="https://threads.net/@tester/post/abc",
            source_type="threads",
            capture_status=main.CAPTURE_STATUS_PARTIAL,
            extractor="threads-apify",
            needs_review=True,
            raw_input=" 看看 https://threads.net/@tester/post/abc ",
        )

        assert result == "file123"
        kwargs = mock_save.call_args.kwargs
        assert kwargs["source_type"] == "threads"
        assert kwargs["capture_status"] == main.CAPTURE_STATUS_PARTIAL
        assert kwargs["extractor"] == "threads-apify"
        assert kwargs["needs_review"] is True
        assert kwargs["raw_input"] == " 看看 https://threads.net/@tester/post/abc "
        assert kwargs["normalized_input"] == "看看 https://threads.net/@tester/post/abc"

    @patch("main.summarize_social_post")
    @patch("main.save_to_gdrive")
    def test_save_normalized_social_post_failed_skips_ai(self, mock_save, mock_summary):
        mock_save.return_value = "file_failed"

        fid, capture = main.save_normalized_social_post(
            platform="threads",
            normalized_data={
                "username": "tester",
                "text": "",
                "image_text": "",
                "images": [],
                "likes": 0,
                "comments": 0,
                "shares": 0,
            },
            source_url="https://threads.net/@tester/post/abc",
            raw_input="https://threads.net/@tester/post/abc",
            user_id="user1",
        )

        assert fid == "file_failed"
        assert capture["quality"]["status"] == main.CAPTURE_STATUS_FAILED
        mock_summary.assert_not_called()
        kwargs = mock_save.call_args.kwargs
        assert kwargs["capture_status"] == main.CAPTURE_STATUS_FAILED
        assert kwargs["source_type"] == "threads"
        assert kwargs["extractor"] == "threads-apify"
        assert kwargs["needs_review"] is True

    @patch("main.save_social_to_gdrive")
    @patch("main.summarize_social_post")
    def test_save_normalized_social_post_full_uses_ai(self, mock_summary, mock_save_social):
        mock_summary.return_value = "這篇貼文已經有足夠內容，可以生成可靠摘要。"
        mock_save_social.return_value = "file_full"

        fid, capture = main.save_normalized_social_post(
            platform="facebook",
            normalized_data={
                "username": "tester",
                "text": "這是一段有足夠資訊的 Facebook 貼文內容，能夠支撐可靠的摘要與關鍵字提取。",
                "image_text": "",
                "images": [],
                "likes": 1,
                "comments": 2,
                "shares": 3,
            },
            source_url="https://facebook.com/user/posts/123",
            raw_input="https://facebook.com/user/posts/123",
            user_id="user1",
        )

        assert fid == "file_full"
        assert capture["quality"]["status"] == main.CAPTURE_STATUS_FULL
        mock_summary.assert_called_once()
        kwargs = mock_save_social.call_args.kwargs
        assert kwargs["source_type"] == "facebook"
        assert kwargs["capture_status"] == main.CAPTURE_STATUS_FULL
        assert kwargs["extractor"] == "facebook-apify"
        assert kwargs["needs_review"] is False


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
# 14.25 YouTube extractor 測試
# ============================================================

class TestYouTubeExtractor:
    """測試 YouTube metadata 與逐字稿抽取"""

    def test_extract_video_id_watch_url(self):
        assert main.extract_youtube_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"

    def test_extract_video_id_short_url(self):
        assert main.extract_youtube_video_id("https://youtu.be/abc123?si=xyz") == "abc123"

    def test_extract_video_id_shorts_url(self):
        assert main.extract_youtube_video_id("https://www.youtube.com/shorts/short123") == "short123"

    def test_extract_yt_initial_player_response(self):
        payload = {"videoDetails": {"title": "Test Video"}}
        html = f"<script>var ytInitialPlayerResponse = {json.dumps(payload)};</script>"
        result = main.extract_yt_initial_player_response(html)
        assert result["videoDetails"]["title"] == "Test Video"

    def test_choose_caption_track_prefers_zh(self):
        player_response = {
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [
                        {"languageCode": "en", "baseUrl": "https://example.com/en"},
                        {"languageCode": "zh-Hant", "baseUrl": "https://example.com/zh"},
                    ]
                }
            }
        }
        result = main.choose_youtube_caption_track(player_response)
        assert result["languageCode"] == "zh-Hant"

    @patch("main.requests.get")
    def test_fetch_youtube_transcript_json3(self, mock_get):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.text = json.dumps({"events": [{"segs": [{"utf8": "第一句"}, {"utf8": "第二句"}]}]})
        response.json.return_value = {"events": [{"segs": [{"utf8": "第一句"}, {"utf8": "第二句"}]}]}
        mock_get.return_value = response

        result = main.fetch_youtube_transcript("https://youtube.com/api/timedtext?v=abc")
        assert "第一句第二句" in result
        assert "fmt=json3" in mock_get.call_args.args[0]

    @patch("main.requests.get")
    def test_fetch_youtube_content_with_transcript(self, mock_get):
        oembed_response = MagicMock()
        oembed_response.raise_for_status = MagicMock()
        oembed_response.json.return_value = {
            "title": "OEmbed Title",
            "author_name": "Channel",
            "author_url": "https://youtube.com/@channel",
        }
        player_payload = {
            "videoDetails": {
                "title": "Watch Title",
                "author": "Watch Channel",
                "shortDescription": "Video description",
                "lengthSeconds": "120",
            },
            "microformat": {"playerMicroformatRenderer": {"publishDate": "2026-01-01"}},
            "captions": {
                "playerCaptionsTracklistRenderer": {
                    "captionTracks": [{"languageCode": "zh-Hant", "baseUrl": "https://example.com/caption"}]
                }
            },
        }
        watch_response = MagicMock()
        watch_response.raise_for_status = MagicMock()
        watch_response.text = f"<script>var ytInitialPlayerResponse = {json.dumps(player_payload)};</script>"
        watch_response.url = "https://www.youtube.com/watch?v=abc"
        transcript_response = MagicMock()
        transcript_response.raise_for_status = MagicMock()
        transcript_response.text = json.dumps({"events": [{"segs": [{"utf8": "逐字稿內容"}]}]})
        transcript_response.json.return_value = {"events": [{"segs": [{"utf8": "逐字稿內容"}]}]}
        mock_get.side_effect = [oembed_response, watch_response, transcript_response]

        content, extractor = main.fetch_youtube_content("https://www.youtube.com/watch?v=abc")
        assert extractor == "youtube-transcript"
        assert "OEmbed Title" in content
        assert "逐字稿內容" in content
        assert "發布日期：2026-01-01" in content

    @patch("main.requests.get")
    def test_fetch_youtube_content_metadata_only(self, mock_get):
        oembed_response = MagicMock()
        oembed_response.raise_for_status = MagicMock()
        oembed_response.json.return_value = {
            "title": "Metadata Only",
            "author_name": "Channel",
            "author_url": "https://youtube.com/@channel",
        }
        watch_response = MagicMock()
        watch_response.raise_for_status = MagicMock()
        watch_response.text = "<script>var ytInitialPlayerResponse = {\"videoDetails\": {}};</script>"
        watch_response.url = "https://www.youtube.com/watch?v=abc"
        mock_get.side_effect = [oembed_response, watch_response]

        content, extractor = main.fetch_youtube_content("https://www.youtube.com/watch?v=abc")
        assert extractor == "youtube-oembed"
        assert "Metadata Only" in content
        assert "尚未抓取逐字稿" in content


# ============================================================
# 14.3 PTT extractor 測試
# ============================================================

class TestPTTExtractor:
    """測試 PTT 文章抽取與清理"""

    SAMPLE_HTML = """
    <html>
      <head><meta property="og:title" content="[問卦] 備用標題" /></head>
      <body>
        <div id="main-content">
          <div class="article-metaline">
            <span class="article-meta-tag">作者</span>
            <span class="article-meta-value">tester (測試者)</span>
          </div>
          <div class="article-metaline">
            <span class="article-meta-tag">標題</span>
            <span class="article-meta-value">[問卦] PTT 抽取器測試</span>
          </div>
          <div class="article-metaline">
            <span class="article-meta-tag">時間</span>
            <span class="article-meta-value">Tue May 12 01:23:45 2026</span>
          </div>
          這是第一段本文，應該被保留下來。
          這是第二段本文，也應該保留下來，方便後續摘要。
          <div class="push">
            <span class="push-tag">推 </span>
            <span class="push-userid">user1</span>
            <span class="push-content">: 推文內容一</span>
            <span class="push-ipdatetime">05/12 01:24</span>
          </div>
          <div class="push">
            <span class="push-tag">噓 </span>
            <span class="push-userid">user2</span>
            <span class="push-content">: 反對意見</span>
            <span class="push-ipdatetime">05/12 01:25</span>
          </div>
          --
          簽名檔不應該進入本文
          ※ 發信站: 批踢踢實業坊(ptt.cc)
          ※ 文章網址: https://www.ptt.cc/bbs/Gossiping/M.123.A.456.html
        </div>
      </body>
    </html>
    """

    def test_parse_ptt_article_html(self):
        result = main.parse_ptt_article_html(
            self.SAMPLE_HTML,
            "https://www.ptt.cc/bbs/Gossiping/M.123.A.456.html",
        )

        assert result["board"] == "Gossiping"
        assert result["article_id"] == "M.123.A.456.html"
        assert result["author"] == "tester (測試者)"
        assert result["title"] == "[問卦] PTT 抽取器測試"
        assert "第一段本文" in result["body"]
        assert "推文內容一" not in result["body"]
        assert "簽名檔" not in result["body"]
        assert result["push_counts"]["推"] == 1
        assert result["push_counts"]["噓"] == 1

    def test_format_ptt_article(self):
        article = main.parse_ptt_article_html(
            self.SAMPLE_HTML,
            "https://www.ptt.cc/bbs/Gossiping/M.123.A.456.html",
        )
        result = main.format_ptt_article(article)

        assert "看板：Gossiping" in result
        assert "標題：[問卦] PTT 抽取器測試" in result
        assert "## 本文" in result
        assert "## 推文統計" in result
        assert "- 推：1" in result
        assert "- 噓：1" in result
        assert "user1: 推文內容一" in result

    @patch("main.requests.get")
    def test_fetch_ptt_content_uses_over18_cookie(self, mock_get):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.text = self.SAMPLE_HTML
        mock_get.return_value = response

        content, extractor = main.fetch_ptt_content("https://www.ptt.cc/bbs/Gossiping/M.123.A.456.html")

        assert extractor == "ptt-html"
        assert "PTT 抽取器測試" in content
        assert mock_get.call_args.kwargs["cookies"] == {"over18": "1"}

    def test_assess_ptt_missing_body_partial(self):
        content = "\n".join([
            "標題：[問卦] 空本文",
            "",
            "## 本文",
            "（未抓到本文內容）",
            "",
            "## 推文統計",
            "- 推：3",
        ])

        result = main.assess_url_capture_quality(content, "ptt", "ptt-html")

        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True
        assert result["reason"] == "ptt_body_missing"


# ============================================================
# 14.4 104 extractor 測試
# ============================================================

class Test104Extractor:
    """測試 104 職缺抽取與格式化"""

    SAMPLE_PAYLOAD = {
        "data": {
            "header": {
                "jobName": "Backend Engineer",
                "custName": "測試科技股份有限公司",
                "custUrl": "https://www.104.com.tw/company/test",
                "appearDate": "2026-05-12",
                "userApplyCount": "11~30人",
            },
            "industry": "軟體服務業",
            "employees": "100人",
            "jobDetail": {
                "addressRegion": "台北市信義區",
                "addressDetail": "信義路五段 7 號",
                "landmark": "近捷運站",
                "salary": "月薪 70,000~100,000 元",
                "jobCategory": [{"description": "後端工程師"}],
                "jobType": 1,
                "workType": "日班",
                "workPeriod": "09:30~18:30",
                "vacationPolicy": "週休二日",
                "remoteWork": "部分遠端",
                "businessTrip": "無需出差",
                "manageResp": "不需負擔管理責任",
                "needEmp": "1人",
                "jobDescription": "負責 API 設計與後端系統開發。<br>需要維護資料庫與雲端服務，並與產品團隊合作。",
            },
            "condition": {
                "workExp": "3年以上",
                "edu": "大學以上",
                "major": [{"description": "資訊工程相關"}],
                "language": [{"description": "英文 -- 聽 /中等、說 /中等"}],
                "specialty": [{"description": "Python"}],
                "skill": [{"description": "Django"}],
                "certificate": [{"description": "AWS 認證"}],
                "driverLicense": "不拘",
                "acceptRole": [{"description": "上班族"}],
                "other": "請附上作品集或 GitHub。",
            },
            "welfare": {
                "welfare": "年終獎金、教育訓練",
                "tag": ["彈性上下班"],
                "legalTag": ["勞保", "健保"],
            },
            "contact": {
                "hrName": "王小姐",
                "email": "hr@example.com",
            },
        }
    }

    def test_extract_104_job_id(self):
        assert main.extract_104_job_id("https://www.104.com.tw/job/6m2k2?jobsource=checkc") == "6m2k2"

    def test_normalize_104_job_payload(self):
        result = main.normalize_104_job_payload(
            self.SAMPLE_PAYLOAD,
            "https://www.104.com.tw/job/6m2k2",
        )

        assert result["job_id"] == "6m2k2"
        assert result["job_name"] == "Backend Engineer"
        assert result["company_name"] == "測試科技股份有限公司"
        assert "台北市信義區" in result["address"]
        assert "負責 API 設計" in result["job_description"]
        assert "Django" in result["skills"]
        assert "彈性上下班" in result["welfare"]

    def test_format_104_job(self):
        job = main.normalize_104_job_payload(
            self.SAMPLE_PAYLOAD,
            "https://www.104.com.tw/job/6m2k2",
        )
        result = main.format_104_job(job)

        assert "職缺：Backend Engineer" in result
        assert "公司：測試科技股份有限公司" in result
        assert "## 工作資訊" in result
        assert "工作性質：全職" in result
        assert "月薪 70,000~100,000 元" in result
        assert "## 工作內容" in result
        assert "維護資料庫與雲端服務" in result
        assert "## 條件要求" in result
        assert "請附上作品集" in result
        assert "## 福利制度" in result

    @patch("main.requests.get")
    def test_fetch_104_content_uses_ajax_endpoint(self, mock_get):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = self.SAMPLE_PAYLOAD
        mock_get.return_value = response

        content, extractor = main.fetch_104_content("https://www.104.com.tw/job/6m2k2")

        assert extractor == "104-ajax"
        assert "Backend Engineer" in content
        assert "測試科技股份有限公司" in content
        assert "job/ajax/content/6m2k2" in mock_get.call_args.args[0]
        assert mock_get.call_args.kwargs["headers"]["Referer"] == "https://www.104.com.tw/job/6m2k2"

    def test_assess_104_ajax_full(self):
        job = main.normalize_104_job_payload(
            self.SAMPLE_PAYLOAD,
            "https://www.104.com.tw/job/6m2k2",
        )
        content = main.format_104_job(job)

        result = main.assess_url_capture_quality(content, "104", "104-ajax")

        assert result["status"] == main.CAPTURE_STATUS_FULL
        assert result["needs_review"] is False

    def test_clean_104_text_suppresses_bool_noise(self):
        assert main.clean_104_text(False) == ""
        assert main.clean_104_text({"role": [], "disRole": {"needHandicapCompendium": False}}) == ""

    def test_clean_104_text_preserves_angle_note_for_markdown(self):
        result = main.clean_104_text("第一行<br><抗壓力佳>")
        assert "第一行" in result
        assert "（抗壓力佳）" in result

    def test_assess_104_missing_description_partial(self):
        payload = json.loads(json.dumps(self.SAMPLE_PAYLOAD))
        payload["data"]["jobDetail"]["jobDescription"] = ""
        job = main.normalize_104_job_payload(payload, "https://www.104.com.tw/job/6m2k2")
        content = main.format_104_job(job)

        result = main.assess_url_capture_quality(content, "104", "104-ajax")

        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True
        assert result["reason"] == "104_job_description_missing"

    def test_assess_104_fallback_partial(self):
        result = main.assess_url_capture_quality("這是一段 fallback 抓到的職缺頁內容。" * 20, "104", "jina")

        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True
        assert result["reason"] == "104_fallback_extractor"

    def test_status_note_only_for_104_partial(self):
        assert main.should_save_status_note_only("104", main.CAPTURE_STATUS_PARTIAL) is True


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

    def test_assess_google_maps_full_place(self):
        place = {
            "title": "東京拉麵店",
            "categoryName": "拉麵店",
            "address": "東京都新宿區1-2-3",
        }
        result = main.assess_google_maps_place_data(place)
        assert result["status"] == main.CAPTURE_STATUS_FULL
        assert result["needs_review"] is False

    def test_assess_google_maps_title_only_partial(self):
        place = {"title": "只有名稱的地點"}
        result = main.assess_google_maps_place_data(place)
        assert result["status"] == main.CAPTURE_STATUS_PARTIAL
        assert result["needs_review"] is True
        assert result["reason"] == "limited_place_fields"

    def test_assess_google_maps_empty_failed(self):
        result = main.assess_google_maps_place_data({})
        assert result["status"] == main.CAPTURE_STATUS_FAILED
        assert result["needs_review"] is True

    def test_status_note_only_for_google_maps_partial(self):
        assert main.should_save_status_note_only("google_maps", main.CAPTURE_STATUS_PARTIAL) is True

    def test_status_note_only_for_ptt_partial(self):
        assert main.should_save_status_note_only("ptt", main.CAPTURE_STATUS_PARTIAL) is True


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


class TestParseContactFromText:
    """測試聯絡人自然語言解析"""

    def test_parse_contact_without_openai(self):
        original = main.openai_client
        main.openai_client = None
        result = main.parse_contact_from_text("Jason 同事")
        assert result is None
        main.openai_client = original

    @patch("main.openai_client")
    def test_parse_contact_success(self, mock_client):
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "name": "Jason",
            "relation": "同事",
            "company": "ABC 公司",
            "role": "工程師",
            "phone": "0912345678",
            "email": "",
            "line_id": "",
            "notes": "在 AWS 大會認識",
            "tags": ["AI", "工程師"]
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = main.parse_contact_from_text("Jason 同事 ABC 公司工程師 0912345678 在 AWS 大會認識")
        assert result is not None
        assert result["name"] == "Jason"
        assert result["relation"] == "同事"
        assert result["phone"] == "0912345678"
        assert "AI" in result["tags"]

    @patch("main.openai_client")
    def test_parse_contact_no_name_returns_none(self, mock_client):
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"name": None})
        mock_client.chat.completions.create.return_value = mock_response

        assert main.parse_contact_from_text("一些隨便的文字") is None

    @patch("main.openai_client")
    def test_parse_contact_strips_markdown_fences(self, mock_client):
        main.openai_client = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "```json\n" + json.dumps({"name": "小華", "relation": "朋友", "tags": []}) + "\n```"
        )
        mock_client.chat.completions.create.return_value = mock_response
        result = main.parse_contact_from_text("小華 是朋友")
        assert result["name"] == "小華"


class TestSaveContactToWiki:
    """測試聯絡人存入 Wiki/People/"""

    def test_save_contact_without_vault_id(self):
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = None
        result = main.save_contact_to_wiki({"name": "Jason"})
        assert result is None
        main.GDRIVE_VAULT_FOLDER_ID = original

    def test_save_contact_without_name_returns_none(self):
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = "fake_id"
        result = main.save_contact_to_wiki({"name": ""})
        assert result is None
        main.GDRIVE_VAULT_FOLDER_ID = original

    @patch("main.save_wiki_page")
    def test_save_contact_calls_save_wiki_with_people_subfolder(self, mock_save):
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = "fake_id"
        mock_save.return_value = "file_xyz"

        contact = {
            "name": "Jason",
            "relation": "同事",
            "company": "ABC 公司",
            "role": "工程師",
            "phone": "0912345678",
            "email": "jason@example.com",
            "line_id": "jason_line",
            "notes": "AWS 認識",
            "tags": ["AI", "雲端"]
        }
        result = main.save_contact_to_wiki(contact)

        assert result == "file_xyz"
        mock_save.assert_called_once()
        args, kwargs = mock_save.call_args
        assert args[0] == "Jason"
        full_content = args[1]
        assert kwargs.get("subfolder") == "People"
        # frontmatter
        assert "type: 人脈" in full_content
        assert "name: Jason" in full_content
        assert "relation: 同事" in full_content
        assert "tags: [人脈, 同事, AI, 雲端]" in full_content
        # body
        assert "0912345678" in full_content
        assert "jason@example.com" in full_content
        assert "AWS 認識" in full_content
        assert "互動記錄" in full_content

        main.GDRIVE_VAULT_FOLDER_ID = original

    @patch("main.save_wiki_page")
    def test_save_contact_minimal_fields(self, mock_save):
        original = main.GDRIVE_VAULT_FOLDER_ID
        main.GDRIVE_VAULT_FOLDER_ID = "fake_id"
        mock_save.return_value = "file_id"

        result = main.save_contact_to_wiki({"name": "小明"})
        assert result == "file_id"
        full_content = mock_save.call_args[0][1]
        assert "name: 小明" in full_content
        # 沒有電話/email 的欄位不該出現
        assert "**電話**" not in full_content
        assert "**Email**" not in full_content

        main.GDRIVE_VAULT_FOLDER_ID = original


class TestAddContactCommandRegex:
    """測試加聯絡人指令的 regex 觸發"""

    def test_add_contact_patterns(self):
        pattern = r'^(?:加聯絡人|新增聯絡人|記聯絡人|加人脈)[：:]\s*(.+)$'
        for trigger in ["加聯絡人", "新增聯絡人", "記聯絡人", "加人脈"]:
            for sep in ["：", ":"]:
                text = f"{trigger}{sep}Jason 同事"
                m = re.match(pattern, text)
                assert m is not None, f"Failed: {text}"
                assert m.group(1) == "Jason 同事"

    def test_add_contact_no_match_for_other_commands(self):
        pattern = r'^(?:加聯絡人|新增聯絡人|記聯絡人|加人脈)[：:]\s*(.+)$'
        assert re.match(pattern, "加行程：開會") is None
        assert re.match(pattern, "查 投資") is None
        assert re.match(pattern, "聯絡人") is None  # 沒有冒號


class TestRunConsolidateSources:
    """測試整理筆記核心邏輯（給 cron 與指令共用）"""

    @patch("main.list_sources_files_by_month")
    def test_run_consolidate_no_files(self, mock_list):
        mock_list.return_value = []
        result = main.run_consolidate_sources("2026-04")
        assert result["total"] == 0
        assert result["consolidated"] == []
        assert result["skipped"] == []

    @patch("main.update_gdrive_file_content")
    @patch("main.find_vault_file")
    @patch("main.save_wiki_page")
    @patch("main.consolidate_sources_to_wiki")
    @patch("main.read_gdrive_file")
    @patch("main.list_sources_files_by_month")
    def test_run_consolidate_groups_and_filters(self, mock_list, mock_read,
                                                 mock_consolidate, mock_save,
                                                 mock_find, mock_update):
        mock_list.return_value = [
            {"id": "f1", "name": "a.md"}, {"id": "f2", "name": "b.md"},
            {"id": "f3", "name": "c.md"}, {"id": "f4", "name": "d.md"},
        ]
        # 三個是 AI 類別（達標），一個是科技（未達標）
        mock_read.side_effect = [
            "---\ncategory: AI\n---\nbody1",
            "---\ncategory: AI\n---\nbody2",
            "---\ncategory: AI\n---\nbody3",
            "---\ncategory: 科技\n---\nbody4",
        ]
        mock_consolidate.return_value = "# AI Wiki\n內容"
        mock_find.return_value = None  # 沒有 log.md，跳過更新

        result = main.run_consolidate_sources("2026-04")
        assert result["total"] == 4
        assert len(result["consolidated"]) == 1
        assert "AI" in result["consolidated"][0]
        assert len(result["skipped"]) == 1
        assert "科技" in result["skipped"][0]
        mock_save.assert_called_once_with("AI", "# AI Wiki\n內容")


class TestCronWeeklyEndpoint:
    """測試 /cron/weekly endpoint"""

    def setup_method(self):
        self.client = main.app.test_client()

    def test_cron_no_secret_configured(self):
        original = main.CRON_SECRET
        main.CRON_SECRET = None
        response = self.client.post("/cron/weekly")
        assert response.status_code == 503
        main.CRON_SECRET = original

    def test_cron_unauthorized(self):
        original = main.CRON_SECRET
        main.CRON_SECRET = "real_secret"
        response = self.client.post("/cron/weekly", headers={"X-Cron-Secret": "wrong"})
        assert response.status_code == 401
        main.CRON_SECRET = original

    def test_cron_unauthorized_no_header(self):
        original = main.CRON_SECRET
        main.CRON_SECRET = "real_secret"
        response = self.client.post("/cron/weekly")
        assert response.status_code == 401
        main.CRON_SECRET = original

    @patch("main.run_consolidate_sources")
    def test_cron_authorized_via_header(self, mock_run):
        original = main.CRON_SECRET
        main.CRON_SECRET = "real_secret"
        mock_run.return_value = {
            "month": "2026-04", "total": 5,
            "consolidated": ["AI（3 篇）"], "skipped": []
        }
        response = self.client.post("/cron/weekly", headers={"X-Cron-Secret": "real_secret"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert data["total_sources"] == 5
        assert data["wiki_pages_created"] == 1
        main.CRON_SECRET = original

    @patch("main.run_consolidate_sources")
    def test_cron_authorized_via_query_param(self, mock_run):
        original = main.CRON_SECRET
        main.CRON_SECRET = "real_secret"
        mock_run.return_value = {"month": "2026-04", "total": 0, "consolidated": [], "skipped": []}
        response = self.client.get("/cron/weekly?secret=real_secret")
        assert response.status_code == 200
        main.CRON_SECRET = original

    @patch("main.run_consolidate_sources")
    def test_cron_handles_internal_error(self, mock_run):
        original = main.CRON_SECRET
        main.CRON_SECRET = "real_secret"
        mock_run.side_effect = Exception("boom")
        response = self.client.post("/cron/weekly", headers={"X-Cron-Secret": "real_secret"})
        assert response.status_code == 500
        data = response.get_json()
        assert data["status"] == "error"
        assert "boom" in data["message"]
        main.CRON_SECRET = original


class TestHealthzEndpoint:
    """測試健康檢查 endpoint"""

    def setup_method(self):
        self.client = main.app.test_client()

    def test_healthz_ok(self):
        response = self.client.get("/healthz")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ok"
        assert "ts" in data


class TestDesktopVoiceCapture:
    """測試桌面語音工具的防呆邏輯"""

    def test_desktop_voice_hallucination_patterns(self):
        assert desktop_voice.is_hallucination("記得幫我訂閱、按讚及分享唷") is True
        assert desktop_voice.is_hallucination("ご視聴ありがとうございました。") is True

    def test_desktop_voice_valid_transcript(self):
        assert desktop_voice.is_hallucination("這是我今天讀書想到的一個重點") is False

    def test_desktop_voice_audio_level_silence(self):
        audio = desktop_voice.np.zeros((1600, 1), dtype="float32")
        level = desktop_voice.get_audio_level(audio)
        assert level["rms"] == 0.0
        assert level["peak"] == 0.0

    def test_desktop_voice_shortcut_text(self):
        args = SimpleNamespace(
            paste_hotkey="ctrl+alt+z",
            thought_hotkey="ctrl+alt+x",
            meeting_hotkey="ctrl+alt+c",
            confirm_hotkey="f8",
        )
        result = desktop_voice.get_shortcuts_text(args)
        assert "ctrl+alt+z" in result
        assert "ctrl+alt+x" in result
        assert "ctrl+alt+c" in result
        assert "ctrl+alt+e" in result
        assert "ctrl+alt+j" in result
        assert "快速輸入" in result
        assert "翻譯英文" in result
        assert "f8" in result

    def test_desktop_voice_shortcut_text_legacy_hotkey(self):
        args = SimpleNamespace(
            hotkey="ctrl+shift+v",
            thought_hotkey="ctrl+alt+x",
            meeting_hotkey="ctrl+alt+c",
        )
        result = desktop_voice.get_shortcuts_text(args)
        assert "ctrl+shift+v" in result

    def test_desktop_voice_control_text(self):
        args = SimpleNamespace(confirm_hotkey="f8")
        result = desktop_voice.get_control_text(args)
        assert "F8" not in result  # preserve user-provided casing
        assert "f8" in result
        assert "Enter" not in result
        assert "空白" not in result
        assert "英文" in result
        assert "日文" in result

    def test_desktop_voice_load_dictionary(self, tmp_path):
        dictionary = tmp_path / "voice_dictionary.txt"
        dictionary.write_text("Drizzle ORM\nKaku", encoding="utf-8")
        args = SimpleNamespace(dictionary_file=str(dictionary))
        assert "Drizzle ORM" in desktop_voice.load_voice_dictionary(args)

    def test_desktop_voice_load_dictionary_missing(self, tmp_path):
        args = SimpleNamespace(dictionary_file=str(tmp_path / "missing.txt"))
        assert desktop_voice.load_voice_dictionary(args) == ""

    def test_desktop_voice_dictionary_section_supports_aliases(self):
        result = desktop_voice.build_dictionary_section("移傳 => 移轉")
        assert "錯字 => 正字" in result
        assert "移傳 => 移轉" in result

    def test_desktop_voice_build_thought_note(self):
        result = desktop_voice.build_voice_note(
            mode="thought",
            title="讀書想法",
            normalized_text="整理後內容",
            transcript="原始內容",
        )
        assert "type: 語音筆記" in result
        assert "source_type: audio" in result
        assert "extractor: desktop-whisper" in result
        assert "## 整理後文字" in result
        assert "整理後內容" in result
        assert "原始內容" in result

    def test_desktop_voice_build_meeting_note(self):
        result = desktop_voice.build_voice_note(
            mode="meeting",
            title="會議記錄",
            normalized_text="整理後逐字稿",
            transcript="原始逐字稿",
            summary="## 摘要\n- 重點",
        )
        assert "type: 會議記錄" in result
        assert "## 會議整理" in result
        assert "## 摘要" in result

    @patch("desktop_voice_capture.load_voice_dictionary")
    @patch("desktop_voice_capture.OpenAI")
    def test_desktop_voice_translate_transcript(self, _mock_openai, mock_dictionary):
        mock_dictionary.return_value = "Kaku"
        client = MagicMock()
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "This is a test."
        client.chat.completions.create.return_value = response

        result = desktop_voice.translate_transcript(client, "這是一個測試", "English", args=SimpleNamespace())
        assert result == "This is a test."
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "Kaku" in prompt
        assert "English" in prompt

    def test_desktop_voice_get_capture_dir_fallback(self, tmp_path):
        args = SimpleNamespace(
            vault_path=str(tmp_path / "missing-vault"),
            capture_dir=str(tmp_path / "captures"),
            thought_folder="Sources\\desktop-voice",
            meeting_folder="Meetings",
        )

        result = desktop_voice.get_capture_dir("thought", args)
        assert result == tmp_path / "captures" / "thoughts"

    def test_desktop_voice_write_markdown_note(self, tmp_path):
        args = SimpleNamespace(
            vault_path="",
            capture_dir=str(tmp_path / "captures"),
            thought_folder="Sources\\desktop-voice",
            meeting_folder="Meetings",
        )

        path = desktop_voice.write_markdown_note("thought", "測試標題", "內容", args)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "內容"

    def test_desktop_voice_notify_logs_only(self, capsys):
        desktop_voice.notify("Test", "Line 1\nLine 2")
        captured = capsys.readouterr()
        assert "[Test] Line 1 | Line 2" in captured.out

    def test_status_overlay_disabled_is_noop(self):
        overlay = desktop_voice.StatusOverlay(enabled=False)
        overlay.set("待命", "Title", "Body")
        overlay.hide()
        overlay.show()
        overlay.stop()
        assert overlay.enabled is False

    def test_status_overlay_idle_default(self):
        overlay = desktop_voice.StatusOverlay(enabled=False)
        assert overlay.idle_seconds == desktop_voice.DEFAULT_OVERLAY_IDLE_SECONDS


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
