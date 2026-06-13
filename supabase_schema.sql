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
  UNIQUE (client_id)                  -- إعدادات واحدة لكل عميل (لازمة لـ upsert)
);

-- ---------------------------------------------------------------------
-- 3) سجل المحادثات
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id       UUID REFERENCES clients(id) ON DELETE CASCADE,
  customer_phone  TEXT NOT NULL,
  messages        JSONB DEFAULT '[]', -- [{role, content, timestamp}]
  last_message_at TIMESTAMPTZ DEFAULT now(),
  message_count   INT DEFAULT 0,
  UNIQUE (client_id, customer_phone)  -- محادثة واحدة لكل عميل/رقم
);

CREATE INDEX IF NOT EXISTS idx_conv_client
  ON conversations (client_id, last_message_at DESC);

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
