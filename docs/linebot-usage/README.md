# LINE Bot 使用圖卡

這個資料夾放 LINE Bot 功能與使用方法的 SVG 圖卡。

- `linebot-usage-01-overview.svg`: 使用總覽與四種主要輸入
- `linebot-usage-02-url-sources.svg`: 網址來源支援範圍
- `linebot-usage-03-commands.svg`: 指令速查
- `linebot-usage-04-buttons.svg`: 按鈕顯示規則
- `linebot-usage-05-workflow.svg`: 整體使用 workflow
- `linebot-usage-06-agent-rhythm.svg`: 定期整理與 AI Agent 節奏

LINE Bot 會使用同名 `.png` 圖檔回傳給使用者。輸入 `/?`、`/？`、
`功能`、`功能說明`、`使用說明`、`使用方法`、`按鈕`、`指令` 或
`help` 時，Bot 會回覆一段簡短說明加四張圖卡。

輸入 `工作流`、`整理流程`、`定期整理`、`每週整理`、`AI整理`、
`agent` 或 `workflow` 時，Bot 會回覆整體使用節奏與 AI Agent 定期
整理圖卡。

設計原則：日常 capture 直接丟內容；只有需要使用者選擇時才顯示按鈕。
