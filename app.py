"""
app.py — السيرفر الرئيسي (Flask Backend) لمنصة بوت واتساب الذكي.

المسارات:
  • POST /webhook/<client_id>              ← استقبال رسائل UltraMsg (نص/صوت) والرد
  • POST /api/signup , /api/login          ← مصادقة بسيطة
  • POST /api/settings                     ← حفظ شخصية البوت + الإعدادات الكاملة
  • GET  /api/settings/<client_id>         ← تحميل الإعدادات (بدون أسرار)
  • GET/POST/DELETE /api/knowledge[...]    ← قاعدة المعرفة
  • GET  /api/stats/<client_id>            ← إحصائيات
  • GET  /api/conversations/<client_id>    ← الإنبوكس (محادثات)
  • POST /api/reply                        ← رد يدوي من المسؤول
  • POST /api/handoff                      ← تحويل/إنهاء التدخّل البشري
  • POST /api/test-connection              ← اختبار UltraMsg
  • تقديم صفحات index / login / dashboard
"""
import os
from datetime import date, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client

from claude_handler import get_reply, append_incoming, get_status
from whatsapp import send_message, send_audio, download_media, test_connection
import voice

# ---------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("متغيرات البيئة SUPABASE_URL و SUPABASE_KEY مطلوبة.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

_HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = _HERE if os.path.isfile(os.path.join(_HERE, "index.html")) else os.path.abspath(os.path.join(_HERE, ".."))
app = Flask(__name__, static_folder=None)
CORS(app)


# =====================================================================
# أدوات مساعدة
# =====================================================================
def build_system_prompt(d: dict) -> str:
    """يبني الـ system prompt الكامل من شخصية البوت."""
    lines = [
        f"أنتَ {d.get('bot_name') or 'مساعد'}"
        + (f"، {d.get('bot_role')}" if d.get('bot_role') else "")
        + f" لـ {d.get('business_name') or 'نشاطنا'}.",
    ]
    if d.get("bot_tone"):
        lines.append(f"\nنبرتك وأسلوبك: {d['bot_tone']}")
    if d.get("business_info"):
        lines.append(f"\nمعلومات عن النشاط:\n{d['business_info']}")
    if d.get("dos"):
        lines.append(f"\nيجب عليك دائماً:\n{d['dos']}")
    if d.get("donts"):
        lines.append(f"\nممنوع عليك:\n{d['donts']}")

    qa = d.get("sample_qa") or []
    if isinstance(qa, list) and qa:
        ex = "\n".join(
            f"س: {x.get('q','')}\nج: {x.get('a','')}"
            for x in qa if x.get("q") and x.get("a")
        )
        if ex:
            lines.append(f"\nأمثلة على الردود المطلوبة:\n{ex}")

    flows = d.get("flows") or []
    if isinstance(flows, list) and flows:
        fl = "\n".join(
            f"- إذا أراد العميل «{f.get('trigger','')}»: {f.get('action','')}"
            for f in flows if f.get("trigger") and f.get("action")
        )
        if fl:
            lines.append(f"\nمسارات جاهزة اتبعها عند الحاجة:\n{fl}")

    lines.append(
        "\nقواعد عامة:\n"
        "- رد بنفس لغة العميل (افتراضياً العربية).\n"
        "- كن مختصراً وواضحاً ومفيداً.\n"
        f"- رسالة الترحيب للعميل الجديد: {d.get('welcome_message') or 'أهلاً بك!'}"
    )
    return "\n".join(lines)


def safe_settings(bot: dict) -> dict:
    """يحذف الأسرار قبل الإرسال للمتصفح."""
    if not bot:
        return {}
    out = dict(bot)
    out.pop("ultramsg_token", None)
    out.pop("claude_system_prompt", None)
    out["has_ultramsg_token"] = bool(bot.get("ultramsg_token"))
    return out


def update_analytics(client_id: str):
    today = str(date.today())
    try:
        existing = (sb.table("analytics").select("*")
                    .eq("client_id", client_id).eq("date", today).limit(1).execute())
        if existing.data:
            row = existing.data[0]
            sb.table("analytics").update({
                "messages_received": row.get("messages_received", 0) + 1,
                "messages_sent": row.get("messages_sent", 0) + 1,
            }).eq("id", row["id"]).execute()
        else:
            sb.table("analytics").insert({
                "client_id": client_id, "date": today,
                "messages_received": 1, "messages_sent": 1,
            }).execute()
    except Exception as e:
        print(f"[analytics] {e}")


def notify_owner(bot: dict, customer_phone: str, reason: str):
    """ينبّه المسؤول على واتسابه بأن محادثة تحتاج تدخّله."""
    owner = (bot.get("owner_phone") or "").strip()
    if not owner:
        return
    text = (f"🙋 تنبيه: محادثة تحتاج تدخّلك\n"
            f"العميل: {customer_phone}\n"
            f"السبب: {reason or 'طلب تحدّث مع موظف'}\n\n"
            f"ادخل لوحة التحكم → الإنبوكس للرد.")
    send_message(bot.get("ultramsg_instance", ""), bot.get("ultramsg_token", ""), owner, text)


# =====================================================================
# 1) Webhook
# =====================================================================
@app.route("/webhook/<client_id>", methods=["POST"])
def webhook(client_id):
    try:
        data = request.get_json(silent=True) or {}
        body_obj = data.get("data", data)

        phone = (body_obj.get("from", "") or "").replace("@c.us", "")
        message = body_obj.get("body", "") or ""
        from_me = body_obj.get("fromMe", False)
        msg_type = body_obj.get("type", "chat")
        media_url = body_obj.get("media", "") or ""

        if from_me or not phone:
            return "ok"

        # جلب الإعدادات
        try:
            res = (sb.table("bot_settings").select("*")
                   .eq("client_id", client_id).eq("is_active", True).limit(1).execute())
        except Exception as e:
            print(f"[webhook] جلب الإعدادات: {e}")
            return "ok"
        if not res.data:
            return "ok"
        bot = res.data[0]
        instance = bot.get("ultramsg_instance", "")
        token = bot.get("ultramsg_token", "")

        # ---- رسالة صوتية ----
        if msg_type in ("ptt", "audio") and not message:
            if bot.get("voice_enabled") and voice.voice_available() and media_url:
                audio = download_media(media_url)
                message = voice.transcribe(audio) if audio else ""
            if not message:
                send_message(instance, token, phone,
                             "وصلتني رسالتك الصوتية 🎙️ ممكن ترسلها كتابة من فضلك؟")
                return "ok"
        elif msg_type != "chat":
            return "ok"  # نتجاهل الصور/الملفات حالياً

        if not message:
            return "ok"

        # ---- وضع التدخّل البشري: لا يرد البوت ----
        if get_status(client_id, phone) == "human":
            append_incoming(client_id, phone, message)
            return "ok"

        # ---- كشف كلمات التحويل الفورية ----
        keywords = [k.strip() for k in (bot.get("handoff_keywords") or "").split(",") if k.strip()]
        if bot.get("handoff_enabled", True) and any(k in message for k in keywords):
            append_incoming(client_id, phone, message)
            hmsg = bot.get("handoff_message") or "لحظة من فضلك، بحوّلك لأحد موظفينا 🙏"
            send_message(instance, token, phone, hmsg)
            try:
                sb.table("conversations").update(
                    {"needs_human": True, "status": "human", "handoff_reason": "طلب موظف"}
                ).eq("client_id", client_id).eq("customer_phone", phone).execute()
            except Exception as e:
                print(f"[webhook] تحديث الحالة: {e}")
            notify_owner(bot, phone, "طلب التحدث مع موظف")
            update_analytics(client_id)
            return "ok"

        # ---- رد البوت ----
        result = get_reply(
            client_id=client_id, phone=phone, message=message,
            system_prompt=bot.get("claude_system_prompt", ""),
            handoff_enabled=bot.get("handoff_enabled", True),
            handoff_message=bot.get("handoff_message", ""),
        )
        send_message(instance, token, phone, result["reply"])

        if result.get("handoff"):
            notify_owner(bot, phone, result.get("reason", ""))

        update_analytics(client_id)
    except Exception as e:
        print(f"[webhook] خطأ غير متوقع: {e}")
    return "ok"


# =====================================================================
# 2) حفظ / تحميل الإعدادات
# =====================================================================
@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"success": False, "error": "client_id مطلوب"}), 400

    business_name = data.get("business_name", "")
    if not business_name:
        try:
            c = sb.table("clients").select("business_name").eq("id", client_id).limit(1).execute()
            if c.data:
                business_name = c.data[0]["business_name"]
        except Exception as e:
            print(f"[settings] اسم النشاط: {e}")

    system_prompt = build_system_prompt({**data, "business_name": business_name})

    payload = {
        "client_id": client_id,
        "bot_name": (data.get("bot_name") or "مساعد").strip(),
        "bot_role": (data.get("bot_role") or "").strip(),
        "bot_tone": (data.get("bot_tone") or "").strip(),
        "bot_avatar": (data.get("bot_avatar") or "🤖").strip(),
        "bot_personality": (data.get("bot_tone") or "").strip(),
        "business_info": (data.get("business_info") or "").strip(),
        "dos": (data.get("dos") or "").strip(),
        "donts": (data.get("donts") or "").strip(),
        "sample_qa": data.get("sample_qa") or [],
        "flows": data.get("flows") or [],
        "welcome_message": (data.get("welcome_message") or "").strip(),
        "ultramsg_instance": (data.get("ultramsg_instance") or "").strip(),
        "voice_enabled": bool(data.get("voice_enabled")),
        "voice_reply": bool(data.get("voice_reply")),
        "handoff_enabled": bool(data.get("handoff_enabled", True)),
        "handoff_message": (data.get("handoff_message") or "").strip(),
        "handoff_keywords": (data.get("handoff_keywords") or "").strip(),
        "owner_phone": (data.get("owner_phone") or "").strip(),
        "claude_system_prompt": system_prompt,
        "is_active": bool(data.get("is_active", True)),
    }
    token = (data.get("ultramsg_token") or "").strip()
    if token:
        payload["ultramsg_token"] = token

    try:
        sb.table("bot_settings").upsert(payload, on_conflict="client_id").execute()
    except Exception as e:
        return jsonify({"success": False, "error": f"تعذّر الحفظ: {e}"}), 500

    base = request.host_url.rstrip("/")
    return jsonify({"success": True, "webhook_url": f"{base}/webhook/{client_id}"})


@app.route("/api/settings/<client_id>", methods=["GET"])
def load_settings(client_id):
    try:
        res = sb.table("bot_settings").select("*").eq("client_id", client_id).limit(1).execute()
    except Exception as e:
        return jsonify({"error": f"تعذّر الجلب: {e}"}), 500
    bot = res.data[0] if res.data else None
    base = request.host_url.rstrip("/")
    return jsonify({
        "settings": safe_settings(bot),
        "webhook_url": f"{base}/webhook/{client_id}",
        "voice_engine": voice.voice_available(),
    })


# =====================================================================
# 3) قاعدة المعرفة
# =====================================================================
@app.route("/api/knowledge/<client_id>", methods=["GET"])
def kb_list(client_id):
    try:
        res = (sb.table("knowledge_base").select("*")
               .eq("client_id", client_id).order("created_at", desc=True).execute())
        return jsonify({"items": res.data or []})
    except Exception as e:
        return jsonify({"items": [], "error": str(e)}), 500


@app.route("/api/knowledge", methods=["POST"])
def kb_add():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    title = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not client_id or not title or not content:
        return jsonify({"success": False, "error": "العنوان والمحتوى مطلوبان"}), 400
    try:
        sb.table("knowledge_base").insert({
            "client_id": client_id,
            "category": (data.get("category") or "").strip() or None,
            "title": title, "content": content,
        }).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/knowledge/<item_id>", methods=["DELETE"])
def kb_delete(item_id):
    try:
        sb.table("knowledge_base").delete().eq("id", item_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =====================================================================
# 4) الإحصائيات
# =====================================================================
@app.route("/api/stats/<client_id>", methods=["GET"])
def stats(client_id):
    today = str(date.today())
    out = {"messages_today": 0, "total_conversations": 0,
           "new_customers_week": 0, "needs_human": 0, "bot_active": False}
    try:
        a = (sb.table("analytics").select("messages_received")
             .eq("client_id", client_id).eq("date", today).limit(1).execute())
        if a.data:
            out["messages_today"] = a.data[0].get("messages_received", 0)
    except Exception as e:
        print(f"[stats] analytics: {e}")
    try:
        c = (sb.table("conversations").select("id", count="exact")
             .eq("client_id", client_id).execute())
        out["total_conversations"] = c.count or 0
    except Exception as e:
        print(f"[stats] conv: {e}")
    try:
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        w = (sb.table("conversations").select("id", count="exact")
             .eq("client_id", client_id).gte("last_message_at", week_ago).execute())
        out["new_customers_week"] = w.count or 0
    except Exception as e:
        print(f"[stats] week: {e}")
    try:
        h = (sb.table("conversations").select("id", count="exact")
             .eq("client_id", client_id).eq("needs_human", True).execute())
        out["needs_human"] = h.count or 0
    except Exception as e:
        print(f"[stats] human: {e}")
    try:
        b = (sb.table("bot_settings").select("is_active")
             .eq("client_id", client_id).limit(1).execute())
        if b.data:
            out["bot_active"] = bool(b.data[0].get("is_active"))
    except Exception as e:
        print(f"[stats] active: {e}")
    return jsonify(out)


# =====================================================================
# 5) الإنبوكس
# =====================================================================
@app.route("/api/conversations/<client_id>", methods=["GET"])
def conversations(client_id):
    try:
        res = (sb.table("conversations")
               .select("id, customer_phone, customer_name, messages, last_message_at, message_count, needs_human, status, handoff_reason")
               .eq("client_id", client_id)
               .order("last_message_at", desc=True).limit(50).execute())
    except Exception as e:
        return jsonify({"error": str(e), "conversations": []}), 500
    items = []
    for c in res.data or []:
        msgs = c.get("messages") or []
        last = msgs[-1]["content"] if msgs else ""
        items.append({
            "id": c["id"], "customer_phone": c["customer_phone"],
            "customer_name": c.get("customer_name"),
            "last_message": (last or "")[:120],
            "last_message_at": c.get("last_message_at"),
            "message_count": c.get("message_count", len(msgs)),
            "needs_human": c.get("needs_human", False),
            "status": c.get("status", "bot"),
            "handoff_reason": c.get("handoff_reason"),
            "messages": msgs,
        })
    return jsonify({"conversations": items})


@app.route("/api/reply", methods=["POST"])
def manual_reply():
    """رد يدوي من المسؤول عبر الإنبوكس."""
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    phone = data.get("customer_phone")
    text = (data.get("message") or "").strip()
    if not client_id or not phone or not text:
        return jsonify({"success": False, "error": "بيانات ناقصة"}), 400
    try:
        b = sb.table("bot_settings").select("ultramsg_instance, ultramsg_token").eq("client_id", client_id).limit(1).execute()
        if not b.data:
            return jsonify({"success": False, "error": "إعدادات الواتساب غير موجودة"}), 400
        bot = b.data[0]
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    r = send_message(bot.get("ultramsg_instance", ""), bot.get("ultramsg_token", ""), phone, text)
    if isinstance(r, dict) and "error" in r:
        return jsonify({"success": False, "error": r["error"]}), 500

    # سجّل الرسالة في المحادثة
    try:
        conv = (sb.table("conversations").select("id, messages")
                .eq("client_id", client_id).eq("customer_phone", phone).limit(1).execute())
        if conv.data:
            from datetime import datetime, timezone
            msgs = conv.data[0].get("messages", [])
            msgs.append({"role": "assistant", "content": text,
                         "timestamp": datetime.now(timezone.utc).isoformat(),
                         "system_note": "رد يدوي من المسؤول"})
            sb.table("conversations").update(
                {"messages": msgs, "message_count": len(msgs)}
            ).eq("id", conv.data[0]["id"]).execute()
    except Exception as e:
        print(f"[reply] حفظ: {e}")
    return jsonify({"success": True})


@app.route("/api/handoff", methods=["POST"])
def handoff():
    """تغيير حالة المحادثة: human (تدخّل) / bot (إرجاع للبوت) / resolved."""
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    phone = data.get("customer_phone")
    new_status = data.get("status", "bot")
    if not client_id or not phone or new_status not in ("bot", "human", "resolved"):
        return jsonify({"success": False, "error": "بيانات غير صحيحة"}), 400
    try:
        sb.table("conversations").update({
            "status": new_status,
            "needs_human": new_status == "human",
        }).eq("client_id", client_id).eq("customer_phone", phone).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =====================================================================
# المصادقة
# =====================================================================
@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    business_name = (data.get("business_name") or "").strip()
    business_type = (data.get("business_type") or "").strip()

    if not email or not password or not business_name:
        return jsonify({"success": False, "error": "البريد وكلمة المرور واسم النشاط مطلوبة"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "كلمة المرور 6 أحرف على الأقل"}), 400
    try:
        existing = sb.table("clients").select("id").eq("email", email).execute()
        if existing.data:
            return jsonify({"success": False, "error": "هذا البريد مسجّل مسبقاً"}), 409
    except Exception as e:
        return jsonify({"success": False, "error": f"قاعدة البيانات: {e}"}), 500
    try:
        result = sb.table("clients").insert({
            "email": email, "password_hash": generate_password_hash(password),
            "business_name": business_name, "business_type": business_type or None,
        }).execute()
        client_id = result.data[0]["id"]
    except Exception as e:
        return jsonify({"success": False, "error": f"تعذّر الإنشاء: {e}"}), 500
    try:
        sb.table("bot_settings").insert({
            "client_id": client_id, "bot_name": "مساعد",
            "welcome_message": f"أهلاً بك في {business_name}! كيف أقدر أساعدك؟",
            "is_active": False,
        }).execute()
    except Exception as e:
        print(f"[signup] إعدادات: {e}")
    return jsonify({"success": True, "client_id": client_id, "business_name": business_name})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify({"success": False, "error": "البريد وكلمة المرور مطلوبة"}), 400
    try:
        res = sb.table("clients").select("*").eq("email", email).limit(1).execute()
    except Exception as e:
        return jsonify({"success": False, "error": f"قاعدة البيانات: {e}"}), 500
    if not res.data or not check_password_hash(res.data[0]["password_hash"], password):
        return jsonify({"success": False, "error": "البريد أو كلمة المرور غير صحيحة"}), 401
    client = res.data[0]
    if not client.get("is_active", True):
        return jsonify({"success": False, "error": "هذا الحساب موقوف"}), 403
    return jsonify({"success": True, "client_id": client["id"], "business_name": client["business_name"]})


@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    data = request.get_json(silent=True) or {}
    instance = (data.get("ultramsg_instance") or "").strip()
    token = (data.get("ultramsg_token") or "").strip()
    client_id = data.get("client_id")
    if not token and client_id:
        try:
            res = (sb.table("bot_settings").select("ultramsg_token, ultramsg_instance")
                   .eq("client_id", client_id).limit(1).execute())
            if res.data:
                token = token or res.data[0].get("ultramsg_token", "")
                instance = instance or res.data[0].get("ultramsg_instance", "")
        except Exception as e:
            print(f"[test-connection] {e}")
    return jsonify(test_connection(instance, token))


# =====================================================================
# صفحات الواجهة
# =====================================================================
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:page>")
def static_pages(page):
    safe = os.path.basename(page)
    full = os.path.join(WEB_DIR, safe)
    if os.path.isfile(full) and safe.endswith(".html"):
        return send_from_directory(WEB_DIR, safe)
    return jsonify({"error": "الصفحة غير موجودة"}), 404


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
