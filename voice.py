"""
voice.py — تحويل الرسائل الصوتية الواردة إلى نص (Speech-to-Text).

ملاحظة مهمة:
  Anthropic (Claude) لا يحوّل الصوت إلى نص، لذلك نستخدم خدمة Whisper من OpenAI.
  الميزة اختيارية: تعمل فقط إذا أضفت متغيّر البيئة OPENAI_API_KEY على Railway.
  إن لم يُضف المفتاح، يردّ البوت برسالة لطيفة تطلب الكتابة بدل الصوت.
"""
import os
import io
import requests

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
TIMEOUT = 40


def voice_available() -> bool:
    return bool(OPENAI_KEY)


def transcribe(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """
    يحوّل بايتات الصوت إلى نص عربي/متعدد اللغات.
    يرجّع النص، أو "" عند الفشل/عدم توفّر المفتاح.
    """
    if not OPENAI_KEY or not audio_bytes:
        return ""
    try:
        files = {"file": (filename, io.BytesIO(audio_bytes))}
        data = {"model": "whisper-1"}
        headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
        r = requests.post(WHISPER_URL, headers=headers, files=files,
                          data=data, timeout=TIMEOUT)
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    except Exception as e:
        print(f"[voice] فشل التحويل: {e}")
        return ""
