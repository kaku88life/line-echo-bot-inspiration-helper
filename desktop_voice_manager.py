"""Desktop voice capture manager UI.

This small Tkinter app edits desktop voice settings, reviews local capture
history, and installs a per-user Windows startup launcher.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError as exc:
    raise SystemExit(f"Tkinter is required for the desktop voice manager: {exc}") from exc

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    from dotenv import load_dotenv
    from openai import OpenAI
except ImportError:
    load_dotenv = None
    OpenAI = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "desktop_voice_config.json"
EXAMPLE_CONFIG_FILE = BASE_DIR / "desktop_voice_config.example.json"
CAPTURE_SCRIPT = BASE_DIR / "desktop_voice_capture.py"
DEFAULT_CAPTURE_DIR = "desktop-captures"
DEFAULT_HISTORY_FILE = str(Path(DEFAULT_CAPTURE_DIR) / "history.jsonl")
STARTUP_NAME = "Line Inspiration Desktop Voice.cmd"

MODE_OPTIONS = {
    "paste": "快速輸入",
    "thought": "語音思考",
    "meeting": "會議記錄",
    "translate_en": "翻譯英文",
    "translate_ja": "翻譯日文",
}

MODE_LABEL_TO_KEY = {f"{label} ({key})": key for key, label in MODE_OPTIONS.items()}
MODE_COMBO_VALUES = list(MODE_LABEL_TO_KEY.keys())

COMBO_PRESETS = {
    "paste_hotkey": ["ctrl+alt+z", "ctrl+shift+z", "ctrl+alt+v", "ctrl+shift+v"],
    "thought_hotkey": ["ctrl+alt+x", "ctrl+shift+x", "ctrl+alt+t", "ctrl+shift+t"],
    "meeting_hotkey": ["ctrl+alt+c", "ctrl+shift+c", "ctrl+alt+m", "ctrl+shift+m"],
    "translate_en_hotkey": ["ctrl+alt+e", "ctrl+shift+e"],
    "translate_ja_hotkey": ["ctrl+alt+j", "ctrl+shift+j"],
    "confirm_hotkey": ["space", "none", "ctrl+space", "alt+space", "f8", "f9", "f10"],
    "language": ["", "zh", "en", "ja"],
    "sample_rate": ["16000", "44100", "48000"],
    "overlay_idle_seconds": ["0", "3", "5", "8", "10", "15"],
    "overlay_font_scale": ["0.9", "1.0", "1.1", "1.15", "1.25", "1.35"],
    "min_rms": ["0.001", "0.002", "0.003", "0.005", "0.008"],
    "min_peak": ["0.008", "0.015", "0.02", "0.03", "0.05"],
}

DEFAULT_CONFIG = {
    "mode": "paste",
    "paste_hotkey": "ctrl+alt+z",
    "thought_hotkey": "ctrl+alt+x",
    "meeting_hotkey": "ctrl+alt+c",
    "translate_en_hotkey": "ctrl+alt+e",
    "translate_ja_hotkey": "ctrl+alt+j",
    "confirm_hotkey": "space",
    "language": "",
    "sample_rate": 16000,
    "vault_path": r"G:/我的雲端硬碟/ObsidianVault",
    "thought_folder": "Sources/desktop-voice",
    "meeting_folder": "Meetings",
    "capture_dir": DEFAULT_CAPTURE_DIR,
    "history_file": DEFAULT_HISTORY_FILE,
    "dictionary_file": "voice_dictionary.txt",
    "overlay_idle_seconds": 5,
    "overlay_font_scale": 1.15,
    "min_rms": 0.003,
    "min_peak": 0.015,
    "thought_paste": False,
    "no_overlay": False,
    "no_normalize": False,
}


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def format_mode_value(mode: str) -> str:
    label = MODE_OPTIONS.get(mode, mode)
    return f"{label} ({mode})"


def parse_mode_value(value: str) -> str:
    value = str(value).strip()
    if value in MODE_LABEL_TO_KEY:
        return MODE_LABEL_TO_KEY[value]
    if value in MODE_OPTIONS:
        return value
    if "(" in value and value.endswith(")"):
        candidate = value.rsplit("(", 1)[1].rstrip(")").strip()
        if candidate in MODE_OPTIONS:
            return candidate
    return "paste"


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


def apply_tk_scaling(root: tk.Tk, scale: float = 1.0) -> None:
    try:
        base_scaling = root.winfo_fpixels("1i") / 72.0
        root.tk.call("tk", "scaling", base_scaling * scale)
    except Exception:
        pass


def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    source = CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG_FILE
    config.update(load_json_file(source))
    return config


def resolve_local_path(raw_path: str | None) -> Path:
    path = Path(raw_path or "").expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def get_startup_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not available.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def get_startup_file() -> Path:
    return get_startup_dir() / STARTUP_NAME


def get_pythonw_path() -> Path:
    candidates = [
        BASE_DIR / ".venv" / "Scripts" / "pythonw.exe",
        BASE_DIR / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def open_path(path: Path) -> None:
    if path.is_file():
        os.startfile(str(path))
    else:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))


class DesktopVoiceManager:
    def __init__(self) -> None:
        self.config = load_config()
        self.history_records: list[dict] = []
        self.selected_history: dict | None = None
        enable_windows_dpi_awareness()
        self.root = tk.Tk()
        apply_tk_scaling(self.root)
        self.root.title("Desktop Voice Manager")
        self.root.geometry("820x560")
        self.root.minsize(720, 480)
        self.root.configure(bg="#f8fafc")
        self.vars: dict[str, tk.Variable] = {}
        self.startup_status_var = tk.StringVar()
        self.listener_status_var = tk.StringVar()
        self.history_path_var = tk.StringVar()
        self.history_filter_var = tk.StringVar(value="全部")
        self.retranslate_target_var = tk.StringVar(value="English")
        self._build_ui()
        self.refresh_startup_status()
        self.refresh_listener_status()
        self.load_history()

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", padding=(10, 5))
        style.configure("Primary.TButton", padding=(8, 4))

        title = tk.Label(
            self.root,
            text="桌面語音工具管理",
            bg="#f8fafc",
            fg="#0f172a",
            font=("Microsoft JhengHei UI", 15, "bold"),
        )
        title.pack(anchor="w", padx=12, pady=(10, 2))

        subtitle = tk.Label(
            self.root,
            text="管理快捷鍵、保存位置、歷史紀錄與 Windows 開機啟動",
            bg="#f8fafc",
            fg="#475569",
            font=("Microsoft JhengHei UI", 9),
        )
        subtitle.pack(anchor="w", padx=14, pady=(0, 6))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        settings_tab = ttk.Frame(notebook)
        history_tab = ttk.Frame(notebook, padding=10)
        startup_tab = ttk.Frame(notebook, padding=10)
        notebook.add(settings_tab, text="設定")
        notebook.add(history_tab, text="歷史")
        notebook.add(startup_tab, text="啟動")

        self._build_settings_tab(self._make_scrollable_frame(settings_tab))
        self._build_history_tab(history_tab)
        self._build_startup_tab(startup_tab)

    def _make_scrollable_frame(self, parent: ttk.Frame) -> ttk.Frame:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        canvas = tk.Canvas(parent, bg="#f8fafc", highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas, padding=10)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def update_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def update_frame_width(event) -> None:
            canvas.itemconfigure(frame_id, width=event.width)

        def on_mousewheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_frame_width)
        canvas.bind("<MouseWheel>", on_mousewheel)
        frame.bind("<MouseWheel>", on_mousewheel)
        return frame

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(parent, text="預設模式").grid(row=row, column=0, sticky="w", pady=5)
        self.vars["mode"] = tk.StringVar(value=format_mode_value(self.config.get("mode", "paste")))
        mode_box = ttk.Combobox(
            parent,
            textvariable=self.vars["mode"],
            values=MODE_COMBO_VALUES,
            state="readonly",
            width=28,
        )
        mode_box.grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        for key, label in [
            ("paste_hotkey", "快速輸入快捷鍵"),
            ("thought_hotkey", "語音思考快捷鍵"),
            ("meeting_hotkey", "會議記錄快捷鍵"),
            ("translate_en_hotkey", "翻譯英文快捷鍵"),
            ("translate_ja_hotkey", "翻譯日文快捷鍵"),
            ("confirm_hotkey", "開始/停止鍵"),
            ("language", "Whisper 語言"),
        ]:
            row = self._add_combo(parent, row, key, label, COMBO_PRESETS[key])

        row = self._add_path_entry(parent, row, "vault_path", "Obsidian Vault", folder=True)
        row = self._add_entry(parent, row, "thought_folder", "語音思考資料夾")
        row = self._add_entry(parent, row, "meeting_folder", "會議記錄資料夾")
        row = self._add_path_entry(parent, row, "capture_dir", "Fallback 保存資料夾", folder=True)
        row = self._add_path_entry(parent, row, "history_file", "歷史紀錄檔")
        row = self._add_path_entry(parent, row, "dictionary_file", "自訂字典檔")

        for key, label in [
            ("sample_rate", "錄音取樣率"),
            ("overlay_idle_seconds", "完成後面板停留秒數"),
            ("overlay_font_scale", "面板字體縮放"),
            ("min_rms", "最小 RMS 音量"),
            ("min_peak", "最小 Peak 音量"),
        ]:
            row = self._add_combo(parent, row, key, label, COMBO_PRESETS[key])

        checkbox_frame = ttk.Frame(parent)
        checkbox_frame.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 12))
        for key, label in [
            ("thought_paste", "語音思考也貼到目前游標"),
            ("no_overlay", "停用浮窗"),
            ("no_normalize", "停用文字整理"),
        ]:
            self.vars[key] = tk.BooleanVar(value=parse_bool(self.config.get(key, False)))
            ttk.Checkbutton(checkbox_frame, text=label, variable=self.vars[key]).pack(side="left", padx=(0, 18))
        row += 1

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(button_frame, text="儲存設定", command=self.save_settings, style="Primary.TButton").pack(side="left")
        ttk.Button(button_frame, text="重新載入", command=self.reload_settings).pack(side="left", padx=8)
        ttk.Button(button_frame, text="開啟設定檔資料夾", command=lambda: open_path(BASE_DIR)).pack(side="left", padx=8)

        note = (
            "儲存後請在「啟動」分頁重啟監聽器，新的快捷鍵與路徑才會套用到背景錄音工具。"
        )
        ttk.Label(parent, text=note, foreground="#475569").grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _add_entry(self, parent: ttk.Frame, row: int, key: str, label: str) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        self.vars[key] = tk.StringVar(value=str(self.config.get(key, DEFAULT_CONFIG.get(key, ""))))
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=5)
        return row + 1

    def _add_combo(self, parent: ttk.Frame, row: int, key: str, label: str, values: list[str]) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        value = self.config.get(key, DEFAULT_CONFIG.get(key, ""))
        self.vars[key] = tk.StringVar(value=str(value))
        ttk.Combobox(
            parent,
            textvariable=self.vars[key],
            values=values,
            state="normal",
        ).grid(row=row, column=1, sticky="ew", pady=5)
        return row + 1

    def _add_path_entry(self, parent: ttk.Frame, row: int, key: str, label: str, folder: bool = False) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        self.vars[key] = tk.StringVar(value=str(self.config.get(key, DEFAULT_CONFIG.get(key, ""))))
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=5)
        command = lambda: self.browse_path(key, folder=folder)
        ttk.Button(parent, text="選擇", command=command).grid(row=row, column=2, sticky="w", padx=(8, 0), pady=5)
        return row + 1

    def browse_path(self, key: str, folder: bool = False) -> None:
        current = resolve_local_path(str(self.vars[key].get()))
        if folder:
            selected = filedialog.askdirectory(initialdir=str(current if current.exists() else BASE_DIR))
        else:
            selected = filedialog.askopenfilename(initialdir=str(current.parent if current.parent.exists() else BASE_DIR))
        if selected:
            self.vars[key].set(selected)

    def collect_settings(self) -> dict:
        config = {}
        for key, var in self.vars.items():
            value = var.get()
            if key == "mode":
                value = parse_mode_value(value)
            elif key in {"overlay_idle_seconds", "overlay_font_scale", "min_rms", "min_peak"}:
                try:
                    value = float(value)
                except ValueError as exc:
                    raise ValueError(f"{key} 必須是數字") from exc
            elif key == "sample_rate":
                try:
                    value = int(value)
                except ValueError as exc:
                    raise ValueError("sample_rate 必須是整數") from exc
            config[key] = value
        return config

    def save_settings(self, silent: bool = False) -> bool:
        try:
            config = self.collect_settings()
            CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.config = config
            self.history_path_var.set(str(resolve_local_path(str(config.get("history_file", DEFAULT_HISTORY_FILE)))))
            if not silent:
                messagebox.showinfo("設定已儲存", f"已寫入 {CONFIG_FILE}")
            self.refresh_startup_status()
            return True
        except Exception as exc:
            if not silent:
                messagebox.showerror("設定儲存失敗", str(exc))
            return False

    def reload_settings(self) -> None:
        self.config = load_config()
        for key, var in self.vars.items():
            if isinstance(var, tk.BooleanVar):
                var.set(parse_bool(self.config.get(key, DEFAULT_CONFIG.get(key, False))))
            elif key == "mode":
                var.set(format_mode_value(self.config.get(key, DEFAULT_CONFIG.get(key, "paste"))))
            else:
                var.set(str(self.config.get(key, DEFAULT_CONFIG.get(key, ""))))
        self.load_history()
        self.refresh_startup_status()

    def _build_history_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        parent.rowconfigure(4, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="重新載入歷史", command=self.load_history).pack(side="left")
        ttk.Button(toolbar, text="開啟歷史資料夾", command=self.open_history_folder).pack(side="left", padx=8)
        ttk.Label(toolbar, text="篩選").pack(side="left", padx=(18, 4))
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.history_filter_var,
            values=["全部", "pasted", "translated", "saved", "skipped", "retranslated"],
            state="readonly",
            width=14,
        )
        filter_box.pack(side="left")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self.populate_history())

        self.history_path_var.set(str(resolve_local_path(str(self.config.get("history_file", DEFAULT_HISTORY_FILE)))))
        ttk.Label(parent, textvariable=self.history_path_var, foreground="#475569").grid(
            row=1, column=0, sticky="w", pady=(10, 6)
        )

        columns = ("timestamp", "mode", "status", "title", "note_path")
        self.history_tree = ttk.Treeview(parent, columns=columns, show="headings", height=7)
        for column, heading, width in [
            ("timestamp", "時間", 140),
            ("mode", "模式", 90),
            ("status", "狀態", 90),
            ("title", "標題/原因", 180),
            ("note_path", "筆記路徑", 280),
        ]:
            self.history_tree.heading(column, text=heading)
            self.history_tree.column(column, width=width, anchor="w")
        self.history_tree.grid(row=2, column=0, sticky="nsew")
        self.history_tree.bind("<<TreeviewSelect>>", self.on_history_select)

        action_bar = ttk.Frame(parent)
        action_bar.grid(row=3, column=0, sticky="ew", pady=10)
        ttk.Button(action_bar, text="複製整理文字", command=self.copy_selected_output).pack(side="left")
        ttk.Button(action_bar, text="貼到目前視窗", command=self.paste_selected_output).pack(side="left", padx=8)
        ttk.Button(action_bar, text="開啟筆記", command=self.open_selected_note).pack(side="left")
        ttk.Label(action_bar, text="重新翻譯").pack(side="left", padx=(18, 4))
        ttk.Combobox(
            action_bar,
            textvariable=self.retranslate_target_var,
            values=["English", "Japanese"],
            state="readonly",
            width=12,
        ).pack(side="left")
        ttk.Button(action_bar, text="翻譯並複製", command=self.retranslate_selected).pack(side="left", padx=8)

        self.history_detail = ScrolledText(parent, height=8, wrap="word", font=("Consolas", 9))
        self.history_detail.grid(row=4, column=0, sticky="nsew")

    def load_history(self) -> None:
        path = resolve_local_path(str(self.config.get("history_file", DEFAULT_HISTORY_FILE)))
        self.history_path_var.set(str(path))
        self.history_records = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    self.history_records.append(record)
        self.populate_history()

    def populate_history(self) -> None:
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        current_filter = self.history_filter_var.get()
        records = list(reversed(self.history_records[-500:]))
        for index, record in enumerate(records):
            status = str(record.get("status", ""))
            if current_filter != "全部" and status != current_filter:
                continue
            title = record.get("title") or record.get("reason") or ""
            values = (
                record.get("timestamp", ""),
                record.get("mode_label") or record.get("mode", ""),
                status,
                title,
                record.get("note_path", ""),
            )
            self.history_tree.insert("", "end", iid=str(index), values=values)

    def on_history_select(self, _event=None) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        records = list(reversed(self.history_records[-500:]))
        self.selected_history = records[int(selection[0])]
        self.history_detail.delete("1.0", tk.END)
        self.history_detail.insert(tk.END, json.dumps(self.selected_history, ensure_ascii=False, indent=2))

    def selected_output_text(self) -> str:
        record = self.selected_history or {}
        for key in ["translated", "output", "summary", "transcript"]:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def copy_to_clipboard(self, text: str) -> None:
        if not text:
            messagebox.showwarning("沒有可複製內容", "這筆歷史沒有整理文字、翻譯或逐字稿。")
            return
        if pyperclip:
            pyperclip.copy(text)
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        messagebox.showinfo("已複製", "內容已複製到剪貼簿。")

    def copy_selected_output(self) -> None:
        self.copy_to_clipboard(self.selected_output_text())

    def paste_selected_output(self) -> None:
        text = self.selected_output_text()
        if not text:
            messagebox.showwarning("沒有可貼上內容", "這筆歷史沒有可重新輸出的文字。")
            return
        if pyperclip:
            pyperclip.copy(text)
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        self.root.iconify()
        if keyboard:
            self.root.after(700, lambda: keyboard.send("ctrl+v"))
        else:
            messagebox.showinfo("已複製", "內容已複製到剪貼簿。")

    def open_selected_note(self) -> None:
        record = self.selected_history or {}
        note_path = record.get("note_path")
        if not note_path:
            messagebox.showwarning("沒有筆記路徑", "這筆歷史沒有對應的 Obsidian 筆記。")
            return
        path = Path(note_path)
        if not path.exists():
            messagebox.showwarning("找不到筆記", str(path))
            return
        open_path(path)

    def retranslate_selected(self) -> None:
        source_text = self.selected_output_text()
        if not source_text:
            messagebox.showwarning("沒有可翻譯內容", "這筆歷史沒有可重新翻譯的文字。")
            return
        target = self.retranslate_target_var.get()
        self.history_detail.insert(tk.END, "\n\n正在重新翻譯，請稍候...\n")
        threading.Thread(target=self._retranslate_worker, args=(source_text, target), daemon=True).start()

    def _retranslate_worker(self, source_text: str, target: str) -> None:
        try:
            translated = self.translate_text(source_text, target)
            self.root.after(0, lambda: self._finish_retranslate(translated, target))
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror("重新翻譯失敗", str(exc)))

    def translate_text(self, source_text: str, target: str) -> str:
        if load_dotenv:
            load_dotenv(BASE_DIR / ".env")
        if OpenAI is None:
            raise RuntimeError("OpenAI 套件未安裝，無法重新翻譯。")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 尚未設定。")
        dictionary_text = ""
        dictionary_path = resolve_local_path(str(self.config.get("dictionary_file", "voice_dictionary.txt")))
        if dictionary_path.exists():
            dictionary_text = dictionary_path.read_text(encoding="utf-8").strip()
        dictionary_section = f"\n個人字典：\n{dictionary_text}\n" if dictionary_text else ""
        prompt = f"""請把以下文字翻譯成 {target}。

規則：
- 保留原意，不要新增原文沒有的資訊。
- 專有名詞、人名、品牌名請依照字典保留、修正或音譯。
- 只輸出翻譯後文字，不要解釋。
{dictionary_section}

文字：
{source_text}
"""
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("VOICE_TRANSLATE_MODEL", os.getenv("VOICE_NORMALIZE_MODEL", "gpt-4.1-mini")),
            messages=[
                {"role": "system", "content": "你是精準的語音輸入翻譯助手，輸出自然、可直接貼上的文字。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1400,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()

    def _finish_retranslate(self, translated: str, target: str) -> None:
        self.copy_to_clipboard(translated)
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": "history_retranslate",
            "mode_label": "歷史重新翻譯",
            "status": "retranslated",
            "target_language": target,
            "output": translated,
            "source_record": self.selected_history,
        }
        self.append_history_record(record)
        self.load_history()

    def append_history_record(self, record: dict) -> None:
        path = resolve_local_path(str(self.config.get("history_file", DEFAULT_HISTORY_FILE)))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def open_history_folder(self) -> None:
        path = resolve_local_path(str(self.config.get("history_file", DEFAULT_HISTORY_FILE)))
        open_path(path.parent)

    def _build_startup_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="Windows 開機啟動", font=("Microsoft JhengHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Label(parent, textvariable=self.startup_status_var, foreground="#475569").grid(row=1, column=0, sticky="w")

        startup_buttons = ttk.Frame(parent)
        startup_buttons.grid(row=2, column=0, sticky="w", pady=12)
        ttk.Button(startup_buttons, text="安裝開機啟動", command=self.install_startup).pack(side="left")
        ttk.Button(startup_buttons, text="移除開機啟動", command=self.remove_startup).pack(side="left", padx=8)
        ttk.Button(startup_buttons, text="開啟啟動資料夾", command=lambda: open_path(get_startup_dir())).pack(side="left")

        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=18)

        ttk.Label(parent, text="背景監聽器", font=("Microsoft JhengHei UI", 11, "bold")).grid(row=4, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.listener_status_var, foreground="#475569").grid(row=5, column=0, sticky="w", pady=(6, 0))
        listener_buttons = ttk.Frame(parent)
        listener_buttons.grid(row=6, column=0, sticky="w", pady=12)
        ttk.Button(listener_buttons, text="啟動監聽器", command=self.start_listener).pack(side="left")
        ttk.Button(listener_buttons, text="停止監聽器", command=self.stop_listener).pack(side="left", padx=8)
        ttk.Button(listener_buttons, text="重啟監聽器", command=self.restart_listener).pack(side="left")
        ttk.Button(listener_buttons, text="更新狀態", command=self.refresh_listener_status).pack(side="left", padx=8)

        note = (
            "開機啟動使用目前使用者的 Startup 資料夾，不需要系統管理員權限。"
            "如果改了設定，請重啟監聽器或下次開機後才會套用。"
        )
        ttk.Label(parent, text=note, wraplength=760, foreground="#475569").grid(row=7, column=0, sticky="w", pady=(16, 0))

    def refresh_startup_status(self) -> None:
        try:
            startup_file = get_startup_file()
            status = "已安裝" if startup_file.exists() else "未安裝"
            self.startup_status_var.set(f"{status}：{startup_file}")
        except Exception as exc:
            self.startup_status_var.set(f"無法檢查開機啟動：{exc}")

    def install_startup(self) -> None:
        if not self.save_settings(silent=True):
            return
        try:
            startup_file = get_startup_file()
            startup_file.parent.mkdir(parents=True, exist_ok=True)
            pythonw_path = get_pythonw_path()
            content = (
                "@echo off\r\n"
                f'cd /d "{BASE_DIR}"\r\n'
                f'start "" /min "{pythonw_path}" "{CAPTURE_SCRIPT}" --listen --config-file "{CONFIG_FILE}"\r\n'
            )
            startup_file.write_text(content, encoding="utf-8")
            self.refresh_startup_status()
            messagebox.showinfo("已安裝", f"已建立開機啟動：\n{startup_file}")
        except Exception as exc:
            messagebox.showerror("安裝失敗", str(exc))

    def remove_startup(self) -> None:
        try:
            startup_file = get_startup_file()
            if startup_file.exists():
                startup_file.unlink()
            self.refresh_startup_status()
            messagebox.showinfo("已移除", "已移除開機啟動。")
        except Exception as exc:
            messagebox.showerror("移除失敗", str(exc))

    def start_listener(self) -> None:
        if not self.save_settings(silent=True):
            return
        pythonw_path = get_pythonw_path()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(pythonw_path), str(CAPTURE_SCRIPT), "--listen", "--config-file", str(CONFIG_FILE)],
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
        self.root.after(900, self.refresh_listener_status)

    def stop_listener(self) -> None:
        command = (
            "$targets = Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*desktop_voice_capture.py*' }; "
            "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
            "'stopped'"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, check=False)
        self.root.after(900, self.refresh_listener_status)

    def restart_listener(self) -> None:
        self.stop_listener()
        self.root.after(1200, self.start_listener)

    def refresh_listener_status(self) -> None:
        command = (
            "$targets = Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*desktop_voice_capture.py*' }; "
            "if ($targets) { ($targets | ForEach-Object { $_.ProcessId }) -join ', ' } else { '' }"
        )
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, check=False)
        pids = result.stdout.strip()
        if pids:
            self.listener_status_var.set(f"執行中，PID：{pids}")
        else:
            self.listener_status_var.set("未執行")


def main() -> None:
    DesktopVoiceManager().run()


if __name__ == "__main__":
    main()
