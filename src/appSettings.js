const db = require('./db');

const getStmt = db.prepare('SELECT value FROM app_settings WHERE key = ?');
const setStmt = db.prepare(`
  INSERT INTO app_settings (key, value) VALUES (?, ?)
  ON CONFLICT(key) DO UPDATE SET value = excluded.value
`);

function getSetting(key, fallback = '') {
  const row = getStmt.get(key);
  return row && row.value !== null && row.value !== undefined ? row.value : fallback;
}

function setSetting(key, value) {
  setStmt.run(key, value == null ? '' : String(value));
}

function getAllSettings() {
  return db.prepare('SELECT key, value FROM app_settings').all()
    .reduce((acc, row) => { acc[row.key] = row.value; return acc; }, {});
}

// כתובת הבסיס הציבורית של האתר (לבניית קישורי תשלום/הסרה/ארכיון וכו').
// עדיפות: הגדרה בממשק הניהול (ניתנת לעריכה בלי redeploy) > משתנה סביבה
// BASE_URL > ברירת מחדל מקומית. חשוב לוודא שזו כתובת הדומיין האמיתי
// שהלקוחות רואים - לא כתובת ה-*.up.railway.app האוטומטית של Railway,
// אם מחובר דומיין מותאם אישית.
function getBaseUrl() {
  const raw = getSetting('base_url', process.env.BASE_URL || 'http://localhost:3000');
  return raw.trim().replace(/\/+$/, ''); // בלי סלאש מסיים, כדי לא לקבל // בקישורים
}

module.exports = { getSetting, setSetting, getAllSettings, getBaseUrl };
