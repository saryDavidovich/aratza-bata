const crypto = require('crypto');

// הגנת CSRF פשוטה מבוססת session, בלי תלות בחבילה חיצונית (csurf הוצא
// משימוש ולא מתוחזק יותר): טוקן אקראי נשמר ב-session, מוזרק אוטומטית
// לכל טופס POST בדפי הניהול (ראה admin/partials/header.ejs - סקריפט
// שמוסיף שדה חבוי לכל <form> בעמוד), ומאומת מול אותו טוקן ששמור ב-session
// בכל בקשת POST. תוקף רק כשיש session פעיל - לכן חל על פאנל הניהול בלבד,
// לא על טפסים ציבוריים אנונימיים (הגשת מודעה, תשלום וכו') שאין להם session
// ייחודי ממילא.

function attachCsrfToken(req, res, next) {
  if (!req.session.csrfToken) {
    req.session.csrfToken = crypto.randomBytes(32).toString('hex');
  }
  res.locals.csrfToken = req.session.csrfToken;
  next();
}

function verifyCsrfToken(req, res, next) {
  if (req.method !== 'POST') return next();
  const submitted = req.body && req.body._csrf;
  if (!submitted || submitted !== req.session.csrfToken) {
    console.warn(`בקשת POST נדחתה - טוקן CSRF חסר או לא תואם (${req.method} ${req.originalUrl})`);
    return res.status(403).send('הבקשה נדחתה מטעמי אבטחה (טוקן CSRF לא תקין) - חזור לעמוד הקודם ונסה שוב.');
  }
  next();
}

module.exports = { attachCsrfToken, verifyCsrfToken };
