const db = require('./db');

function escapeHtml(str) {
  return (str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderQuestionBlock(q, answers) {
  const answersHtml = answers.map(a => `
    <div style="margin-top:10px;padding:12px 16px;background:#f6f5f1;border-radius:8px;">
      <div style="font-size:13px;color:#5f5e5a;margin-bottom:4px;">תגובה:</div>
      <div style="font-size:15px;line-height:1.6;">${escapeHtml(a.body)}</div>
    </div>
  `).join('');

  const replyLink = `mailto:reply+${q.reply_token}@yourdomain.com?subject=${encodeURIComponent('Re: ' + (q.subject || 'שאלה'))}`;

  return `
    <div style="margin-bottom:24px;padding:18px 20px;border:1px solid #e3e1d8;border-radius:12px;">
      <div style="font-size:16px;font-weight:500;margin-bottom:6px;">שאלה: ${escapeHtml(q.subject || '')}</div>
      <div style="font-size:15px;line-height:1.6;">${escapeHtml(q.body)}</div>
      ${answersHtml}
      <div style="margin-top:12px;">
        <a href="${replyLink}" style="font-size:13px;color:#185fa5;text-decoration:none;">← להגיב לשאלה הזו במייל</a>
      </div>
    </div>
  `;
}

function renderClassifiedBlock(c) {
  // גודל התצוגה בעתיד ישתנה לפי is_paid_tier - כרגע כולם באותו גודל
  return `
    <div style="margin-bottom:14px;padding:14px 16px;background:#fff;border:1px solid #e3e1d8;border-radius:10px;">
      <div style="font-size:14px;line-height:1.6;">${escapeHtml(c.body)}</div>
      <div style="font-size:12px;color:#888780;margin-top:6px;">נשלח על ידי ${escapeHtml(c.sender_name || c.sender_email)}</div>
    </div>
  `;
}

function compileDigest(topicId) {
  const topic = db.prepare('SELECT * FROM topics WHERE id = ?').get(topicId);
  if (!topic) throw new Error('נושא לא נמצא');

  const questions = db.prepare(`
    SELECT * FROM queue_items
    WHERE topic_id = ? AND type = 'question' AND status = 'approved'
    ORDER BY approved_at ASC
  `).all(topicId);

  const classifieds = db.prepare(`
    SELECT * FROM queue_items
    WHERE topic_id = ? AND type = 'classified' AND status = 'approved'
    ORDER BY approved_at ASC
  `).all(topicId);

  const questionBlocks = questions.map(q => {
    const answers = db.prepare(`
      SELECT * FROM queue_items WHERE parent_id = ? AND type = 'answer' AND status = 'approved'
      ORDER BY approved_at ASC
    `).all(q.id);
    return renderQuestionBlock(q, answers);
  }).join('');

  const classifiedBlocks = classifieds.map(renderClassifiedBlock).join('');

  const html = `
  <div style="font-family: -apple-system, Arial, sans-serif; max-width:600px; margin:0 auto; color:#2c2c2a;">
    <div style="background:${topic.theme_color}; padding:24px 20px; border-radius:12px 12px 0 0;">
      <div style="color:#fff; font-size:20px; font-weight:500;">${escapeHtml(topic.name)}</div>
      <div style="color:#ffffffcc; font-size:13px; margin-top:4px;">עדכון שבועי</div>
    </div>
    <div style="padding:20px;">
      ${questionBlocks || '<div style="color:#888780;font-size:14px;">אין שאלות חדשות השבוע.</div>'}
      ${classifiedBlocks ? `<h3 style="font-size:16px;font-weight:500;margin-top:24px;">לוח מודעות</h3>${classifiedBlocks}` : ''}
    </div>
    <div style="padding:16px 20px; font-size:12px; color:#888780; border-top:1px solid #e3e1d8;">
      <a href="#" style="color:#888780;">להסרה מרשימת התפוצה</a>
    </div>
  </div>`;

  const result = db.prepare(`
    INSERT INTO digests (topic_id, html_content, status) VALUES (?, ?, 'draft')
  `).run(topicId, html);

  return { digestId: result.lastInsertRowid, html, questionCount: questions.length, classifiedCount: classifieds.length };
}

// שליחה בפועל - כרגע מדומה. בהמשך יש לחבר כאן קריאה אמיתית לספק המייל (Postmark/Mailgun/SendGrid)
async function sendDigest(digestId) {
  const digest = db.prepare('SELECT * FROM digests WHERE id = ?').get(digestId);
  if (!digest) throw new Error('דיגסט לא נמצא');

  const subscribers = db.prepare('SELECT * FROM subscribers WHERE topic_id = ?').all(digest.topic_id);

  // TODO: כאן מתבצעת קריאה אמיתית ל-API של ספק המייל, לכל מנוי או ב-batch
  console.log(`[MOCK SEND] שולח דיגסט ${digestId} ל-${subscribers.length} מנויים`);

  db.prepare(`UPDATE digests SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?`).run(digestId);
  db.prepare(`
    UPDATE queue_items SET status = 'sent'
    WHERE topic_id = ? AND status = 'approved'
  `).run(digest.topic_id);

  return { sentTo: subscribers.length };
}

module.exports = { compileDigest, sendDigest };
