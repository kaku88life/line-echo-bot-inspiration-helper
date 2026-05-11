"""Windows desktop voice capture helper.

First version supports paste mode only:

    python desktop_voice_capture.py --listen
    python desktop_voice_capture.py --once

Default hotkey is Ctrl+Alt+Z. Press once to start recording, press again to stop,
then the transcript is lightly normalized and pasted into the active text field.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import OpenAI

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


@dataclass
class Recorder:
    sample_rate: int = DEFAULT_SAMPLE_RATE
    frames: list = field(default_factory=list)
    stream: object | None = None
    started_at: float | None = None

    def start(self) -> None:
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
        print("Recording started. Press hotkey again to stop.")

    def stop(self) -> str | None:
        if not self.stream:
            return None
        self.stream.stop()
        self.stream.close()
        self.stream = None
        duration = time.time() - (self.started_at or time.time())
        self.started_at = None
        if duration < 0.4 or not self.frames:
            print("Recording too short; skipped.")
            return None
        audio = np.concatenate(self.frames, axis=0)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        sf.write(tmp.name, audio, self.sample_rate)
        print(f"Recording saved: {tmp.name}")
        return tmp.name

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


def normalize_transcript(client: OpenAI, text: str) -> str:
    if not text.strip():
        return ""
    prompt = f"""請輕度整理以下語音轉文字結果。

規則：
- 使用原本語言，支援中文、日文、英文混合。
- 只修正標點、斷句、明顯贅詞與明顯聽寫錯字。
- 不要擅自改變法規、證照、專有名詞或數字的意思。
- 只輸出整理後文字，不要解釋。

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
    print("Transcript pasted.")


def process_audio(audio_path: str, args) -> None:
    client = get_openai_client()
    try:
        transcript = transcribe_audio(client, audio_path, language=args.language)
        if not transcript:
            print("Whisper returned empty transcript.")
            return
        output = transcript if args.no_normalize else normalize_transcript(client, transcript)
        if args.mode == "paste":
            paste_text(output)
        else:
            raise ValueError(f"Unsupported mode: {args.mode}")
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass


def run_once(args) -> None:
    recorder = Recorder(sample_rate=args.sample_rate)
    input("Press Enter to start recording.")
    recorder.start()
    input("Press Enter to stop recording.")
    audio_path = recorder.stop()
    if audio_path:
        process_audio(audio_path, args)


def run_hotkey_listener(args) -> None:
    recorder = Recorder(sample_rate=args.sample_rate)

    def toggle_recording() -> None:
        if recorder.stream:
            audio_path = recorder.stop()
            if audio_path:
                process_audio(audio_path, args)
        else:
            recorder.start()

    keyboard.add_hotkey(args.hotkey, toggle_recording)
    print(f"Listening. Press {args.hotkey} to start/stop recording. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        if recorder.stream:
            recorder.stop()
        print("Stopped.")


def parse_args():
    parser = argparse.ArgumentParser(description="Desktop voice capture helper")
    parser.add_argument("--mode", choices=["paste"], default="paste")
    parser.add_argument("--hotkey", default=DEFAULT_HOTKEY)
    parser.add_argument("--listen", action="store_true", help="Run global hotkey listener")
    parser.add_argument("--once", action="store_true", help="Use Enter to start/stop one recording")
    parser.add_argument("--language", default=None, help="Optional Whisper language code, such as zh, ja, en")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--no-normalize", action="store_true")
    return parser.parse_args()


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
