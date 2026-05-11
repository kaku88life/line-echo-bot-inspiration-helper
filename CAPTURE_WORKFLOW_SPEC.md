# Capture Workflow Spec

## Purpose

This project is the capture layer for Kaku's Obsidian-centered knowledge workflow.
LINE Bot, desktop voice capture, and future URL extractors should all write into
the same ObsidianVault structure instead of creating separate knowledge stores.

## Vault Structure

- `Sources/`: raw captured notes from LINE Bot, desktop tools, images, audio, and URLs.
- `Wiki/`: AI-maintained durable knowledge pages.
- `Logs/`: project and system operation logs.
- `40_Outputs/weekly-digests/`: weekly digest reports for human review.
- `90_System/wiki-schema.md`: shared rules for Codex, Claude Code, and automation tools.

## Capture Frontmatter

Every new Source note should include these fields when available:

```yaml
date: YYYY-MM-DD
type: URL摘要 | 文字筆記 | 語音筆記 | 圖片分析 | 翻譯 | 社群分析
category: 科技 | AI | 金融 | 商業 | 新聞 | 教學 | 地圖 | 投資 | 生活 | 其他
source_type: text | audio | image | webpage | threads | facebook | youtube | google_maps | 104 | ptt
capture_status: full | partial | failed
extractor: line-text | line-audio | line-image | jina | direct-html | apify | youtube | google-maps | ptt | 104 | fallback
needs_review: true | false
tags: [tag1, tag2]
source: "https://example.com"
```

The body should preserve the original input and the normalized result when the
tool changes user-entered text. High-stakes notes such as finance certificates
or regulations should be lightly normalized only.

## URL Capture Rules

- Never generate a normal summary from failed or near-empty extracted content.
- If extraction fails, save a Source note with `capture_status: failed`,
  `needs_review: true`, and the original URL.
- If extraction is incomplete but useful, save `capture_status: partial`.
- LINE replies should stay short; the full extraction, raw input, and status live
  in Obsidian.

## Desktop Voice Rules

- Desktop voice is Windows desktop-first. Mobile Termux/native microphone support
  is cancelled for now; mobile capture should stay on LINE Bot.
- Mode hotkeys:
  - `Ctrl+Alt+Z`: quick paste.
  - `Ctrl+Alt+X`: voice thought saved to `Sources/desktop-voice/`.
  - `Ctrl+Alt+C`: meeting note saved to `Meetings/`.
  - `Ctrl+Alt+E`: translate to English and paste.
  - `Ctrl+Alt+J`: translate to Japanese and paste.
- After choosing a mode, `Space` starts recording and `Space` stops recording and
  outputs the result. Re-pressing the current mode hotkey can also start/stop.
- Flow: record audio, transcribe with OpenAI Whisper, apply the local dictionary,
  lightly normalize, then paste or save based on mode.
- The local dictionary is `voice_dictionary.txt`, ignored by git. Keep
  `voice_dictionary.example.txt` as the shareable template.
- Successful or skipped desktop recordings should append local history to
  `desktop-captures/history.jsonl` so a future history UI can inspect, re-output,
  or re-translate entries.
- Local settings should be read from `desktop_voice_config.json` when present,
  with `desktop_voice_config.example.json` as the shareable template.
- The overlay should prioritize readable status text over visual polish. Improve
  typography and contrast before building a custom UI.
- Do not override `Ctrl+Z`.
- Future desktop work: settings UI, history management UI, and Windows startup or
  installer support.

## Weekly Review Rules

- `本週回顧` lists new captures and their capture status.
- `消化狀態` lists full, partial, failed, and needs-review notes.
- `整理本週` creates a weekly digest in `40_Outputs/weekly-digests/` and logs the
  result in `log.md`.
