// עיצוב אחיד לכל הרשימות - "בית" אחד עם מיתוג עקבי.
// ההבדל בין רשימה לרשימה הוא רק צבע ההדגשה (accent) ושם הרשימה בכותרת,
// לא לוגו/פריסה/גופן שונה. זה מה שגורם לזה להרגיש כמו גוף אחד ומקצועי,
// ולא כמו כמה אתרים חובבניים שונים.
//
// עיקרון מנחה: כל פעולה (שאלה, מודעה, הצטרפות, הסרה, תגובה) ניתנת לביצוע
// כ-mailto בלבד, כדי שגם לקוחות עם גישה מוגבלת לדפדפן (כמו "נטו מייל")
// יוכלו להשתמש בכל התכונות בלי לצאת מתוכנת המייל שלהם.

const fs = require('fs');
const path = require('path');

const BRAND_NAME = process.env.BRAND_NAME || 'הרשימות שלנו';
const { getBaseUrl } = require('./appSettings');
const INBOUND_DOMAIN = process.env.INBOUND_DOMAIN || 'yourdomain.com';
const UPLOAD_DIR = path.join(__dirname, '..', 'data', 'uploads');

const MIME_BY_EXT = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.gif': 'image/gif', '.webp': 'image/webp'
};

function escapeHtml(str = '') {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// עיצוב טקסט בסיסי: **מודגש**, *נטוי*, __קו תחתון__ - מיושם אחרי ה-escape,
// כך שאין שום סיכון של הזרקת HTML - התווים המיוחדים היחידים שמזוהים הם
// אלה, שום תג HTML גולמי לא עובר.
function applyBasicFormatting(escapedText) {
  return escapedText
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<u>$1</u>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>');
}

// חלק מלקוחות המייל (Gmail, Outlook ועוד) הופכים כל טקסט שנראה ככתובת
// אתר ללחיץ בעצמם, בסריקה על הטקסט המוצג - גם כשאין שום <a> מסביב
// בקוד ה-HTML שלנו. בלי הפונקציה הזו, מודעה שהלקוח לא ביקש שתהיה לחיצה
// (לא מילא את שדה "קישור", ראה link_url) הייתה בכל זאת הופכת ללחיצה
// בפועל אצל חלק מהנמענים, כי התבנית "http://..."/"www...." עדיין
// מופיעה כטקסט רגיל. מוסיפים תו ברוחב אפס (zero-width non-joiner)
// בתוך התבנית - שובר את הזיהוי האוטומטי, בלי לשנות איך זה נראה לעין.
function neutralizeUrls(html) {
  return html
    .replace(/(https?):\/\//gi, '$1:\u200C//')
    .replace(/\bwww\./gi, 'www\u200C.');
}

function formatBody(raw) {
  return neutralizeUrls(applyBasicFormatting(escapeHtml(raw))).replace(/\n/g, '<br>');
}

function absoluteUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${getBaseUrl()}${path.startsWith('/') ? '' : '/'}${path}`;
}

// הופך תמונה שהועלתה למערכת (נתיב כמו /uploads/xxx.png) למחרוזת base64
// המוטמעת ישירות בתוך ה-HTML (data URI). שימושי לתצוגה בדפדפן (תצוגה
// מקדימה/ארכיון/היסטוריה) - שם זה תמיד עובד כי דפדפנים תומכים ב-data URI
// בלי יוצא מן הכלל.
function embedImageAsDataUri(relativePath) {
  try {
    if (/^https?:\/\//i.test(relativePath)) return null;
    const ext = path.extname(relativePath).toLowerCase();
    const mime = MIME_BY_EXT[ext];
    if (!mime) return null;

    const filename = path.basename(relativePath);
    const filePath = path.join(UPLOAD_DIR, filename);
    if (!fs.existsSync(filePath)) return null;

    const buffer = fs.readFileSync(filePath);
    return `data:${mime};base64,${buffer.toString('base64')}`;
  } catch (err) {
    console.error('שגיאה בהטמעת תמונה כ-base64:', err.message);
    return null;
  }
}

// למייל בפועל שנשלח (useCid=true) לא משתמשים ב-data URI, כי חלק ניכר
// מתוכנות המייל - הבולטת שבהן Outlook - פשוט לא מציגות תמונות data URI
// בכלל. הפתרון הסטנדרטי שכל שירותי הניוזלטרים משתמשים בו הוא הטמעת
// התמונה כקובץ מצורף עם Content-ID (cid), ואז הפניה אליה מתוך ה-HTML
// דרך src="cid:...". זה עדיין חלק מאותה הודעה (לא קובץ נפרד שנטען
// מבחוץ), אבל נתמך כמעט בכל תוכנת מייל שקיימת.
function imageCid(itemId, index) {
  return `img-${itemId}-${index}`;
}

// גוף מייל מוכן-מראש (רק כשצריך - עכשיו כמעט תמיד ריק, ראה למטה) לכל
// כפתור mailto.
function mailto(action, slug, subjectText, bodyText = '') {
  const params = new URLSearchParams({ subject: subjectText });
  if (bodyText) params.set('body', bodyText);
  return `mailto:${action}+${slug}@${INBOUND_DOMAIN}?${params.toString().replace(/\+/g, '%20')}`;
}

// [היסטוריה] הטקסטים האלה שימשו בעבר למילוי מראש של גוף המייל - הוסרו
// מהטיוטות עצמן (נשארות ריקות עכשיו, ראה renderHoverButton למטה שמציג את
// ההסבר כריבוע ב-hover בתוך הגיליון במקום). נשארים כאן רק כדי ש-inbound.js
// ימשיך לזהות ולנקות אותם אם הם עדיין מופיעים אצל לקוח שיש לו טיוטה ישנה
// שנשמרה לפני העדכון הזה.
const INSTR_ASK = 'כתבו כאן את השאלה שלכם ולחצו שליחה - היא תיכנס לתור אישור ותתפרסם בגיליון הקרוב.';
const INSTR_FREE_AD = 'כתבו כאן את תוכן המודעה ולחצו שליחה - זו מודעת שורה פשוטה (טקסט בלבד), תיכנס לתור אישור ותתפרסם בגיליון הקרוב.';
const INSTR_REPLY = 'כתבו כאן את התגובה שלכם ולחצו שליחה - היא תצורף לשאלה הזו בגיליון הבא, אחרי אישור.';
const INSTR_CONTACT = 'כתבו כאן את ההודעה שלכם למנהל הרשימה ולחצו שליחה - זו פנייה פרטית, היא לא מתפרסמת בגיליון.';

function colorNamesList(list) {
  try {
    const palette = JSON.parse(list.ad_color_palette_json || '[]');
    return palette.map(c => c.name).filter(Boolean);
  } catch (e) { return []; }
}

function colorPalette(list) {
  try { return JSON.parse(list.ad_color_palette_json || '[]'); } catch (e) { return []; }
}

// [היסטוריה] כמו INSTR_* למעלה - נשארות רק לצורך ניקוי טיוטות ישנות
// ב-inbound.js, לא מיוצרות יותר בפועל בכפתורים.
function instrPlusAd(list) {
  const names = colorNamesList(list);
  const example = names[0] || 'כחול';
  const namesText = names.length ? ` הצבעים הזמינים: ${names.join(', ')}.` : '';
  return `כתבו כאן את תוכן המודעה. רוצים לבחור צבע רקע? הוסיפו שורה בנוסח "צבע: ${example}".${namesText} לחצו שליחה - המודעה תיכנס לתור אישור.`;
}
function instrPremiumAd(list) {
  const names = colorNamesList(list);
  const example = names[0] || 'כחול';
  const namesText = names.length ? ` הצבעים הזמינים: ${names.join(', ')}.` : '';
  return `כתבו כאן את תוכן המודעה, אפשר לצרף תמונה או גיף כקובץ מצורף למייל הזה. רוצים לבחור צבע רקע? הוסיפו שורה בנוסח "צבע: ${example}".${namesText} לחצו שליחה - המודעה תיכנס לתור אישור.`;
}

// כל טקסטי ההוראה הישנים שהמערכת נהגה להזין - inbound.js מסיר כל הופעה
// מדויקת שלהם מהתוכן שנשמר, כדי שלא "ידביקו" לתוך המודעה/תשובה/נושא אם
// לקוח שולח בחזרה טיוטה ישנה שנשמרה אצלו לפני העדכון.
function getKnownInstructionStrings(list) {
  return [INSTR_ASK, INSTR_FREE_AD, INSTR_REPLY, INSTR_CONTACT, instrPlusAd(list), instrPremiumAd(list)];
}

// גוף מייל מובנה לשלוש שורות נפרדות - תוכן/צבע/קישור - כדי ש-inbound.js
// יוכל לשאוב כל שדה לפי מיקומו (השורה שמתחילה בתווית שלו), במקום לחפש
// טקסט חופשי בכל הגוף. משמש למודגשת ולפרימיום כאחד.
function adBodyTemplate() {
  return 'תוכן המודעה: \nצבע רקע: \nקישור: ';
}

// עיגולי צבע אמיתיים (לא רק שם) - בדיוק מה שהמנהל הגדיר בהגדרות הרשימה -
// מוצגים בריבוע ה-hover של כפתורי מודגשת/פרימיום, כדי שהלקוח יראה ממש את
// הגוון לפני שהוא כותב את השם בטיוטה.
function renderColorSwatchesHtml(list) {
  const palette = colorPalette(list);
  if (!palette.length) return '';
  const swatches = palette.map(c => `
    <span style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin:3px 8px 3px 0;">
      <span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:${escapeHtml(c.hex)};border:1px solid rgba(0,0,0,0.15);vertical-align:middle;"></span>
      ${escapeHtml(c.name)}
    </span>`).join('');
  return `<div style="margin-top:8px;"><strong>צבעים זמינים:</strong><br>${swatches}</div>`;
}

// עוטפת כל כפתור mailto (שתמיד עבד ועדיין עובד בדיוק כמו קודם - הקישור
// עצמו לא השתנה) בריבוע הסבר שמופיע ב-hover (מעבר עכבר), מתחת לכפתור.
//
// מסקנה מבדיקה בפועל: Gmail מוחק לחלוטין כל מאפיין CSS מסוג position
// (absolute/relative/fixed) מכל מייל שמתקבל - אז אין דרך "לצוף" מעל
// הגיליון בלי לגעת בזרימה שלו. בפועל, הריבוע פשוט מצטרף לזרימה הרגילה
// כילד בתוך ה-wrap, וזה בסדר - זה בדיוק מה שביקשת (שהגיליון כן יזוז
// כלפי מטה, רק שהכפתורים עצמם לא יזוזו הצידה).
//
// הסיבה שבגרסה קודמת הכפתורים "קפצו הצידה": ה-wrap (span שעוטף כל
// כפתור) היה ברוחב אוטומטי (shrink-to-fit) לפי רוחב הכפתור עצמו (בד"כ
// צר, ~140-180px) - וכשהריבוע (רחב יותר, ~220px) הופיע בתוכו, ה-wrap
// כולו התרחב לרוחב הריבוע כדי להכיל אותו, ודחף את שאר הכפתורים לצד.
// התיקון: לתת ל-wrap רוחב קבוע מראש (זהה לרוחב הריבוע) שלא משתנה בין
// מצב פתוח לסגור - כך רק הגובה גדל (כלפי מטה), הרוחב תמיד זהה, ושום
// כפתור ליד לא זז הצידה. אם שני כפתורים יושבים זה ליד זה, כל אחד שומר
// בדיוק על המקום שלו; מתחת לכפתור שעליו עוברים עם העכבר נפתח מרווח
// ומופיע בו הריבוע, והגיליון מתחתיו פשוט זז למטה בהתאם.
const HOVER_BOX_WIDTH = 230;
function renderHoverButton(accent, { label, buttonStyle, mailtoUrl, explanation, extraHtml = '' }) {
  return `
    <span class="hover-wrap" style="display:inline-block;vertical-align:top;width:${HOVER_BOX_WIDTH}px;text-align:center;margin:3px;">
      <a href="${mailtoUrl}" style="${buttonStyle}">${label}</a>
      <span class="hover-box" style="display:none;margin-top:8px;text-align:right;width:${HOVER_BOX_WIDTH}px;border:1px solid ${accent}55;background:#fffdf8;border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.55;color:#2c2c2a;box-sizing:border-box;">
        ${escapeHtml(explanation)}
        ${extraHtml}
      </span>
    </span>`;
}

function wordLimitBadge(item) {
  const tierLabel = { free: '', plus: 'מודעה מודגשת', premium: 'מודעה פרימיום' }[item.paid_tier] || '';
  if (!tierLabel) return '';
  return `<span style="font-size:11px;background:#f1efe8;color:#5f5e5a;padding:2px 8px;border-radius:10px;margin-inline-start:6px;">${tierLabel}</span>`;
}

// מסגרת אחידה ומעוצבת בצבע הרשימה - כל תוכן בגיליון (שאלה+תשובה, נושא,
// מודעה) יושב בתוך "כרטיס" עם גבול וגוון רקע עדין בצבע ההדגשה של הרשימה,
// כך שהגיליון מרגיש כמו מוצר אחד מעוצב, לא רשימת טקסט עם קווי הפרדה.
function cardWrapper(accent, innerHtml, { bg, border } = {}) {
  const background = bg || `${accent}0d`;
  const borderColor = border || `${accent}40`;
  return `
  <tr><td style="padding:8px 0;">
    <div style="border:1px solid ${borderColor};border-radius:12px;background:${background};padding:16px 18px;">
      ${innerHtml}
    </div>
  </td></tr>`;
}

function renderAd(item, useCid, accent) {
  const images = JSON.parse(item.images_json || '[]');
  const body = formatBody(item.body_edited ?? item.body_raw);

  const fg = item.text_color || '#2c2c2a';

  // קישור כללי שהלקוח צירף למודעה (מודגשת/פרימיום בלבד, ראה link_url) -
  // הופך את כל המודעה ללחיצה. שני סוגים אפשריים (link_type): 'website' -
  // קישור חיצוני שהלקוח הזין בעצמו (link_url); 'lead' - קישור לדף מילוי
  // פרטים גנרי שהמערכת מארחת בעצמה (ראה routes/leads.js), לפי lead_token.
  // אם שניהם ריקים, המודעה נשארת בלי שום קישור לחיצה, כולל קישורים
  // שמופיעים בטקסט עצמו (אלה כבר לא הופכים ללחיצים אוטומטית).
  const wholeAdLink = item.link_type === 'lead'
    ? (item.lead_token ? `${getBaseUrl()}/leads/${item.lead_token}` : null)
    : (item.link_url || null);

  // במייל בפועל (useCid=true): src="cid:..." - הקובץ מצורף להודעה עם
  // אותו מזהה, ראה compiler.js. בתצוגה בדפדפן (preview/history/archive):
  // data URI, כי שם אין "מצורפים" בכלל, רק HTML גולמי.
  const imagesHtml = images.map((src, index) => {
    // אם כל המודעה כבר עטופה בקישור (wholeAdLink), לא עוטפים גם את
    // התמונה בנפרד - קישור מקונן (<a> בתוך <a>) לא תקין ב-HTML. אם אין
    // קישור כללי אבל יש image_link (המנגנון הישן, ידני-בלבד) - משתמשים
    // בו רק על התמונה, כמו קודם.
    const wrap = (html) => (!wholeAdLink && item.image_link) ? `<a href="${escapeHtml(item.image_link)}">${html}</a>` : html;
    if (useCid) {
      return wrap(`<img src="cid:${imageCid(item.id, index)}" alt="" style="max-width:100%;border-radius:8px;margin-bottom:8px;display:block;" />`);
    }
    const dataUri = embedImageAsDataUri(src);
    if (dataUri) {
      return wrap(`<img src="${dataUri}" alt="" style="max-width:100%;border-radius:8px;margin-bottom:8px;display:block;" />`);
    }
    const fallbackColor = item.bg_color ? fg : '#185fa5';
    return `<a href="${escapeHtml(absoluteUrl(src))}" style="display:inline-block;font-size:13px;color:${fallbackColor};text-decoration:underline;margin-bottom:8px;">לצפייה בתמונה &#8599;</a>`;
  }).join('');

  let inner = `
    <div style="font-size:15px;line-height:1.6;color:${fg};">
      ${imagesHtml}
      ${item.subject ? `<strong>${escapeHtml(item.subject)}</strong>${wordLimitBadge(item)}<br>` : wordLimitBadge(item)}
      ${body}
    </div>`;

  if (wholeAdLink) {
    inner = `<a href="${escapeHtml(wholeAdLink)}" style="display:block;text-decoration:none;color:inherit;">${inner}</a>`;
  }

  // מודעות עם צבע רקע מותאם אישית (מודגשת/פרימיום שהאדמין צבע): הצבע
  // עצמו הופך למסגרת. אחרת (חינם, או מודגשת/פרימיום בלי צבע שנבחר) -
  // המסגרת האחידה בצבע הרשימה, כמו כל שאר התוכן בגיליון.
  return cardWrapper(accent, inner, item.bg_color ? { bg: item.bg_color, border: 'rgba(0,0,0,0.08)' } : {});
}

// אוספת את כל התמונות של המודעות בגיליון כדי לצרף אותן בפועל להודעה
// (attachments עם content_id תואם למה ש-renderAd ייצר ב-cid:...).
// נקראת מ-compiler.js רק כשבונים את המייל שבאמת יישלח.
function collectImageAttachments(ads) {
  const attachments = [];
  ads.forEach(item => {
    const images = JSON.parse(item.images_json || '[]');
    images.forEach((src, index) => {
      if (/^https?:\/\//i.test(src)) return; // תמיכה רק בתמונות שהועלו למערכת עצמה
      const ext = path.extname(src).toLowerCase();
      const mime = MIME_BY_EXT[ext];
      if (!mime) return;
      const filePath = path.join(UPLOAD_DIR, path.basename(src));
      if (!fs.existsSync(filePath)) return;

      attachments.push({
        content: fs.readFileSync(filePath).toString('base64'),
        filename: path.basename(src),
        type: mime,
        disposition: 'inline',
        content_id: imageCid(item.id, index)
      });
    });
  });
  return attachments;
}

function renderTopic(item, accent) {
  const body = formatBody(item.body_edited ?? item.body_raw);
  const inner = `
    ${item.subject ? `<div style="font-size:15px;font-weight:700;color:${accent};margin-bottom:4px;">${escapeHtml(item.subject)}</div>` : ''}
    <div style="font-size:15px;line-height:1.6;color:#2c2c2a;">${body}</div>`;
  return cardWrapper(accent, inner);
}

function renderQA(question, answer, accent) {
  const qBody = formatBody(question.body_edited ?? question.body_raw);
  const aBody = answer ? formatBody(answer.body_edited ?? answer.body_raw) : '';
  const replyUrl = mailto('reply', question.id, 'תגובה: ' + (question.subject || ''));

  const inner = `
    <div style="font-size:14px;color:${accent};font-weight:600;margin-bottom:4px;">שאלה</div>
    <div style="font-size:15px;line-height:1.6;color:#2c2c2a;">${qBody}</div>
    ${answer ? `
    <div style="font-size:14px;color:${accent};font-weight:600;margin:10px 0 4px;">תשובה</div>
    <div style="font-size:15px;line-height:1.6;color:#2c2c2a;">${aBody}</div>
    ` : ''}
    <div style="margin-top:10px;">
      ${renderHoverButton(accent, {
        buttonStyle: `display:inline-block;font-size:13px;color:${accent};text-decoration:none;border:1px solid ${accent};padding:4px 10px;border-radius:14px;`,
        label: 'להגיב לשאלה הזו במייל &larr;',
        mailtoUrl: replyUrl,
        explanation: 'כתבו את התגובה שלכם בגוף המייל שנפתח ולחצו שליחה - היא תצורף לשאלה הזו בגיליון הבא, אחרי אישור.'
      })}
    </div>`;

  return cardWrapper(accent, inner);
}

// כפתורי הצטרפות/הסרה בולטים בראש הגיליון - שניהם דרך מייל, לפי כתובת
// השולח בפועל (from), בלי צורך בקישור אישי או טוקן.
function renderTopButtons(list) {
  const accent = list.accent_color || '#1D9E75';
  const joinUrl = mailto('join', list.slug, 'הצטרפות');
  const leaveUrl = mailto('leave', list.slug, 'הסרה');
  return `
  <tr><td style="padding:14px 24px;text-align:center;background:#faf9f6;">
    ${renderHoverButton(accent, {
      buttonStyle: 'display:inline-block;font-size:13px;color:#fff;background:#1D9E75;text-decoration:none;padding:8px 16px;border-radius:16px;margin:3px;font-weight:600;',
      label: 'הצטרפות לרשימה',
      mailtoUrl: joinUrl,
      explanation: 'לחיצה על "שליחה" מצרפת אתכם באופן אוטומטי לרשימה - אין צורך לכתוב שום דבר בגוף ההודעה.'
    })}
    ${renderHoverButton(accent, {
      buttonStyle: 'display:inline-block;font-size:13px;color:#c04828;background:transparent;border:1px solid #c04828;text-decoration:none;padding:7px 16px;border-radius:16px;margin:3px;font-weight:600;',
      label: 'הסרה מהרשימה',
      mailtoUrl: leaveUrl,
      explanation: 'לחיצה על "שליחה" מסירה אתכם באופן אוטומטי מהרשימה - אין צורך לכתוב שום דבר בגוף ההודעה.'
    })}
  </td></tr>`;
}

// שורת כפתורים: שליחת שאלה + פרסום מודעה (כל רמה עם מתג הצגה נפרד) +
// יצירת קשר - כל הכפתורים פותחים mailto עם גוף ריק (חוץ ממודגשת/פרימיום,
// ששם כבר כתוב "צבע: " מוכן למילוי) והסבר מופיע ב-hover, לא בגוף המייל.
function renderActionButtons(list, accent) {
  const buttons = [];

  if (list.show_ask_button) {
    buttons.push(renderHoverButton(accent, {
      buttonStyle: btnStyle(accent, true),
      label: 'לשליחת שאלה חדשה',
      mailtoUrl: mailto('ask', list.slug, 'שאלה חדשה'),
      explanation: 'כתבו את השאלה שלכם בגוף המייל שנפתח ולחצו שליחה - היא תיכנס לתור אישור ותתפרסם בגיליון הקרוב.'
    }));
  }

  if (list.show_ads_free) {
    buttons.push(renderHoverButton(accent, {
      buttonStyle: btnStyle(accent, false),
      label: 'פרסום מודעת שורה (חינם)',
      mailtoUrl: mailto('ads', list.slug, 'מודעת שורה'),
      explanation: 'זו מודעת שורה פשוטה (טקסט בלבד). כתבו את התוכן בגוף המייל שנפתח ולחצו שליחה - היא תיכנס לתור אישור.'
    }));
  }
  if (list.show_ads_plus) {
    const linkNote = Number(list.link_price_plus) > 0
      ? ` צירוף קישור (שהופך את כל המודעה ללחיצה) כרוך בתוספת ${Number(list.link_price_plus)} ש"ח.`
      : ' אפשר גם לצרף קישור - לחיצה על המודעה תעביר אליו.';
    buttons.push(renderHoverButton(accent, {
      buttonStyle: btnStyle(accent, false),
      label: 'פרסום מודעה מודגשת',
      mailtoUrl: mailto('adsplus', list.slug, 'מודעה מודגשת', adBodyTemplate()),
      explanation: `המודעה תפורסם בתוך מסגרת צבעונית בולטת. במייל שנפתח יש שלוש שורות למילוי: תוכן המודעה, צבע רקע (אופציונלי), וקישור (אופציונלי).${linkNote}`,
      extraHtml: renderColorSwatchesHtml(list)
    }));
  }
  if (list.show_ads_premium) {
    const linkNote = Number(list.link_price_premium) > 0
      ? ` צירוף קישור (שהופך את כל המודעה ללחיצה) כרוך בתוספת ${Number(list.link_price_premium)} ש"ח.`
      : ' אפשר גם לצרף קישור - לחיצה על המודעה תעביר אליו.';
    buttons.push(renderHoverButton(accent, {
      buttonStyle: btnStyle(accent, false),
      label: 'פרסום מודעה פרימיום',
      mailtoUrl: mailto('adspremium', list.slug, 'מודעה פרימיום', adBodyTemplate()),
      explanation: `אפשר לצרף תמונה או גיף כקובץ מצורף למייל. במייל שנפתח יש שלוש שורות למילוי: תוכן המודעה, צבע רקע (אופציונלי), וקישור (אופציונלי).${linkNote}`,
      extraHtml: renderColorSwatchesHtml(list)
    }));
  }

  buttons.push(renderHoverButton(accent, {
    buttonStyle: btnStyle(accent, false),
    label: 'צור קשר',
    mailtoUrl: mailto('contact', list.slug, 'פנייה למנהל הרשימה'),
    explanation: 'זו פנייה פרטית למנהל הרשימה - לא מתפרסמת בגיליון.'
  }));

  if (buttons.length === 0) return '';

  return `
  <tr><td style="padding:18px 24px;text-align:center;">
    <div>
      ${buttons.join('')}
    </div>
  </td></tr>`;
}

// [ניסויי אינטראקטיביות קודמים הוסרו: checkbox hack, קישור עוגן + :target,
// ו-<details> - כולם נכשלו בבדיקה בג'ימייל אמיתי. הניסיון שכן עבד - מבוסס
// :hover - הוטמע בפועל בכל הכפתורים דרך renderHoverButton למעלה.]

function btnStyle(accent, filled) {
  return filled
    ? `display:inline-block;font-size:13px;color:#fff;background:${accent};text-decoration:none;padding:8px 14px;border-radius:16px;margin:3px;`
    : `display:inline-block;font-size:13px;color:${accent};background:transparent;border:1px solid ${accent};text-decoration:none;padding:7px 14px;border-radius:16px;margin:3px;`;
}

// [renderColorLegend הוסרה] - רשימת הצבעים כבר לא מוצגת בגיליון שנשלח
// למנויים; היא מוטמעת ישירות בטיוטת המייל של הלקוח (ראה instrPlusAd /
// instrPremiumAd למעלה) - שם היא רלוונטית (כשהוא בוחר צבע), לא כאן.

// כפתורי הצטרפות לשאר הרשימות הפעילות + כפתור הצטרפות לכולן ביחד, בתחתית
// הגיליון - כדי שמנוי לרשימה אחת יגלה בקלות שיש עוד רשימות ויוכל להצטרף
// אליהן, הכל עדיין דרך מייל בלבד.
function renderOtherListsPromo(list) {
  const db = require('./db');
  const otherLists = db.prepare('SELECT * FROM lists WHERE active = 1 AND id != ? ORDER BY name ASC').all(list.id);
  if (otherLists.length === 0) return '';

  const joinAllUrl = mailto('joinall', 'all', 'הצטרפות לכל הרשימות');
  const buttons = otherLists.map(l => {
    const url = mailto('join', l.slug, 'הצטרפות');
    return `<a href="${url}" style="display:inline-block;font-size:12px;color:${l.accent_color || '#1D9E75'};background:transparent;border:1px solid ${l.accent_color || '#1D9E75'};text-decoration:none;padding:5px 12px;border-radius:14px;margin:3px;">${escapeHtml(l.name)}</a>`;
  }).join('');

  return `
  <tr><td style="padding:16px 24px;background:#faf9f6;text-align:center;">
    <div style="font-size:12px;color:#5f5e5a;margin-bottom:8px;">רשימות נוספות שאולי יעניינו אתכם:</div>
    <div>${buttons}</div>
    <div style="margin-top:10px;">
      <a href="${joinAllUrl}" style="display:inline-block;font-size:12px;color:#fff;background:#2c2c2a;text-decoration:none;padding:6px 14px;border-radius:14px;">הצטרפות לכל הרשימות בבת אחת</a>
    </div>
  </td></tr>`;
}

// entries = מערך שטוח, כבר בסדר התצוגה הרצוי (ראה issueBuilder.js) - כל
// איבר הוא שאלה+תשובה, תגובת המשך, מודעה, או נושא - בכל סדר אפשרי, כולל
// מעורב. הוחלף ה-H2/section הישן (שאלות/נושאים/מודעות בקבוצות נפרדות)
// בזרימה חופשית אחת, כדי לאפשר גרירה חופשית לגמרי בתצוגה המקדימה.
function renderEntry(entry, accent, useCid) {
  if (entry.kind === 'qa' || entry.kind === 'followup') {
    return renderQA(entry.question, entry.answer, accent);
  }
  if (entry.kind === 'ad') {
    return renderAd(entry.item, useCid, accent);
  }
  if (entry.kind === 'topic') {
    return renderTopic(entry.item, accent);
  }
  return '';
}

function renderIssue({ list, entries = [], unsubscribeToken, useCid = false }) {
  const accent = list.accent_color || '#1D9E75';
  const bodyHtml = entries.map(entry => renderEntry(entry, accent, useCid)).join('');
  const unsubUrl = `${getBaseUrl()}/unsubscribe/${unsubscribeToken}`;

  return `<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<style>
  /* ריבוע הסבר שמופיע ב-hover (מעבר עכבר) מתחת לכל כפתור. הוא מתווסף
     לזרימה הרגילה (Gmail מוחק position ממילא) - כלומר הגיליון מתחתיו כן
     זז למטה כשנפתח, אבל בזכות הרוחב הקבוע על ה-wrap (ראה renderHoverButton
     ב-templates.js) שום כפתור לא זז הצידה - רק נפתח מרווח מתחתיו. */
  .hover-wrap:hover .hover-box { display: block !important; }
</style>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#ffffff;">
    <tr>
      <td style="background:${accent};padding:22px 24px;">
        <div style="color:#ffffff;font-size:13px;opacity:0.9;">${escapeHtml(BRAND_NAME)}</div>
        <div style="color:#ffffff;font-size:20px;font-weight:700;margin-top:2px;">${escapeHtml(list.name)}</div>
      </td>
    </tr>
    ${renderTopButtons(list)}
    <tr><td style="padding:0 24px;">
      <table role="presentation" width="100%">${bodyHtml}</table>
    </td></tr>
    ${renderActionButtons(list, accent)}
    ${renderOtherListsPromo(list)}
    <tr>
      <td style="padding:18px 24px;background:#f6f5f1;text-align:center;">
        <div style="font-size:12px;color:#888780;margin-bottom:10px;">
          קיבלת מייל זה כי אתה רשום לרשימת "${escapeHtml(list.name)}" של ${escapeHtml(BRAND_NAME)}.
        </div>
        <a href="${getBaseUrl()}/archive/${list.slug}" style="display:inline-block;font-size:12px;color:${accent};background:transparent;border:1px solid ${accent};text-decoration:none;padding:6px 14px;border-radius:14px;margin:3px;">גיליונות קודמים</a>
        <a href="${unsubUrl}" style="display:inline-block;font-size:12px;color:#c04828;background:transparent;border:1px solid #c04828;text-decoration:none;padding:6px 14px;border-radius:14px;margin:3px;">להסרה מרשימה זו</a>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

module.exports = { renderIssue, escapeHtml, collectImageAttachments, getKnownInstructionStrings };
