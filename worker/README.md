# LINE 到工統計助手（Cloudflare Worker）

每個上班日：

- **16:00**（TW）在群組 @全員 詢問**隔一個上班日上午**的到工狀況，附 Flex 按鈕（在部／請假／公出・出差）
- **20:00**（TW）依 `data/attendance_roster.json` 的名冊順序統整並公布，同時把當日紀錄存回
  `data/attendance/<YYYY-MM>.json`

```
8月4日（二）上午到工狀況
科長-在部
陳昱-在部
葉維展-在三大合署作業
王耀駿-未回報
──────────
在部 12｜請假 1｜公出 1｜未回報 1
```

## 為什麼是 Worker 而不是 GitHub Actions

repo 內既有的 `src/SendMessage.py` 是**單向推播**（只呼叫 `/v2/bot/message/push`）。要**讀取**群組
回覆就必須有 24h 在線的 HTTPS endpoint 接 LINE webhook，而 GitHub Actions 是 cron，收不到 webhook。
附帶好處：Cloudflare Cron Triggers 準時，GitHub Actions 排程常延遲 5–30 分鐘。

本 Worker 與既有的每日海警推播（`LINEBot.yml` + `SendMessage.py`）**互不影響**，可以共用同一個
LINE channel。

## 群組 ID 不需要人工設定

把 bot 邀進群組時 LINE 會送 `join` 事件，`source.groupId` 就寫進 KV；之後兩支 cron 對所有已註冊
群組推播。bot 被踢出時 `leave` 事件自動移除。可以同時服務多個群組。

## 架構

| 檔案 | 職責 |
|---|---|
| `src/index.js` | webhook 路由 + cron 分派；`runAsk()` / `runSummary()` |
| `src/attendance.js` | **純函式**：日期計算、狀態解析、名冊配對、清冊格式化 |
| `src/line.js` | LINE API client、驗簽、`buildAskMessages()` |
| `src/storage.js` | Workers KV 存取（群組、當日紀錄、名冊快取） |
| `src/github.js` | Contents API 寫回 `data/attendance/<YYYY-MM>.json` |

KV 是即時狀態，repo 內的 JSON 是永久存檔。只有 20:00 那一支 cron 會寫月檔，無併發競爭。

## 部署

```bash
cd worker
npm install -g wrangler          # 或用 npx

# 1. 建 KV namespace，把印出的 id 填進 wrangler.toml
npx wrangler kv namespace create ATTENDANCE

# 2. 設定 secrets
npx wrangler secret put LINE_CHANNEL_ACCESS_TOKEN   # 與現有 GitHub secret 同一把
npx wrangler secret put LINE_CHANNEL_SECRET         # LINE Console → Basic settings
npx wrangler secret put GITHUB_TOKEN                # fine-grained PAT，只給本 repo contents: write
npx wrangler secret put ADMIN_KEY                   # /admin/* 端點的共用密鑰（自己隨機產一個）

# 3. 部署
npx wrangler deploy
```

> 既有的 GitHub secret `CLAUDEFARETOKEN` 是 **Radar Read** 權限，**不能**用來部署 Worker。
> 要做 CI 自動部署需另開一把有 `Workers Scripts: Edit` 的 token。

### LINE 後台設定（一次性）

**LINE Developers Console → Messaging API**
- Webhook URL = `https://<your-worker>.workers.dev/webhook`
- `Use webhook` 開啟 → 按 **Verify**，須回 200

**LINE Official Account Manager → 回應設定**
- **允許加入群組／多人聊天室**：開啟
- **自動回應訊息**：**關閉**（不關的話每則群組訊息都會被官方罐頭訊息洗版）

### 建立名冊

1. 把 bot 邀進群組（會收到「已加入」回覆）
2. 每位同仁在群組送一次 `/我是`，bot 回覆其 `userId`
3. 或由科長送 `/名冊`，一次匯出所有已記錄成員，是可直接貼進 JSON 的格式
4. 把 `userId` 填進 `data/attendance_roster.json` 並 commit 到 `main`

`members` 的順序就是清冊的輸出順序。Worker 每 24 小時重讀名冊，**改名冊不用重新部署**。
`userId` 留 `null` 也能運作 —— 會退回用 LINE 顯示名稱／`aliases` 比對。

## 群組指令

| 指令 | 作用 |
|---|---|
| 點按鈕 | 回報自己的狀態（按鈕留在訊息裡，不會被其他人的發言蓋掉） |
| `到工 在三大合署作業` | 自訂狀態（前綴也可用 `回報` / `/到工`） |
| `在部` / `請假` / `公出` … | 整則訊息剛好等於關鍵字時才算回報 |
| `葉維展-在三大合署作業` | 代人回報（姓名須在名冊內），可整份多行貼上 |
| `/我是` | 查自己的 userId |
| `/名冊` | 匯出已知成員的 userId 清單 |
| `/提問` | 立刻發出回報訊息（不清空既有回報） |
| `/統計` | 立刻統整並公布 |
| `/說明` | 指令說明 |

## 管理端點

都需要 `?key=<ADMIN_KEY>`：

```bash
curl "https://<worker>/admin/state"                    # 目前開放日、群組、當日紀錄
curl "https://<worker>/admin/ask?key=$ADMIN_KEY"       # 手動發問
curl "https://<worker>/admin/summary?key=$ADMIN_KEY"   # 手動統整
curl "https://<worker>/admin/roster?key=$ADMIN_KEY"    # 強制重讀名冊
curl "https://<worker>/health"                         # 免密鑰
```

## 開發與測試

```bash
cd worker
node --test                                  # 純函式單元測試（CI 也跑這個）

npx wrangler dev                             # 本機起 Worker
# 另一個終端：送出帶合法簽章的合成事件
LINE_CHANNEL_SECRET=xxx node scripts/send-fixture.js join
LINE_CHANNEL_SECRET=xxx node scripts/send-fixture.js postback 2026-08-04 in_office
LINE_CHANNEL_SECRET=xxx node scripts/send-fixture.js text "到工 在三大合署作業"

npx wrangler tail                            # 線上即時 log
```

名冊 schema 由 Python 端把關：`python -m pytest tests/test_attendance_roster.py -v`。

## 已知限制

- **國定假日不處理**：只跳過週六日。連假前一天仍會發問，需要時人工忽略，或當天不理它
  （沒人回報時 20:00 的清冊會整排「未回報」）。
- **`textV2` @全員**：若該帳號／區域不支援，`pushMessages` 收到非 2xx 會自動用不含 mention 的
  純文字重送 —— 功能不受影響，只是少了 @全員 通知。
- **名冊需人工維護**：LINE 的「取得群組成員 userId 清單」端點需要認證／付費帳號，本案刻意不
  依賴它。新同仁入群後要跑一次 `/我是` 並補進名冊，在那之前會以 LINE 顯示名稱列在「名冊外」。
- **同時回報可能漏記**：當日紀錄是單一 KV 鍵（`day:<iso>`）的 read-modify-write，KV 沒有 CAS，
  兩個人在同一秒內回報理論上可能有一筆被覆蓋。14 人分散在 4 小時內回報，實務上碰不到；真的
  漏了就再點一次按鈕（同一個人重複回報以最後一次為準）。要根治得換成一人一鍵或 Durable Object。
