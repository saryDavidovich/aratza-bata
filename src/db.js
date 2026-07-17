const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');

const DATA_DIR = path.join(__dirname, '..', 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

// לוג אבחוני - תראה את זה ב-Railway logs. משווים את הנתיב הזה ל-Mount Path
// שהגדרת ל-Volume: אם הם לא זהים, זו הסיבה שהנתונים לא נשמרים.
console.log('=== אבחון מסד נתונים ===');
console.log('נתיב מוחלט לקובץ מסד הנתונים:', path.join(DATA_DIR, 'newsletter.db'));
console.log('תיקיית הנתונים (DATA_DIR):', DATA_DIR);
console.log('תיקיית העבודה הנוכחית (cwd):', process.cwd());
console.log('========================');

// בודקים אם התיקייה אכן ניתנת לכתיבה, ומשאירים "עקבה" שתעזור לבדוק בין
// דיפלוימנטים אם זו אותה תיקייה (persistent) או תיקייה חדשה שנוצרה מאפס בכל פעם.
try {
  const markerPath = path.join(DATA_DIR, '.startup-marker.txt');
  const previousMarker = fs.existsSync(markerPath) ? fs.readFileSync(markerPath, 'utf8') : null;
  if (previousMarker) {
    console.log('נמצאה עקבה מהפעלה קודמת - זה סימן טוב, ה-Volume כנראה מחובר נכון:', previousMarker.trim());
  } else {
    console.log('לא נמצאה עקבה מהפעלה קודמת - אם זו לא ההפעלה הראשונה אי-פעם, זה סימן שה-Volume לא מחובר.');
  }
  fs.writeFileSync(markerPath, `הופעל לאחרונה ב-${new Date().toISOString()}\n`);
} catch (err) {
  console.error('שגיאה בכתיבה לתיקיית הנתונים - כנראה בעיית הרשאות:', err.message);
}

const db = new Database(path.join(DATA_DIR, 'newsletter.db'));
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

db.exec(`
-- הגדרות גלובליות שניתנות לעריכה מהממשק בלי redeploy (למשל פרטי חיבור
-- נדרים פלוס) - key-value פשוט, במקום טבלה עם עמודה קבועה לכל הגדרה.
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- יומן מלא של כל בקשת CallBack שהתקבלה מ-/payment/webhook, כולל כאלה
-- שנדחו (IP לא מוכר) - כדי שיהיה תיעוד לבדוק/להוכיח מה קרה בפועל, ולתת
-- למנהל דרך לזהות ולשחרר ידנית תשלום שנדחה בטעות (למשל אם נדרים פלוס
-- מוסיפים כתובת IP חדשה שעדיין לא בתיעוד - ראה src/nedarim.js).
CREATE TABLE IF NOT EXISTS webhook_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT DEFAULT (datetime('now')),
  source_ip TEXT,
  trusted INTEGER DEFAULT 0,
  item_id INTEGER,
  payment_token TEXT,
  raw_body TEXT,
  outcome TEXT
);

CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  accent_color TEXT DEFAULT '#1D9E75',
  active INTEGER DEFAULT 1,
  show_ad_buttons INTEGER DEFAULT 1,
  show_ask_button INTEGER DEFAULT 1,
  section_order TEXT DEFAULT 'qa,topics,ads',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  email TEXT NOT NULL,
  confirmed INTEGER DEFAULT 0,
  unsubscribed INTEGER DEFAULT 0,
  token TEXT UNIQUE NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(list_id, email)
);

-- items = questions, answers, classified ads, and topics/articles - one
-- unified table so the approval queue and the weekly compiler can treat
-- them uniformly.
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK(type IN ('question','answer','ad','article')),
  parent_id INTEGER REFERENCES items(id), -- answer -> points to its question
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','sent')),
  from_email TEXT,
  subject TEXT DEFAULT '',
  body_raw TEXT DEFAULT '',
  body_edited TEXT,
  word_count INTEGER DEFAULT 0,
  paid_tier TEXT DEFAULT 'free' CHECK(paid_tier IN ('free','plus','premium')),
  images_json TEXT DEFAULT '[]',
  links_json TEXT DEFAULT '[]',
  bg_color TEXT,
  text_color TEXT,
  issue_id INTEGER REFERENCES issues(id),
  created_at TEXT DEFAULT (datetime('now')),
  approved_at TEXT
);

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  html TEXT,
  sent_at TEXT,
  recipient_count INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft' CHECK(status IN ('draft','sent','failed')),
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_list_status ON items(list_id, status);
CREATE INDEX IF NOT EXISTS idx_subscribers_list ON subscribers(list_id);

-- לידים שנכנסו דרך דף מילוי הפרטים הגנרי (ראה routes/leads.js) - לכל
-- מודעה עם link_type='lead' יש דף כזה משלה (מזוהה לפי items.lead_token).
-- מנותק מטבלת items כי מודעה אחת יכולה לקבל כמה לידים. בכוונה בלי
-- REFERENCES items(id) - כמו webhook_log.item_id למעלה: מיגרציות items
-- בקובץ הזה בונות מחדש את הטבלה (ALTER TABLE items RENAME TO items_old2
-- וכו', כי SQLite לא מאפשר לשנות CHECK קיים) - וב-SQLite, רינום טבלה
-- עם FK פעיל מעדכן אוטומטית את הפניית ה-FK בטבלאות אחרות לשם הזמני, ואז
-- נשאר תלוי באוויר אחרי שהטבלה הזמנית נמחקת. FK בפועל לא היה נאכף כאן
-- ממילא (better-sqlite3 לא מפעיל foreign_keys כברירת מחדל).
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_leads_item ON leads(item_id);
`);

// ---------------------------------------------------------------------
// מיגרציות - עבור מסדי נתונים שכבר נוצרו לפני העדכון הזה (למשל אצלך
// בפרודקשן). CREATE TABLE IF NOT EXISTS למעלה לא נוגע בטבלה קיימת, אז
// מוסיפים כאן את מה שחסר בבטחה, בלי לאבד נתונים קיימים.
// ---------------------------------------------------------------------

// א. עמודות חדשות בטבלת lists
const listCols = db.prepare("PRAGMA table_info(lists)").all().map(c => c.name);
if (!listCols.includes('show_ad_buttons')) db.exec("ALTER TABLE lists ADD COLUMN show_ad_buttons INTEGER DEFAULT 1");
if (!listCols.includes('show_ask_button')) db.exec("ALTER TABLE lists ADD COLUMN show_ask_button INTEGER DEFAULT 1");
if (!listCols.includes('section_order')) db.exec("ALTER TABLE lists ADD COLUMN section_order TEXT DEFAULT 'qa,topics,ads'");

// שליחה אוטומטית שבועית - יום ושעה לפי שעון ישראל (כולל קיץ/חורף אוטומטית,
// ראה timeUtil.js + server.js). ברירת המחדל (יום 4 = חמישי, 09:00) זהה
// למה שהיה קבוע בקוד קודם, כדי שלא ישתנה כלום עד שמישהו משנה בהגדרות.
if (!listCols.includes('send_day')) db.exec("ALTER TABLE lists ADD COLUMN send_day INTEGER DEFAULT 4");
if (!listCols.includes('send_hour')) db.exec("ALTER TABLE lists ADD COLUMN send_hour INTEGER DEFAULT 9");
if (!listCols.includes('send_minute')) db.exec("ALTER TABLE lists ADD COLUMN send_minute INTEGER DEFAULT 0");
// תאריך (לפי שעון ישראל, כמו '2026-07-13') של השליחה האוטומטית האחרונה -
// מונע שליחה כפולה אם השרת בודק כמה פעמים באותה דקה/יום.
if (!listCols.includes('last_auto_send_date')) db.exec("ALTER TABLE lists ADD COLUMN last_auto_send_date TEXT");

// פלטת הצבעים שהלקוח יכול לבקש במייל (ראה inbound.js) - כל רשימה יכולה
// להגדיר אילו שמות צבע קיימים ולאיזה גוון מדויק כל שם מתאים.
const DEFAULT_COLOR_PALETTE = JSON.stringify([
  { name: 'לבן', hex: '#FFFFFF' }, { name: 'שחור', hex: '#111111' },
  { name: 'אדום', hex: '#E4572E' }, { name: 'ורוד', hex: '#F7B2C4' },
  { name: 'כתום', hex: '#F2994A' }, { name: 'צהוב', hex: '#F6D860' },
  { name: 'ירוק', hex: '#8FD19E' }, { name: 'תכלת', hex: '#A7D8F0' },
  { name: 'כחול', hex: '#5B8DEF' }, { name: 'סגול', hex: '#B48EF0' },
  { name: 'חום', hex: '#B08968' }, { name: 'אפור', hex: '#C9C9C9' },
  { name: 'קרם', hex: '#FFF6E5' }, { name: 'בז\'', hex: '#F3E5D0' }
]);
if (!listCols.includes('ad_color_palette_json')) {
  const escaped = DEFAULT_COLOR_PALETTE.replace(/'/g, "''");
  db.exec(`ALTER TABLE lists ADD COLUMN ad_color_palette_json TEXT DEFAULT '${escaped}'`);
}
db.prepare("UPDATE lists SET ad_color_palette_json = ? WHERE ad_color_palette_json IS NULL")
  .run(DEFAULT_COLOR_PALETTE);

// כפתורי "פרסום מודעה" - היה מתג אחד לכל השלוש ביחד, עכשיו נפרד לכל רמה
// (למשל אפשר להציג רק "מודעת שורה" בלי מודגשת/פרימיום). מיגרציה: מי
// שהיה לו show_ad_buttons=1 מקבל את כל השלושה מופעלות (כמו שהיה בפועל
// עד עכשיו), כדי שכלום לא ישתנה מבחינת מי שכבר משתמש במערכת.
if (!listCols.includes('show_ads_free')) {
  db.exec("ALTER TABLE lists ADD COLUMN show_ads_free INTEGER DEFAULT 1");
  db.exec("ALTER TABLE lists ADD COLUMN show_ads_plus INTEGER DEFAULT 1");
  db.exec("ALTER TABLE lists ADD COLUMN show_ads_premium INTEGER DEFAULT 1");
  db.exec("UPDATE lists SET show_ads_free = show_ad_buttons, show_ads_plus = show_ad_buttons, show_ads_premium = show_ad_buttons");
}

// מחיר (בשקלים, מספר שלם) למודעה מודגשת/פרימיום - נגדר בנפרד לכל רשימה
// (ראה admin/settings). 0 = בחינם, כמו שהיה עד עכשיו - כך שדרוג המערכת
// הזה לא משנה כלום למי שלא נכנס להגדיר מחיר בפועל.
if (!listCols.includes('plus_price')) {
  db.exec("ALTER TABLE lists ADD COLUMN plus_price INTEGER DEFAULT 0");
  db.exec("ALTER TABLE lists ADD COLUMN premium_price INTEGER DEFAULT 0");
}

// תוספת מחיר (בשקלים) כשהלקוח מצרף קישור למודעה מודגשת/פרימיום - מעל
// מחיר הבסיס של הרמה. נפרד לכל רשימה, כמו plus_price/premium_price.
// 0 = בלי תוספת (קישור בחינם, אם מופעל בכלל).
if (!listCols.includes('link_price_plus')) {
  db.exec("ALTER TABLE lists ADD COLUMN link_price_plus INTEGER DEFAULT 0");
  db.exec("ALTER TABLE lists ADD COLUMN link_price_premium INTEGER DEFAULT 0");
}

// הגדרות ליד: האם המפרסם (advertiser, מזוהה לפי from_email על המודעה)
// מקבל מייל על כל ליד שנכנס, ומחיר-ייחוס לליד (לא נגבה אוטומטית - רק
// מוצג למנהל כתזכורת לחיוב ידני, ראה admin/lists/:id/leads).
if (!listCols.includes('lead_notify_advertiser')) {
  db.exec("ALTER TABLE lists ADD COLUMN lead_notify_advertiser INTEGER DEFAULT 0");
  db.exec("ALTER TABLE lists ADD COLUMN lead_price INTEGER DEFAULT 0");
}

// הודעות "צור קשר" מהלקוחות למנהל - טבלה נפרדת (לא items, כי אלה לא
// מיועדות לפרסום בגיליון) - עם ציון מאיזו רשימה כל הודעה הגיעה.
db.exec(`
CREATE TABLE IF NOT EXISTS contact_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  list_id INTEGER REFERENCES lists(id) ON DELETE SET NULL,
  from_email TEXT,
  subject TEXT DEFAULT '',
  body TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);
`);

// ב. טבלת items: אם היא נוצרה עם הסכמה הישנה (בלי 'article' ובלי צבעים),
// צריך לבנות אותה מחדש כי SQLite לא מאפשר לשנות CHECK קיים. שומרים את כל
// הנתונים הקיימים בתהליך.
const itemsTableSql = db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name='items'").get();
const itemsNeedsMigration = itemsTableSql && !itemsTableSql.sql.includes("'article'");

if (itemsNeedsMigration) {
  console.log('מריץ מיגרציה של טבלת items (הוספת תמיכה בנושאים וצבעי מודעה)...');
  db.exec(`
    ALTER TABLE items RENAME TO items_old;

    CREATE TABLE items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
      type TEXT NOT NULL CHECK(type IN ('question','answer','ad','article')),
      parent_id INTEGER REFERENCES items(id),
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','sent')),
      from_email TEXT,
      subject TEXT DEFAULT '',
      body_raw TEXT DEFAULT '',
      body_edited TEXT,
      word_count INTEGER DEFAULT 0,
      paid_tier TEXT DEFAULT 'free' CHECK(paid_tier IN ('free','plus','premium')),
      images_json TEXT DEFAULT '[]',
      links_json TEXT DEFAULT '[]',
      bg_color TEXT,
      text_color TEXT,
      issue_id INTEGER REFERENCES issues(id),
      created_at TEXT DEFAULT (datetime('now')),
      approved_at TEXT
    );

    INSERT INTO items (id, list_id, type, parent_id, status, from_email, subject, body_raw,
      body_edited, word_count, paid_tier, images_json, links_json, issue_id, created_at, approved_at)
    SELECT id, list_id, type, parent_id, status, from_email, subject, body_raw,
      body_edited, word_count, paid_tier, images_json, links_json, issue_id, created_at, approved_at
    FROM items_old;

    DROP TABLE items_old;

    CREATE INDEX IF NOT EXISTS idx_items_list_status ON items(list_id, status);
  `);
  console.log('מיגרציית items הושלמה בהצלחה.');
}

// ג2. תשלום למודעות בתשלום (מודגשת/פרימיום) - כשמוגדר מחיר > 0 לרשימה,
// מודעה חדשה לא נכנסת ישר ל"ממתין לאישור" אלא ל-status='pending_payment'
// עד שנדרים פלוס מאשרים בפועל שהתשלום בוצע (ראה src/nedarim.js +
// src/routes/payment.js). SQLite לא מאפשר ALTER על CHECK קיים, אז צריך
// לבנות מחדש את הטבלה כמו במיגרציה הקודמת (article) - באותו אופן, שומרים
// את כל הנתונים הקיימים.
const itemsTableSql2 = db.prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name='items'").get();
const itemsNeedsPaymentMigration = itemsTableSql2 && !itemsTableSql2.sql.includes("'pending_payment'");

if (itemsNeedsPaymentMigration) {
  console.log('מריץ מיגרציה של טבלת items (הוספת תמיכה בתשלום סליקה)...');
  // manual_order יתכן שעדיין לא קיים בטבלה הישנה (למשל בהתקנה חדשה
  // לגמרי, שעדיין לא הגיעה למיגרציה הייעודית לו למטה בקובץ) - בודקים
  // דינמית איזה מהעמודות באמת קיימות לפני ההעתקה, כדי לא לקבל שגיאת
  // "no such column". אם היא לא קיימת, manual_order פשוט יישאר NULL
  // בטבלה החדשה, וימולא כרגיל על ידי לוגיקת ה-backfill שמופיעה בהמשך.
  const oldItemsHasManualOrder = db.prepare("PRAGMA table_info(items)").all().some(c => c.name === 'manual_order');
  db.exec(`
    ALTER TABLE items RENAME TO items_old2;

    CREATE TABLE items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
      type TEXT NOT NULL CHECK(type IN ('question','answer','ad','article')),
      parent_id INTEGER REFERENCES items(id),
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending_payment','pending','approved','rejected','sent')),
      from_email TEXT,
      subject TEXT DEFAULT '',
      body_raw TEXT DEFAULT '',
      body_edited TEXT,
      word_count INTEGER DEFAULT 0,
      paid_tier TEXT DEFAULT 'free' CHECK(paid_tier IN ('free','plus','premium')),
      images_json TEXT DEFAULT '[]',
      links_json TEXT DEFAULT '[]',
      bg_color TEXT,
      text_color TEXT,
      issue_id INTEGER REFERENCES issues(id),
      payment_token TEXT,
      payment_amount INTEGER,
      payment_status TEXT DEFAULT 'not_required' CHECK(payment_status IN ('not_required','pending','paid')),
      nedarim_transaction_id TEXT,
      paid_at TEXT,
      manual_order INTEGER,
      created_at TEXT DEFAULT (datetime('now')),
      approved_at TEXT
    );

    INSERT INTO items (id, list_id, type, parent_id, status, from_email, subject, body_raw,
      body_edited, word_count, paid_tier, images_json, links_json, bg_color, text_color,
      issue_id, ${oldItemsHasManualOrder ? 'manual_order,' : ''} created_at, approved_at)
    SELECT id, list_id, type, parent_id, status, from_email, subject, body_raw,
      body_edited, word_count, paid_tier, images_json, links_json, bg_color, text_color,
      issue_id, ${oldItemsHasManualOrder ? 'manual_order,' : ''} created_at, approved_at
    FROM items_old2;

    DROP TABLE items_old2;

    CREATE INDEX IF NOT EXISTS idx_items_list_status ON items(list_id, status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_items_payment_token ON items(payment_token) WHERE payment_token IS NOT NULL;
  `);
  console.log('מיגרציית תשלום הושלמה בהצלחה.');
}

// ג. עמודת manual_order - סדר תצוגה גמיש לחלוטין (כל פריט - שאלה, מודעה,
// נושא - יכול לזוז לכל מקום בגיליון, לא רק בתוך "הקבוצה" שלו). כשעמודה
// זו NULL עבור פריט שכבר אושר בעבר (מלפני העדכון), ממלאים אותה פעם אחת
// לפי הסדר הישן (section_order + approved_at) כדי שהגיליון לא "יקפוץ"
// ברגע השדרוג - מכאן והלאה כל אישור חדש מקבל מספר סידורי, וגרירה בתצוגה
// המקדימה יכולה לשנות אותו בחופשיות.
const itemCols = db.prepare("PRAGMA table_info(items)").all().map(c => c.name);
if (!itemCols.includes('manual_order')) {
  db.exec("ALTER TABLE items ADD COLUMN manual_order INTEGER");
}

// שם וטלפון של שולח המודעה - נאספים בטופס האתר (לא חובה בשליחה חינמית,
// אבל נדרשים בפועל למודעה בתשלום כדי שיועברו לנדרים פלוס יחד עם המייל
// בעת יצירת העסקה - ראה src/nedarim.js createServerTransaction).
if (!itemCols.includes('client_name')) {
  db.exec("ALTER TABLE items ADD COLUMN client_name TEXT");
  db.exec("ALTER TABLE items ADD COLUMN phone TEXT");
}

// קישור לחיצה על תמונה במודעת פרימיום - אם מוגדר, לוחצים על התמונה
// ועוברים לכתובת הזו (למשל לאתר של המפרסם), במקום שהתמונה תהיה סטטית.
if (!itemCols.includes('image_link')) {
  db.exec("ALTER TABLE items ADD COLUMN image_link TEXT");
}

// קישור כללי שהלקוח עצמו מצרף למודעה מודגשת/פרימיום (בטופס האתר או
// בשורת "קישור:" במייל) - עם קישור כזה, כל המודעה (לא רק תמונה) הופכת
// ללחיצה שמעבירה לכתובת הזו. שונה מ-image_link (שהוא תוספת ידנית של
// המנהל, ספציפית לתמונה בפרימיום, ונשאר כפי שהיה לצורך תאימות לאחור).
if (!itemCols.includes('link_url')) {
  db.exec("ALTER TABLE items ADD COLUMN link_url TEXT");
}

// סוג הקישור שהמפרסם בחר: 'website' (ברירת מחדל, קישור חיצוני רגיל,
// ראה link_url) או 'lead' (הקישור מוביל לדף מילוי פרטים גנרי שהמערכת
// עצמה מארחת, ראה routes/leads.js + טבלת leads) - לא לקישור חיצוני
// שהמפרסם מגדיר בעצמו. lead_token הוא המזהה הציבורי של הדף הזה
// (בנפרד מ-payment_token, כדי שאי אפשר יהיה לנחש/לגלוש בין מודעות).
if (!itemCols.includes('link_type')) {
  db.exec("ALTER TABLE items ADD COLUMN link_type TEXT DEFAULT 'website'");
  db.exec("ALTER TABLE items ADD COLUMN lead_token TEXT");
}

const needsBackfill = db.prepare(`
  SELECT COUNT(*) AS c FROM items
  WHERE status = 'approved' AND manual_order IS NULL
    AND (
      type IN ('question', 'article', 'ad')
      OR (type = 'answer' AND EXISTS (
        SELECT 1 FROM items q WHERE q.id = items.parent_id AND q.status = 'sent'
      ))
    )
`).get().c > 0;

if (needsBackfill) {
  console.log('ממלא manual_order עבור פריטים מאושרים קיימים (לפי הסדר הישן, חד-פעמי)...');
  const setOrder = db.prepare('UPDATE items SET manual_order = ? WHERE id = ?');
  const lists = db.prepare('SELECT * FROM lists').all();
  const backfillTx = db.transaction(() => {
    for (const list of lists) {
      let seq = 1;
      const order = (list.section_order || 'qa,topics,ads').split(',').map(s => s.trim());
      for (const key of order) {
        if (key === 'qa') {
          const qs = db.prepare(`
            SELECT id FROM items WHERE list_id = ? AND type = 'question' AND status = 'approved' ORDER BY approved_at ASC
          `).all(list.id);
          for (const q of qs) setOrder.run(seq++, q.id);
        } else if (key === 'topics') {
          const ts = db.prepare(`
            SELECT id FROM items WHERE list_id = ? AND type = 'article' AND status = 'approved' ORDER BY approved_at ASC
          `).all(list.id);
          for (const t of ts) setOrder.run(seq++, t.id);
        } else if (key === 'ads') {
          const tierOrder = { premium: 0, plus: 1, free: 2 };
          const adsRows = db.prepare(`
            SELECT id, paid_tier FROM items WHERE list_id = ? AND type = 'ad' AND status = 'approved' ORDER BY approved_at ASC
          `).all(list.id).sort((a, b) => (tierOrder[a.paid_tier] ?? 9) - (tierOrder[b.paid_tier] ?? 9));
          for (const a of adsRows) setOrder.run(seq++, a.id);
        }
      }
      // תגובות המשך לשאלות שכבר נשלחו - היו תמיד בסוף, נשארות בסוף
      const followUps = db.prepare(`
        SELECT a.id FROM items a JOIN items q ON a.parent_id = q.id
        WHERE a.list_id = ? AND a.type = 'answer' AND a.status = 'approved' AND q.status = 'sent'
        ORDER BY a.approved_at ASC
      `).all(list.id);
      for (const a of followUps) setOrder.run(seq++, a.id);
    }
  });
  backfillTx();
  console.log('מילוי manual_order הושלם.');
}

module.exports = db;
