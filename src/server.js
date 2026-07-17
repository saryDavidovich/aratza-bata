require('dotenv').config();
const express = require('express');
const session = require('express-session');
const path = require('path');
const cron = require('node-cron');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');

// רשת ביטחון: בלי זה, שגיאה לא-מטופלת בכל בקשה בודדת (כמו העלאת קובץ
// פגום) מפילה את כל השרת עבור כולם עד שRailway מפעיל אותו מחדש. עדיף
// לתעד את השגיאה ולהמשיך לרוץ, מאשר שכל המערכת תיפול בגלל בקשה אחת.
process.on('uncaughtException', (err) => {
  console.error('=== שגיאה לא מטופלת (uncaughtException) - השרת ממשיך לרוץ ===', err);
});
process.on('unhandledRejection', (err) => {
  console.error('=== Promise נדחה בלי טיפול (unhandledRejection) - השרת ממשיך לרוץ ===', err);
});

const adminRoutes = require('./routes/admin');
const inboundRoutes = require('./routes/inbound');
const publicRoutes = require('./routes/public');
const paymentRoutes = require('./routes/payment');
const leadsRoutes = require('./routes/leads');
const { checkAndRunDueSends } = require('./compiler');

const app = express();

// חובה כדי ש-req.ip ישקף את הכתובת האמיתית של הפונה ולא את כתובת ה-proxy
// הפנימי של Railway - בלי זה, אימות ה-IP של ה-CallBack מנדרים פלוס
// (src/nedarim.js) תמיד ייכשל. '1' = לסמוך על ה-hop הראשון בלבד (הפרוקסי
// המיידי של Railway), מספיק כאן כי אין שרשרת פרוקסי נוספת.
app.set('trust proxy', 1);

// כותרות אבטחה סטנדרטיות. ה-CSP מותאם ידנית (לא ברירת המחדל המחמירה של
// helmet) כי בעמודי הניהול ובדף התשלום יש סקריפטים/סגנונות inline רבים -
// CSP מחמיר מדי היה שובר את הכל בלי תועלת ממשית (אין nonce-based setup
// כאן). frame-src חייב לכלול את matara.pro - זה דומיין נדרים פלוס
// שהאייפרם של התשלום נטען ממנו (ראה src/views/payment.ejs).
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: ["'self'", "'unsafe-inline'"],
      // script-src-attr הוא דירקטיבה נפרדת מ-script-src בCSP3, ומיועדת
      // ספציפית לתכונות כמו onchange="..."/onclick="..." (לא לתגית
      // <script>). ברירת המחדל של helmet לדירקטיבה הזו היא 'none' - בלי
      // השורה הבאה, כל תכונת onXXX inline באתר (יש עשרות כאלה בקוד -
      // בורר רמת מודעה, כפתורי עיצוב טקסט, טפסי ניהול ועוד) הייתה נחסמת
      // בדפדפן אמיתי, גם אם script-src עצמו מתיר unsafe-inline.
      scriptSrcAttr: ["'unsafe-inline'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      imgSrc: ["'self'", "data:", "https:"],
      connectSrc: ["'self'"],
      frameSrc: ["'self'", "https://www.matara.pro"]
    }
  },
  crossOriginEmbedderPolicy: false
}));

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(express.static(path.join(__dirname, 'public')));

// תמונות/גיפים שהועלו (מודעות פרימיום/מודגשות) - מוגשות מהתיקייה המתמשכת
// data/uploads, כדי שלא יאבדו בדיפלוי חדש (בהנחה שיש Volume מחובר ב-Railway).
const fs = require('fs');
const UPLOAD_DIR = path.join(__dirname, '..', 'data', 'uploads');
if (!fs.existsSync(UPLOAD_DIR)) fs.mkdirSync(UPLOAD_DIR, { recursive: true });
app.use('/uploads', express.static(UPLOAD_DIR));

const isProduction = (process.env.BASE_URL || '').startsWith('https://');
app.use(session({
  secret: process.env.SESSION_SECRET || 'change-me-in-env',
  resave: false,
  saveUninitialized: false,
  cookie: {
    httpOnly: true,
    // secure=true דורש HTTPS בפועל - מוסק אוטומטית מ-BASE_URL, כדי לא
    // לנעול פיתוח מקומי (http://localhost) בטעות.
    secure: isProduction,
    sameSite: 'lax',
    maxAge: 1000 * 60 * 60 * 24 * 7 // שבוע
  }
}));

// הגבלת קצב - שכבת הגנה בסיסית נגד ניסיונות brute-force על הסיסמה
// וספאם על טפסים ציבוריים (שליחת מודעות/הרשמה). לא חל על ה-webhook
// מנדרים פלוס (אין לו קצב חריג צפוי, ואסור לחסום קריאות אמיתיות מהם).
const loginLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: 'יותר מדי ניסיונות התחברות. נסה שוב בעוד כמה דקות.'
});
app.use('/admin/login', loginLimiter);

const publicFormLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 30,
  standardHeaders: true,
  legacyHeaders: false,
  message: 'יותר מדי בקשות. נסה שוב בעוד כמה דקות.'
});
app.use(['/ads', '/ask', '/subscribe', '/payment', '/leads'], (req, res, next) => {
  if (req.path === '/webhook') return next(); // ה-webhook מנדרים פלוס לא מוגבל
  return publicFormLimiter(req, res, next);
});

app.get('/', (req, res) => res.redirect('/admin'));
app.use('/admin', adminRoutes);
app.use('/webhooks', inboundRoutes);
app.use('/leads', leadsRoutes);
app.use('/', paymentRoutes);
app.use('/', publicRoutes);

// שליחה אוטומטית - כל רשימה קובעת לעצמה יום+שעה משלה (בהגדרות הרשימה),
// לפי שעון ישראל (כולל קיץ/חורף אוטומטית, ראה timeUtil.js). בודקים כל
// דקה איזו רשימה "הגיע התור שלה" ברגע הזה - במקום cron קבוע אחד לכולם.
cron.schedule('* * * * *', () => {
  checkAndRunDueSends().catch(err => console.error('שגיאה בבדיקת שליחות אוטומטיות:', err));
}, { timezone: 'Asia/Jerusalem' });

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`המערכת רצה על http://localhost:${PORT}`);
  console.log(`פאנל ניהול: http://localhost:${PORT}/admin`);
});

module.exports = app;
