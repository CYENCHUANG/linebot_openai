import os
import traceback
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    PostbackEvent, MemberJoinedEvent, FollowEvent,
    QuickReply, QuickReplyButton, MessageAction
)
import google.generativeai as genai
from datetime import datetime
from collections import OrderedDict
import psutil

app = Flask(__name__)

# 初始化 LINE Bot
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# 初始化 Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# 載入 Prompt 模板（只讀一次）
try:
    with open("prompt_config.md", "r", encoding="utf-8") as f:
        PROMPT_TEMPLATE = f.read().strip()
except Exception:
    PROMPT_TEMPLATE = ""

# 使用者狀態管理（限制最大容量）
user_status = OrderedDict()
MAX_USERS = 1000

def set_user_status(uid, status):
    if uid in user_status:
        user_status.move_to_end(uid)
    user_status[uid] = status
    if len(user_status) > MAX_USERS:
        user_status.popitem(last=False)

def GPT_response(text):
    try:
        prompt = f"{PROMPT_TEMPLATE}\n\n{text.strip()}"
        response = gemini_model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.4,
                "max_output_tokens": 600,
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

def handle_translation_mode(msg):
    prompt = f"""請對以下內容做詳細處理：

1. 中英文對照翻譯
2. 用字與文法優化建議

原文：{msg}

請限制回覆在 300 字內，並以條列方式回答，格式清楚易讀。"""
    return GPT_response(prompt)

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

@app.route("/ping", methods=["GET"])
def ping():
    now = datetime.utcnow().isoformat()
    mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    return jsonify({"status": "ok", "timestamp": now, "memory_MB": round(mem, 2)}), 200

def quick_reply_buttons():
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="翻譯小助理", text="啟動翻譯小助理")),
        QuickReplyButton(action=MessageAction(label="結束翻譯小助理", text="結束翻譯小助理")),
    ])

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    uid = event.source.user_id
    msg = event.message.text.strip()

    try:
        if msg == "啟動翻譯小助理":
            set_user_status(uid, "translating")
            reply_text = "已啟動翻譯小助理！請輸入想要翻譯的內容。"

        elif msg == "結束翻譯小助理":
            if user_status.get(uid) == "translating":
                user_status.pop(uid, None)
                reply_text = "已退出翻譯小助理功能。"
            else:
                reply_text = "你目前不在翻譯小助理模式。"

        elif user_status.get(uid) == "translating":
            reply_text = handle_translation_mode(msg)

        else:
            prompt = f"""請針對以下內容簡短回覆，限 300 字內：

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
    message = TextSendMessage(
        text=f'{name} 歡迎加入！目前作者正在白金打工！請多多指教！',
        quick_reply=quick_reply_buttons()
    )
    line_bot_api.reply_message(event.reply_token, message)

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    message = TextSendMessage(
        text="歡迎使用本 Bot，請點選下方按鈕開始。",
        quick_reply=quick_reply_buttons()
    )
    line_bot_api.push_message(user_id, message)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
