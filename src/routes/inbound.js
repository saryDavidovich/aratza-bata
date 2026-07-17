const express = require('express');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const router = express.Router();
const db = require('../db');

// -----------------------------------------------------------------------
// זהו הלב האוטומטי של המערכת. כתובת ה-webhook הזו מחוברת ל-SendGrid Inbound
// Parse. כל מייל שמגיע ל-domain שלך מנותב הנה, וה"פעולה" מזוהה לפי הכתובת
// אליה המייל נשלח (plus-addressing), למשל:
//
//   ask+parenting@mail.yourdomain.com          -> שאלה חדשה לרשימת "הורות"
//   ads+parenting@mail.yourdomain.com          -> מודעת שורה חינם
//   adsplus+parenting@mail.yourdomain.com      -> מודעה מודגשת
//   adspremium+parenting@mail.yourdomain.com   -> מודעה פרימיום
//   topic+parenting@mail.yourdomain.com        -> נושא/מאמר חדש
//   reply+482@mail.yourdomain.com              -> תשובה לשאלה מספר 482
//   join+parenting@mail.yourdomain.com         -> הצטרפות לרשימה (לפי כתובת השולח)
//   leave+parenting@mail.yourdomain.com        -> הסרה מרשימה (לפי כתובת השולח)
//
// כל זה קורה בלי שום מגע ידני - הפריט פשוט נוחת בתור ההמתנה שלך לאישור.
// כל התהליך מתבסס על תגובות/שליחות מייל בלבד, כדי שגם לקוחות עם גישה
// מוגבלת לדפדפן (למשל "נטו מייל") יוכלו להשתמש בכל התכונות.
// -----------------------------------------------------------------------

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

const { requiresPayment, priceFor, generatePaymentToken } = require('../paymentUtil');
const { getBaseUrl } = require('../appSettings');

// שדה ה-"to" של SendGrid מגיע בפורמט כמו: "Name <ask+parenting@yourdomain.com>"
// או פשוט "ask+parenting@yourdomain.com", ולפעמים כמה כתובות מופרדות בפסיק.
function extractEmailAddresses(raw) {
  return String(raw || '')
    .split(',')
    .map(part => {
      const match = part.match(/<([^>]+)>/);
      return (match ? match[1] : part).trim();
    })
    .filter(Boolean);
}

function parseRecipient(address) {
  const match = address.match(/^([^+@]+)\+([^@]+)@/);
  if (!match) return null;
  return { action: match[1].toLowerCase(), extra: match[2].toLowerCase() };
}

// עוברים על כל הכתובות שהופיעו ב-to (יכול להיות יותר מאחת אם הלקוח הוסיף
// עותק/CC), ומחזירים את הראשונה שבאמת תואמת את הפורמט שלנו (action+extra@).
function findMatchingRecipient(toRaw) {
  const addresses = extractEmailAddresses(toRaw);
  for (const addr of addresses) {
    const parsed = parseRecipient(addr);
    if (parsed) return parsed;
  }
  return null;
}

// מפרסרת את הטיוטה המובנית של מודגשת/פרימיום (ראה adBodyTemplate ב-
// templates.js): שלוש שורות בדיוק - "תוכן המודעה:" / "צבע רקע:" /
// "קישור:". כל שדה נשאב לפי מיקומו (השורה שמתחילה בתווית שלו), לא לפי
// חיפוש חופשי בכל הטקסט - כך שאם הלקוח מזכיר "קישור" או "צבע" בתוך תוכן
// המודעה עצמו זה לא "נתפס" בטעות כשדה. מחזירה null אם לא נמצאה בכלל
// התווית "תוכן המודעה:" (סימן לטיוטה ישנה, מלפני העדכון הזה).
function parseAdEmailTemplate(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const bodyLabelRe = /^[ \t]*תוכן\s*המודעה[ \t]*[:\-][ \t]*(.*)$/;
  const colorLabelRe = /^[ \t]*צבע(?:[ \t]*רקע)?[ \t]*[:\-][ \t]*(.*)$/;
  const linkLabelRe = /^[ \t]*קישור[ \t]*[:\-][ \t]*(.*)$/;

  let bodyStart = -1, bodyFirstLine = '', colorIdx = -1, linkIdx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (bodyStart === -1) {
      const m = lines[i].match(bodyLabelRe);
      if (m) { bodyStart = i; bodyFirstLine = m[1]; continue; }
    }
    if (colorIdx === -1 && colorLabelRe.test(lines[i])) { colorIdx = i; continue; }
    if (linkIdx === -1 && linkLabelRe.test(lines[i])) { linkIdx = i; continue; }
  }
  if (bodyStart === -1) return null;

  const bodyEnd = colorIdx !== -1 ? colorIdx : (linkIdx !== -1 ? linkIdx : lines.length);
  const bodyLines = [];
  if (bodyFirstLine.trim()) bodyLines.push(bodyFirstLine);
  bodyLines.push(...lines.slice(bodyStart + 1, bodyEnd));

  const colorRaw = colorIdx !== -1 ? (lines[colorIdx].match(colorLabelRe)[1] || '').trim() : '';
  const linkRaw = linkIdx !== -1 ? (lines[linkIdx].match(linkLabelRe)[1] || '').trim() : '';

  return { body: bodyLines.join('\n').trim(), colorRaw, linkRaw };
}

// כמו extractRequestedColor למטה, אבל מקבלת ישר את הערך הגולמי (בלי
// התווית "צבע:") - משמש לפענוח הטיוטה המובנית החדשה, אחרי שהשורה כבר
// זוהתה ע"י parseAdEmailTemplate.
function resolveColorValue(raw, palette) {
  if (!raw) return null;
  const hexMatch = raw.match(/^#?([0-9a-fA-F]{6})$/);
  if (hexMatch) return '#' + hexMatch[1].toUpperCase();
  const cleaned = raw.replace(/[^\u05D0-\u05EA]/g, '');
  for (const c of (palette || [])) {
    if (c.name && cleaned.includes(c.name.replace(/[^\u05D0-\u05EA]/g, ''))) return c.hex;
  }
  return null;
}

// רק http/https - כמו image_link בטופס האתר (routes/public.js) - מונע
// הזרקת javascript: או כתובות אחרות מסוכנות שיוצגו כ-href בגיליון. אם
// לא תקין, פשוט מתעלמים (המודעה מתפרסמת בלי קישור, לא נחסמת).
function resolveLinkValue(raw) {
  const trimmed = (raw || '').trim();
  return /^https?:\/\/\S+$/i.test(trimmed) ? trimmed : null;
}

// מסיר כל הופעה מדויקת של טקסטי ההוראה שהמערכת עצמה הזינה מראש לתוך
// המייל (ראה getKnownInstructionStrings ב-templates.js) - כדי שאם לקוח
// שולח בחזרה בלי למחוק את הטיוטה המקורית, ההוראות עצמן לא "יידבקו" בתוך
// המודעה/שאלה/תשובה/נושא שמתפרסמים בפועל.
function stripKnownInstructions(text, list) {
  const { getKnownInstructionStrings } = require('../templates');
  let cleaned = String(text || '');
  for (const instr of getKnownInstructionStrings(list)) {
    cleaned = cleaned.split(instr).join('');
  }
  return cleaned.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

// מחפשת שורה בנוסח "צבע: X" או "צבע רקע: X" בגוף המייל - X יכול להיות שם
// צבע מהפלטה שהוגדרה לרשימה הזו בהגדרות (ראה admin.js /settings), או קוד
// hex ישיר (#A7D8F0 / A7D8F0) כגיבוי לא-מפורסם. מחזירה את הצבע שנמצא ואת
// השורה המדויקת, כדי שאפשר יהיה להסיר אותה מהתוכן הגלוי.
function extractRequestedColor(text, palette) {
  const match = String(text || '').match(/^[ \t]*צבע(?:[ \t]*רקע)?[ \t]*[:\-][ \t]*(.+)$/im);
  if (!match) return null;

  const raw = match[1].trim();
  const hexMatch = raw.match(/^#?([0-9a-fA-F]{6})$/);
  if (hexMatch) return { bg: '#' + hexMatch[1].toUpperCase(), matchedLine: match[0] };

  const cleaned = raw.replace(/[^\u05D0-\u05EA]/g, '');
  for (const c of (palette || [])) {
    if (c.name && cleaned.includes(c.name.replace(/[^\u05D0-\u05EA]/g, ''))) {
      return { bg: c.hex, matchedLine: match[0] };
    }
  }
  return null;
}

// בוחרת אוטומטית טקסט לבן או כהה, לפי בהירות צבע הרקע שהתבקש - כדי
// שהטקסט תמיד יהיה קריא, גם אם הלקוח בחר רקע כהה.
function pickReadableTextColor(hexBg) {
  const hex = hexBg.replace('#', '');
  const r = parseInt(hex.slice(0, 2), 16), g = parseInt(hex.slice(2, 4), 16), b = parseInt(hex.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? '#2C2C2A' : '#FFFFFF';
}

router.post('/inbound', upload.any(), async (req, res) => {
  try {
    const body = req.body || {};
    const fromAddresses = extractEmailAddresses(body.from || '');
    const fromEmail = (fromAddresses[0] || '').toLowerCase();
    const toRaw = body.to || '';
    const subject = body.subject || '';
    const text = body.text || '';

    // לוג מלא של כל מייל נכנס - חשוב לאבחון בעיות. תראה את זה ב-Railway logs.
    console.log('=== מייל נכנס ===', {
      to: toRaw, from: fromEmail, subject, textLength: text.length, filesCount: (req.files || []).length
    });

    const parsed = findMatchingRecipient(toRaw);
    if (!parsed) {
      console.warn('לא זוהתה כתובת יעד תואמת בשדה to:', toRaw);
      return res.status(200).send('ignored: unrecognized address');
    }

    const { action, extra } = parsed;

    if (action === 'ask' || action === 'ads' || action === 'adsplus' || action === 'adspremium' || action === 'topic') {
      const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(extra);
      if (!list) {
        console.warn(`לא נמצאה רשימה פעילה עם slug="${extra}" (פעולה: ${action})`);
        return res.status(200).send('ignored: unknown list');
      }

      const typeMap = { ask: 'question', ads: 'ad', adsplus: 'ad', adspremium: 'ad', topic: 'article' };
      const tierMap = { ads: 'free', adsplus: 'plus', adspremium: 'premium' };
      const type = typeMap[action];
      const tier = tierMap[action] || 'free';

      // תמונה/גיף מצורפים למייל - נתמך רק בפרימיום (עקבי עם מה שכתוב
      // בהוראות שהלקוח קיבל בגיליון עצמו וגם בממשק הניהול).
      let attachedImages = [];
      if (type === 'ad' && tier === 'premium') {
        const { compressUploadedImage } = require('../imageProcessing');
        const imageFiles = (req.files || []).filter(f => /^image\//.test(f.mimetype));
        for (const f of imageFiles) {
          const finalPath = await compressUploadedImage(f.path);
          attachedImages.push(`/uploads/${path.basename(finalPath)}`);
        }
      }

      // מודגשת/פרימיום: הטיוטה שנשלחת ללקוח בנויה משלוש שורות ("תוכן
      // המודעה:" / "צבע רקע:" / "קישור:", ראה adBodyTemplate ב-
      // templates.js) - כל שדה נשאב לפי מיקומו. אם הלקוח שלח טיוטה ישנה
      // בלי התווית "תוכן המודעה:" (מלפני העדכון) - נופלים חזרה לפענוח
      // החופשי הישן של צבע (בלי קישור, כי השדה הזה לא היה קיים אז).
      // שימו לב: קישורים שמופיעים בטקסט החופשי של המודעה עצמה כבר לא
      // הופכים ללחיצים אוטומטית - רק מה שהוזן במפורש בשורת "קישור:".
      let bodyText;
      let bgColor = null;
      let textColor = null;
      let linkUrl = null;

      if (type === 'ad' && (tier === 'plus' || tier === 'premium')) {
        let palette = [];
        try { palette = JSON.parse(list.ad_color_palette_json || '[]'); } catch (e) { palette = []; }

        const structured = parseAdEmailTemplate(text);
        if (structured) {
          bodyText = stripKnownInstructions(structured.body, list);
          const resolvedColor = resolveColorValue(structured.colorRaw, palette);
          if (resolvedColor) { bgColor = resolvedColor; textColor = pickReadableTextColor(resolvedColor); }
          linkUrl = resolveLinkValue(structured.linkRaw);
        } else {
          bodyText = stripKnownInstructions(text, list);
          const colorRequest = extractRequestedColor(bodyText, palette);
          if (colorRequest) {
            bgColor = colorRequest.bg;
            textColor = pickReadableTextColor(colorRequest.bg);
            bodyText = bodyText.replace(colorRequest.matchedLine, '').trim();
          }
        }
      } else {
        bodyText = stripKnownInstructions(text, list);
      }

      // מודעה מודגשת/פרימיום עם מחיר מוגדר לרשימה זו (כולל תוספת מחיר של
      // קישור, אם צורף) - לא נכנסת ישר לתור, אלא ל"ממתינה לתשלום" (ראה
      // src/paymentUtil.js + routes/payment.js, אותה לוגיקה בדיוק כמו
      // בטופס האתר). ללקוח שנרשם דרך המייל שולחים בחזרה מייל עם קישור
      // לדף התשלום, כי אין לו דף תגובה מיידי כמו בטופס.
      const hasLink = !!linkUrl;
      const needsPayment = type === 'ad' && requiresPayment(list, tier, hasLink);
      const status = needsPayment ? 'pending_payment' : 'pending';
      const paymentToken = needsPayment ? generatePaymentToken() : null;
      const paymentAmount = needsPayment ? priceFor(list, tier, hasLink) : null;
      const paymentStatus = needsPayment ? 'pending' : 'not_required';

      db.prepare(`
        INSERT INTO items (list_id, type, status, from_email, subject, body_raw, word_count, paid_tier, images_json, bg_color, text_color, link_url, payment_token, payment_amount, payment_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        list.id, type, status, fromEmail, subject, bodyText, countWords(bodyText), tier,
        JSON.stringify(attachedImages), bgColor, textColor, linkUrl,
        paymentToken, paymentAmount, paymentStatus
      );

      if (needsPayment) {
        const { sendViaSendGrid } = require('../compiler');
        const paymentUrl = `${getBaseUrl()}/payment/${paymentToken}`;
        const tierName = tier === 'premium' ? 'פרימיום' : 'מודגשת';
        await sendViaSendGrid(
          fromEmail,
          `נותר שלב אחד - תשלום עבור המודעה ב"${list.name}"`,
          `<div dir="rtl" style="font-family:Arial,sans-serif;">
            <p>המודעה שלך (${tierName}, ${paymentAmount} ש"ח) התקבלה וממתינה לתשלום.</p>
            <p>לחץ כאן כדי להשלים את התשלום ולשלוח את המודעה לתור האישור:</p>
            <p><a href="${paymentUrl}">${paymentUrl}</a></p>
          </div>`
        );
        console.log(`נקלטה מודעה בתשלום (רמה: ${tier}, ${paymentAmount} ש"ח) לרשימת "${list.name}" מאת ${fromEmail} - נשלח קישור תשלום.`);
        return res.status(200).send('awaiting payment');
      }

      console.log(`נקלט בהצלחה: ${type} (רמה: ${tier}${bgColor ? ', צבע: ' + bgColor : ''}${attachedImages.length ? ', ' + attachedImages.length + ' תמונות' : ''}) לרשימת "${list.name}" מאת ${fromEmail}`);
      return res.status(200).send('queued');
    }

    // פנייה פרטית למנהל ("צור קשר" בתחתית הגיליון) - לא מתפרסמת בגיליון,
    // נשמרת בטבלה נפרדת ומוצגת בלוח הבקרה עם ציון מאיזו רשימה היא הגיעה.
    if (action === 'contact') {
      const list = db.prepare('SELECT * FROM lists WHERE slug = ?').get(extra);
      const cleanedText = stripKnownInstructions(text, list || {});
      db.prepare(`
        INSERT INTO contact_messages (list_id, from_email, subject, body) VALUES (?, ?, ?, ?)
      `).run(list ? list.id : null, fromEmail, subject, cleanedText);
      console.log(`פנייה חדשה מ-${fromEmail}${list ? ' (רשימת "' + list.name + '")' : ''}.`);
      return res.status(200).send('contact received');
    }

    if (action === 'reply') {
      const parentId = parseInt(extra, 10);
      const question = db.prepare('SELECT * FROM items WHERE id = ? AND type = ?').get(parentId, 'question');
      if (!question) {
        console.warn(`תגובה הגיעה לשאלה שלא נמצאה, מזהה=${parentId}`);
        return res.status(200).send('ignored: unknown question');
      }
      const list = db.prepare('SELECT * FROM lists WHERE id = ?').get(question.list_id);
      const cleanedText = stripKnownInstructions(text, list);

      db.prepare(`
        INSERT INTO items (list_id, type, parent_id, status, from_email, subject, body_raw, word_count)
        VALUES (?, 'answer', ?, 'pending', ?, ?, ?, ?)
      `).run(question.list_id, question.id, fromEmail, subject, cleanedText, countWords(cleanedText));

      console.log(`תגובה נקלטה בהצלחה לשאלה #${question.id} מאת ${fromEmail}`);
      return res.status(200).send('queued');
    }

    // הצטרפות/הסרה דרך מייל בלבד - לא צריך שום קישור או טוקן, כתובת השולח
    // עצמה (from) היא מה שמזהה את המנוי. שימושי במיוחד למי שאין לו גישה
    // נוחה לדפדפן.
    if (action === 'join') {
      const list = db.prepare('SELECT * FROM lists WHERE slug = ? AND active = 1').get(extra);
      if (!list) {
        console.warn(`ניסיון הצטרפות לרשימה לא קיימת: slug="${extra}"`);
        return res.status(200).send('ignored: unknown list');
      }
      const { subscribeEmail } = require('../subscriberUtil');
      const result = subscribeEmail(list.id, fromEmail);
      console.log(result.reactivated
        ? `${fromEmail} הצטרף מחדש לרשימת "${list.name}" (הוסר בעבר).`
        : `הצטרפות במייל: ${fromEmail} לרשימת "${list.name}".`);
      return res.status(200).send('joined');
    }

    if (action === 'leave') {
      const list = db.prepare('SELECT * FROM lists WHERE slug = ?').get(extra);
      if (!list) return res.status(200).send('ignored: unknown list');
      const result = db.prepare(`UPDATE subscribers SET unsubscribed = 1 WHERE list_id = ? AND email = ?`)
        .run(list.id, fromEmail);
      console.log(`הסרה במייל: ${fromEmail} מרשימת "${list.name}" (${result.changes} שורות עודכנו)`);
      return res.status(200).send('left');
    }

    // הצטרפות לכל הרשימות הפעילות בבת אחת (כפתור "הצטרפות לכל הרשימות"
    // בתחתית כל גיליון) - לא צריך slug כי זה חל על כולן, ה-extra מתעלמים ממנו.
    if (action === 'joinall') {
      const { subscribeEmail } = require('../subscriberUtil');
      const allLists = db.prepare('SELECT * FROM lists WHERE active = 1').all();
      let joinedCount = 0;
      for (const list of allLists) {
        const result = subscribeEmail(list.id, fromEmail);
        if (result.ok) joinedCount++;
      }
      console.log(`הצטרפות לכל הרשימות: ${fromEmail} נוסף/הופעל מחדש ב-${joinedCount} רשימות (מתוך ${allLists.length}).`);
      return res.status(200).send('joined all');
    }

    console.warn(`פעולה לא מוכרת: "${action}"`);
    return res.status(200).send('ignored: unknown action');

  } catch (err) {
    // חשוב מאוד: תמיד מחזירים 200 גם בשגיאה, אחרת SendGrid ינסה לשלוח שוב
    // ושוב ועלול לחסום את הכתובת שלנו. הטעות מתועדת ביומן לבדיקה.
    console.error('שגיאה בטיפול במייל נכנס:', err);
    return res.status(200).send('error logged');
  }
});

module.exports = router;
