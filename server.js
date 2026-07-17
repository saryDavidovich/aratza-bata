const express = require('express');
const path = require('path');
const crypto = require('crypto');
const db = require('./lib/db');
const { processInboundEmail } = require('./lib/emailParser');
const { compileDigest, sendDigest } = require('./lib/digest');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'views')));

// ---------- Webhook: מייל נכנס (שאלה / תגובה / מודעה) ----------
// בפרודקשן: זו הכתובת שמחוברת ל-Inbound Parse של Postmark/Mailgun/SendGrid
app.post('/webhook/inbound-email', (req, res) => {
  const result = processInboundEmail(req.body);
  if (!result.ok) return res.status(400).json(result);
  res.json(result);
});

// ---------- טופס ציבורי: פרסום מודעה בלוח המילים (ללא מייל) ----------
app.post('/api/classifieds/submit', (req, res) => {
  const { senderEmail, senderName, body } = req.body;
  if (!senderEmail || !body) {
    return res.status(400).json({ ok: false, reason: 'missing_fields' });
  }
  const result = processInboundEmail({
    to: 'ads@yourdomain.com',
    from: senderEmail,
    fromName: senderName,
    subject: 'מודעה מטופס ציבורי',
    textBody: body,
  });
  if (!result.ok) return res.status(400).json(result);
  res.json(result);
});

// ---------- ניהול נושאים ----------
app.get('/api/topics', (req, res) => {
  res.json(db.prepare('SELECT * FROM topics ORDER BY id').all());
});

app.post('/api/topics', (req, res) => {
  const { name, slug, inboundEmail, themeColor } = req.body;
  if (!name || !slug || !inboundEmail) {
    return res.status(400).json({ ok: false, reason: 'missing_fields' });
  }
  const result = db.prepare(`
    INSERT INTO topics (name, slug, inbound_email, theme_color)
    VALUES (?, ?, ?, ?)
  `).run(name, slug, inboundEmail, themeColor || '#1D9E75');
  res.json({ ok: true, topicId: result.lastInsertRowid });
});

// ---------- הרשמה לרשימת תפוצה ----------
app.post('/api/subscribe', (req, res) => {
  const { email, topicId } = req.body;
  if (!email || !topicId) return res.status(400).json({ ok: false, reason: 'missing_fields' });
  const token = crypto.randomBytes(8).toString('hex');
  try {
    db.prepare(`
      INSERT INTO subscribers (email, topic_id, unsubscribe_token) VALUES (?, ?, ?)
    `).run(email, topicId, token);
    res.json({ ok: true });
  } catch (e) {
    res.json({ ok: true, note: 'כבר רשום' });
  }
});

app.post('/api/unsubscribe', (req, res) => {
  const { token } = req.body;
  db.prepare('DELETE FROM subscribers WHERE unsubscribe_token = ?').run(token);
  res.json({ ok: true });
});

// ---------- פאנל ניהול: תור ההמתנה ----------
app.get('/api/queue', (req, res) => {
  const status = req.query.status || 'pending';
  const rows = db.prepare(`
    SELECT q.*, t.name AS topic_name
    FROM queue_items q JOIN topics t ON t.id = q.topic_id
    WHERE q.status = ?
    ORDER BY q.received_at ASC
  `).all(status);
  res.json(rows);
});

app.put('/api/queue/:id', (req, res) => {
  const { body, subject } = req.body;
  db.prepare('UPDATE queue_items SET body = COALESCE(?, body), subject = COALESCE(?, subject) WHERE id = ?')
    .run(body, subject, req.params.id);
  res.json({ ok: true });
});

app.post('/api/queue/:id/approve', (req, res) => {
  db.prepare(`UPDATE queue_items SET status = 'approved', approved_at = CURRENT_TIMESTAMP WHERE id = ?`)
    .run(req.params.id);
  res.json({ ok: true });
});

app.post('/api/queue/:id/reject', (req, res) => {
  db.prepare(`UPDATE queue_items SET status = 'rejected' WHERE id = ?`).run(req.params.id);
  res.json({ ok: true });
});

// ---------- דיגסט שבועי ----------
app.post('/api/digest/:topicId/compile', (req, res) => {
  try {
    const result = compileDigest(req.params.topicId);
    res.json({ ok: true, ...result });
  } catch (e) {
    res.status(400).json({ ok: false, reason: e.message });
  }
});

app.get('/api/digest/:digestId/preview', (req, res) => {
  const digest = db.prepare('SELECT * FROM digests WHERE id = ?').get(req.params.digestId);
  if (!digest) return res.status(404).send('לא נמצא');
  res.send(digest.html_content);
});

app.post('/api/digest/:digestId/send', async (req, res) => {
  try {
    const result = await sendDigest(req.params.digestId);
    res.json({ ok: true, ...result });
  } catch (e) {
    res.status(400).json({ ok: false, reason: e.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`השרת רץ על http://localhost:${PORT}`);
  console.log(`פאנל ניהול: http://localhost:${PORT}/admin.html`);
  console.log(`טופס מודעה: http://localhost:${PORT}/submit.html`);
});
