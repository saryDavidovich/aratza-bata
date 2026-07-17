const express = require('express');
const router = express.Router();
const path = require('path');
const fs = require('fs');
const multer = require('multer');
const db = require('../db');

// דגל תכונה: ניתן להפעיל דרך משתנה סביבה (PAID_FEATURES_ENABLED=true)
// או, בלי redeploy, דרך המתג בהגדרות התשלום בפאנל הניהול (ראה
// admin.js /payment-settings + paymentUtil.js).
const { requiresPayment, priceFor, generatePaymentToken, generateLeadToken, paidFeaturesEnabled } = require('../paymentUtil');
const FREE_WORD_LIMIT = parseInt(process.env.FREE_WORD_LIMIT || '40', 10);

const UPLOAD_DIR = path.join(__dirname, '..', '..', 'data', 'uploads');
if (!fs.existsSync(UPLOAD_DIR)) fs.mkdirSync(UPLOAD_DIR, { recursive: true });
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => {
    const safeExt = (path.extname(file.originalname) || '').slice(0, 10);
    cb(null, `${Date.now()}-${Math.round(Math.random() * 1e9)}${safeExt}`);
  }
});
const upload = multer({ storage });

function countWords(str = '') {
  return str.trim().split(/\s+/).filter(Boolean).length;
}

function validTier(t) {
  return ['free', 'plus', 'premium'].includes(t) ? t : 'free';
}

// -------- פרסום מודעה --------
router.get('/ads/:slug', (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  const PAID_FEATURES_ENABLED = paidFeaturesEnabled();
  const requestedTier = validTier(req.query.tier);
  res.render('ads/submit', {
    list, paidEnabled: PAID_FEATURES_ENABLED, wordLimit: FREE_WORD_LIMIT,
    requestedTier, error: null, sent: false,
    plusPrice: priceFor(list, 'plus'), premiumPrice: priceFor(list, 'premium'),
    linkPricePlus: Number(list.link_price_plus) || 0, linkPricePremium: Number(list.link_price_premium) || 0
  });
});

router.post('/ads/:slug', upload.single('image'), async (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  const PAID_FEATURES_ENABLED = paidFeaturesEnabled();

  const { email, subject, body, bg_color, text_color, client_name, phone, image_link, link_url } = req.body;
  const tier = validTier(req.body.paid_tier);
  const wc = countWords(body || '');

  // מגבלת המילים חלה רק על המודעה החינמית - מודעות מודגשות/פרימיום
  // (כשיופעלו בתשלום) לא כפופות למגבלה הזו.
  if (tier === 'free' && wc > FREE_WORD_LIMIT) {
    return res.render('ads/submit', {
      list, paidEnabled: PAID_FEATURES_ENABLED, wordLimit: FREE_WORD_LIMIT, requestedTier: tier,
      error: `המודעה החינמית מוגבלת ל-${FREE_WORD_LIMIT} מילים (כרגע: ${wc}).`,
      sent: false, plusPrice: priceFor(list, 'plus'), premiumPrice: priceFor(list, 'premium'),
      linkPricePlus: Number(list.link_price_plus) || 0, linkPricePremium: Number(list.link_price_premium) || 0
    });
  }

  try {
    let images = [];
    let imageLink = null;
    if (PAID_FEATURES_ENABLED && tier === 'premium' && req.file) {
      const { compressUploadedImage } = require('../imageProcessing');
      const finalPath = await compressUploadedImage(req.file.path);
      images = [`/uploads/${path.basename(finalPath)}`];
      // רק http/https - מונע הזרקת javascript: או כתובות אחרות שיכולות
      // להיות מסוכנות כשהן מוצגות כ-href בגיליון בפועל.
      const trimmedLink = (image_link || '').trim();
      if (/^https?:\/\/\S+$/i.test(trimmedLink)) imageLink = trimmedLink;
    }
    const useStyle = PAID_FEATURES_ENABLED && (tier === 'plus' || tier === 'premium');

    // קישור כללי למודעה - זמין רק במודגשת/פרימיום (במודעת שורה חינמית
    // האפשרות לא קיימת בכלל). שני סוגים: 'website' (קישור חיצוני, רק
    // http/https, אותה בדיקה כמו image_link) או 'lead' (דף מילוי פרטים
    // גנרי שהמערכת מארחת - לא צריך URL מהמפרסם, רק מייצרים lead_token
    // חדש). כל בחירה אחרת/לא תקינה נופלת חזרה ל-'website'.
    let linkType = 'website';
    let linkUrl = null;
    let leadToken = null;
    if (tier === 'plus' || tier === 'premium') {
      if (req.body.link_type === 'lead') {
        linkType = 'lead';
        leadToken = generateLeadToken();
      } else {
        const trimmedAdLink = (link_url || '').trim();
        if (/^https?:\/\/\S+$/i.test(trimmedAdLink)) linkUrl = trimmedAdLink;
      }
    }
    const hasLink = linkType === 'lead' || !!linkUrl;

    // מודעה בתשלום (מודגשת/פרימיום עם מחיר > 0 לרשימה זו, כולל תוספת
    // מחיר אם צורף קישור/ליד) לא נכנסת ישר ל"ממתין לאישור" - היא נשמרת
    // כ"ממתינה לתשלום" ומועברת לדף הסליקה. רק אחרי שנדרים פלוס מאשרים
    // בפועל (webhook, ראה routes/payment.js) היא הופכת ל-pending הרגיל
    // ונכנסת לתור.
    const needsPayment = requiresPayment(list, tier, hasLink);
    const status = needsPayment ? 'pending_payment' : 'pending';
    const paymentToken = needsPayment ? generatePaymentToken() : null;
    const paymentAmount = needsPayment ? priceFor(list, tier, hasLink) : null;
    const paymentStatus = needsPayment ? 'pending' : 'not_required';

    db.prepare(`
      INSERT INTO items (list_id, type, status, from_email, subject, body_raw, word_count, paid_tier, images_json, bg_color, text_color, link_url, link_type, lead_token, payment_token, payment_amount, payment_status, client_name, phone, image_link)
      VALUES (?, 'ad', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      list.id, status, email, subject || '', body, wc, tier, JSON.stringify(images),
      useStyle ? (bg_color || null) : null, useStyle ? (text_color || null) : null, linkUrl, linkType, leadToken,
      paymentToken, paymentAmount, paymentStatus,
      (client_name || '').trim() || null, (phone || '').trim() || null, imageLink
    );

    if (needsPayment) {
      try {
        const { sendViaSendGrid } = require('../compiler');
        const paymentUrl = `${require('../appSettings').getBaseUrl()}/payment/${paymentToken}`;
        const tierName = tier === 'premium' ? 'פרימיום' : 'מודגשת';
        await sendViaSendGrid(
          email,
          `נותר שלב אחד - תשלום עבור המודעה ב"${list.name}"`,
          `<div dir="rtl" style="font-family:Arial,sans-serif;">
            <p>המודעה שלך (${tierName}, ${paymentAmount} ש"ח) התקבלה וממתינה לתשלום.</p>
            <p>לחץ כאן כדי להשלים את התשלום ולשלוח את המודעה לתור האישור:</p>
            <p><a href="${paymentUrl}">${paymentUrl}</a></p>
          </div>`
        );
      } catch (mailErr) {
        // לא עוצרים את התהליך אם שליחת המייל נכשלה - הלקוח כבר עומד לעבור
        // לדף התשלום בעצמו, המייל הוא רק גיבוי/תזכורת.
        console.error('שגיאה בשליחת מייל קישור תשלום:', mailErr);
      }
      return res.redirect(`/payment/${paymentToken}`);
    }

    res.render('ads/submit', {
      list, paidEnabled: PAID_FEATURES_ENABLED, wordLimit: FREE_WORD_LIMIT, requestedTier: tier, error: null, sent: true,
      plusPrice: priceFor(list, 'plus'), premiumPrice: priceFor(list, 'premium'),
      linkPricePlus: Number(list.link_price_plus) || 0, linkPricePremium: Number(list.link_price_premium) || 0
    });
  } catch (err) {
    console.error('שגיאה בפרסום מודעה מהלקוח:', err);
    res.render('ads/submit', {
      list, paidEnabled: PAID_FEATURES_ENABLED, wordLimit: FREE_WORD_LIMIT, requestedTier: tier,
      error: 'אירעה שגיאה בשליחת המודעה. נסה שוב, אולי בלי תמונה.',
      sent: false, plusPrice: priceFor(list, 'plus'), premiumPrice: priceFor(list, 'premium'),
      linkPricePlus: Number(list.link_price_plus) || 0, linkPricePremium: Number(list.link_price_premium) || 0
    });
  }
});

// -------- פרסום נושא/מאמר (מהלקוח, בלי מייל) --------
router.get('/topics/:slug', (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  res.render('topics/submit', { list, error: null, sent: false });
});

router.post('/topics/:slug', express.urlencoded({ extended: true }), (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');

  const { email, subject, body } = req.body;
  const wc = countWords(body || '');

  db.prepare(`
    INSERT INTO items (list_id, type, status, from_email, subject, body_raw, word_count)
    VALUES (?, 'article', 'pending', ?, ?, ?, ?)
  `).run(list.id, email, subject || '', body, wc);

  res.render('topics/submit', { list, error: null, sent: true });
});

// -------- הרשמה להצטרפות לרשימה --------
router.get('/subscribe/:slug', (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  res.render('subscribe', { list, subscribed: false });
});

router.post('/subscribe/:slug', express.urlencoded({ extended: true }), (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  const email = (req.body.email || '').toLowerCase().trim();
  if (!email) return res.status(400).send('נא להזין מייל');

  try {
    const { subscribeEmail } = require('../subscriberUtil');
    const result = subscribeEmail(list.id, email);
    console.log(result.reactivated
      ? `${email} הצטרף מחדש לרשימה "${list.name}" (הוסר בעבר).`
      : `מנוי נרשם: ${email} לרשימה "${list.name}".`);
  } catch (e) {
    console.error(`שגיאה אמיתית בהרשמת מנוי ${email} לרשימה "${list.name}":`, e);
    return res.status(500).send('אירעה שגיאה בהרשמה. נסה שוב מאוחר יותר.');
  }

  res.render('subscribe', { list, subscribed: true });
});

// -------- הסרה מרשימה --------
router.get('/unsubscribe/:token', (req, res) => {
  const sub = db.prepare('SELECT * FROM subscribers WHERE token = ?').get(req.params.token);
  if (!sub) return res.status(404).send('קישור לא תקין');
  db.prepare('UPDATE subscribers SET unsubscribed = 1 WHERE id = ?').run(sub.id);
  res.send('הוסרת בהצלחה מרשימת התפוצה.');
});

// -------- ארכיון ציבורי - הלקוחות יכולים לראות גיליונות עבר --------
router.get('/archive/:slug', (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');

  const { formatIsraelDateTime } = require('../timeUtil');
  const issues = db.prepare(`
    SELECT id, sent_at FROM issues WHERE list_id = ? AND status = 'sent' ORDER BY sent_at DESC
  `).all(list.id).map(issue => ({ ...issue, sent_at_display: formatIsraelDateTime(issue.sent_at) }));

  res.render('archive_list', { list, issues });
});

router.get('/archive/:slug/:issueId', (req, res) => {
  const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(req.params.slug);
  if (!list) return res.status(404).send('רשימה לא נמצאה');
  const issue = db.prepare('SELECT * FROM issues WHERE id = ? AND list_id = ? AND status = ?').get(req.params.issueId, list.id, 'sent');
  if (!issue) return res.status(404).send('גיליון לא נמצא');
  res.send(issue.html);
});

module.exports = router;
