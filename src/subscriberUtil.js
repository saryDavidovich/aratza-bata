const db = require('./db');
const { v4: uuidv4 } = require('uuid');

// מצרף מייל לרשימה. אם הוא כבר קיים בטבלה (כולל אם הוסר בעבר עם
// unsubscribed=1), מפעיל אותו מחדש דרך ON CONFLICT DO UPDATE, במקום לנסות
// INSERT ולהיכשל על אילוץ ה-UNIQUE(list_id, email) ולוותר בשקט - זה בדיוק
// מה שגרם לזה שלקוח שהוסר פעם אחת לא הצליח להצטרף שוב, בין אם ניסה בעצמו
// במייל ובין אם המנהל הוסיף אותו ידנית. פונקציה אחת משותפת לכל הנתיבים
// (הצטרפות מהלקוח במייל, טופס באתר, הוספה ידנית, העלאת קובץ) כדי שההתנהגות
// תהיה עקבית בכל מקום.
function subscribeEmail(listId, email) {
  const normalized = String(email || '').toLowerCase().trim();
  if (!normalized) return { ok: false, reactivated: false };

  const before = db.prepare('SELECT unsubscribed FROM subscribers WHERE list_id = ? AND email = ?').get(listId, normalized);

  db.prepare(`
    INSERT INTO subscribers (list_id, email, confirmed, token)
    VALUES (?, ?, 1, ?)
    ON CONFLICT(list_id, email) DO UPDATE SET unsubscribed = 0
  `).run(listId, normalized, uuidv4());

  return { ok: true, reactivated: !!(before && before.unsubscribed), isNew: !before };
}

module.exports = { subscribeEmail };
