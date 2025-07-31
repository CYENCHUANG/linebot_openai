from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    PostbackEvent, MemberJoinedEvent, FollowEvent,
    QuickReply, QuickReplyButton, MessageAction
)
import os
import traceback
import google.generativeai as genai
import threading
import requests
import time
from functools import lru_cache



app = Flask(__name__)

# 初始化 LINE Bot
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# 初始化 Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# 用戶狀態管理 (重啟後清空)
user_status = {}


# 讀入模板
def load_prompt_template():
    try:
        with open("prompt_config.md", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""  # 若找不到模板就略過，不讓程式中斷


# 快取 GPT 回應結果，減少重複呼叫
@lru_cache(maxsize=256)
def GPT_response(text):
    try:
        model = genai.GenerativeModel("gemini-2.5-flash-lite")
        prompt = f"{load_prompt_template()}\n\n{text.strip()}"  # 套用模板

        response = model.generate_content(  # ✅ 正確使用 prompt
            prompt,
            generation_config={
                "temperature": 0.4,
                "max_output_tokens": 400,  # 約 200 中文字
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
        return response.text.strip()
    except Exception as e:
        print("[Gemini ERROR]", e)
        return "⚠️ AI 回應發生錯誤，請稍後再試或檢查 API 金鑰。"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# 建立通用 Quick Reply 按鈕（含啟動及結束翻譯小助理）
def quick_reply_buttons():
    return QuickReply(
        items=[
            QuickReplyButton(
                action=MessageAction(label="翻譯小助理", text="啟動翻譯小助理")
            ),
            QuickReplyButton(
                action=MessageAction(label="結束翻譯小助理", text="結束翻譯小助理")
            ),
        ]
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    uid = event.source.user_id
    msg = event.message.text.strip()

    try:
        if msg == "啟動翻譯小助理":
            user_status[uid] = "translating"
            reply_text = "已啟動翻譯小助理！請輸入想要翻譯的內容，我會同時提供中英文翻譯、文法優化和同義詞建議。"
        
        elif msg == "結束翻譯小助理":
            if user_status.get(uid) == "translating":
                user_status.pop(uid, None)
                reply_text = "已退出翻譯小助理功能，如需再次使用請點選下方「翻譯小助理」按鈕。"
            else:
                reply_text = "你目前不在翻譯小助理模式。"

        elif user_status.get(uid) == "translating":
            prompt = f"""請對以下內容做詳細處理：

1. 中英文對照翻譯
2. 用字與文法優化建議

原文：{msg}

請限制回覆在 200 字內，並以條列方式回答，格式清楚易讀。"""
            reply_text = GPT_response(prompt)

        else:
            prompt = f"""請針對以下內容簡短回覆，限 200 字內：
            
使用者輸入：{msg}"""
            reply_text = GPT_response(prompt)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons())
        )

    except Exception:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text='AI 回應發生錯誤，請檢查伺服器 Log 或 API 金鑰。', quick_reply=quick_reply_buttons())
        )





@handler.add(PostbackEvent)
def handle_postback(event):
    print(f"Postback data: {event.postback.data}")







@handler.add(MemberJoinedEvent)
def welcome(event):
    uid = event.joined.members[0].user_id
    gid = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name = profile.display_name
    message = TextSendMessage(text=f'{name} 歡迎加入！目前我正在白金打工！請多多指教！', quick_reply=quick_reply_buttons())
    line_bot_api.reply_message(event.reply_token, message)

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    # 新用戶追蹤時推送啟動翻譯小助理的 Quick Reply
    message = TextSendMessage(
        text="歡迎使用本 Bot，請點選下方按鈕開始。",
        quick_reply=quick_reply_buttons()
    )
    line_bot_api.push_message(user_id, message)

# Render 自我喚醒，避免服務睡死
WAKE_URL = 'https://linebot-openai-kysy.onrender.com/callback'  # 請替換為你的正式網址

def keep_awake():
    while True:
        try:
            res = requests.get(WAKE_URL)
            print(f"[WAKE-UP] Status: {res.status_code}")
        except Exception as e:
            print(f"[WAKE-UP ERROR] {e}")
        time.sleep(300)  # 每14分鐘喚醒一次

if __name__ == "__main__":
    threading.Thread(target=keep_awake, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
