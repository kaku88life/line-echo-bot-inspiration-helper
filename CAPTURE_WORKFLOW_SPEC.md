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

- First desktop version is paste-only.
- Hotkey: `Ctrl+Alt+Z`.
- Flow: record audio, transcribe with OpenAI Whisper, lightly normalize, paste
  into the active text field.
- Do not override `Ctrl+Z`.
- Store-to-Obsidian mode is a later phase.

## Weekly Review Rules

- `本週回顧` lists new captures and their capture status.
- `消化狀態` lists full, partial, failed, and needs-review notes.
- `整理本週` creates a weekly digest in `40_Outputs/weekly-digests/` and logs the
  result in `log.md`.
