const db = require('./db');

// כל פריט מאושר-ועדיין-לא-נשלח מקבל "יחידת תוכן" אחת ברשימה השטוחה הזו,
// ממוינת לפי manual_order. זה מה שמאפשר גרירה חופשית לגמרי בתצוגה המקדימה -
// מודעה יכולה לזוז מעל שאלה, שאלה אחת יכולה להיות למעלה ואחרת למטה, וכו' -
// בניגוד לגרסה הקודמת שקיבצה הכל לפי סוג (כל השאלות ביחד, כל המודעות ביחד).
function getOrderedApprovedEntries(listId) {
  const questions = db.prepare(`
    SELECT * FROM items WHERE list_id = ? AND type = 'question' AND status = 'approved'
  `).all(listId);

  const qaEntries = questions.map(question => {
    const answer = db.prepare(`
      SELECT * FROM items WHERE parent_id = ? AND type = 'answer' AND status = 'approved' ORDER BY approved_at ASC LIMIT 1
    `).get(question.id);
    return {
      kind: 'qa',
      order: question.manual_order ?? Number.MAX_SAFE_INTEGER,
      id: question.id,
      question,
      answer: answer || null
    };
  });

  // תשובות חדשות שהגיעו לשאלות שכבר נשלחו בעבר - מוצגות כיחידה עצמאית
  // משלהן (עם ציטוט השאלה המקורית), כדי שיהיה להן מקום לזוז בגיליון הבא.
  const followUpAnswers = db.prepare(`
    SELECT a.* FROM items a
    JOIN items q ON a.parent_id = q.id
    WHERE a.list_id = ? AND a.type = 'answer' AND a.status = 'approved' AND q.status = 'sent'
  `).all(listId);

  const followUpEntries = followUpAnswers.map(answer => {
    const question = db.prepare('SELECT * FROM items WHERE id = ?').get(answer.parent_id);
    return {
      kind: 'followup',
      order: answer.manual_order ?? Number.MAX_SAFE_INTEGER,
      id: answer.id,
      question,
      answer
    };
  });

  const ads = db.prepare(`
    SELECT * FROM items WHERE list_id = ? AND type = 'ad' AND status = 'approved'
  `).all(listId);
  const adEntries = ads.map(item => ({
    kind: 'ad', order: item.manual_order ?? Number.MAX_SAFE_INTEGER, id: item.id, item
  }));

  const topics = db.prepare(`
    SELECT * FROM items WHERE list_id = ? AND type = 'article' AND status = 'approved'
  `).all(listId);
  const topicEntries = topics.map(item => ({
    kind: 'topic', order: item.manual_order ?? Number.MAX_SAFE_INTEGER, id: item.id, item
  }));

  return [...qaEntries, ...followUpEntries, ...adEntries, ...topicEntries]
    .sort((a, b) => a.order - b.order || a.id - b.id);
}

// קורא לזה כל מקום שמאשר/יוצר פריט חדש (תור אישור, כתיבה ישירה) - נותן לו
// את המספר הסידורי הבא ברשימה, כך שבברירת מחדל הוא נכנס בסוף הגיליון,
// ואפשר אח"כ לגרור אותו לכל מקום אחר בתצוגה המקדימה.
function nextManualOrder(listId) {
  const row = db.prepare('SELECT COALESCE(MAX(manual_order), 0) AS m FROM items WHERE list_id = ?').get(listId);
  return row.m + 1;
}

// שומר סדר חדש שהתקבל מגרירה בתצוגה המקדימה - מערך של מזהי "יחידות"
// (id של שאלה/מודעה/נושא/תגובת-המשך) בסדר הרצוי.
function saveManualOrder(listId, orderedIds) {
  const setOrder = db.prepare('UPDATE items SET manual_order = ? WHERE id = ? AND list_id = ?');
  const tx = db.transaction((ids) => {
    ids.forEach((id, index) => setOrder.run(index + 1, id, listId));
  });
  tx(orderedIds);
}

// גודל הגיליון הבא: HTML בפועל (עם תמונות כ-CID, לא data URI - זה מה שבאמת
// נשלח) + סך התמונות המצורפות, מול שני הגבולות שחשובים בפועל: החיתוך של
// Gmail (בערך 102KB להודעה) והגבול הכללי של SendGrid לגודל הודעה (30MB).
function getIssueSizeInfo(list) {
  const { renderIssue, collectImageAttachments } = require('./templates');
  const entries = getOrderedApprovedEntries(list.id);
  const ads = entries.filter(e => e.kind === 'ad').map(e => e.item);

  const html = renderIssue({ list, entries, unsubscribeToken: 'size-check', useCid: true });
  const htmlBytes = Buffer.byteLength(html, 'utf8');

  const attachments = collectImageAttachments(ads);
  const attachmentsBytes = attachments.reduce((sum, a) => sum + Buffer.byteLength(a.content, 'base64'), 0);

  const totalBytes = htmlBytes + attachmentsBytes;
  // חשוב: הבדיקה של Gmail (~102KB) היא רק על משקל קוד ה-HTML עצמו - טקסט,
  // עיצוב, קישורים - ולא כוללת בכלל את משקל התמונות (שמצורפות כקובץ נפרד
  // עם cid:, לא מוטמעות בתוך הטקסט). זו הסיבה שה-HTML שנשלח בפועל נשאר קטן
  // גם עם תמונות. מגבלת ה-30MB של SendGrid לעומת זאת כן כוללת הכל (HTML +
  // כל הקבצים המצורפים ביחד) - שני דברים שונים לגמרי, נבדקים בנפרד.
  const GMAIL_CLIP_LIMIT_KB = 102;
  const SENDGRID_LIMIT_KB = 30 * 1024;

  return {
    entriesCount: entries.length,
    htmlKB: Math.round(htmlBytes / 1024),
    imagesKB: Math.round(attachmentsBytes / 1024),
    totalKB: Math.round(totalBytes / 1024),
    gmailLimitKB: GMAIL_CLIP_LIMIT_KB,
    sendgridLimitKB: SENDGRID_LIMIT_KB,
    // אחוז מהגבול של Gmail - לפי משקל ה-HTML בלבד (זה מה ש-Gmail בפועל בודק).
    percentOfGmailLimit: Math.min(999, Math.round((htmlBytes / 1024 / GMAIL_CLIP_LIMIT_KB) * 100)),
    nearGmailLimit: htmlBytes / 1024 > GMAIL_CLIP_LIMIT_KB * 0.75,
    // אחוז מהגבול של SendGrid - לפי הגודל הכולל (HTML + תמונות ביחד).
    percentOfSendgridLimit: Math.min(999, Math.round((totalBytes / 1024 / SENDGRID_LIMIT_KB) * 100)),
    nearSendgridLimit: totalBytes / 1024 > SENDGRID_LIMIT_KB * 0.75
  };
}

const KIND_LABELS = { qa: 'שאלה', followup: 'תגובה לשאלה שנשלחה', ad: 'מודעה', topic: 'נושא' };
const KIND_ICONS = { qa: '❓', followup: '↩️', ad: '📢', topic: '📝' };
const TIER_LABELS = { plus: 'מודגשת', premium: 'פרימיום' };

function snippetOf(text = '', max = 60) {
  const clean = String(text).replace(/\s+/g, ' ').trim();
  return clean.length > max ? clean.slice(0, max) + '…' : clean;
}

// הופך "יחידת" תוכן (entry) לתיאור קצר וקריא לאדם, לשימוש בעמודת הגרירה
// בתצוגה המקדימה - לא בגיליון עצמו.
function describeEntry(entry) {
  const kindLabel = KIND_LABELS[entry.kind] || entry.kind;
  const icon = KIND_ICONS[entry.kind] || '•';

  if (entry.kind === 'qa' || entry.kind === 'followup') {
    const q = entry.question;
    const title = q && q.subject ? q.subject : snippetOf(q ? (q.body_edited ?? q.body_raw) : '');
    return { id: entry.id, kind: entry.kind, icon, kindLabel, title: title || '(ללא כותרת)' };
  }

  if (entry.kind === 'ad') {
    const item = entry.item;
    const tier = TIER_LABELS[item.paid_tier] ? ` · ${TIER_LABELS[item.paid_tier]}` : '';
    const title = item.subject || snippetOf(item.body_edited ?? item.body_raw);
    return { id: entry.id, kind: entry.kind, icon, kindLabel: kindLabel + tier, title: title || '(ללא כותרת)' };
  }

  // topic
  const item = entry.item;
  const title = item.subject || snippetOf(item.body_edited ?? item.body_raw);
  return { id: entry.id, kind: entry.kind, icon, kindLabel, title: title || '(ללא כותרת)' };
}

module.exports = { getOrderedApprovedEntries, nextManualOrder, saveManualOrder, getIssueSizeInfo, describeEntry };
