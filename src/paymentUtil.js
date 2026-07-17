const { v4: uuidv4 } = require('uuid');
const { getSetting } = require('./appSettings');
const nedarim = require('./nedarim');

// שני מקורות אפשריים להפעלה: משתנה הסביבה (ברירת מחדל/גיבוי, כמו שהיה עד
// עכשיו) או המתג בהגדרות התשלום בפאנל הניהול (ניתן לשינוי בלי redeploy).
// כל אחד מהם לבד מספיק כדי להפעיל.
function paidFeaturesEnabled() {
  return process.env.PAID_FEATURES_ENABLED === 'true' || getSetting('paid_features_enabled', '') === '1';
}

// מחזירה את המחיר (בשקלים) שהוגדר לרשימה הזו לרמה הנתונה, או 0 אם אין
// מחיר/הרמה חינמית. משמש גם את טופס האתר וגם את הקליטה במייל, כדי
// ששתי הדרכים יתנהגו זהה. hasLink=true מוסיף את תוספת המחיר של קישור
// (link_price_plus/link_price_premium) לרשימה הזו, מעל מחיר הבסיס.
function priceFor(list, tier, hasLink = false) {
  let price = 0;
  if (tier === 'plus') price = Number(list.plus_price) || 0;
  else if (tier === 'premium') price = Number(list.premium_price) || 0;
  else return 0;

  if (hasLink) {
    const linkPrice = tier === 'plus' ? Number(list.link_price_plus) || 0 : Number(list.link_price_premium) || 0;
    price += linkPrice;
  }
  return price;
}

// האם מודעה ברמה הזו ברשימה הזו צריכה לעבור דרך תשלום לפני שהיא נכנסת
// לתור האישור הרגיל - רק אם התכונה המשולמת פעילה בכלל (.env), נדרים פלוס
// מוגדר, והמחיר לרמה הזו (כולל תוספת קישור, אם יש) גדול מ-0.
function requiresPayment(list, tier, hasLink = false) {
  return paidFeaturesEnabled() && nedarim.isConfigured() && priceFor(list, tier, hasLink) > 0;
}

function generatePaymentToken() {
  return uuidv4();
}

// מזהה ציבורי לדף מילוי הפרטים של ליד (ראה routes/leads.js) - נפרד
// מ-payment_token בכוונה, כדי ששני הדפים (תשלום/ליד) לא יחלקו מרחב
// טוקנים משותף.
function generateLeadToken() {
  return uuidv4();
}

module.exports = { paidFeaturesEnabled, priceFor, requiresPayment, generatePaymentToken, generateLeadToken };
