const TZ = 'Asia/Jerusalem';

const DOW_NAMES = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת'];

// שם היום לפי המספר ששמור ב-DB (0=ראשון...6=שבת, כמו שדה יום-בשבוע של cron).
function dayName(dow) {
  return DOW_NAMES[dow] ?? '';
}

// הרכיבים הנוכחיים של הזמן בישראל (יום בשבוע, שעה, דקה, ותאריך כמחרוזת
// למניעת שליחה כפולה) - Intl.DateTimeFormat עם timeZone מטפל אוטומטית
// במעבר לשעון קיץ/חורף, בלי צורך בשום חישוב ידני.
function nowIsraelParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, weekday: 'short', hour: '2-digit', minute: '2-digit',
    hour12: false, year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(date);

  const map = {};
  parts.forEach(p => { map[p.type] = p.value; });

  const weekdayIndex = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[map.weekday];
  const hour = map.hour === '24' ? 0 : parseInt(map.hour, 10); // en-US hour12:false לפעמים מחזיר '24' בחצות
  const minute = parseInt(map.minute, 10);
  const dateStr = `${map.year}-${map.month}-${map.day}`;

  return { dow: weekdayIndex, hour, minute, dateStr };
}

// הופכת timestamp שמור ב-DB (תמיד ב-UTC, כי זה מה ש-SQLite datetime('now')
// מחזיר) לתצוגה קריאה בעברית לפי שעון ישראל - למשל "יום חמישי, 09:00".
function formatIsraelDateTime(utcString) {
  if (!utcString) return '';
  const date = new Date(utcString.replace(' ', 'T') + 'Z');
  if (isNaN(date.getTime())) return utcString;

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, weekday: 'short', hour: '2-digit', minute: '2-digit',
    hour12: false, year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(date);

  const map = {};
  parts.forEach(p => { map[p.type] = p.value; });
  const hour = map.hour === '24' ? '00' : map.hour;
  const dowHe = { Sun: 'ראשון', Mon: 'שני', Tue: 'שלישי', Wed: 'רביעי', Thu: 'חמישי', Fri: 'שישי', Sat: 'שבת' }[map.weekday];

  return `יום ${dowHe}, ${map.day}/${map.month}/${map.year} · ${hour}:${map.minute}`;
}

module.exports = { TZ, dayName, DOW_NAMES, nowIsraelParts, formatIsraelDateTime };
