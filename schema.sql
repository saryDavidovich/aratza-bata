-- מבנה נתונים למערכת רשימות התפוצה

CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                -- שם הנושא, לדוגמה "הורות"
  slug TEXT NOT NULL UNIQUE,         -- מזהה לכתובת המייל, לדוגמה parenting
  inbound_email TEXT NOT NULL,       -- הכתובת שאליה שולחים: parenting@yourdomain.com
  send_day TEXT DEFAULT 'thursday',  -- יום שליחה שבועי
  send_hour INTEGER DEFAULT 9,       -- שעת שליחה
  theme_color TEXT DEFAULT '#1D9E75',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  topic_id INTEGER NOT NULL REFERENCES topics(id),
  unsubscribe_token TEXT NOT NULL,
  subscribed_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(email, topic_id)
);

-- כל פריט שממתין לאישור: שאלה, תשובה (תגובה לשאלה קיימת), או מודעת לוח מילים
CREATE TABLE IF NOT EXISTS queue_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id),
  type TEXT NOT NULL CHECK (type IN ('question', 'answer', 'classified')),
  parent_id INTEGER REFERENCES queue_items(id), -- לתשובה: מצביע לשאלה המקורית
  sender_email TEXT NOT NULL,
  sender_name TEXT,
  subject TEXT,
  body TEXT NOT NULL,
  word_count INTEGER,
  -- שדות עתידיים בתשלום - כבר בנויים, לא חשופים ללקוח כרגע
  is_paid_tier INTEGER DEFAULT 0,
  image_urls TEXT DEFAULT '[]',   -- JSON array, ריק כברירת מחדל
  gif_urls TEXT DEFAULT '[]',
  link_url TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'sent')),
  reply_token TEXT,               -- מזהה ייחודי לכתובת המענה של שאלה
  received_at TEXT DEFAULT CURRENT_TIMESTAMP,
  approved_at TEXT
);

CREATE TABLE IF NOT EXISTS digests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id INTEGER NOT NULL REFERENCES topics(id),
  html_content TEXT NOT NULL,
  sent_at TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- הגבלת מספר מודעות חינמיות בשבוע ללקוח (הגנה מפני ניצול לרעה)
CREATE TABLE IF NOT EXISTS rate_limits (
  sender_email TEXT NOT NULL,
  topic_id INTEGER NOT NULL,
  week_key TEXT NOT NULL, -- לדוגמה 2026-W27
  count INTEGER DEFAULT 0,
  PRIMARY KEY (sender_email, topic_id, week_key)
);
