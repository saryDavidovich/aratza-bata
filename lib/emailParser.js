const crypto = require('crypto');
const db = require('./db');

const FREE_WORD_LIMIT = 40; // מגבלת מילים חינמית ללוח המודעות
const FREE_ADS_PER_WEEK = 3; // הגבלת ספאם - מודעות חינמיות מקסימום בשבוע

function countWords(text) {
  return (text || '').trim().split(/\s+/).filter(Boolean).length;
}

function weekKey(date = new Date()) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
  return `${d.getUTCFullYear()}-W${weekNo}`;
}

function findTopicByInboundAddress(toAddress) {
  const clean = (toAddress || '').toLowerCase().trim();
  return db.prepare('SELECT * FROM topics WHERE lower(inbound_email) = ?').get(clean);
}

// בודק אם זו תגובה לשאלה קיימת, לפי טוקן שהוטבע בכתובת התגובה או בכותרות References/In-Reply-To
function findParentQuestion(payload) {
  const candidates = [
    payload.to,
    payload.references,
    payload.inReplyTo,
    payload.subject,
  ].filter(Boolean).join(' ');

  const match = candidates.match(/reply\+([a-zA-Z0-9]{8,})/);
  if (!match) return null;

  return db.prepare('SELECT * FROM queue_items WHERE reply_token = ?').get(match[1]);
}

function checkRateLimit(senderEmail, topicId) {
  const wk = weekKey();
  const row = db.prepare(
    'SELECT count FROM rate_limits WHERE sender_email = ? AND topic_id = ? AND week_key = ?'
  ).get(senderEmail, topicId, wk);
  return !row || row.count < FREE_ADS_PER_WEEK;
}

function incrementRateLimit(senderEmail, topicId) {
  const wk = weekKey();
  db.prepare(`
    INSERT INTO rate_limits (sender_email, topic_id, week_key, count)
    VALUES (?, ?, ?, 1)
    ON CONFLICT(sender_email, topic_id, week_key)
    DO UPDATE SET count = count + 1
  `).run(senderEmail, topicId, wk);
}

/**
 * מעבד payload של מייל נכנס (בפורמט דומה ל-Postmark/Mailgun inbound webhook)
 * ומכניס אותו לתור ההמתנה לאישור.
 *
 * payload צפוי: { to, from, fromName, subject, textBody, references, inReplyTo }
 */
function processInboundEmail(payload) {
  const topic = findTopicByInboundAddress(payload.to);
  if (!topic) {
    return { ok: false, reason: 'unknown_topic', detail: `לא נמצא נושא עבור הכתובת ${payload.to}` };
  }

  const parentQuestion = findParentQuestion(payload);
  const isClassifiedsTopic = topic.slug === 'classifieds';
  const type = parentQuestion ? 'answer' : (isClassifiedsTopic ? 'classified' : 'question');

  const body = (payload.textBody || '').trim();
  const wc = countWords(body);

  if (type === 'classified') {
    if (!checkRateLimit(payload.from, topic.id)) {
      return { ok: false, reason: 'rate_limited', detail: 'חריגה ממכסת המודעות החינמיות השבועית' };
    }
    if (wc > FREE_WORD_LIMIT) {
      return {
        ok: false,
        reason: 'over_word_limit',
        detail: `המודעה חורגת מ-${FREE_WORD_LIMIT} מילים (${wc} מילים). בעתיד ניתן יהיה לשדרג.`,
      };
    }
  }

  const replyToken = type === 'question' ? crypto.randomBytes(6).toString('hex') : null;

  const stmt = db.prepare(`
    INSERT INTO queue_items
      (topic_id, type, parent_id, sender_email, sender_name, subject, body, word_count, reply_token)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  const result = stmt.run(
    topic.id,
    type,
    parentQuestion ? parentQuestion.id : null,
    payload.from,
    payload.fromName || null,
    payload.subject || null,
    body,
    wc,
    replyToken
  );

  if (type === 'classified') incrementRateLimit(payload.from, topic.id);

  return { ok: true, queueItemId: result.lastInsertRowid, type, topic: topic.name };
}

module.exports = { processInboundEmail, countWords, weekKey, FREE_WORD_LIMIT, FREE_ADS_PER_WEEK };
