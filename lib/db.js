const Database = require('better-sqlite3');
const fs = require('fs');
const path = require('path');

const DB_PATH = path.join(__dirname, '..', 'data', 'app.db');
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

const schema = fs.readFileSync(path.join(__dirname, '..', 'schema.sql'), 'utf8');
db.exec(schema);

// נתוני הדגמה ראשוניים - נוצרים רק אם אין עדיין נושאים
const topicCount = db.prepare('SELECT COUNT(*) AS c FROM topics').get().c;
if (topicCount === 0) {
  const insertTopic = db.prepare(`
    INSERT INTO topics (name, slug, inbound_email, theme_color)
    VALUES (?, ?, ?, ?)
  `);
  insertTopic.run('הורות', 'parenting', 'parenting@yourdomain.com', '#1D9E75');
  insertTopic.run('בריאות ותזונה', 'health', 'health@yourdomain.com', '#378ADD');
  insertTopic.run('לוח מילים חינמי', 'classifieds', 'ads@yourdomain.com', '#D85A30');
}

module.exports = db;
