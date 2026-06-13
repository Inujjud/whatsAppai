"""
claude_handler.py — توليد ردود البوت عبر Claude مع حفظ سياق المحادثة.

- يحتفظ بآخر 20 رسالة فقط في السياق (توفير تكاليف التوكن).
- كل استدعاء Supabase داخل try/except مع رسالة واضحة.
"""
import os
from datetime import datetime, timezone

import anthropic
from supabase import create_client

# عميل Supabase خاص بهذا الموديول (SERVICE_ROLE key على السيرفر فقط)
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# عميل Claude
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 500
CONTEXT_WINDOW = 20  # آخر 20 رسالة فقط


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_conversation(client_id: str, phone: str):
    """يجلب المحادثة الحالية (history, conv_id) أو ([], None)."""
    try:
        conv = (
            sb.table("conversations")
            .select("*")
            .eq("client_id", client_id)
            .eq("customer_phone", phone)
            .limit(1)
            .execute()
        )
        if conv.data:
            return conv.data[0].get("messages", []), conv.data[0]["id"]
    except Exception as e:
        print(f"[claude_handler] خطأ في جلب المحادثة: {e}")
    return [], None


def _save_conversation(client_id, phone, conv_id, history):
    """يحفظ/يحدّث المحادثة في Supabase."""
    payload = {
        "messages": history,
        "last_message_at": _now(),
        "message_count": len(history),
    }
    try:
        if conv_id:
            sb.table("conversations").update(payload).eq("id", conv_id).execute()
        else:
            payload.update({"client_id": client_id, "customer_phone": phone})
            sb.table("conversations").insert(payload).execute()
    except Exception as e:
        print(f"[claude_handler] خطأ في حفظ المحادثة: {e}")


def get_reply(client_id: str, phone: str, message: str, system_prompt: str) -> str:
    """
    يرجّع رد البوت على رسالة العميل، مع تحديث سجل المحادثة.
    عند أي خطأ يرجّع رسالة لطيفة بدلاً من رفع استثناء يكسر الـ webhook.
    """
    history, conv_id = _load_conversation(client_id, phone)

    # أضف رسالة المستخدم
    history.append({"role": "user", "content": message, "timestamp": _now()})

    # خذ آخر CONTEXT_WINDOW رسالة فقط للسياق
    recent = [
        {"role": m["role"], "content": m["content"]}
        for m in history[-CONTEXT_WINDOW:]
    ]

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt or "أنت مساعد ودود.",
            messages=recent,
        )
        reply = response.content[0].text
    except Exception as e:
        print(f"[claude_handler] خطأ في استدعاء Claude: {e}")
        reply = "عذراً، حدث خطأ مؤقت. يرجى المحاولة بعد قليل 🙏"
        # نحفظ رسالة المستخدم على الأقل
        _save_conversation(client_id, phone, conv_id, history)
        return reply

    # أضف رد البوت واحفظ
    history.append({"role": "assistant", "content": reply, "timestamp": _now()})
    _save_conversation(client_id, phone, conv_id, history)

    return reply
