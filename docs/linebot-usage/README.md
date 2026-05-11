# LINE Bot 使用圖卡

這個資料夾放 LINE Bot 功能與使用方法的 SVG 圖卡。

- `linebot-usage-01-overview.svg`: 使用總覽與四種主要輸入
- `linebot-usage-02-url-sources.svg`: 網址來源支援範圍
- `linebot-usage-03-commands.svg`: 指令速查
- `linebot-usage-04-buttons.svg`: 按鈕顯示規則

LINE Bot 會使用同名 `.png` 圖檔回傳給使用者。輸入 `/?`、`/？`、
`功能`、`功能說明`、`使用說明`、`使用方法`、`按鈕`、`指令` 或
`help` 時，Bot 會回覆一段簡短說明加四張圖卡。

設計原則：日常 capture 直接丟內容；只有需要使用者選擇時才顯示按鈕。
