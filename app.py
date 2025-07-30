from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import os
import traceback
import google.generativeai as genai
import threading
import requests
import time
from functools import lru_cache  # ✅ 加入快取功能

# Flask 設定
app = Flask(__name__)

# LINE Bot Token & Secret
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# Gemini API 初始化設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ✅ GPT 回應函式（強化模型 + 快取 + 錯誤處理 + 安全性）
@lru_cache(maxsize=256)
def GPT_response(text):
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        response = model.generate_content(
            text,
            generation_config={
                "temperature": 0.4,
                "max_output_tokens": 300,
                "top_p": 0.9,
                "top_k": 40
            },
            safety_settings=[
                {"category": "HARM_CATEGORY_SEXUAL", "threshold": 3},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": 3},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": 3},
                {"category": "HARM_CATEGORY_DANGEROUS", "threshold": 3}
            ]
        )
        answer = response.text.strip().replace("。", "")
        return answer
    except Exception as e:
        print("[Gemini ERROR]", e)
        return "⚠️ AI 回應發生錯誤，請稍後再試或檢查 API 金鑰。"

# 接收 LINE callback
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# 分段工具（用於 Flex Message）
def split_text(text, max_length=400):
    return [text[i:i+max_length] for i in range(0, len(text), max_length)]

# 處理使用者文字訊息 with Flex Message 回覆
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    try:
        GPT_answer = GPT_response(msg)
        parts = split_text(GPT_answer, 400)

        bubbles = []
        for part in parts[:5]:
            bubble = {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [{
                        "type": "text",
                        "text": part,
                        "wrap": True,
                        "size": "sm"
                    }]
                }
            }
            bubbles.append(bubble)

        flex_contents = {
            "type": "carousel",
            "contents": bubbles
        }

        if len(parts) > 5:
            tips = TextSendMessage(text="⚠️ 回覆內容較長，僅顯示前五段。如需完整內容請改用 Web 或郵件查詢。")
            line_bot_api.reply_message(event.reply_token, [FlexSendMessage(alt_text="AI 回覆", contents=flex_contents), tips])
        else:
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="AI 回覆", contents=flex_contents))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage('AI 回應發生錯誤，請查看伺服器 Log 訊息或確認 API 金鑰是否有效'))

# 處理 Postback 回傳事件（可擴充）
@handler.add(PostbackEvent)
def handle_postback(event):
    print(event.postback.data)

# 新成員加入群組歡迎訊息
@handler.add(MemberJoinedEvent)
def welcome(event):
    uid = event.joined.members[0].user_id
    gid = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name = profile.display_name
    message = TextSendMessage(text=f'{name} 歡迎加入！目前我正在白金打工！請多多指教！')
    line_bot_api.reply_message(event.reply_token, message)

# Render 自我喚醒機制（避免睡死）
WAKE_URL = 'https://linebot-openai-kysy.onrender.com/callback'  # ⬅️ 改成你的正式網址

def keep_awake():
    while True:
        try:
            res = requests.get(WAKE_URL)
            print(f"[WAKE-UP] Status: {res.status_code}")
        except Exception as e:
            print(f"[WAKE-UP ERROR] {e}")
        time.sleep(840)  # 每 14 分鐘喚醒一次

# 啟動伺服器
if __name__ == "__main__":
    threading.Thread(target=keep_awake, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
