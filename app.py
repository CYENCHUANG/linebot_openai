from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *

#====== 函數庫 ======
import os
import traceback
import google.generativeai as genai
#====== 函數庫 ======

# Flask 設定
app = Flask(__name__)

# LINE Bot Token & Secret
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))

# Gemini API 初始化設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# GPT 回應函式
def GPT_response(text):
    model = genai.GenerativeModel("gemini-2.5")

    response = model.generate_content(
        text,
        generation_config={
            "temperature": 0.5,
            "max_output_tokens": 500
        }
    )

    answer = response.text.strip().replace("。", "")
    return answer

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

# 處理使用者文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    try:
        GPT_answer = GPT_response(msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(GPT_answer))
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
    message = TextSendMessage(text=f'{name} 歡迎加入！')
    line_bot_api.reply_message(event.reply_token, message)

# 啟動伺服器
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
