import os  # 匯入作業系統相關函式（讀環境變數等）
import traceback  # 匯入 traceback 用來印出完整錯誤堆疊
from flask import Flask, request, abort, jsonify  # 從 Flask 匯入 Web 相關物件與方法
from linebot import LineBotApi, WebhookHandler  # 匯入 LINE Bot API 與 Webhook 處理器
from linebot.exceptions import InvalidSignatureError  # 匯入 LINE 簽章驗證錯誤例外
from linebot.models import (  # 匯入各種 LINE 訊息與事件的模型類別
    MessageEvent, TextMessage, TextSendMessage,
    PostbackEvent, MemberJoinedEvent, FollowEvent,
    QuickReply, QuickReplyButton, MessageAction
)
import google.generativeai as genai  # 匯入 Google Generative AI SDK 並簡寫為 genai
from datetime import datetime  # 匯入 datetime 以取得時間
from collections import OrderedDict  # 匯入 OrderedDict 以保留插入順序的字典
import psutil  # 匯入 psutil 取得系統資源使用狀況

app = Flask(__name__)  # 建立 Flask 應用程式實例

# 初始化 LINE Bot
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))  # 用環境變數中的 CHANNEL_ACCESS_TOKEN 建立 LineBotApi
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))  # 用環境變數中的 CHANNEL_SECRET 建立 WebhookHandler

# 初始化 Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))  # 使用環境變數中的 GEMINI_API_KEY 設定 Gemini API 金鑰
GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"  # 指定使用的 Gemini 模型名稱
gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME)  # 建立指定模型的 GenerativeModel 實例

# 載入 Prompt 模板（只讀一次）
try:
    with open("prompt_config.md", "r", encoding="utf-8") as f:  # 嘗試讀取本地的 prompt_config.md 作為提示模板
        PROMPT_TEMPLATE = f.read().strip()  # 讀入檔案內容並去除前後空白儲存到 PROMPT_TEMPLATE
except Exception:
    PROMPT_TEMPLATE = ""  # 若讀檔發生錯誤就使用空字串作為預設模板

# 使用者狀態管理（限制最大容量）
user_status = OrderedDict()  # 使用 OrderedDict 保存使用者狀態（可做 LRU 式管理）
MAX_USERS = 1000  # 最多紀錄 1000 位使用者狀態

def set_user_status(uid, status):  # 設定特定使用者的狀態
    if uid in user_status:  # 如果使用者已存在於狀態字典
        user_status.move_to_end(uid)  # 將該使用者移到 OrderedDict 末端（視為最近使用）
    user_status[uid] = status  # 更新或新增使用者狀態
    if len(user_status) > MAX_USERS:  # 如果超過最大容量
        user_status.popitem(last=False)  # 移除最前面的舊使用者（last=False 代表從頭部 pop）

def GPT_response(text):  # 封裝呼叫 Gemini 模型產生回覆的函式
    try:
        prompt = f"{PROMPT_TEMPLATE}\n\n{text.strip()}"  # 將預設 PROMPT_TEMPLATE 與使用者輸入內容組成完整提示
        response = gemini_model.generate_content(  # 呼叫 Gemini 產生內容
            prompt,
            generation_config={  # 設定生成參數
                "temperature": 0.4,  # 溫度較低，使回答較穩定
                "max_output_tokens": 600,  # 回應最大 token 數
                "top_p": 0.9,  # nucleus sampling 參數
                "top_k": 40  # 最多考慮的候選詞數
            },
            safety_settings=[  # 安全性設定，避免產出有害內容
                {"category": "HARM_CATEGORY_SEXUAL", "threshold": 3},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": 3},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": 3},
                {"category": "HARM_CATEGORY_DANGEROUS", "threshold": 3}
            ]
        )
        return response.text.strip()  # 回傳模型回應的純文字並去除前後空白
    except Exception as e:
        print("[Gemini ERROR]", e)  # 若呼叫失敗，在伺服器端印出錯誤訊息
        return "⚠️ AI 回應發生錯誤，請稍後再試或檢查 API 金鑰。"  # 回傳給使用者的錯誤訊息

def handle_translation_mode(msg):  # 處理「翻譯小助理」模式的函式
    prompt = f"""請對以下內容做詳細處理：

1. 中英文對照翻譯
2. 用字與文法優化建議

原文：{msg}

請限制回覆在 300 字內，並以條列方式回答，格式清楚易讀。"""
    return GPT_response(prompt)  # 將組好的翻譯提示丟給 GPT_response 取得回覆

@app.route("/callback", methods=['POST'])  # 定義 LINE Webhook callback 路由，只接受 POST
def callback():
    signature = request.headers.get('X-Line-Signature', '')  # 從 HTTP Header 取得 X-Line-Signature 驗證簽章
    body = request.get_data(as_text=True)  # 取得請求的原始 body 文字
    app.logger.info("Request body: " + body)  # 將請求 body 記錄到 log
    try:
        handler.handle(body, signature)  # 交給 WebhookHandler 驗證簽章並分派事件
    except InvalidSignatureError:
        abort(400)  # 簽章驗證失敗就回傳 400 Bad Request
    return 'OK'  # 驗證成功並處理事件後回傳 OK

@app.route("/ping", methods=["GET"])  # 健康檢查路由，用 GET 呼叫 /ping
def ping():
    now = datetime.utcnow().isoformat()  # 使用 UTC 時區取得現在時間並轉成 ISO 字串
    mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024  # 取得目前行程記憶體使用量 (RSS) 並轉成 MB
    return jsonify({"status": "ok", "timestamp": now, "memory_MB": round(mem, 2)}), 200  # 回傳 JSON 狀態與 HTTP 200

def quick_reply_buttons():  # 建立預設的 quick reply 按鈕
    return QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="翻譯小助理", text="啟動翻譯小助理")),  # 點擊後送出文字「啟動翻譯小助理」
        QuickReplyButton(action=MessageAction(label="結束翻譯小助理", text="結束翻譯小助理")),  # 點擊後送出文字「結束翻譯小助理」
    ])

@handler.add(MessageEvent, message=TextMessage)  # 當收到文字訊息事件時，由此 handler 處理
def handle_message(event):
    uid = event.source.user_id  # 取得觸發事件的使用者 ID
    msg = event.message.text.strip()  # 取得使用者輸入的文字並去除前後空白

    try:
        if msg == "啟動翻譯小助理":  # 若使用者輸入啟動指令
            set_user_status(uid, "translating")  # 將該使用者狀態設為 translating
            reply_text = "已啟動翻譯小助理！請輸入想要翻譯的內容。"  # 回覆開啟翻譯模式訊息

        elif msg == "結束翻譯小助理":  # 若使用者輸入結束指令
            if user_status.get(uid) == "translating":  # 判斷目前是否處於翻譯模式
                user_status.pop(uid, None)  # 從狀態管理中移除該使用者紀錄
                reply_text = "已退出翻譯小助理功能。"  # 告知使用者已退出翻譯模式
            else:
                reply_text = "你目前不在翻譯小助理模式。"  # 告知目前沒有啟用翻譯模式

        elif user_status.get(uid) == "translating":  # 若使用者目前狀態為翻譯模式
            reply_text = handle_translation_mode(msg)  # 將使用者訊息丟給翻譯模式處理

        else:  # 其他一般文字訊息處理
            prompt = f"""請針對以下內容簡短回覆，限 300 字內：

使用者輸入：{msg}"""
            reply_text = GPT_response(prompt)  # 使用 GPT_response 產生一般問答回覆

        line_bot_api.reply_message(  # 回覆訊息給使用者
            event.reply_token,
            TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons())  # 附上 quick reply 按鈕
        )

    except Exception:
        print(traceback.format_exc())  # 若處理訊息時發生任何錯誤，印出完整錯誤堆疊
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text='AI 回應發生錯誤，請檢查伺服器 Log 或 API 金鑰。', quick_reply=quick_reply_buttons())  # 回覆通用錯誤提示
        )

@handler.add(PostbackEvent)  # 監聽處理 Postback 事件
def handle_postback(event):
    print(f"Postback data: {event.postback.data}")  # 將 Postback 的 data 內容印出（目前只做記錄，不做邏輯）

@handler.add(MemberJoinedEvent)  # 監聽群組新成員加入事件
def welcome(event):
    uid = event.joined.members[0].user_id  # 取得新加入成員的 user_id（假設第一個為新成員）
    gid = event.source.group_id  # 取得觸發事件的群組 ID
    profile = line_bot_api.get_group_member_profile(gid, uid)  # 透過 API 取得該成員在群組中的個人資料
    name = profile.display_name  # 取得顯示名稱
    message = TextSendMessage(
        text=f'{name} 歡迎加入！目前作者正在白金打工！請多多指教！',  # 建立歡迎訊息文字
        quick_reply=quick_reply_buttons()  # 附上 quick reply 按鈕
    )
    line_bot_api.reply_message(event.reply_token, message)  # 回覆歡迎訊息到群組

@handler.add(FollowEvent)  # 監聽使用者加入好友/追蹤 Bot 的事件
def handle_follow(event):
    user_id = event.source.user_id  # 取得新追蹤者的 user_id
    message = TextSendMessage(
        text="歡迎使用本 Bot，請點選下方按鈕開始。",  # 發送歡迎使用的文字
        quick_reply=quick_reply_buttons()  # 附上 quick reply 按鈕
    )
    line_bot_api.push_message(user_id, message)  # 主動推播歡迎訊息給新追蹤者

if __name__ == "__main__":  # 當此檔案被直接執行時進入這裡
    port = int(os.environ.get('PORT', 5000))  # 從環境變數讀取 PORT，若沒有就預設 5000
    app.run(host='0.0.0.0', port=port)  # 啟動 Flask 伺服器，對外監聽所有網路介面
