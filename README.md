# 💬 واتس بوت — منصة WhatsApp AI SaaS

منصة كاملة تتيح لأي نشاط تجاري (مطعم، صالون، عيادة، محل…) تشغيل بوت واتساب ذكي
مدعوم بـ Claude AI. كل عميل يشترك، يدخل بياناته من لوحة تحكم، ويشتغل البوت بنفسه
بدون أي تدخل تقني.

---

## 📁 هيكل المشروع

```
whatsapp-saas/
├── index.html              ← الصفحة التسويقية
├── login.html              ← تسجيل دخول / إنشاء حساب
├── dashboard.html          ← لوحة تحكم العميل
├── supabase_schema.sql     ← جداول قاعدة البيانات
├── railway.json            ← إعداد النشر على Railway
├── nixpacks.toml           ← إعداد البناء
├── .gitignore
└── server/
    ├── app.py              ← Flask (Webhook + API + مصادقة + تقديم الصفحات)
    ├── claude_handler.py   ← منطق ردود Claude + سجل المحادثة
    ├── whatsapp.py         ← تكامل UltraMsg (إرسال + اختبار)
    ├── requirements.txt
    ├── Procfile
    └── .env.example
```

---

## 🚀 خطوات الإعداد

### 1) قاعدة البيانات — Supabase
1. أنشئ مشروعاً على [supabase.com](https://supabase.com).
2. افتح **SQL Editor → New Query** والصق محتوى `supabase_schema.sql` ثم **Run**.
3. من **Project Settings → API** انسخ:
   - `Project URL` → سيكون `SUPABASE_URL`
   - مفتاح **`service_role`** (وليس anon) → سيكون `SUPABASE_KEY`
   > ⚠️ مفتاح service_role يُستخدم على السيرفر فقط ولا يُكشف في المتصفح أبداً.

### 2) المفاتيح المطلوبة (متغيرات البيئة)
```env
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=eyJ...service_role...
ANTHROPIC_API_KEY=sk-ant-...
SECRET_KEY=قيمة-عشوائية-طويلة-وسرية
```
- `ANTHROPIC_API_KEY` من [console.anthropic.com](https://console.anthropic.com).
- `SECRET_KEY` ولّده بأمر: `python -c "import secrets; print(secrets.token_hex(32))"`.

### 3) التشغيل محلياً (اختياري للتجربة)
```bash
cd server
cp .env.example .env          # املأ القيم
pip install -r requirements.txt
python app.py                 # يفتح على http://localhost:5000
```

---

## ☁️ النشر على Railway

1. ارفع المشروع على **GitHub**.
2. ادخل [railway.app](https://railway.app) → **New Project → Deploy from GitHub Repo**.
3. بعد الربط، أضف المتغيرات في **Variables**:
   `SUPABASE_URL`، `SUPABASE_KEY`، `ANTHROPIC_API_KEY`، `SECRET_KEY`.
4. Railway يبني المشروع تلقائياً (عبر `nixpacks.toml`) ويعطيك رابطاً مثل:
   ```
   https://your-app.railway.app
   ```
5. رابط الـ Webhook لكل عميل يكون تلقائياً:
   ```
   https://your-app.railway.app/webhook/{client_id}
   ```
   (لوحة التحكم تعرض الرابط الصحيح لكل عميل بعد الحفظ.)

---

## 🧩 خطوات التسليم للعميل

1. العميل يفتح الرابط ← **حساب جديد** ويدخل اسم نشاطه وبريده.
2. من لوحة التحكم يكتب: اسم البوت، شخصيته، معلومات نشاطه، ورسالة الترحيب.
3. يشتري اشتراك [UltraMsg](https://ultramsg.com) ويربط رقم واتساب نشاطه.
4. يدخل **Instance ID** و **Token** في لوحة التحكم ويضغط **اختبر الاتصال**.
5. يضغط **احفظ وشغّل البوت** ← ينسخ رابط الـ Webhook.
6. يضع الرابط في **UltraMsg → Settings → Webhook → URL**.
7. ✅ خلاص — البوت يرد على عملائه على واتساب.

---

## 🔌 واجهات الـ API

| الطريق | الوصف |
|---|---|
| `POST /api/signup` | إنشاء حساب |
| `POST /api/login` | تسجيل الدخول (يرجّع `client_id`) |
| `POST /api/settings` | حفظ الإعدادات وتشغيل البوت (يرجّع `webhook_url`) |
| `GET  /api/settings/<client_id>` | تحميل الإعدادات (بدون أسرار) |
| `GET  /api/stats/<client_id>` | إحصائيات لوحة التحكم |
| `GET  /api/conversations/<client_id>` | آخر 10 محادثات |
| `POST /api/test-connection` | اختبار UltraMsg |
| `POST /webhook/<client_id>` | استقبال رسائل واتساب (يرجّع `ok` دائماً) |

اللوحة تحفظ `client_id` بعد تسجيل الدخول وتستخدمه في مسارات الـ API.

---

## 🔒 ملاحظات الأمان
- كلمات المرور مخزّنة مجزّأة بـ **werkzeug** (`generate_password_hash`) — لا نص صريح.
- توكن UltraMsg و API keys **لا تُعاد أبداً** للمتصفح في أي رد.
- السيرفر فقط يحمل مفتاح `service_role`.
- يُحتفظ بآخر **٢٠ رسالة** فقط في سياق Claude لتوفير تكاليف التوكن.

> 🔐 للإنتاج (مهم): اللوحة حالياً تستخدم `client_id` في مسار الـ API بدون توكن
> جلسة، وهذا كافٍ للـ MVP لكنه غير محمي ضد التخمين. عند التوسّع أضِف:
> توكن جلسة موقّع (JWT/itsdangerous)، تفعيل RLS في Supabase، وتشفير
> `ultramsg_token` قبل تخزينه (مثلاً بـ Fernet).

---

© ٢٠٢٦ واتس بوت
