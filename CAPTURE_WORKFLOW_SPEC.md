# Capture Workflow Spec

## Purpose

This project is the capture layer for Kaku's Obsidian-centered knowledge workflow.
LINE Bot, desktop voice capture, and future URL extractors should all write into
the same ObsidianVault structure instead of creating separate knowledge stores.
The canonical local vault path is `G:\我的雲端硬碟\ObsidianVault`.

## Vault Structure

- `.obsidian/`: Obsidian app settings for the single canonical vault root.
- `Sources/`: raw captured notes from LINE Bot, desktop tools, images, audio, and URLs.
- `Wiki/`: AI-maintained durable knowledge pages.
- `Meetings/`: desktop voice meeting notes and summaries.
- `Logs/`: project and system operation logs.
- `40_Outputs/weekly-digests/`: weekly digest reports for human review.
- `90_System/wiki-schema.md`: shared rules for Codex, Claude Code, and automation tools.
- `90_System/web-clipper-templates/`: vault-local copies of the Web Clipper
  templates imported from this repo.

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

## PTT Capture Rules

- PTT article URLs should use the `ptt-html` extractor before generic webpage
  fallback.
- Extract and preserve board, article id, title, author, publish time, body,
  push counts, and a bounded push-comment excerpt.
- Remove PTT navigation, system footer lines, signature blocks, scripts, styles,
  and raw push DOM noise from the article body.
- If the article body is missing, save a `capture_status: partial` status note
  with `needs_review: true` instead of generating a normal summary.

## 104 Capture Rules

- 104 job URLs should use the `104-ajax` extractor before generic webpage
  fallback.
- Extract and preserve job id, title, company, location, salary, job category,
  work type, schedule, job description, requirements, skills, welfare, company
  info, contact info, and original URL when available.
- Convert known numeric codes to human-readable labels only when the mapping is
  stable; suppress empty boolean/noise fields instead of saving raw API artifacts.
- If the ajax endpoint fails or the job title, company, or job description is
  missing, save a `capture_status: partial` status note with `needs_review:
  true` instead of generating a normal summary.

## Web Clipper Rules

- Obsidian Web Clipper is the manual browser-capture path for pages the user is
  actively reading.
- Default folder: `Sources/web-clips`.
- Default template: `web_clipper_templates/line-inspiration-web-clip.json`.
- Selection template: `web_clipper_templates/line-inspiration-selection-clip.json`.
- Web Clipper notes should use `source_type: webpage`,
  `capture_status: full`, `extractor: web-clipper`, and
  `needs_review: true`.
- Selected text or term-definition clips should go to `Sources/web-clips/terms`
  with `capture_status: partial` and `extractor: web-clipper-selection`.
- Do not auto-generate a summary in the clipper template. Save the page content
  plus a review checklist; later digestion can happen through weekly review or
  a separate AI workflow.

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
- `desktop_voice_manager.py` is the desktop management UI for editing settings,
  reviewing local history, restarting the listener, and installing or removing
  per-user Windows startup.
- Common manager settings should use dropdowns or editable dropdowns instead of
  requiring users to remember raw values. Path settings should keep browse
  buttons.
- `open_desktop_voice_manager.cmd` is the double-click launcher for the manager.
- The recording overlay should include a settings button that opens the desktop
  manager without interrupting the current recording.
- If the overlay is hidden, the next voice hotkey should reopen the overlay only;
  recording should require another explicit action after the overlay is visible.
- For the everyday desktop flow, keep `overlay_idle_seconds` at `0` so the
  overlay stays visible and the record/save hotkey sequence stays predictable.
- Keep the default silence gate lenient (`min_rms: 0.001`, `min_peak: 0.008`)
  and surface skipped-recording reasons in the overlay/history before tightening
  microphone thresholds.
- The overlay should prioritize readable status text over visual polish. Improve
  typography and contrast before building a custom UI.
- Do not override `Ctrl+Z`.
- Future desktop work: make the manager more polished and consider a packaged
  installer if the Startup-folder launcher is not enough.

## Weekly Review Rules

- `本週回顧` lists new captures and their capture status.
- `消化狀態` lists full, partial, failed, and needs-review notes.
- `整理本週` creates a weekly digest in `40_Outputs/weekly-digests/` and logs the
  result in `log.md`.
