from flask import Flask, request, jsonify, render_template
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
import os

app = Flask(__name__)

MASTER_ACCESS_KEY = "noobster"
DEFAULT_API_ID = 1234567      # <-- Yahan apna API ID daal
DEFAULT_API_HASH = "your_hash" # <-- Yahan apna API Hash daal

accounts = {}

def check_auth(req):
    return req.headers.get("Authorization") == "secure_token_noobster"

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if data.get('accessKey') == MASTER_ACCESS_KEY:
        return jsonify({"success": True, "token": "secure_token_noobster"})
    return jsonify({"error": "Invalid Key"}), 401

# ============================================
# ALAG ASYNC FUNCTIONS (Error Fix)
# ============================================
async def login_pyrogram(session_str, api_id, api_hash):
    from pyrogram import Client as PyroClient
    client = PyroClient(":memory:", api_id=api_id, api_hash=api_hash, session_string=session_str, in_memory=True)
    await client.start()
    me = await client.get_me()
    return client, me

async def login_telethon(session_str, api_id, api_hash):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        return None, None
    me = await client.get_me()
    return client, me

@app.route('/api/add-account', methods=['POST'])
def add_account():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    session_str = data.get('session', '').strip()
    api_id = int(data.get('apiId') or DEFAULT_API_ID)
    api_hash = data.get('apiHash') or DEFAULT_API_HASH

    if not session_str:
        return jsonify({"error": "Session required"}), 400

    try:
        if session_str.startswith("BQ") or session_str.startswith("1B"):
            # Pyrogram Login
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client, me = loop.run_until_complete(login_pyrogram(session_str, api_id, api_hash))
            
            acc_id = str(me.id)
            accounts[acc_id] = {"type": "pyrogram", "client": client, "me": me, "loop": loop}
            return jsonify({"success": True, "id": acc_id})
        else:
            # Telethon Login
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client, me = loop.run_until_complete(login_telethon(session_str, api_id, api_hash))

            if not client:
                return jsonify({"error": "Invalid Session or Expired"}), 401
                
            acc_id = str(me.id)
            accounts[acc_id] = {"type": "telethon", "client": client, "me": me, "loop": loop}
            return jsonify({"success": True, "id": acc_id})

    except Exception as e:
        print("LOGIN ERROR:", e) # Terminal me error dikhega
        return jsonify({"error": str(e)}), 500

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    acc_list = []
    for acc_id, data in accounts.items():
        me = data['me']
        acc_list.append({
            "id": acc_id,
            "name": getattr(me, 'first_name', 'Unknown') or "Unknown",
            "phone": getattr(me, 'phone_number', 'Hidden'),
            "username": getattr(me, 'username', 'None') or "None",
            "photo": f"https://ui-avatars.com/api/?name={getattr(me, 'first_name', 'U')}&background=2b5278&color=fff"
        })
    return jsonify(acc_list)

@app.route('/api/chats/<acc_id>', methods=['GET'])
def get_chats(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    data = accounts[acc_id]
    client = data['client']
    loop = data['loop']
    chats = []

    try:
        if data['type'] == 'pyrogram':
            async def fetch_pyro():
                return [d async for d in client.get_dialogs(limit=30)]
            dialogs = loop.run_until_complete(fetch_pyro())
            for d in dialogs:
                chats.append({
                    "id": str(d.chat.id), 
                    "name": d.chat.first_name or d.chat.title or "Unknown", 
                    "lastMsg": d.chat.last_message.text if d.chat.last_message and d.chat.last_message.text else "[Media]"
                })
        else:
            async def fetch_tele():
                return await client.get_dialogs(limit=30)
            dialogs = loop.run_until_complete(fetch_tele())
            for d in dialogs:
                chats.append({
                    "id": str(d.id), 
                    "name": d.name or "Unknown", 
                    "lastMsg": d.message.message if d.message else "[Media]"
                })
        return jsonify(chats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/messages/<acc_id>/<chat_id>', methods=['GET'])
def get_messages(acc_id, chat_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    data = accounts[acc_id]
    client = data['client']
    loop = data['loop']
    msgs = []

    try:
        if data['type'] == 'pyrogram':
            async def fetch_msgs_pyro():
                return [m async for m in client.get_messages(chat_id, limit=40)]
            messages = loop.run_until_complete(fetch_msgs_pyro())
            for m in reversed(messages):
                msgs.append({"text": m.text or "[Media]", "isMine": m.outgoing})
        else:
            async def fetch_msgs_tele():
                return await client.get_messages(chat_id, limit=40)
            messages = loop.run_until_complete(fetch_msgs_tele())
            for m in reversed(messages):
                msgs.append({"text": m.message or "[Media]", "isMine": m.out})
        return jsonify(msgs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/send/<acc_id>/<chat_id>', methods=['POST'])
def send_message(acc_id, chat_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    text = request.json.get('text')
    data = accounts[acc_id]
    client = data['client']
    loop = data['loop']

    try:
        if data['type'] == 'pyrogram':
            async def send_pyro():
                await client.send_message(chat_id, text)
            loop.run_until_complete(send_pyro())
        else:
            async def send_tele():
                await client.send_message(chat_id, text)
            loop.run_until_complete(send_tele())
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
