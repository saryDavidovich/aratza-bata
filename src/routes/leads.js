const express = require('express');
const router = express.Router();
const db = require('../db');

// -------- דף מילוי פרטים גנרי לליד --------
// כשמפרסם בוחר "ליד" במקום קישור רגיל (ראה routes/public.js), הקישור
// שהמודעה עוטפת מוביל לכאן במקום לאתר חיצוני - דף אחד גנרי שהמערכת
// עצמה מארחת, מזוהה לפי lead_token (לא item.id, כדי שלא יהיה ניתן
// לנחש/לגלוש בין מודעות של לקוחות אחרים).

router.get('/:token', (req, res) => {
  const item = db.prepare(`
    SELECT items.*, lists.name AS list_name, lists.accent_color
    FROM items JOIN lists ON lists.id = items.list_id
    WHERE items.lead_token = ? AND items.link_type = 'lead'
  `).get(req.params.token);

  if (!item) return res.status(404).send('הדף לא נמצא - ייתכן שהקישור שגוי או שהמודעה כבר לא פעילה.');

  res.render('leads/capture', { item, error: null, sent: false });
});

router.post('/:token', express.urlencoded({ extended: true }), async (req, res) => {
  const item = db.prepare(`
    SELECT items.*, lists.id AS list_id, lists.name AS list_name, lists.accent_color,
      lists.lead_notify_advertiser
    FROM items JOIN lists ON lists.id = items.list_id
    WHERE items.lead_token = ? AND items.link_type = 'lead'
  `).get(req.params.token);

  if (!item) return res.status(404).send('הדף לא נמצא - ייתכן שהקישור שגוי או שהמודעה כבר לא פעילה.');

  const name = (req.body.name || '').trim();
  const phone = (req.body.phone || '').trim();
  const note = (req.body.note || '').trim();

  if (!name || !phone) {
    return res.render('leads/capture', { item, error: 'נא למלא שם וטלפון.', sent: false });
  }

  db.prepare(`
    INSERT INTO leads (item_id, name, phone, note)
    VALUES (?, ?, ?, ?)
  `).run(item.id, name, phone, note);

  // הודעה למפרסם (from_email על המודעה עצמה) - רק אם המנהל הפעיל את זה
  // בהגדרות הרשימה (lead_notify_advertiser). לא חוסמים את שמירת הליד אם
  // שליחת המייל נכשלת - הליד כבר נשמר ורשום בפאנל הניהול בכל מקרה.
  if (item.lead_notify_advertiser && item.from_email) {
    try {
      const { sendViaSendGrid } = require('../compiler');
      await sendViaSendGrid(
        item.from_email,
        `ליד חדש מהמודעה שלך ב"${item.list_name}"`,
        `<div dir="rtl" style="font-family:Arial,sans-serif;">
          <p>התקבל ליד חדש מהמודעה שלך:</p>
          <p><b>שם:</b> ${escapeForEmail(name)}<br>
             <b>טלפון:</b> ${escapeForEmail(phone)}
             ${note ? `<br><b>הערה:</b> ${escapeForEmail(note)}` : ''}</p>
        </div>`
      );
    } catch (err) {
      console.error('שגיאה בשליחת הודעת ליד למפרסם:', err);
    }
  }

  res.render('leads/capture', { item, error: null, sent: true });
});

// escaping פשוט לשדות חופשיים שמוזרקים לגוף מייל HTML - מונע הזרקת
// תגיות מהערה/שם שהמשתמש הקליד (אין כאן תלות ב-templates.js כי זה
// מודול נפרד וקטן, לא רוצים תלות מעגלית מיותרת).
function escapeForEmail(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

module.exports = router;
