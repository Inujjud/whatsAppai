-- =====================================================================
--  WhatsApp AI SaaS — Supabase Schema
--  شغّل هذا الملف كاملاً في:  Supabase Dashboard → SQL Editor → New Query
-- =====================================================================

-- لتفعيل gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------
-- 1) حسابات العملاء
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
  id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  business_name TEXT NOT NULL,
  business_type TEXT,                 -- مطعم / صالون / عيادة / محل / أخرى
  plan          TEXT DEFAULT 'trial', -- trial / basic / pro
  created_at    TIMESTAMPTZ DEFAULT now(),
  is_active     BOOL DEFAULT true
);

-- ---------------------------------------------------------------------
-- 2) إعدادات البوت لكل عميل
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_settings (
  id                  UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id           UUID REFERENCES clients(id) ON DELETE CASCADE,
  bot_name            TEXT DEFAULT 'مساعد',
  bot_personality     TEXT,           -- وصف شخصية البوت
  business_info       TEXT,           -- معلومات النشاط (خدمات، أوقات، موقع...)
  welcome_message     TEXT,           -- رسالة الترحيب
  ultramsg_instance   TEXT,           -- UltraMsg instance ID
  ultramsg_token      TEXT,           -- UltraMsg API token (مشفّر في الإنتاج)
  claude_system_prompt TEXT,          -- الـ system prompt الكامل (يُبنى تلقائياً)
  is_active           BOOL DEFAULT false,
  updated_at          TIMESTAMPTZ DEFAULT now(),
  -- --- باني شخصية البوت (Personality Builder) ---
  bot_role            TEXT,           -- دور البوت (موظف خدمة عملاء، مساعد مبيعات...)
  bot_tone            TEXT,           -- نبرة الحديث (ودّي، رسمي، مرح...)
  bot_avatar          TEXT DEFAULT '🤖', -- أيقونة/إيموجي البوت
  language            TEXT DEFAULT 'ar', -- لغة البوت
  dos                 TEXT,           -- ما يجب أن يفعله البوت
  donts               TEXT,           -- ما يجب أن يتجنبه البوت
  sample_qa           JSONB DEFAULT '[]', -- [{q, a}] أمثلة أسئلة/أجوبة
  flows               JSONB DEFAULT '[]', -- [{trigger, action}] مسارات ذكية
  -- --- الرسائل الصوتية (Voice) ---
  voice_enabled       BOOL DEFAULT false, -- تحويل الصوت الوارد إلى نص
  voice_reply         BOOL DEFAULT false, -- الرد بصوت (مستقبلاً)
  -- --- التسليم للمسؤول (Human Handoff) ---
  handoff_enabled     BOOL DEFAULT true,  -- تفعيل التحويل التلقائي لمسؤول
  handoff_message     TEXT,           -- رسالة تُرسل للعميل عند التحويل
  handoff_keywords    TEXT,           -- كلمات تُفعّل التحويل فوراً (مفصولة بفاصلة)
  owner_phone         TEXT,           -- رقم المسؤول لإشعاره عند التحويل
  UNIQUE (client_id)                  -- إعدادات واحدة لكل عميل (لازمة لـ upsert)
);

-- ---------------------------------------------------------------------
-- 3) سجل المحادثات
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id       UUID REFERENCES clients(id) ON DELETE CASCADE,
  customer_phone  TEXT NOT NULL,
  customer_name   TEXT,               -- اسم العميل (إن توفّر)
  messages        JSONB DEFAULT '[]', -- [{role, content, timestamp}]
  last_message_at TIMESTAMPTZ DEFAULT now(),
  message_count   INT DEFAULT 0,
  needs_human     BOOL DEFAULT false, -- يحتاج تدخّل مسؤول بشري
  status          TEXT DEFAULT 'bot', -- bot / human / resolved
  handoff_reason  TEXT,               -- سبب التحويل للمسؤول
  UNIQUE (client_id, customer_phone)  -- محادثة واحدة لكل عميل/رقم
);

CREATE INDEX IF NOT EXISTS idx_conv_client
  ON conversations (client_id, last_message_at DESC);

CREATE INDEX IF NOT EXISTS idx_conv_needs_human
  ON conversations (client_id, needs_human);

-- ---------------------------------------------------------------------
-- 3b) قاعدة المعرفة (Knowledge Base) — معلومات يستند إليها البوت
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_base (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id   UUID REFERENCES clients(id) ON DELETE CASCADE,
  category    TEXT,                   -- تصنيف (أسعار، أوقات، سياسات...)
  title       TEXT NOT NULL,          -- عنوان البند
  content     TEXT NOT NULL,          -- محتوى البند
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_client
  ON knowledge_base (client_id);

-- ---------------------------------------------------------------------
-- 4) إحصائيات يومية
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics (
  id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id         UUID REFERENCES clients(id) ON DELETE CASCADE,
  date              DATE DEFAULT CURRENT_DATE,
  messages_received INT DEFAULT 0,
  messages_sent     INT DEFAULT 0,
  unique_customers  INT DEFAULT 0,
  UNIQUE (client_id, date)            -- سجل واحد لكل عميل/يوم
);

CREATE INDEX IF NOT EXISTS idx_analytics_client
  ON analytics (client_id, date DESC);

-- =====================================================================
--  ملاحظة الأمان (Row Level Security)
--  السيرفر يستخدم SERVICE_ROLE key الذي يتجاوز RLS، لذلك كل التحقق
--  من الصلاحيات يتم داخل Flask. لا تكشف الـ service key للمتصفح إطلاقاً.
--  إن أردت استخدام anon key من المتصفح مباشرة فعّل RLS وأضف سياسات مناسبة.
-- =====================================================================
