"""
whatsapp.py — تكامل UltraMsg: إرسال نص/صوت + اختبار الاتصال.
"""
import requests

ULTRAMSG_BASE = "https://api.ultramsg.com"
TIMEOUT = 20


def send_message(instance: str, token: str, phone: str, message: str) -> dict:
    """إرسال رسالة نصية عبر UltraMsg."""
    if not instance or not token:
        return {"error": "instance أو token مفقود"}

    url = f"{ULTRAMSG_BASE}/{instance}/messages/chat"
    payload = {"token": token, "to": phone, "body": message}
    try:
        response = requests.post(url, data=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"فشل إرسال الرسالة عبر UltraMsg: {str(e)}"}
    except ValueError:
        return {"error": "رد UltraMsg غير صالح (ليس JSON)"}


def send_audio(instance: str, token: str, phone: str, audio_url: str) -> dict:
    """إرسال رسالة صوتية (URL عام لملف صوتي) عبر UltraMsg."""
    if not instance or not token:
        return {"error": "instance أو token مفقود"}
    if not audio_url:
        return {"error": "رابط الصوت مفقود"}

    url = f"{ULTRAMSG_BASE}/{instance}/messages/audio"
    payload = {"token": token, "to": phone, "audio": audio_url}
    try:
        response = requests.post(url, data=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"فشل إرسال الصوت عبر UltraMsg: {str(e)}"}
    except ValueError:
        return {"error": "رد UltraMsg غير صالح"}


def download_media(media_url: str) -> bytes:
    """تحميل ملف وسائط (صوت/صورة) من رابط UltraMsg. يرجّع bytes أو None."""
    if not media_url:
        return None
    try:
        r = requests.get(media_url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException as e:
        print(f"[whatsapp] تحميل الوسائط: {e}")
        return None


def test_connection(instance: str, token: str) -> dict:
    """اختبار صلاحية بيانات UltraMsg عبر استعلام حالة الـ instance."""
    if not instance or not token:
        return {"ok": False, "error": "أدخل Instance ID و Token أولاً"}

    url = f"{ULTRAMSG_BASE}/{instance}/instance/status"
    try:
        response = requests.get(url, params={"token": token}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        status = (
            data.get("accountStatus", {}).get("status")
            or data.get("status")
            or ""
        )
        if str(status).lower() in ("authenticated", "connected", "success", "got qr code"):
            return {"ok": True, "status": status}
        if "error" in data:
            return {"ok": False, "error": str(data.get("error"))}
        return {"ok": True, "status": status or "تم الاتصال"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"تعذّر الاتصال بـ UltraMsg: {str(e)}"}
    except ValueError:
        return {"ok": False, "error": "رد UltraMsg غير صالح"}
