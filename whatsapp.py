"""
whatsapp.py — تكامل UltraMsg لإرسال رسائل واتساب.
"""
import requests

ULTRAMSG_BASE = "https://api.ultramsg.com"
TIMEOUT = 20


def send_message(instance: str, token: str, phone: str, message: str) -> dict:
    """
    إرسال رسالة نصية عبر UltraMsg.

    Returns: dict من رد UltraMsg، أو {'error': '...'} عند الفشل.
    """
    if not instance or not token:
        return {"error": "instance أو token مفقود"}

    url = f"{ULTRAMSG_BASE}/{instance}/messages/chat"
    payload = {"token": token, "to": phone, "body": message}

    try:
        response = requests.post(url, data=payload, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        # لا نكشف الـ token في أي رسالة خطأ
        return {"error": f"فشل إرسال الرسالة عبر UltraMsg: {str(e)}"}
    except ValueError:
        return {"error": "رد UltraMsg غير صالح (ليس JSON)"}


def test_connection(instance: str, token: str) -> dict:
    """
    اختبار صلاحية بيانات UltraMsg عبر استعلام حالة الـ instance.

    Returns: {'ok': True} أو {'ok': False, 'error': '...'}
    """
    if not instance or not token:
        return {"ok": False, "error": "أدخل Instance ID و Token أولاً"}

    url = f"{ULTRAMSG_BASE}/{instance}/instance/status"
    try:
        response = requests.get(url, params={"token": token}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()

        # UltraMsg يرجّع حالة الجهاز داخل accountStatus / status
        status = (
            data.get("accountStatus", {}).get("status")
            or data.get("status")
            or ""
        )
        if str(status).lower() in ("authenticated", "connected", "success", "got qr code"):
            return {"ok": True, "status": status}
        if "error" in data:
            return {"ok": False, "error": str(data.get("error"))}
        # نرجّع الحالة كما هي ليقرأها العميل
        return {"ok": True, "status": status or "تم الاتصال"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"تعذّر الاتصال بـ UltraMsg: {str(e)}"}
    except ValueError:
        return {"ok": False, "error": "رد UltraMsg غير صالح"}
