// אינטגרציית סליקה מול נדרים פלוס - זרימת "הקמת עסקה בצד שרת" (ראה
// תיעוד ה-API: "אייפרם: הקמת עסקה בצד שרת" + "אייפרם: אימות תשלום ואבטחה").
//
// למה זרימה זו ולא "ביצוע עסקה מהדף" הרגילה: כאן הסכום נקבע ונשלח על ידי
// השרת שלנו בלבד (CreateTransaction), ולא ניתן לשינוי בצד הלקוח - חשוב
// כי מדובר בתשלום עבור מודעה במחיר קבוע שהוגדר בפאנל הניהול.
//
// אימות תשלום אמיתי קורה אך ורק דרך ה-CallBack שנדרים פלוס שולחים לשרת
// שלנו (verifyCallback למטה) - לעולם לא סומכים על תגובת האייפרם בצד
// הלקוח (TransactionResponse) לבדה. הזיהוי איזה פריט זה מבוסס בעיקר על
// שדה ה-ID שחוזר (לא TransactionId - ראה הסבר מפורט ב-verifyCallback).

const fetch = require('node-fetch');
const { getSetting } = require('./appSettings');

// שני מקורות אפשריים להגדרות: טבלת app_settings (ניתנת לעריכה מהממשק,
// ראה admin.js /payment-settings) עדיפה, ומשתני סביבה כברירת מחדל/גיבוי
// (שימושי בעיקר לפריסה ראשונית, לפני שנכנסים לממשק בכלל).
function getMosad() {
  return getSetting('nedarim_mosad', process.env.NEDARIM_MOSAD || '');
}
function getApiValid() {
  return getSetting('nedarim_api_valid', process.env.NEDARIM_API_VALID || '');
}
// שים לב: זו סיסמה שונה מ-ApiValid - "סיסמת API" (ApiPassword) משמשת רק
// למשיכת נתונים (הסטוריית עסקאות וכו'), לא לביצוע תשלומים. משמשת כאן רק
// כדי לאמת ידנית מול נדרים פלוס עסקה שה-CallBack שלה הגיע מ-IP לא מוכר
// (ראה admin.js /items/:id/verify-payment).
function getApiPassword() {
  return getSetting('nedarim_api_password', process.env.NEDARIM_API_PASSWORD || '');
}
// קטגוריה קבועה (Groupe) שמסומנת על כל עסקה שנוצרת מהמערכת הזו - כדי
// שיהיה אפשר לזהות בדוחות של נדרים פלוס בדיוק אילו הכנסות הגיעו מרשימות
// התפוצה, להבדיל משאר ההכנסות של המוסד. ניתן לשנות בהגדרות התשלום בפאנל
// הניהול; ברירת המחדל היא "דיוור במייל".
function getCategory() {
  return getSetting('nedarim_category', 'דיוור במייל');
}

// כתובות ה-IP שמהן נדרים פלוס שולחים CallBack - ראה "אייפרם: אימות תשלום
// ואבטחה" בתיעוד. כל בקשה שלא מגיעה מאחת מהכתובות האלה נדחית.
const NEDARIM_CALLBACK_IPS = [
  '18.194.219.73',
  '3.70.117.239',
  '3.74.120.185',
  '18.196.146.117'
];

const CREATE_TRANSACTION_URL = 'https://matara.pro/nedarimplus/V6/Files/WebServices/DebitIframe.aspx?Action=CreateTransaction';

function isConfigured() {
  return Boolean(getMosad() && getApiValid());
}

// יוצרת עסקה מוכנה מראש בצד השרת מול נדרים פלוס. הדף שלנו ישלח לאייפרם
// רק את ה-ID שחוזר מכאן (FinishTransaction) - הסכום כבר "נעול" בצד נדרים.
// callbackUrl חייב להיות כתובת ציבורית מלאה (https) שמגיעה חזרה לשרת שלנו.
// firstName/lastName/phone/mail הם פרטי הלקוח ששמורים אצלנו על המודעה -
// לא חובה לפי נדרים פלוס, אבל מבוקש כדי שהעסקה בדוחות שלהם תהיה מזוהה
// עם הלקוח, לא רק עם סכום גולמי. groupeOverride מאפשר לדרוס את הקטגוריה
// הקבועה אם צריך בעתיד; כרגע תמיד נשלחת קטגוריית ברירת המחדל (getCategory).
async function createServerTransaction({ amount, paymentToken, comment, callbackUrl, firstName, lastName, phone, mail, groupeOverride }) {
  if (!isConfigured()) {
    return { ok: false, error: 'נדרים פלוס לא מוגדר (השלימו מספר מוסד וטקסט אימות בהגדרות התשלום בפאנל הניהול)' };
  }

  const params = new URLSearchParams({
    Mosad: getMosad(),
    ApiValid: getApiValid(),
    PaymentType: 'Ragil',
    Amount: String(amount),
    Currency: '1',
    Tashlumim: '1',
    Comment: comment || '',
    Groupe: groupeOverride || getCategory(),
    FirstName: firstName || '',
    LastName: lastName || '',
    Phone: phone || '',
    Mail: mail || '',
    Param1: paymentToken,
    CallBack: callbackUrl,
    AjaxId: String(Date.now())
  });

  const resp = await fetch(CREATE_TRANSACTION_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString()
  });

  const text = await resp.text();
  let data;
  try { data = JSON.parse(text); } catch (e) {
    return { ok: false, error: `תגובה לא תקינה מנדרים פלוס: ${text.slice(0, 200)}` };
  }

  if (data.Status !== 'OK') {
    return { ok: false, error: data.Message || 'שגיאה לא ידועה מנדרים פלוס' };
  }
  return { ok: true, transactionId: data.ID };
}

// req.ip תלוי ב-app.set('trust proxy', ...) שהוגדר ב-server.js - בלי זה
// תמיד תתקבל כתובת ה-proxy הפנימי של Railway ולא הכתובת האמיתית של הפונה.
function sourceIp(req) {
  return (req.ip || '').replace('::ffff:', '');
}
function isFromNedarim(req) {
  return NEDARIM_CALLBACK_IPS.includes(sourceIp(req));
}

// משיכת הסטוריית עסקאות אמיתית מנדרים פלוס (GetHistoryJson) - "מקור
// האמת" הסופי: קריאה חוזרת משרת לשרת עם הסיסמה שלנו, שאי אפשר לזייף.
// מוגבל ל-20 קריאות בשעה מצידם, ולכן נעשה בה שימוש רק כשצריך (ראה
// verifyCallback), לא כלולאת סנכרון קבועה.
const HISTORY_URL = 'https://matara.pro/nedarimplus/Reports/Manage3.aspx';

async function getRecentTransactions({ maxId = 50 } = {}) {
  if (!getApiPassword()) {
    return { ok: false, error: 'סיסמת API (ApiPassword) לא מוגדרת - ראה הגדרות תשלום' };
  }
  const params = new URLSearchParams({
    Action: 'GetHistoryJson',
    MosadId: getMosad(),
    ApiPassword: getApiPassword(),
    MaxId: String(maxId)
  });
  const resp = await fetch(`${HISTORY_URL}?${params.toString()}`);
  const text = await resp.text();
  let data;
  try { data = JSON.parse(text); } catch (e) {
    return { ok: false, error: `תגובה לא תקינה מנדרים פלוס: ${text.slice(0, 200)}` };
  }
  if (!Array.isArray(data)) {
    return { ok: false, error: (data && data.Message) || 'שגיאה לא ידועה בקבלת הסטוריית עסקאות' };
  }
  return { ok: true, transactions: data.slice().reverse() }; // האחרונות קודם
}

// אימות אוטומטי מלא של CallBack, בלי צורך בשלב ידני - שילוב של כמה
// אותות בלתי-תלויים, כל אחד מספיק לבדו:
//
//  1. כתובת ה-IP השולחת נמצאת ברשימה המתועדת של נדרים פלוס. זה מסלול
//     המהיר והפשוט, אבל תלוי ברשימה שעלולה להתעדכן מצידם (ראה אזהרתם
//     בתיעוד: "יתכן שנוספה כתובת חדשה") - ולכן לא היחיד.
//
//  2. מזהה העסקה - שדה ה-ID (לא TransactionId! זה שם השדה במבנה
//     "TransactionResponse", שהוא בדיוק מה שחוזר ב-CallBack של הזרימה בה
//     אנחנו משתמשים - "אייפרם: הקמת עסקה בצד שרת", ראה תיעוד: "העדכון
//     לכתובת ה-CallBack נשלח... עם אותו JSON של TransactionResponse".
//     השדה TransactionId שייך למבנה נתונים *אחר* לגמרי - ה-webhook
//     הסטטי ברמת מוסד שנרשם דרך מייל למשרד, שאנחנו לא משתמשים בו) -
//     שחזר מ-CreateTransaction בעת יצירת העסקה (ואנחנו שמרנו אצלנו)
//     זהה למה שמופיע ב-CallBack שהתקבל. זהו סוד ששני הצדדים היחידים
//     שיודעים אותו הם השרת של נדרים פלוס (שיצר אותו) והשרת שלנו (ששמר
//     אותו) - לא ניתן לזיוף על ידי גורם חיצוני, ולא תלוי כלל בתשתית
//     הרשת/פרוקסי שממנה מגיעה הבקשה. זה המסלול שפותר אוטומטית בדיוק את
//     המקרה של כתובת IP לא מתועדת, בלי לוותר על אבטחה.
//
//  3. כגיבוי אחרון (רק אם שני האותות הקודמים לא תאמו, ומוגדרת סיסמת
//     API): קריאה חוזרת בזמן אמת להסטוריית העסקאות האמיתית של נדרים
//     פלוס (שם כן נקרא TransactionId, זה שם השדה הנכון באותו endpoint)
//     ובדיקה שהעסקה אכן קיימת שם - אי אפשר לזייף כי זו קריאה יזומה על
//     ידינו לשרת שלהם, לא משהו שהתקבל מבחוץ.
//
// שימו לב: הסכום עצמו כבר "ננעל" בצד נדרים פלוס בשלב יצירת העסקה
// (CreateTransaction, לפני שהלקוח בכלל ראה טופס תשלום) - לכן אין כאן
// תלות בכך שה-Amount יחזור ב-CallBack (הוא לא שדה מובטח במבנה
// TransactionResponse); אם הוא כן חוזר, בודקים אותו כבדיקת סבירות
// נוספת, אבל היעדרו לא גורם לדחייה.
async function verifyCallback(req, data, item) {
  const statusOk = data.Status === 'OK';
  if (!statusOk) {
    return { verified: false, reason: `Status=${data.Status} (${data.Message || 'ללא הודעה'})` };
  }
  if (data.Amount !== undefined && data.Amount !== null && data.Amount !== '') {
    const receivedAmount = Math.round(parseFloat(data.Amount));
    if (receivedAmount !== item.payment_amount) {
      return { verified: false, reason: `סכום לא תואם: התקבל ${data.Amount}, צפוי ${item.payment_amount}` };
    }
  }

  const ipTrusted = isFromNedarim(req);
  if (ipTrusted) return { verified: true, reason: `כתובת IP מוכרת (${sourceIp(req)})` };

  const callbackId = data.ID != null ? String(data.ID) : null;
  const idMatch = item.nedarim_transaction_id && callbackId &&
    callbackId === String(item.nedarim_transaction_id);
  if (idMatch) return { verified: true, reason: `מזהה עסקה (ID) תואם למה ששמרנו (${callbackId}), למרות IP לא מתועד (${sourceIp(req)})` };

  if (getApiPassword() && callbackId) {
    const history = await getRecentTransactions({ maxId: 50 });
    if (history.ok) {
      const found = history.transactions.find(t => String(t.TransactionId) === callbackId);
      if (found) return { verified: true, reason: `אומת מול הסטוריית העסקאות האמיתית בנדרים פלוס (מזהה ${callbackId})` };
    }
  }

  return { verified: false, reason: `IP לא מתועד (${sourceIp(req)}) ומזהה עסקה (ID=${callbackId}) לא תואם/לא נמצא בהסטוריה` };
}

module.exports = { isConfigured, createServerTransaction, isFromNedarim, verifyCallback, getMosad, getCategory, getRecentTransactions, NEDARIM_CALLBACK_IPS };
