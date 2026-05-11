"""Windows desktop voice capture helper.

Desktop voice capture helper:

    python desktop_voice_capture.py --listen
    python desktop_voice_capture.py --once

Default mode hotkeys choose an output mode. Press Space after choosing a mode to
start recording, then press Space again to stop and output.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

try:
    import tkinter as tk
except ImportError:
    tk = None

try:
    import keyboard
    import numpy as np
    import pyperclip
    import sounddevice as sd
    import soundfile as sf
except ImportError as exc:
    missing_import = exc
else:
    missing_import = None


DEFAULT_SAMPLE_RATE = 16000
DEFAULT_HOTKEY = "ctrl+alt+z"
DEFAULT_THOUGHT_HOTKEY = "ctrl+alt+x"
DEFAULT_MEETING_HOTKEY = "ctrl+alt+c"
DEFAULT_TRANSLATE_EN_HOTKEY = "ctrl+alt+e"
DEFAULT_TRANSLATE_JA_HOTKEY = "ctrl+alt+j"
DEFAULT_CONFIRM_HOTKEY = "space"
DEFAULT_MIN_RMS = 0.003
DEFAULT_MIN_PEAK = 0.015
DEFAULT_VAULT_PATH = r"G:\我的雲端硬碟\ObsidianVault"
DEFAULT_CAPTURE_DIR = "desktop-captures"
DEFAULT_OVERLAY_IDLE_SECONDS = 5.0
DEFAULT_OVERLAY_FONT_SCALE = 1.15
DEFAULT_DICTIONARY_FILE = "voice_dictionary.txt"
DEFAULT_CONFIG_FILE = "desktop_voice_config.json"
DEFAULT_HISTORY_FILE = str(Path(DEFAULT_CAPTURE_DIR) / "history.jsonl")
MANAGER_SCRIPT = "desktop_voice_manager.py"

MODE_LABELS = {
    "paste": "快速輸入",
    "thought": "語音思考",
    "meeting": "會議記錄",
    "translate_en": "翻譯英文",
    "translate_ja": "翻譯日文",
}

MODE_HOTKEYS = {
    "paste": DEFAULT_HOTKEY,
    "thought": DEFAULT_THOUGHT_HOTKEY,
    "meeting": DEFAULT_MEETING_HOTKEY,
    "translate_en": DEFAULT_TRANSLATE_EN_HOTKEY,
    "translate_ja": DEFAULT_TRANSLATE_JA_HOTKEY,
}

MODE_CHOICES = list(MODE_LABELS.keys())
TRANSLATION_TARGETS = {
    "translate_en": "English",
    "translate_ja": "Japanese",
}

HALLUCINATION_PATTERNS = [
    "请不吝点赞",
    "點贊訂閱",
    "订阅转发",
    "訂閱轉發",
    "感謝觀看",
    "感谢观看",
    "謝謝收看",
    "谢谢收看",
    "歡迎訂閱",
    "欢迎订阅",
    "訂閱、按讚",
    "订阅、点赞",
    "訂閱、按讚及分享",
    "按讚及分享",
    "幫我訂閱",
    "影片就到這",
    "like and subscribe",
    "thanks for watching",
    "ご視聴ありがとうございました",
    "字幕由",
    "字幕提供",
    "subtitles by",
    "amara.org",
]


def get_audio_level(audio) -> dict:
    if audio is None or len(audio) == 0:
        return {"rms": 0.0, "peak": 0.0}
    rms = float(np.sqrt(np.mean(np.square(audio))))
    peak = float(np.max(np.abs(audio)))
    return {"rms": rms, "peak": peak}


def is_hallucination(text: str) -> bool:
    if not text or not text.strip():
        return True
    text_lower = text.lower().strip()
    for pattern in HALLUCINATION_PATTERNS:
        if pattern.lower() in text_lower:
            return True
    if len(text_lower) < 5:
        return True
    words = text_lower.split()
    if len(words) > 2 and len(set(words)) == 1:
        return True
    if re.fullmatch(r'[\s。．.、，,!?！？嗯啊呃喔哦]+', text_lower):
        return True
    return False


def beep(kind: str) -> None:
    try:
        import winsound
        tones = {
            "start": (880, 120),
            "stop": (660, 120),
            "done": (1040, 100),
            "error": (330, 180),
        }
        frequency, duration = tones.get(kind, tones["done"])
        winsound.Beep(frequency, duration)
    except Exception:
        pass


def notify(title: str, message: str) -> None:
    print(f"[{title}] {message.replace(chr(10), ' | ')}")


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "none", ""}:
        return False
    return default


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result == 0:
            return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def apply_tk_scaling(root, scale: float = 1.0) -> None:
    try:
        base_scaling = root.winfo_fpixels("1i") / 72.0
        root.tk.call("tk", "scaling", base_scaling * scale)
    except Exception:
        pass


def launch_desktop_voice_manager() -> None:
    base_dir = Path(__file__).resolve().parent
    manager_path = base_dir / MANAGER_SCRIPT
    if not manager_path.exists():
        notify("Desktop Voice Capture", f"找不到設定管理器：{manager_path}")
        return
    if focus_window_by_title("Desktop Voice Manager"):
        notify("Desktop Voice Capture", "已切換到設定管理器。")
        return
    candidates = [
        base_dir / ".venv" / "Scripts" / "pythonw.exe",
        base_dir / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    python_path = next((candidate for candidate in candidates if candidate.exists()), Path(sys.executable))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [str(python_path), str(manager_path)],
            cwd=str(base_dir),
            creationflags=creationflags,
        )
        notify("Desktop Voice Capture", "已開啟設定管理器。")
    except Exception as exc:
        notify("Desktop Voice Capture", f"設定管理器開啟失敗：{exc}")


def focus_window_by_title(title: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if not hwnd:
            return False
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def load_voice_config(config_file: str | None) -> dict:
    if not config_file:
        return {}
    path = Path(config_file).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Voice config load failed: {exc}")
        return {}
    if not isinstance(data, dict):
        print("Voice config ignored: root value must be an object.")
        return {}
    return data


def get_mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)


def get_confirm_hotkey(args=None) -> str:
    value = getattr(args, "confirm_hotkey", DEFAULT_CONFIRM_HOTKEY) if args else DEFAULT_CONFIRM_HOTKEY
    value = (value or "").strip()
    if value.lower() in {"0", "false", "no", "none", "off", "disable", "disabled"}:
        return ""
    return value


def format_hotkey_label(hotkey: str) -> str:
    labels = {
        "space": "空白鍵",
        "enter": "Enter",
        "esc": "Esc",
        "ctrl": "Ctrl",
        "alt": "Alt",
        "shift": "Shift",
    }
    parts = [part.strip() for part in hotkey.split("+") if part.strip()]
    return "+".join(labels.get(part.lower(), part) for part in parts)


def get_shortcuts_text(args) -> str:
    paste_hotkey = getattr(args, "paste_hotkey", getattr(args, "hotkey", DEFAULT_HOTKEY))
    thought_hotkey = getattr(args, "thought_hotkey", DEFAULT_THOUGHT_HOTKEY)
    meeting_hotkey = getattr(args, "meeting_hotkey", DEFAULT_MEETING_HOTKEY)
    translate_en_hotkey = getattr(args, "translate_en_hotkey", DEFAULT_TRANSLATE_EN_HOTKEY)
    translate_ja_hotkey = getattr(args, "translate_ja_hotkey", DEFAULT_TRANSLATE_JA_HOTKEY)
    lines = [
        f"{paste_hotkey} 快速輸入",
        f"{thought_hotkey} 語音思考",
        f"{meeting_hotkey} 會議記錄",
        f"{translate_en_hotkey} 翻譯英文",
        f"{translate_ja_hotkey} 翻譯日文",
        "重按目前模式快捷鍵開始/停止",
    ]
    confirm_hotkey = get_confirm_hotkey(args)
    if confirm_hotkey:
        lines.append(f"{format_hotkey_label(confirm_hotkey)} 開始/停止")
    return "\n".join(lines)


def get_control_text(args=None) -> str:
    lines = ["再按目前模式快捷鍵開始/停止"]
    confirm_hotkey = get_confirm_hotkey(args)
    if confirm_hotkey:
        lines.append(f"{format_hotkey_label(confirm_hotkey)} 開始或停止")
    lines.extend(
        [
            "1 快速輸入",
            "2 語音思考",
            "3 會議記錄",
            "4 英文翻譯",
            "5 日文翻譯",
            "Esc 隱藏或取消",
        ]
    )
    return "\n".join(lines)


def load_voice_dictionary(args) -> str:
    dictionary_file = getattr(args, "dictionary_file", None) or os.getenv("VOICE_DICTIONARY_FILE") or DEFAULT_DICTIONARY_FILE
    path = Path(dictionary_file).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        print(f"Dictionary load failed: {exc}")
        return ""


def build_dictionary_section(dictionary_text: str, extra_instruction: str = "") -> str:
    if not dictionary_text:
        return ""
    instruction = extra_instruction or "請優先保留上述名稱、品牌、技術詞與大小寫。"
    return f"""

個人字典與專有名詞：
{dictionary_text}

字典規則：
- 一行一個詞時，請優先保留該詞。
- 如果出現「錯字 => 正字」或「錯字 -> 正字」，請把左邊修正成右邊。
- 如果出現「詞：說明」，請把說明當成語境提示，不要輸出說明本身。
- 如果有簡短說明，請依照說明判斷語境，不要把相近音詞誤用，也不要機械套用到無關語境。
{instruction}"""


def safe_filename(text: str, fallback: str = "voice") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\r\n\t]+', " ", text or "").strip()
    cleaned = re.sub(r'\s+', " ", cleaned)
    cleaned = re.sub(r'[^\w\s\u4e00-\u9fff-]', "", cleaned)
    return (cleaned[:40].strip() or fallback)


def get_vault_path(args) -> Path | None:
    raw_path = args.vault_path or os.getenv("OBSIDIAN_VAULT_PATH") or DEFAULT_VAULT_PATH
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    return path if path.exists() else None


def get_capture_dir(mode: str, args) -> Path:
    vault_path = get_vault_path(args)
    if vault_path:
        if mode == "meeting":
            return vault_path / args.meeting_folder
        return vault_path / args.thought_folder
    folder_name = "meetings" if mode == "meeting" else "thoughts"
    return Path(args.capture_dir).expanduser() / folder_name


def write_markdown_note(mode: str, title: str, body: str, args) -> Path:
    output_dir = get_capture_dir(mode, args)
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{safe_filename(title, mode)}.md"
    path = output_dir / filename
    path.write_text(body, encoding="utf-8")
    return path


def get_history_file(args) -> Path | None:
    raw_path = getattr(args, "history_file", None)
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def append_history(args, event: dict) -> None:
    history_file = get_history_file(args)
    if not history_file:
        return
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **event,
        }
        with history_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"History write failed: {exc}")


def build_voice_note(
    mode: str,
    title: str,
    normalized_text: str,
    transcript: str,
    summary: str = "",
) -> str:
    now = datetime.now()
    mode_label = get_mode_label(mode)
    parts = [
        "---",
        f"date: {now.strftime('%Y-%m-%d')}",
        f"type: {'會議記錄' if mode == 'meeting' else '語音筆記'}",
        f"source_type: audio",
        "capture_status: full",
        "extractor: desktop-whisper",
        "needs_review: false",
        f"mode: {mode}",
        "---",
        "",
        f"# {title}",
        "",
        "## 模式",
        mode_label,
        "",
    ]
    if summary:
        parts.extend(["## 會議整理", summary.strip(), ""])
    parts.extend([
        "## 整理後文字",
        normalized_text.strip(),
        "",
        "## 原始逐字稿",
        transcript.strip(),
        "",
    ])
    return "\n".join(parts)


def summarize_meeting(client: OpenAI, transcript: str) -> str:
    if not transcript.strip():
        return ""
    prompt = f"""請把以下會議逐字稿整理成繁體中文會議記錄。

請使用以下格式：

## 摘要
- [3-5 點重點]

## 決議
- [如果沒有明確決議，寫「未明確提到」]

## 待辦
- [負責人/事項/期限，如果無法判斷就寫「未明確提到」]

## 風險或待確認
- [需要後續釐清的地方]

逐字稿：
{transcript}
"""
    try:
        response = client.chat.completions.create(
            model=os.getenv("VOICE_MEETING_SUMMARY_MODEL", os.getenv("VOICE_NORMALIZE_MODEL", "gpt-4.1-mini")),
            messages=[
                {"role": "system", "content": "你是可靠的會議記錄整理助手，只根據逐字稿整理，不要補不存在的決議。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1800,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"Meeting summary failed: {exc}")
        return ""


def translate_transcript(client: OpenAI, text: str, target_language: str, args=None) -> str:
    if not text.strip():
        return ""
    dictionary_text = load_voice_dictionary(args) if args else ""
    dictionary_section = build_dictionary_section(
        dictionary_text,
        extra_instruction="專有名詞、人名、品牌名請依照字典保留、修正或音譯。",
    )
    prompt = f"""請把以下語音輸入整理後翻譯成 {target_language}。

規則：
- 移除不影響意思的贅詞、重複語句與口語修正指令。
- 保留原意，不要新增原文沒有的資訊。
- 如果內容適合條列，請自動整理成條列。
- 只輸出翻譯後文字，不要解釋。
{dictionary_section}

文字：
{text}
"""
    try:
        response = client.chat.completions.create(
            model=os.getenv("VOICE_TRANSLATE_MODEL", os.getenv("VOICE_NORMALIZE_MODEL", "gpt-4.1-mini")),
            messages=[
                {"role": "system", "content": "你是精準的語音輸入翻譯助手，輸出自然、可直接貼上的文字。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1400,
            temperature=0.2,
        )
        translated = response.choices[0].message.content.strip()
        return translated or text
    except Exception as exc:
        print(f"Translate failed, using normalized transcript: {exc}")
        return text


class StatusOverlay:
    def __init__(
        self,
        enabled: bool = True,
        idle_seconds: float = DEFAULT_OVERLAY_IDLE_SECONDS,
        font_scale: float = DEFAULT_OVERLAY_FONT_SCALE,
    ) -> None:
        self.enabled = enabled and tk is not None
        self.queue: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self.idle_seconds = max(0.0, idle_seconds)
        self.font_scale = max(0.8, min(2.0, font_scale))
        self.root = None
        self.title_var = None
        self.body_var = None
        self.status_var = None
        self.hide_after_id = None
        self.is_hidden = False
        self.ui_thread_id = None

    def start(self, initial_title: str, initial_body: str) -> None:
        if not self.enabled:
            return
        self.ui_thread_id = threading.get_ident()
        enable_windows_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("Desktop Voice Capture")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg="#111827")
        apply_tk_scaling(self.root, self.font_scale)

        frame = tk.Frame(self.root, bg="#111827", padx=22, pady=18)
        frame.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="待命")
        self.title_var = tk.StringVar(value=initial_title)
        self.body_var = tk.StringVar(value=initial_body)

        header = tk.Frame(frame, bg="#111827")
        header.pack(fill="x")
        tk.Label(header, textvariable=self.status_var, bg="#111827", fg="#93c5fd",
                 font=("Microsoft JhengHei UI", 11, "bold")).pack(side="left", anchor="w")
        tk.Button(header, text="設定", command=launch_desktop_voice_manager, bg="#1f2937", fg="#d1d5db",
                  activebackground="#374151", activeforeground="#ffffff",
                  relief="flat", padx=10, pady=3,
                  font=("Microsoft JhengHei UI", 10)).pack(side="right", padx=(6, 0))
        tk.Button(header, text="隱藏", command=self.hide, bg="#1f2937", fg="#d1d5db",
                  activebackground="#374151", activeforeground="#ffffff",
                  relief="flat", padx=10, pady=3,
                  font=("Microsoft JhengHei UI", 10)).pack(side="right")
        tk.Label(frame, textvariable=self.title_var, bg="#111827", fg="#f9fafb",
                 font=("Microsoft JhengHei UI", 17, "bold")).pack(anchor="w", pady=(8, 4), fill="x")
        tk.Label(frame, textvariable=self.body_var, bg="#111827", fg="#d1d5db",
                 justify="left", wraplength=640,
                 font=("Microsoft JhengHei UI", 13)).pack(anchor="w", fill="x")

        self.root.update_idletasks()
        self._position_window()
        self.root.after(120, self._drain_queue)
        self.root.mainloop()

    def set(self, status: str, title: str, body: str) -> None:
        if not self.enabled:
            return
        self.queue.put((status, title, body))

    def hide(self) -> None:
        if not self.enabled or not self.root:
            return
        if threading.get_ident() != self.ui_thread_id:
            self.queue.put(("__hide__", "", ""))
            return
        self._hide_now()

    def _hide_now(self) -> None:
        if not self.root:
            return
        self._cancel_hide_timer()
        self.root.withdraw()
        self.is_hidden = True

    def show(self) -> None:
        if not self.enabled or not self.root:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.is_hidden = False
        self._position_window()

    def stop(self) -> None:
        if self.enabled and self.root:
            self.root.after(0, self.root.destroy)

    def _position_window(self) -> None:
        if not self.root:
            return
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = max(self.root.winfo_reqwidth(), self.root.winfo_width(), 560)
        height = max(self.root.winfo_reqheight(), self.root.winfo_height(), 240)
        width = min(width, screen_w - 48)
        height = min(height, screen_h - 88)
        x = screen_w - width - 24
        y = max(24, screen_h - height - 64)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _drain_queue(self) -> None:
        if not self.root:
            return
        while not self.queue.empty():
            status, title, body = self.queue.get_nowait()
            if status == "__hide__":
                self._hide_now()
                continue
            self.show()
            self.status_var.set(status)
            self.title_var.set(title)
            self.body_var.set(body)
            self.root.update_idletasks()
            self._position_window()
            self._schedule_hide_if_idle(status)
        self.root.after(120, self._drain_queue)

    def _schedule_hide_if_idle(self, status: str) -> None:
        self._cancel_hide_timer()
        if self.idle_seconds <= 0:
            return
        if status in ["待命", "已完成", "已略過"]:
            self.hide_after_id = self.root.after(int(self.idle_seconds * 1000), self.hide)

    def _cancel_hide_timer(self) -> None:
        if self.root and self.hide_after_id:
            try:
                self.root.after_cancel(self.hide_after_id)
            except Exception:
                pass
        self.hide_after_id = None


@dataclass
class Recorder:
    sample_rate: int = DEFAULT_SAMPLE_RATE
    min_rms: float = DEFAULT_MIN_RMS
    min_peak: float = DEFAULT_MIN_PEAK
    frames: list = field(default_factory=list)
    stream: object | None = None
    started_at: float | None = None

    def start(self, mode_label: str, shortcuts_text: str) -> None:
        if self.stream:
            return
        self.frames = []
        self.started_at = time.time()
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()
        beep("start")
        message = f"錄音中：{mode_label}\n再次按目前快捷鍵停止。\n{shortcuts_text}"
        notify("Desktop Voice Capture", message)
        print(message)

    def stop(self) -> str | None:
        if not self.stream:
            return None
        self.stream.stop()
        self.stream.close()
        self.stream = None
        duration = time.time() - (self.started_at or time.time())
        self.started_at = None
        if duration < 0.4 or not self.frames:
            beep("error")
            notify("Desktop Voice Capture", "錄音太短，已略過。")
            print("Recording too short; skipped.")
            return None
        audio = np.concatenate(self.frames, axis=0)
        level = get_audio_level(audio)
        if level["rms"] < self.min_rms and level["peak"] < self.min_peak:
            beep("error")
            notify(
                "Desktop Voice Capture",
                f"沒有偵測到清楚語音，已略過。\nRMS={level['rms']:.5f}, Peak={level['peak']:.5f}",
            )
            print(f"Audio too quiet; skipped. RMS={level['rms']:.5f}, Peak={level['peak']:.5f}")
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        sf.write(tmp.name, audio, self.sample_rate)
        beep("stop")
        notify("Desktop Voice Capture", "錄音已停止，正在轉文字。")
        print(f"Recording saved: {tmp.name}")
        return tmp.name

    def cancel(self) -> None:
        if not self.stream:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.frames = []
        self.started_at = None
        beep("error")
        notify("Desktop Voice Capture", "錄音已取消，沒有輸出。")

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            print(f"Audio status: {status}")
        self.frames.append(indata.copy())


def get_openai_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=api_key)


def transcribe_audio(client: OpenAI, audio_path: str, language: str | None = None) -> str:
    kwargs = {
        "model": os.getenv("VOICE_TRANSCRIBE_MODEL", "whisper-1"),
        "file": open(audio_path, "rb"),
    }
    if language:
        kwargs["language"] = language
    try:
        result = client.audio.transcriptions.create(**kwargs)
    finally:
        kwargs["file"].close()
    return (result.text or "").strip()


def normalize_transcript(client: OpenAI, text: str, args=None) -> str:
    if not text.strip():
        return ""
    dictionary_text = load_voice_dictionary(args) if args else ""
    dictionary_section = build_dictionary_section(dictionary_text)
    prompt = f"""請輕度整理以下語音轉文字結果。

規則：
- 使用原本語言，支援中文、日文、英文混合。
- 修正標點、斷句、明顯贅詞、重複語句與明顯聽寫錯字。
- 如果使用者用「第一點、第二點」、「首先、再來、最後」等口述結構，請自動整理成清楚條列。
- 如果出現「重說一次」、「等一下改成」、「前面那句改成」等口語編輯指令，請依照後面的修正意圖輸出乾淨版本，不要保留編輯指令本身。
- 不要擅自改變法規、證照、專有名詞或數字的意思。
- 只輸出整理後文字，不要解釋。
{dictionary_section}

文字：
{text}
"""
    try:
        response = client.chat.completions.create(
            model=os.getenv("VOICE_NORMALIZE_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": "你是中日英混合語音輸入的輕修正助手。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.2,
        )
        normalized = response.choices[0].message.content.strip()
        return normalized or text
    except Exception as exc:
        print(f"Normalize failed, using raw transcript: {exc}")
        return text


def paste_text(text: str) -> None:
    if not text.strip():
        print("Transcript is empty; nothing pasted.")
        return
    pyperclip.copy(text)
    time.sleep(0.1)
    keyboard.send("ctrl+v")
    beep("done")
    notify("Desktop Voice Capture", "已貼上轉文字內容。")
    print("Transcript pasted.")


def process_audio(audio_path: str, args, mode: str | None = None, overlay: StatusOverlay | None = None) -> None:
    mode = mode or args.mode
    mode_label = get_mode_label(mode)
    client = get_openai_client()
    try:
        if overlay:
            overlay.set("轉文字中", f"正在處理：{mode_label}", "Whisper 轉文字中，請稍候。")
        notify("Desktop Voice Capture", f"正在轉文字：{mode_label}")
        transcript = transcribe_audio(client, audio_path, language=args.language)
        if not transcript:
            beep("error")
            if overlay:
                overlay.set("已略過", "沒有回傳文字", get_shortcuts_text(args))
            notify("Desktop Voice Capture", "Whisper 沒有回傳文字，已略過。")
            print("Whisper returned empty transcript.")
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "skipped",
                "reason": "empty_transcript",
                "transcript": "",
            })
            return
        if is_hallucination(transcript):
            beep("error")
            if overlay:
                overlay.set("已略過", "偵測到空白錄音幻覺", get_shortcuts_text(args))
            notify(
                "Desktop Voice Capture",
                "偵測到可能是空白錄音幻覺文字，已略過，沒有貼上。",
            )
            print(f"Likely hallucination skipped: {transcript}")
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "skipped",
                "reason": "likely_hallucination",
                "transcript": transcript,
            })
            return
        if overlay:
            overlay.set("整理中", f"正在整理：{mode_label}", "輕度修正標點與斷句。")
        output = transcript if args.no_normalize else normalize_transcript(client, transcript, args=args)
        if mode == "paste":
            paste_text(output)
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "pasted",
                "transcript": transcript,
                "output": output,
            })
            if overlay:
                overlay.set("已完成", "已貼上", get_shortcuts_text(args))
        elif mode in TRANSLATION_TARGETS:
            if overlay:
                overlay.set("翻譯中", f"正在輸出：{mode_label}", "整理並翻譯成指定語言。")
            translated = translate_transcript(client, output, TRANSLATION_TARGETS[mode], args=args)
            paste_text(translated)
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "translated",
                "target_language": TRANSLATION_TARGETS[mode],
                "transcript": transcript,
                "output": output,
                "translated": translated,
            })
            if overlay:
                overlay.set("已完成", f"{mode_label}已貼上", get_shortcuts_text(args))
        elif mode == "thought":
            title = output.splitlines()[0][:40] if output.strip() else "語音思考"
            content = build_voice_note(mode, title, output, transcript)
            note_path = write_markdown_note(mode, title, content, args)
            if args.thought_paste:
                paste_text(output)
            beep("done")
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "saved",
                "title": title,
                "note_path": str(note_path),
                "transcript": transcript,
                "output": output,
            })
            if overlay:
                overlay.set("已完成", "語音思考已保存", f"{note_path}\n{get_shortcuts_text(args)}")
            notify("Desktop Voice Capture", f"語音思考已保存：\n{note_path}")
        elif mode == "meeting":
            if overlay:
                overlay.set("整理中", "正在產生會議記錄", "摘要、決議、待辦整理中。")
            summary = summarize_meeting(client, transcript)
            title = f"會議記錄 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            content = build_voice_note(mode, title, output, transcript, summary=summary)
            note_path = write_markdown_note(mode, title, content, args)
            beep("done")
            append_history(args, {
                "mode": mode,
                "mode_label": mode_label,
                "status": "saved",
                "title": title,
                "note_path": str(note_path),
                "transcript": transcript,
                "output": output,
                "summary": summary,
            })
            if overlay:
                overlay.set("已完成", "會議記錄已保存", f"{note_path}\n{get_shortcuts_text(args)}")
            notify("Desktop Voice Capture", f"會議記錄已保存：\n{note_path}")
        else:
            raise ValueError(f"Unsupported mode: {mode}")
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass


def run_once(args) -> None:
    recorder = Recorder(sample_rate=args.sample_rate, min_rms=args.min_rms, min_peak=args.min_peak)
    input("Press Enter to start recording.")
    recorder.start(get_mode_label(args.mode), get_shortcuts_text(args))
    input("Press Enter to stop recording.")
    audio_path = recorder.stop()
    if audio_path:
        process_audio(audio_path, args)


def run_hotkey_listener(args) -> None:
    recorder = Recorder(sample_rate=args.sample_rate, min_rms=args.min_rms, min_peak=args.min_peak)
    overlay = StatusOverlay(
        enabled=not args.no_overlay,
        idle_seconds=args.overlay_idle_seconds,
        font_scale=args.overlay_font_scale,
    )
    state = {
        "active_mode": None,
        "armed": False,
        "processing": False,
        "control_hotkeys": [],
    }
    lock = threading.Lock()

    def set_overlay(status: str, title: str, body: str = "") -> None:
        overlay.set(status, title, body or get_shortcuts_text(args))

    def remove_control_hotkeys() -> None:
        hotkey_ids = state.get("control_hotkeys", [])
        for hotkey_id in hotkey_ids:
            try:
                keyboard.remove_hotkey(hotkey_id)
            except Exception:
                pass
        state["control_hotkeys"] = []

    def add_control_hotkeys() -> None:
        if state.get("control_hotkeys"):
            return
        controls = [
            ("esc", cancel_or_hide),
            ("1", lambda: arm_mode("paste")),
            ("2", lambda: arm_mode("thought")),
            ("3", lambda: arm_mode("meeting")),
            ("4", lambda: arm_mode("translate_en")),
            ("5", lambda: arm_mode("translate_ja")),
            ("p", lambda: arm_mode("paste")),
            ("t", lambda: arm_mode("thought")),
            ("m", lambda: arm_mode("meeting")),
            ("e", lambda: arm_mode("translate_en")),
            ("j", lambda: arm_mode("translate_ja")),
        ]
        confirm_hotkey = get_confirm_hotkey(args)
        if confirm_hotkey:
            controls.insert(0, (confirm_hotkey, confirm_control))
        hotkey_ids = []
        for key, callback in controls:
            try:
                hotkey_ids.append(keyboard.add_hotkey(key, callback, suppress=True))
            except Exception as exc:
                print(f"Failed to register control hotkey {key}: {exc}")
        state["control_hotkeys"] = hotkey_ids

    def begin_recording_locked(mode: str) -> None:
        mode_label = get_mode_label(mode)
        state["active_mode"] = mode
        state["armed"] = False
        add_control_hotkeys()
        recorder.start(mode_label, get_shortcuts_text(args))
        stop_lines = ["再按目前模式快捷鍵停止並輸出"]
        confirm_hotkey = get_confirm_hotkey(args)
        if confirm_hotkey:
            stop_lines.append(f"{format_hotkey_label(confirm_hotkey)} 停止並輸出")
        stop_lines.append("Esc 取消錄音")
        set_overlay("錄音中", f"錄音中：{mode_label}", "\n".join(stop_lines))

    def show_overlay_without_action_locked() -> None:
        if recorder.stream:
            active_label = get_mode_label(state["active_mode"] or args.mode)
            stop_lines = ["面板已開啟，錄音仍在進行中"]
            confirm_hotkey = get_confirm_hotkey(args)
            if confirm_hotkey:
                stop_lines.append(f"{format_hotkey_label(confirm_hotkey)} 停止並輸出")
            stop_lines.append("Esc 取消錄音")
            set_overlay("錄音中", f"錄音中：{active_label}", "\n".join(stop_lines))
            return
        state["active_mode"] = None
        state["armed"] = False
        remove_control_hotkeys()
        set_overlay("待命", "桌面語音待命中", f"面板已開啟\n{get_shortcuts_text(args)}")

    def finish_recording_locked(mode: str) -> None:
        mode_label = get_mode_label(mode)
        audio_path = recorder.stop()
        state["active_mode"] = None
        state["armed"] = False
        if audio_path:
            state["processing"] = True
            set_overlay("轉文字中", f"正在處理：{mode_label}", "Whisper 轉文字中，請稍候。")
            threading.Thread(target=process_in_background, args=(audio_path, mode), daemon=True).start()
        else:
            remove_control_hotkeys()
            set_overlay("待命", "桌面語音待命中", get_shortcuts_text(args))

    def process_in_background(audio_path: str, mode: str) -> None:
        try:
            process_audio(audio_path, args, mode=mode, overlay=overlay)
        finally:
            with lock:
                state["processing"] = False
                state["armed"] = False
                state["active_mode"] = None
                remove_control_hotkeys()
            time.sleep(2.0)
            set_overlay("待命", "桌面語音待命中", get_shortcuts_text(args))

    def arm_mode(mode: str) -> None:
        mode_label = get_mode_label(mode)
        with lock:
            if state["processing"]:
                beep("error")
                set_overlay("處理中", "請稍候", "上一段錄音仍在轉文字或保存中。")
                notify("Desktop Voice Capture", "上一段錄音仍在處理中，請稍候。")
                return
            if overlay.enabled and overlay.is_hidden:
                show_overlay_without_action_locked()
                return
            if recorder.stream:
                if state["active_mode"] != mode:
                    active_label = get_mode_label(state["active_mode"])
                    beep("error")
                    set_overlay("錄音中", f"目前正在錄音：{active_label}", f"請按原本的 {active_label} 快捷鍵停止。")
                    notify("Desktop Voice Capture", f"目前正在錄音：{active_label}，請先停止。")
                    return
                finish_recording_locked(mode)
                return
            if state["armed"] and state["active_mode"] == mode:
                begin_recording_locked(mode)
                return

            state["active_mode"] = mode
            state["armed"] = True
            add_control_hotkeys()
            beep("done")
            set_overlay("準備錄音", f"已選擇：{mode_label}", get_control_text(args))

    def confirm_control() -> None:
        with lock:
            if state["processing"]:
                beep("error")
                set_overlay("處理中", "請稍候", "上一段錄音仍在處理中。")
                return
            if overlay.enabled and overlay.is_hidden:
                show_overlay_without_action_locked()
                return
            mode = state["active_mode"] or args.mode
            if recorder.stream:
                finish_recording_locked(mode)
                return

            begin_recording_locked(mode)

    def cancel_or_hide() -> None:
        with lock:
            if recorder.stream:
                recorder.cancel()
            state["active_mode"] = None
            state["armed"] = False
            state["processing"] = False
            remove_control_hotkeys()
            set_overlay("待命", "桌面語音待命中", get_shortcuts_text(args))
            overlay.hide()

    keyboard.add_hotkey(args.paste_hotkey, lambda: arm_mode("paste"))
    keyboard.add_hotkey(args.thought_hotkey, lambda: arm_mode("thought"))
    keyboard.add_hotkey(args.meeting_hotkey, lambda: arm_mode("meeting"))
    keyboard.add_hotkey(args.translate_en_hotkey, lambda: arm_mode("translate_en"))
    keyboard.add_hotkey(args.translate_ja_hotkey, lambda: arm_mode("translate_ja"))
    startup_message = (
        "待命中\n"
        f"{get_shortcuts_text(args)}\n"
        "按同一組快捷鍵開始/停止錄音。"
    )
    notify("Desktop Voice Capture", startup_message)
    print(
        "Listening. "
        f"Paste={args.paste_hotkey}, Thought={args.thought_hotkey}, Meeting={args.meeting_hotkey}. "
        "Press Ctrl+C to exit."
    )
    set_overlay("待命", "桌面語音待命中", get_shortcuts_text(args))
    try:
        if overlay.enabled:
            overlay.start("桌面語音待命中", get_shortcuts_text(args))
        else:
            while True:
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if recorder.stream:
            recorder.stop()
        remove_control_hotkeys()
        overlay.stop()
        notify("Desktop Voice Capture", "語音工具已停止。")
        print("Stopped.")


def parse_args():
    load_dotenv()
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config-file", default=os.getenv("VOICE_CONFIG_FILE", DEFAULT_CONFIG_FILE))
    pre_args, _ = pre_parser.parse_known_args(sys.argv[1:])
    config = load_voice_config(pre_args.config_file)

    def cfg(name: str, env_name: str | None, default):
        if env_name and os.getenv(env_name) is not None:
            return os.getenv(env_name)
        return config.get(name, default)

    parser = argparse.ArgumentParser(description="Desktop voice capture helper", parents=[pre_parser])
    parser.add_argument("--mode", choices=MODE_CHOICES, default=cfg("mode", "VOICE_MODE", "paste"))
    parser.add_argument("--hotkey", default=None, help="Legacy alias for --paste-hotkey")
    parser.add_argument("--paste-hotkey", default=cfg("paste_hotkey", "VOICE_PASTE_HOTKEY", DEFAULT_HOTKEY))
    parser.add_argument("--thought-hotkey", default=cfg("thought_hotkey", "VOICE_THOUGHT_HOTKEY", DEFAULT_THOUGHT_HOTKEY))
    parser.add_argument("--meeting-hotkey", default=cfg("meeting_hotkey", "VOICE_MEETING_HOTKEY", DEFAULT_MEETING_HOTKEY))
    parser.add_argument("--translate-en-hotkey", default=cfg("translate_en_hotkey", "VOICE_TRANSLATE_EN_HOTKEY", DEFAULT_TRANSLATE_EN_HOTKEY))
    parser.add_argument("--translate-ja-hotkey", default=cfg("translate_ja_hotkey", "VOICE_TRANSLATE_JA_HOTKEY", DEFAULT_TRANSLATE_JA_HOTKEY))
    parser.add_argument(
        "--confirm-hotkey",
        default=cfg("confirm_hotkey", "VOICE_CONFIRM_HOTKEY", DEFAULT_CONFIRM_HOTKEY),
        help="Optional extra hotkey to start/stop after choosing a mode; use none/off to disable.",
    )
    parser.add_argument("--listen", action="store_true", help="Run global hotkey listener")
    parser.add_argument("--once", action="store_true", help="Console-only mode; use Enter in terminal to start/stop one recording")
    parser.add_argument("--language", default=cfg("language", "VOICE_LANGUAGE", None), help="Optional Whisper language code, such as zh, ja, en")
    parser.add_argument("--sample-rate", type=int, default=int(cfg("sample_rate", "VOICE_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)))
    parser.add_argument("--min-rms", type=float, default=float(cfg("min_rms", "VOICE_MIN_RMS", DEFAULT_MIN_RMS)))
    parser.add_argument("--min-peak", type=float, default=float(cfg("min_peak", "VOICE_MIN_PEAK", DEFAULT_MIN_PEAK)))
    parser.add_argument("--vault-path", default=cfg("vault_path", "OBSIDIAN_VAULT_PATH", DEFAULT_VAULT_PATH))
    parser.add_argument("--capture-dir", default=cfg("capture_dir", "VOICE_CAPTURE_DIR", DEFAULT_CAPTURE_DIR))
    parser.add_argument("--thought-folder", default=cfg("thought_folder", "VOICE_THOUGHT_FOLDER", r"Sources\desktop-voice"))
    parser.add_argument("--meeting-folder", default=cfg("meeting_folder", "VOICE_MEETING_FOLDER", "Meetings"))
    parser.add_argument("--thought-paste", action="store_true", default=parse_bool(cfg("thought_paste", "VOICE_THOUGHT_PASTE", False)), help="Also paste thought mode output")
    parser.add_argument("--dictionary-file", default=cfg("dictionary_file", "VOICE_DICTIONARY_FILE", DEFAULT_DICTIONARY_FILE))
    parser.add_argument("--history-file", default=cfg("history_file", "VOICE_HISTORY_FILE", DEFAULT_HISTORY_FILE))
    parser.add_argument("--no-overlay", action="store_true", default=parse_bool(cfg("no_overlay", "VOICE_NO_OVERLAY", False)))
    parser.add_argument(
        "--overlay-idle-seconds",
        type=float,
        default=float(cfg("overlay_idle_seconds", "VOICE_OVERLAY_IDLE_SECONDS", DEFAULT_OVERLAY_IDLE_SECONDS)),
        help="Hide overlay after this many idle/completed seconds; set 0 to keep it visible.",
    )
    parser.add_argument(
        "--overlay-font-scale",
        type=float,
        default=float(cfg("overlay_font_scale", "VOICE_OVERLAY_FONT_SCALE", DEFAULT_OVERLAY_FONT_SCALE)),
    )
    parser.add_argument("--no-normalize", action="store_true", default=parse_bool(cfg("no_normalize", "VOICE_NO_NORMALIZE", False)))
    args = parser.parse_args()
    if args.hotkey:
        args.paste_hotkey = args.hotkey
    return args


def main() -> None:
    if missing_import:
        raise SystemExit(
            "Missing desktop voice dependency. Install keyboard, sounddevice, soundfile, numpy, and pyperclip first. "
            f"Original error: {missing_import}"
        )
    args = parse_args()
    if not args.listen and not args.once:
        args.listen = True
    if args.once:
        run_once(args)
    else:
        run_hotkey_listener(args)


if __name__ == "__main__":
    main()
