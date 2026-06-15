"""
claude_handler.py — توليد ردود البوت عبر Claude مع:
  • حفظ سياق المحادثة (آخر 20 رسالة).
  • دمج قاعدة المعرفة (Knowledge Base) في السياق.
  • كشف الأسئلة خارج النطاق وتفعيل "التسليم للمسؤول" (Human Handoff).

get_reply() يرجّع dict:
  {
    "reply":   نص الرد الذي يُرسل للعميل,
    "handoff": True/False  هل يجب تحويل المحادثة لمسؤول بشري,
    "reason":  سبب التحويل (للعرض في الإنبوكس),
  }
"""
import os
from datetime import datetime, timezone

import anthropic
from supabase import create_client

# عميل Supabase (SERVICE_ROLE key على السيرفر فقط)
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# عميل Claude
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 600
CONTEXT_WINDOW = 20            # آخر 20 رسالة فقط
HANDOFF_TOKEN = "[[HANDOFF]]"  # علامة يخرجها Claude عند الحاجة لتدخّل بشري


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------
# قاعدة المعرفة
# ---------------------------------------------------------------------
def _load_knowledge(client_id: str) -> str:
    """يجمع كل بنود قاعدة المعرفة لهذا العميل كنص واحد للحقن في الـ prompt."""
    try:
        res = (
            sb.table("knowledge_base").select("category, title, content")
            .eq("client_id", client_id).order("created_at", desc=False).execute()
        )
    except Exception as e:
        print(f"[knowledge] خطأ: {e}")
        return ""
    rows = res.data or []
    if not rows:
        return ""
    parts = []
    for r in rows:
        cat = f"[{r['category']}] " if r.get("category") else ""
        parts.append(f"• {cat}{r.get('title', '')}: {r.get('content', '')}")
    return "\n".join(parts)


# ---------------------------------------------------------------------
# المحادثة
# ---------------------------------------------------------------------
def _load_conversation(client_id: str, phone: str):
    """يرجّع (history, conv_id, status)."""
    try:
        conv = (
            sb.table("conversations").select("*")
            .eq("client_id", client_id).eq("customer_phone", phone)
            .limit(1).execute()
        )
        if conv.data:
            row = conv.data[0]
            return row.get("messages", []), row["id"], row.get("status", "bot")
    except Exception as e:
        print(f"[claude_handler] جلب المحادثة: {e}")
    return [], None, "bot"


def _save_conversation(client_id, phone, conv_id, history, **extra):
    """يحفظ/يحدّث المحادثة. extra: needs_human, status, handoff_reason..."""
    payload = {
        "messages": history,
        "last_message_at": _now(),
        "message_count": len(history),
    }
    payload.update(extra)
    try:
        if conv_id:
            sb.table("conversations").update(payload).eq("id", conv_id).execute()
        else:
            payload.update({"client_id": client_id, "customer_phone": phone})
            sb.table("conversations").insert(payload).execute()
    except Exception as e:
        print(f"[claude_handler] حفظ المحادثة: {e}")


def append_incoming(client_id: str, phone: str, message: str, media: str = ""):
    """يسجّل رسالة عميل واردة دون توليد رد (وضع التدخّل البشري أو صورة/وسائط)."""
    history, conv_id, _ = _load_conversation(client_id, phone)
    entry = {"role": "user", "content": message, "timestamp": _now()}
    if media:
        entry["media"] = media
    history.append(entry)
    _save_conversation(client_id, phone, conv_id, history)


def get_status(client_id: str, phone: str) -> str:
    """يرجّع حالة المحادثة: bot / human / resolved."""
    _, _, status = _load_conversation(client_id, phone)
    return status


def get_conversation_row(client_id: str, phone: str):
    """يرجّع صف المحادثة كاملاً (dict) أو None — لقراءة human_since/awaiting_rating..."""
    try:
        conv = (
            sb.table("conversations").select("*")
            .eq("client_id", client_id).eq("customer_phone", phone)
            .limit(1).execute()
        )
        if conv.data:
            return conv.data[0]
    except Exception as e:
        print(f"[claude_handler] جلب صف المحادثة: {e}")
    return None


def update_conversation(client_id: str, phone: str, **fields):
    """تحديث حقول محددة في المحادثة (status, human_since, awaiting_rating, rating...)."""
    if not fields:
        return
    try:
        (sb.table("conversations").update(fields)
         .eq("client_id", client_id).eq("customer_phone", phone).execute())
    except Exception as e:
        print(f"[claude_handler] تحديث المحادثة: {e}")


def append_assistant(client_id: str, phone: str, message: str, system_note: str = ""):
    """يسجّل رسالة صادرة من البوت/النظام في المحادثة (بدون استدعاء Claude)."""
    history, conv_id, _ = _load_conversation(client_id, phone)
    entry = {"role": "assistant", "content": message, "timestamp": _now()}
    if system_note:
        entry["system_note"] = system_note
    history.append(entry)
    _save_conversation(client_id, phone, conv_id, history)


def conversation_exists(client_id: str, phone: str) -> bool:
    """هل توجد محادثة سابقة مع هذا العميل؟ (لتحديد العميل الجديد)."""
    try:
        conv = (
            sb.table("conversations").select("id")
            .eq("client_id", client_id).eq("customer_phone", phone)
            .limit(1).execute()
        )
        return bool(conv.data)
    except Exception as e:
        print(f"[claude_handler] فحص وجود المحادثة: {e}")
        return True  # عند الشك: لا ترسل ترحيب مكرر


# ---------------------------------------------------------------------
# بناء الـ system prompt النهائي (شخصية + معرفة + قواعد التسليم)
# ---------------------------------------------------------------------
# قواعد أسلوب الرد — تُطبَّق على كل البوتات وقت الرد (جودة كلام احترافية)
_STYLE_RULES = (
    "=== قواعد الرد الاحترافي (التزم بها دائماً) ===\n"
    "- اكتب رداً قصيراً ومركّزاً: من جملة إلى خمس جمل كحدّ أقصى. الاختصار أقوى.\n"
    "- رحّب بالعميل في أول رسالة فقط؛ بعدها لا تكرر التحية ولا تعيد تقديم نفسك.\n"
    "- إن عرفت اسم العميل فاستخدمه بشكل طبيعي ومن غير مبالغة.\n"
    "- لا تكرّر نفس الجُمل أو العبارات في كل رد، ونوّع صياغتك.\n"
    "- تجنّب تماماً عبارات الحشو مثل: «بالطبع»، «بكل سرور»، «يسعدني»، «نفخر بتقديم».\n"
    "- رد بنفس لغة العميل ولهجته العامة، وبأسلوب واضح ومباشر.\n"
    "- إذا كان السؤال خارج نطاق خدماتنا أو لا تملك معلومة مؤكدة عنه، اعتذر بإيجاز "
    "ووجّه العميل للتواصل المباشر بدل أن تخمّن.\n"
    "- لا تقدّم نصائح طبية أو قانونية أو مالية؛ وجّه العميل لمختص عند الحاجة.\n"
    "- استند فقط إلى معلومات النشاط وقاعدة المعرفة، ولا تخترع أسعاراً أو وعوداً."
)


def _full_system(base_prompt: str, knowledge: str, handoff_enabled: bool) -> str:
    blocks = [base_prompt or "أنت مساعد ودود.", _STYLE_RULES]

    if knowledge:
        blocks.append(
            "=== قاعدة المعرفة (استخدمها للإجابة بدقة، ولا تخترع معلومات خارجها) ===\n"
            + knowledge
        )

    if handoff_enabled:
        blocks.append(
            "=== قاعدة التحويل لمسؤول بشري ===\n"
            "إذا سأل العميل عن شيء خارج نطاق خدماتنا، أو طلب التحدث مع موظف/إنسان، "
            "أو قدّم شكوى تحتاج تدخّلاً بشرياً، أو طلب أمراً لا تملك معلومات مؤكدة عنه "
            "(مثل طلب خاص، استرجاع، سعر غير موجود في قاعدة المعرفة) — "
            f"فلا تخمّن أبداً. بدلاً من ذلك اكتب هذه العلامة فقط ولا شيء غيرها: {HANDOFF_TOKEN}"
        )

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------
# الدالة الرئيسية
# ---------------------------------------------------------------------
def get_reply(
    client_id: str,
    phone: str,
    message: str,
    system_prompt: str,
    handoff_enabled: bool = True,
    handoff_message: str = "",
) -> dict:
    """يولّد رد البوت ويحدّد هل تحتاج المحادثة تدخّلاً بشرياً."""
    history, conv_id, _ = _load_conversation(client_id, phone)
    history.append({"role": "user", "content": message, "timestamp": _now()})

    knowledge = _load_knowledge(client_id)
    system = _full_system(system_prompt, knowledge, handoff_enabled)

    recent = [
        {"role": m["role"], "content": m["content"]}
        for m in history[-CONTEXT_WINDOW:]
    ]

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=recent,
        )
        reply = (response.content[0].text or "").strip()
    except Exception as e:
        print(f"[claude_handler] استدعاء Claude: {e}")
        _save_conversation(client_id, phone, conv_id, history)
        return {"reply": "عذراً، حدث خطأ مؤقت. يرجى المحاولة بعد قليل 🙏",
                "handoff": False, "reason": ""}

    # كشف التحويل
    if handoff_enabled and HANDOFF_TOKEN in reply:
        msg = handoff_message or "لحظة من فضلك، بحوّلك لأحد موظفينا ليساعدك 🙏"
        history.append({"role": "assistant", "content": msg, "timestamp": _now(),
                        "system_note": "تحويل تلقائي لمسؤول"})
        _save_conversation(
            client_id, phone, conv_id, history,
            needs_human=True, status="human",
            handoff_reason="سؤال خارج نطاق البوت",
        )
        return {"reply": msg, "handoff": True, "reason": "سؤال خارج نطاق البوت"}

    # رد عادي
    history.append({"role": "assistant", "content": reply, "timestamp": _now()})
    _save_conversation(client_id, phone, conv_id, history)
    return {"reply": reply, "handoff": False, "reason": ""}
