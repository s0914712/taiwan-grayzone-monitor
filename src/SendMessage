import os
import requests

# 讀取 GitHub Secrets 中的環境變數
token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
user_id = os.environ.get("LINE_USER_ID")

# LINE Push Message API 的端點
url = "https://api.line.me/v2/bot/message/push"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# 這裡設定你要發送的訊息內容
payload = {
    "to": user_id,
    "messages": [
        {
            "type": "text",
            "text": "早安！這是由 GitHub Actions 自動排程發送的訊息 🤖"
        }
    ]
}

# 發送 POST 請求
response = requests.post(url, headers=headers, json=payload)

if response.status_code == 200:
    print("訊息發送成功！")
else:
    print(f"發送失敗：{response.status_code}")
    print(response.text)
