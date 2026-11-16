import asyncio
import os
import threading

# 🔥 PYTHON 3.14 FIX
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask, request, jsonify, render_template
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat
from telethon.tl.types.account import PasswordInputSettings
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    GetPasswordRequest,
    UpdatePasswordSettingsRequest,
    SendVerifyEmailCodeRequest,
    VerifyEmailRequest
)

app = Flask(__name__)
MASTER_ACCESS_KEY = os.environ.get("MASTER_ACCESS_KEY", "noobster")
accounts = {}

def check_auth(req):
    return req.headers.get("Authorization") == "secure_token_noobster"

# ---------- 🔧 PERSISTENT EVENT LOOP ----------
# One loop lives for the whole process. All Telethon clients bind to THIS loop.
_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()

def run_async(coro):
    """Submit coroutine to the persistent loop from any thread."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()

# ---------- auth ----------
@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json or {}
    if data.get('accessKey') == MASTER_ACCESS_KEY:
        return jsonify({"success": True, "token": "secure_token_noobster"})
    return jsonify({"error": "Invalid Key"}), 401

async def login_pyrogram(session_str, api_id, api_hash):
    from pyrogram import Client as PyroClient
    client = PyroClient(":memory:", api_id=api_id, api_hash=api_hash,
                        session_string=session_str, in_memory=True)
    await client.start()
    me = await client.get_me()
    return client, me

async def login_telethon(session_str, api_id, api_hash):
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
    data = request.json or {}
    session_str = (data.get('session') or '').strip()
    raw_id = data.get('apiId')
    api_hash = (data.get('apiHash') or '').strip()

    if not session_str: return jsonify({"error": "Session required"}), 400
    if not raw_id or not api_hash: return jsonify({"error": "API ID and API Hash required"}), 400
    try:
        api_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "API ID must be numeric"}), 400

    try:
        if session_str.startswith(("BQ", "1B")):
            client, me = run_async(login_pyrogram(session_str, api_id, api_hash))
            atype = "pyrogram"
        else:
            client, me = run_async(login_telethon(session_str, api_id, api_hash))
            atype = "telethon"
            if not client:
                return jsonify({"error": "Invalid or expired session"}), 401

        accounts[str(me.id)] = {"type": atype, "client": client, "me": me,
                                "api_id": api_id, "api_hash": api_hash, "session": session_str}
        return jsonify({"success": True, "id": str(me.id),
                        "name": getattr(me, 'first_name', 'Unknown')})
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 500

@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    out = []
    for acc_id, d in accounts.items():
        me = d['me']
        out.append({
            "id": acc_id,
            "name": getattr(me, 'first_name', 'Unknown'),
            "phone": getattr(me, 'phone_number', 'Hidden'),
            "type": d['type'],
            "photo": f"https://ui-avatars.com/api/?name={getattr(me, 'first_name', 'U')}&background=2b5278&color=fff"
        })
    return jsonify(out)

@app.route('/api/chats/<acc_id>', methods=['GET'])
def get_chats(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    return jsonify([{"id": "777000", "name": "Telegram (Notification Bot)",
                     "lastMsg": "Verification codes and service updates"}])

@app.route('/api/messages/<acc_id>/<chat_id>', methods=['GET'])
def get_messages(acc_id, chat_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    d = accounts[acc_id]
    client, atype = d['client'], d['type']
    try:
        if atype == 'pyrogram':
            async def fetch(): return [m async for m in client.get_messages(int(chat_id), limit=20)]
            messages = run_async(fetch())
            out = [{"text": m.text or "[Media]", "isMine": m.outgoing} for m in reversed(messages)]
        else:
            async def fetch():
                entity = await client.get_entity(int(chat_id))
                return await client.get_messages(entity, limit=20)
            messages = run_async(fetch())
            out = [{"text": m.message or "[Media]", "isMine": m.out} for m in reversed(messages)]
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": f"Msg Error: {str(e)}"}), 500

@app.route('/api/profile/<acc_id>', methods=['GET'])
def get_profile(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404

    d = accounts[acc_id]; me = d['me']; client = d['client']
    try:
        if d['type'] == 'pyrogram':
            async def fetch_pyro():
                try:
                    hint = await client.get_password_hint()
                    fa = hint is not None
                except Exception:
                    fa = False
                return fa, [], []
            has_2fa, devices, admin_chats = run_async(fetch_pyro())
        else:
            async def fetch_tele():
                pwd_info = await client(GetPasswordRequest())
                auths = await client(GetAuthorizationsRequest())
                devs = [{"device": a.device_model, "ip": a.ip, "current": a.current}
                        for a in auths.authorizations]
                admins = []
                async for dialog in client.iter_dialogs(limit=50):
                    if isinstance(dialog.entity, (Channel, Chat)):
                        try:
                            perms = await client.get_permissions(dialog.entity, "me")
                            if perms.is_admin or perms.is_creator:
                                admins.append({"name": dialog.name,
                                               "type": "Owner" if perms.is_creator else "Admin"})
                        except Exception:
                            pass
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

@app.route('/api/terminate', methods=['POST'])
def terminate_sessions():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, action = data.get('acc_id'), data.get('action')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    try:
        if acc['type'] != 'telethon':
            return jsonify({"error": "Terminate only supported for Telethon."}), 400

        async def terminate():
            auths = await client(GetAuthorizationsRequest())
            count = 0
            for auth in auths.authorizations:
                if action == "current" and auth.current:
                    await client(ResetAuthorizationRequest(hash=auth.hash)); count += 1
                elif action == "all" and not auth.current:
                    await client(ResetAuthorizationRequest(hash=auth.hash)); count += 1
            return count

        count = run_async(terminate())
        if action == "current":
            run_async(client.disconnect())
            del accounts[acc_id]
        return jsonify({"success": True, "message": f"Terminated {count} session(s)."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/change-2fa', methods=['POST'])
def change_2fa():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, new_pass = data.get('acc_id'), data.get('new_password')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    try:
        if acc['type'] != 'telethon':
            return jsonify({"error": "2FA Reset only supported for Telethon."}), 400

        async def set_pwd():
            pwd_info = await client(GetPasswordRequest())
            if not pwd_info.has_password:
                return "NO_2FA"
            await client(UpdatePasswordSettingsRequest(
                password=pwd_info,
                new_settings=PasswordInputSettings(
                    new_algo=pwd_info.new_algo,
                    new_password_hash=pwd_info.srp_B,
                    hint="Reset via Panel", email=None
                )
            ))
            return "SUCCESS"

        result = run_async(set_pwd())
        if result == "NO_2FA":
            return jsonify({"error": "2FA is not enabled."}), 400
        return jsonify({"success": True, "message": "2FA Password changed successfully."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/request-email-otp', methods=['POST'])
def request_email_otp():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, new_email = data.get('acc_id'), data.get('new_email')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    try:
        if acc['type'] != 'telethon':
            return jsonify({"error": "Email change only supported for Telethon."}), 400
        async def send_otp(): await client(SendVerifyEmailCodeRequest(email=new_email))
        run_async(send_otp())
        return jsonify({"success": True, "message": "OTP sent to new email."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/verify-email-otp', methods=['POST'])
def verify_email_otp():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, new_email, otp = data.get('acc_id'), data.get('new_email'), data.get('otp_code')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    try:
        async def verify(): await client(VerifyEmailRequest(email=new_email, code=otp))
        run_async(verify())
        return jsonify({"success": True, "message": "Email changed successfully."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
