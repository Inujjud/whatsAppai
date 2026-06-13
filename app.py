"""
app.py — السيرفر الرئيسي (Flask Backend).

Endpoints حسب المواصفة:
  • POST /webhook/<client_id>            ← استقبال رسائل UltraMsg والرد عليها
  • POST /api/settings                   ← حفظ إعدادات البوت وبناء الـ system prompt
  • GET  /api/stats/<client_id>          ← إحصائيات اليوم والإجمالي
  • GET  /api/conversations/<client_id>  ← آخر 10 محادثات

إضافات لازمة لعمل اللوحة:
  • POST /api/signup , POST /api/login   ← مصادقة بسيطة (email + password)
  • GET  /api/settings/<client_id>       ← تحميل الإعدادات في اللوحة (بدون أسرار)
  • POST /api/test-connection            ← اختبار UltraMsg
  • تقديم صفحات index / login / dashboard

القواعد:
  • كل استدعاء Supabase داخل try/except مع return مناسب.
  • لا تُكشف أي مفاتيح (Anthropic / UltraMsg token) في أي response.
  • الـ webhook يرجّع 'ok' دائماً (UltraMsg يتوقع ذلك) حتى عند الخطأ.
  • CORS مفعّل للـ dashboard.
"""
import os
from datetime import date, timedelta

# تحميل متغيرات البيئة محلياً (قبل أي استخدام لـ os.environ)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client

from claude_handler import get_reply
from whatsapp import send_message, test_connection

# ---------------------------------------------------------------------
# تهيئة
# ---------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("متغيرات البيئة SUPABASE_URL و SUPABASE_KEY مطلوبة.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
app = Flask(__name__, static_folder=None)
CORS(app)  # CORS مفعّل للـ dashboard


# ---------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------
def build_system_prompt(data: dict) -> str:
    """يبني الـ claude_system_prompt تلقائياً من بيانات العميل."""
    return f"""أنتَ {data.get('bot_name') or 'مساعد'} — مساعد ذكي لـ {data.get('business_name') or 'نشاطنا'}.

معلومات النشاط:
{data.get('business_info') or '—'}

أسلوبك: {data.get('bot_personality') or 'ودود ومختصر'}

قواعد:
- رد بالعربية دائماً ما لم يتكلم العميل بلغة أخرى.
- كن مختصراً ومفيداً.
- إذا سأل عن شيء خارج نطاق نشاطنا اعتذر بأدب ووجّهه لما نقدّمه.
- لا تعطِ معلومات طبية أو قانونية متخصّصة.
- رسالة الترحيب: {data.get('welcome_message') or 'أهلاً بك!'}"""


def safe_settings(bot: dict) -> dict:
    """يحذف الأسرار من الإعدادات قبل إرسالها للمتصفح."""
    if not bot:
        return {}
    out = dict(bot)
    out.pop("ultramsg_token", None)
    out["has_ultramsg_token"] = bool(bot.get("ultramsg_token"))
    return out


def update_analytics(client_id: str):
    """تحديث الإحصائيات اليومية (استلام + إرسال)."""
    today = str(date.today())
    try:
        existing = (
            sb.table("analytics").select("*")
            .eq("client_id", client_id).eq("date", today).limit(1).execute()
        )
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
        print(f"[analytics] خطأ: {e}")


# =====================================================================
# 1) Webhook — POST /webhook/<client_id>
# =====================================================================
@app.route("/webhook/<client_id>", methods=["POST"])
def webhook(client_id):
    """
    يستقبل رسائل UltraMsg، يرد عبر Claude، ويرسل الرد عبر UltraMsg.
    يرجّع 'ok' دائماً (UltraMsg يتوقع ذلك).
    """
    try:
        data = request.get_json(silent=True) or {}
        body_obj = data.get("data", data)  # UltraMsg يغلّف داخل "data" أحياناً

        phone = (body_obj.get("from", "") or "").replace("@c.us", "")
        message = body_obj.get("body", "") or ""
        from_me = body_obj.get("fromMe", False)
        msg_type = body_obj.get("type", "chat")

        # تجاهل: رسائلنا، الفارغة، وغير النصية
        if from_me or not phone or not message or msg_type != "chat":
            return "ok"

        # جلب إعدادات البوت (يجب أن يكون مفعّلاً)
        try:
            res = (
                sb.table("bot_settings").select("*")
                .eq("client_id", client_id).eq("is_active", True).limit(1).execute()
            )
        except Exception as e:
            print(f"[webhook] خطأ في جلب الإعدادات: {e}")
            return "ok"

        if not res.data:
            return "ok"  # البوت متوقف أو غير موجود
        bot = res.data[0]

        # رد Claude
        reply = get_reply(
            client_id=client_id,
            phone=phone,
            message=message,
            system_prompt=bot.get("claude_system_prompt", ""),
        )

        # إرسال عبر UltraMsg
        result = send_message(
            instance=bot.get("ultramsg_instance", ""),
            token=bot.get("ultramsg_token", ""),
            phone=phone,
            message=reply,
        )
        if isinstance(result, dict) and "error" in result:
            print(f"[webhook] فشل الإرسال: {result['error']}")

        update_analytics(client_id)
    except Exception as e:
        print(f"[webhook] خطأ غير متوقع: {e}")

    return "ok"


# =====================================================================
# 2) حفظ الإعدادات — POST /api/settings
# =====================================================================
@app.route("/api/settings", methods=["POST"])
def save_settings():
    """يحفظ إعدادات البوت من لوحة التحكم ويرجّع webhook_url."""
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"success": False, "error": "client_id مطلوب"}), 400

    # نجلب اسم النشاط لبناء الـ prompt
    business_name = data.get("business_name", "")
    if not business_name:
        try:
            c = sb.table("clients").select("business_name").eq("id", client_id).limit(1).execute()
            if c.data:
                business_name = c.data[0]["business_name"]
        except Exception as e:
            print(f"[settings] جلب اسم النشاط: {e}")

    system_prompt = build_system_prompt({**data, "business_name": business_name})

    payload = {
        "client_id": client_id,
        "bot_name": (data.get("bot_name") or "مساعد").strip(),
        "bot_personality": (data.get("bot_personality") or "").strip(),
        "business_info": (data.get("business_info") or "").strip(),
        "welcome_message": (data.get("welcome_message") or "").strip(),
        "ultramsg_instance": (data.get("ultramsg_instance") or "").strip(),
        "claude_system_prompt": system_prompt,
        "is_active": True,
    }
    # لا نمسح التوكن إذا تُرك فارغاً
    token = (data.get("ultramsg_token") or "").strip()
    if token:
        payload["ultramsg_token"] = token

    try:
        sb.table("bot_settings").upsert(payload, on_conflict="client_id").execute()
    except Exception as e:
        return jsonify({"success": False, "error": f"تعذّر حفظ الإعدادات: {e}"}), 500

    base = request.host_url.rstrip("/")
    return jsonify({"success": True, "webhook_url": f"{base}/webhook/{client_id}"})


@app.route("/api/settings/<client_id>", methods=["GET"])
def load_settings(client_id):
    """تحميل إعدادات البوت في اللوحة (بدون الأسرار)."""
    try:
        res = sb.table("bot_settings").select("*").eq("client_id", client_id).limit(1).execute()
    except Exception as e:
        return jsonify({"error": f"تعذّر جلب الإعدادات: {e}"}), 500
    bot = res.data[0] if res.data else None
    base = request.host_url.rstrip("/")
    return jsonify({
        "settings": safe_settings(bot),
        "webhook_url": f"{base}/webhook/{client_id}",
    })


# =====================================================================
# 3) الإحصائيات — GET /api/stats/<client_id>
# =====================================================================
@app.route("/api/stats/<client_id>", methods=["GET"])
def stats(client_id):
    """يرجّع رسائل اليوم، إجمالي المحادثات، عملاء الأسبوع، وحالة البوت."""
    today = str(date.today())
    out = {
        "messages_today": 0,
        "total_conversations": 0,
        "new_customers_week": 0,
        "bot_active": False,
    }
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
        print(f"[stats] conversations: {e}")

    try:
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        w = (sb.table("conversations").select("id", count="exact")
             .eq("client_id", client_id).gte("last_message_at", week_ago).execute())
        out["new_customers_week"] = w.count or 0
    except Exception as e:
        print(f"[stats] week: {e}")

    try:
        b = (sb.table("bot_settings").select("is_active")
             .eq("client_id", client_id).limit(1).execute())
        if b.data:
            out["bot_active"] = bool(b.data[0].get("is_active"))
    except Exception as e:
        print(f"[stats] bot status: {e}")

    return jsonify(out)


# =====================================================================
# 4) المحادثات — GET /api/conversations/<client_id>
# =====================================================================
@app.route("/api/conversations/<client_id>", methods=["GET"])
def conversations(client_id):
    """يرجّع آخر 10 محادثات."""
    try:
        res = (sb.table("conversations")
               .select("id, customer_phone, messages, last_message_at, message_count")
               .eq("client_id", client_id)
               .order("last_message_at", desc=True).limit(10).execute())
    except Exception as e:
        return jsonify({"error": f"تعذّر جلب المحادثات: {e}", "conversations": []}), 500

    items = []
    for c in res.data or []:
        msgs = c.get("messages") or []
        last = msgs[-1]["content"] if msgs else ""
        items.append({
            "id": c["id"],
            "customer_phone": c["customer_phone"],
            "last_message": (last or "")[:120],
            "last_message_at": c.get("last_message_at"),
            "message_count": c.get("message_count", len(msgs)),
            "messages": msgs,
        })
    return jsonify({"conversations": items})


# =====================================================================
# مصادقة بسيطة (email + password)
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
        return jsonify({"success": False, "error": f"خطأ في قاعدة البيانات: {e}"}), 500

    try:
        result = sb.table("clients").insert({
            "email": email,
            "password_hash": generate_password_hash(password),
            "business_name": business_name,
            "business_type": business_type or None,
        }).execute()
        client_id = result.data[0]["id"]
    except Exception as e:
        return jsonify({"success": False, "error": f"تعذّر إنشاء الحساب: {e}"}), 500

    try:
        sb.table("bot_settings").insert({
            "client_id": client_id,
            "bot_name": "مساعد",
            "welcome_message": f"أهلاً بك في {business_name}! كيف أقدر أساعدك؟",
            "is_active": False,
        }).execute()
    except Exception as e:
        print(f"[signup] إعدادات افتراضية: {e}")

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
        return jsonify({"success": False, "error": f"خطأ في قاعدة البيانات: {e}"}), 500

    if not res.data or not check_password_hash(res.data[0]["password_hash"], password):
        return jsonify({"success": False, "error": "البريد أو كلمة المرور غير صحيحة"}), 401

    client = res.data[0]
    if not client.get("is_active", True):
        return jsonify({"success": False, "error": "هذا الحساب موقوف"}), 403

    return jsonify({
        "success": True,
        "client_id": client["id"],
        "business_name": client["business_name"],
    })


# =====================================================================
# اختبار اتصال UltraMsg
# =====================================================================
@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    data = request.get_json(silent=True) or {}
    instance = (data.get("ultramsg_instance") or "").strip()
    token = (data.get("ultramsg_token") or "").strip()
    client_id = data.get("client_id")

    # إن لم يُرسل التوكن استخدم المخزّن
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
# تقديم صفحات الواجهة
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
