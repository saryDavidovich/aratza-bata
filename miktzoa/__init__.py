"""
מקצוע ביד - אתר שמחבר בין בעלי מקצוע שמלמדים לבין מי שרוצה ללמוד (/miktzoa).

הדפים עצמם הם אפליקציה אחת בדפדפן (app.html). כל הנתונים והכללים יושבים כאן בשרת:
  GET  /miktzoa/                 הדפים
  GET  /miktzoa/api/state        הנתונים שהמשתמש הנוכחי מורשה לראות
  POST /miktzoa/api/<פעולה>      כל פעולה (פנייה, פתיחת ליד, הודעה, קורס, ניהול...)
  POST /miktzoa/pay/callback/... עדכון תשלום מנדרים פלוס (שרת לשרת)

כללים שנאכפים בשרת (לא רק בדפדפן):
  - פנייה, פתיחת בקשה, הודעות ושיעורים: רק גבר-גבר ואישה-אישה.
  - פרטי קשר של תלמיד נחשפים למורה רק אחרי שפתח את הליד ושילם עליו מהיתרה.
  - מחיר ליד נקבע לפי התחום של הקורס (המנהל קובע).

הגדרות (משתני סביבה, לא חובה):
  MIKTZOA_ADMIN_EMAILS   מיילים של מנהלים (ברירת מחדל: LAB_DEFAULT_EMAIL)
  MIKTZOA_DATA_DIR       תיקיית נתונים (ברירת מחדל: /data אם קיימת)
  MIKTZOA_CATEGORY       קטגוריה בנדרים פלוס (ברירת מחדל: "מקצוע ביד")
  NEDARIM_MOSAD / NEDARIM_API_VALID / NEDARIM_WEBHOOK_SECRET   כמו בלוח המודעות
  SENDGRID_API_KEY / SENDGRID_FROM_EMAIL   מיילים (אימות, שחזור סיסמה, התראות)
"""
import os
import io
import json
import time
import uuid
import hmac
import hashlib
import secrets
import sqlite3
import logging
import functools
from datetime import datetime, timedelta

from flask import Blueprint, request, session, jsonify, abort, send_from_directory, g, Response, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

log = logging.getLogger(__name__)
bp = Blueprint('miktzoa', __name__, url_prefix='/miktzoa')

HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DIR = '/data' if os.path.isdir('/data') else os.path.dirname(HERE)
DATA_DIR = os.environ.get('MIKTZOA_DATA_DIR', _DEFAULT_DIR)
DB_PATH = os.path.join(DATA_DIR, 'miktzoa.db')
FILES_DIR = os.path.join(DATA_DIR, 'miktzoa_files')

PACKS = [{'amount': 100, 'bonus': 0}, {'amount': 250, 'bonus': 15}, {'amount': 500, 'bonus': 50, 'pop': True}, {'amount': 1000, 'bonus': 150}]
DEFAULT_SETTINGS = {'maxOpensPerRequest': 3, 'highlightPrice': 30, 'highlightDays': 30, 'leadExpiryHours': 48}
MODES = {'front', 'video', 'phone'}
TRACKS = {'practical', 'exam', 'mentor'}
LEVELS = {'begin', 'adv'}
SLEVEL = {'none', 'some', 'working'}
STARTS = {'week', 'month', 'flex'}
PROGRESS = {'', 'contacted', 'scheduled', 'learning', 'unreachable'}
NEDARIM_CREATE_URL = 'https://matara.pro/nedarimplus/V6/Files/WebServices/DebitIframe.aspx?Action=CreateTransaction'
NEDARIM_IFRAME = 'https://www.matara.pro/nedarimplus/iframe/v3/'
NEDARIM_IPS = {'18.196.146.117', '18.194.219.73'}
DEFAULT_FIELDS = [('חשמלאות', 1, 35), ('אינסטלציה', 0, 25), ('הנהלת חשבונות', 0, 30), ('מיזוג אוויר', 0, 30),
                  ('תיקון מחשבים', 0, 20), ('עיצוב גרפי', 0, 25), ('אפייה מקצועית', 0, 20), ('תפירה ותיקוני בגדים', 0, 15),
                  ('שיפוצים וגבס', 0, 25), ('נהיגה', 1, 40), ('עריכת וידאו', 0, 20), ('גננות ופיתוח הגן', 0, 15)]


class ApiError(Exception):
    pass


def fail(msg):
    raise ApiError(msg)


# ------------------------------------------------------------------
# db
# ------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT, gender TEXT, email TEXT UNIQUE, phone TEXT, city TEXT,
  password_hash TEXT, role TEXT DEFAULT 'student', wallet INTEGER DEFAULT 0, news INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,
  phone_verified INTEGER DEFAULT 0, email_verified INTEGER DEFAULT 0, teacher TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS fields (id TEXT PRIMARY KEY, name TEXT, regulated INTEGER DEFAULT 0, lead_price INTEGER, sort INTEGER);
CREATE TABLE IF NOT EXISTS courses (id TEXT PRIMARY KEY, teacher_id TEXT, status TEXT, highlighted_until TEXT, views INTEGER DEFAULT 0,
  created_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, course_id TEXT, request_id TEXT, student_id TEXT, teacher_id TEXT, data TEXT,
  status TEXT, progress TEXT DEFAULT '', price INTEGER, created_at TEXT, opened_at TEXT);
CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, student_id TEXT, field_id TEXT, data TEXT, opens TEXT DEFAULT '[]',
  status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, lead_id TEXT, sender TEXT, recipient TEXT, text TEXT, file TEXT,
  file_name TEXT, at TEXT, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS lessons (id TEXT PRIMARY KEY, lead_id TEXT, at TEXT, duration INTEGER, topic TEXT, done TEXT DEFAULT '[]', room TEXT);
CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, course_id TEXT, student_id TEXT, lead_id TEXT, stars INTEGER, delivered TEXT,
  patience INTEGER, punctual INTEGER, text TEXT, reply TEXT DEFAULT '', at TEXT, hidden INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ledger (id TEXT PRIMARY KEY, teacher_id TEXT, kind TEXT, amount INTEGER, at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE, teacher_id TEXT, amount INTEGER,
  credit INTEGER, nedarim_id TEXT, nedarim_key TEXT, status TEXT DEFAULT 'created', confirmation TEXT, last_error TEXT,
  verified_by TEXT, raw TEXT, created_at TEXT, paid_at TEXT);
CREATE TABLE IF NOT EXISTS lead_reports (id TEXT PRIMARY KEY, lead_id TEXT, reporter TEXT, reason TEXT, text TEXT, status TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, owner_id TEXT, kind TEXT, lead_id TEXT, original TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, user_id TEXT, kind TEXT, expires TEXT);
CREATE TABLE IF NOT EXISTS rtc (id INTEGER PRIMARY KEY AUTOINCREMENT, lesson_id TEXT, sender TEXT, type TEXT, data TEXT, at REAL);
CREATE INDEX IF NOT EXISTS leads_teacher ON leads(teacher_id);
CREATE INDEX IF NOT EXISTS leads_student ON leads(student_id);
CREATE INDEX IF NOT EXISTS msgs_lead ON messages(lead_id);
"""


def db():
    if 'mk_db' not in g:
        c = sqlite3.connect(DB_PATH, timeout=15)
        c.row_factory = sqlite3.Row
        g.mk_db = c
    return g.mk_db


def _close(exc=None):
    c = g.pop('mk_db', None)
    if c is not None:
        c.close()


def now():
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def nid(p):
    return p + secrets.token_hex(5)


def J(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except ValueError:
        return default if default is not None else {}


def _init_db():
    os.makedirs(FILES_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    if c.execute('SELECT COUNT(*) FROM fields').fetchone()[0] == 0:
        for i, (name, reg, price) in enumerate(DEFAULT_FIELDS):
            c.execute('INSERT INTO fields VALUES (?,?,?,?,?)', (f'f{i + 1}', name, reg, price, i))
    c.commit()
    c.close()


def settings():
    s = dict(DEFAULT_SETTINGS)
    for r in db().execute('SELECT key, value FROM settings'):
        try:
            s[r['key']] = int(r['value'])
        except ValueError:
            pass
    return s


def _admin_emails():
    raw = os.environ.get('MIKTZOA_ADMIN_EMAILS') or os.environ.get('LAB_DEFAULT_EMAIL', '')
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def _mail_ready():
    return bool(os.environ.get('SENDGRID_API_KEY') and os.environ.get('SENDGRID_FROM_EMAIL'))


def _nedarim_ready():
    return bool(os.environ.get('NEDARIM_MOSAD') and os.environ.get('NEDARIM_API_VALID'))


def _base_url():
    return (os.environ.get('APP_BASE_URL') or request.url_root).rstrip('/')


def send_mail(to, subject, title, text, link=None, button=None):
    if not to or not _mail_ready():
        return False
    btn = f'<p><a href="{link}" style="display:inline-block;background:#0B6E99;color:#fff;padding:11px 24px;border-radius:8px;text-decoration:none">{button}</a></p>' if link else ''
    html = f'<div dir="rtl" style="font-family:Arial,sans-serif;max-width:520px;margin:auto;color:#102331"><h2>{title}</h2><p style="line-height:1.6">{text}</p>{btn}<p style="color:#667;font-size:13px">מקצוע ביד</p></div>'
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        sg.send(Mail(from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מקצוע ביד'), to_emails=to, subject=subject, html_content=html))
        return True
    except Exception as e:
        log.error(f'miktzoa mail error: {e}')
        return False


def link(hash_):
    return _base_url() + url_for('miktzoa.index') + hash_


# ------------------------------------------------------------------
# users & session
# ------------------------------------------------------------------
def user_row(uid):
    return db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() if uid else None


def me():
    if 'mk_me' in g:
        return g.mk_me
    r = user_row(session.get('mk_uid'))
    if r and r['blocked']:
        r = None
    g.mk_me = r
    return r


def is_admin(u):
    return bool(u) and (u['role'] == 'admin' or (u['email'] or '').lower() in _admin_emails())


def teacher_of(u):
    return J(u['teacher'], None) if u and u['teacher'] else None


def is_teacher(u):
    t = teacher_of(u)
    return bool(t) and t.get('status') == 'approved' and not u['blocked']


def need_me():
    u = me()
    if not u:
        fail('צריך להתחבר')
    return u


def need_teacher():
    u = need_me()
    if not is_teacher(u):
        fail('הפעולה למורים מאושרים בלבד')
    return u


def need_admin():
    u = need_me()
    if not is_admin(u):
        fail('אזור מנהלים בלבד')
    return u


def short(name):
    p = (name or '').split()
    return (p[0] + (' ' + p[1][0] + '.' if len(p) > 1 else '')) if p else ''


def ledger(teacher_id, kind, amount, note):
    db().execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)', (nid('g'), teacher_id, kind, amount, now(), note))


def csrf_token():
    if 'mk_csrf' not in session:
        session['mk_csrf'] = secrets.token_urlsafe(24)
    return session['mk_csrf']


@bp.before_request
def _csrf():
    if request.method == 'POST' and not request.path.startswith('/miktzoa/pay/callback/'):
        sent = request.headers.get('X-CSRF') or request.form.get('csrf') or ''
        if not sent or not secrets.compare_digest(sent, session.get('mk_csrf', '')):
            return jsonify(ok=False, error='פג תוקף הדף. רענן ונסה שוב.'), 400


_fails = {}


def _client_ip():
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (request.remote_addr or '')


# ------------------------------------------------------------------
# what the current user may see
# ------------------------------------------------------------------
def course_out(r, fields_by_id):
    d = J(r['data'])
    hl = bool(r['highlighted_until'] and r['highlighted_until'] > now())
    d.update(id=r['id'], teacherId=r['teacher_id'], status=r['status'], highlighted=hl, views=r['views'], createdAt=r['created_at'])
    return d


def teacher_public(u, full=False):
    t = teacher_of(u) or {}
    pub = {k: t.get(k) for k in ('status', 'verified', 'years', 'workplace', 'about', 'availability', 'modes', 'areas', 'fields', 'joined')}
    pub['credentials'] = [{'name': c.get('name'), 'pending': c.get('pending', False)} for c in t.get('credentials', []) if full or not c.get('pending')]
    if full:
        pub['otherField'] = t.get('otherField', '')
        pub['credentials'] = [{'name': c.get('name'), 'pending': c.get('pending', False),
                               'fileUrl': url_for('miktzoa.file', name=c['file']) if c.get('file') else ''} for c in t.get('credentials', [])]
        pub['rejectReason'] = t.get('rejectReason', '')
    return pub


def expire_leads():
    hours = settings()['leadExpiryHours']
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat(timespec='seconds') + 'Z'
    db().execute("UPDATE leads SET status='expired' WHERE status='new' AND created_at < ?", (cutoff,))


def build_state():
    c = db()
    expire_leads()
    c.commit()
    u = me()
    admin = is_admin(u)
    uid = u['id'] if u else None
    fields = [dict(id=r['id'], name=r['name'], regulated=bool(r['regulated']), leadPrice=r['lead_price']) for r in c.execute('SELECT * FROM fields ORDER BY sort, name')]
    fmap = {f['id']: f for f in fields}
    users = {}

    def add_user(r, level):
        """level: 'public' (teachers), 'min' (name+gender), 'contact' (+phone/email), 'self', 'admin'"""
        if not r:
            return
        cur = users.get(r['id'])
        rank = ['min', 'public', 'contact', 'self', 'admin']
        if cur and rank.index(cur['_lvl']) >= rank.index(level):
            return
        d = {'id': r['id'], 'gender': r['gender'], 'city': r['city'], '_lvl': level}
        d['name'] = r['name'] if level != 'min' else short(r['name'])
        if r['teacher'] and level in ('public', 'contact', 'self', 'admin'):
            d['teacher'] = teacher_public(r, full=level in ('self', 'admin'))
        if level in ('contact', 'self', 'admin'):
            d.update(phone=r['phone'], email=r['email'])
        if level in ('self', 'admin'):
            d.update(wallet=r['wallet'], news=bool(r['news']), phoneVerified=bool(r['phone_verified']),
                     role='admin' if is_admin(r) else r['role'], blocked=bool(r['blocked']))
        users[r['id']] = d

    for r in c.execute("SELECT * FROM users WHERE teacher IS NOT NULL AND blocked=0"):
        if (teacher_of(r) or {}).get('status') == 'approved':
            add_user(r, 'public')
    # courses
    if admin:
        crow = c.execute('SELECT * FROM courses').fetchall()
    else:
        crow = c.execute("SELECT * FROM courses WHERE status='active' OR teacher_id=?", (uid or '',)).fetchall()
    approved_ids = set(users.keys())
    courses = [course_out(r, fmap) for r in crow if admin or r['teacher_id'] == uid or r['teacher_id'] in approved_ids]
    # reviews (public)
    reviews = []
    for r in c.execute('SELECT * FROM reviews' + ('' if admin else ' WHERE hidden=0')):
        reviews.append(dict(id=r['id'], courseId=r['course_id'], studentId=r['student_id'], stars=r['stars'], delivered=r['delivered'],
                            patience=r['patience'], punctual=r['punctual'], text=r['text'], reply=r['reply'], at=r['at'], hidden=bool(r['hidden'])))
        add_user(user_row(r['student_id']), 'min')
    # requests (open ones public; mine all)
    reqs = []
    for r in c.execute("SELECT * FROM requests WHERE status='open' OR student_id=?", (uid or '',)):
        d = J(r['data'])
        opens = J(r['opens'], [])
        d.update(id=r['id'], studentId=r['student_id'], fieldId=r['field_id'], status=r['status'], createdAt=r['created_at'],
                 opensCount=len(opens), openedByMe=bool(uid and uid in opens))
        reqs.append(d)
        add_user(user_row(r['student_id']), 'min')
    leads, messages, lessons, ledger_rows, payments, reports = [], [], [], [], [], []
    if u:
        add_user(u, 'admin' if admin else 'self')
        for r in c.execute('SELECT * FROM leads WHERE teacher_id=? OR student_id=?', (uid, uid)):
            d = J(r['data'])
            d.update(id=r['id'], courseId=r['course_id'], requestId=r['request_id'], studentId=r['student_id'], teacherId=r['teacher_id'],
                     status=r['status'], progress=r['progress'], price=r['price'], createdAt=r['created_at'], openedAt=r['opened_at'])
            leads.append(d)
            if r['teacher_id'] == uid:
                stu = user_row(r['student_id'])
                add_user(stu, 'contact' if r['status'] == 'open' else 'min')
                if stu:
                    users[stu['id']]['phoneVerified'] = bool(stu['phone_verified'])
                    if r['status'] != 'open':
                        users[stu['id']]['name'] = (stu['name'] or '').split()[0] if stu['name'] else ''
            else:
                add_user(user_row(r['teacher_id']), 'public')
        open_ids = [l['id'] for l in leads if l['status'] == 'open']
        if open_ids:
            q = ','.join('?' * len(open_ids))
            for r in c.execute(f'SELECT * FROM messages WHERE lead_id IN ({q}) ORDER BY at', open_ids):
                messages.append(dict(id=r['id'], leadId=r['lead_id'], **{'from': r['sender']}, to=r['recipient'], text=r['text'],
                                     file=r['file_name'] or '', fileUrl=url_for('miktzoa.file', name=r['file']) if r['file'] else '',
                                     at=r['at'], read=bool(r['read'])))
            for r in c.execute(f'SELECT * FROM lessons WHERE lead_id IN ({q}) ORDER BY at', open_ids):
                lessons.append(dict(id=r['id'], leadId=r['lead_id'], at=r['at'], duration=r['duration'], topic=r['topic'], done=J(r['done'], []), room=r['room']))
        if u['teacher']:
            ledger_rows = [dict(id=r['id'], teacherId=r['teacher_id'], kind=r['kind'], amount=r['amount'], at=r['at'], note=r['note'])
                           for r in c.execute('SELECT * FROM ledger WHERE teacher_id=? ORDER BY at DESC', (uid,))]
        if admin:
            for r in c.execute('SELECT * FROM users'):
                add_user(r, 'admin')
            payments = [dict(id=r['id'], teacherId=r['teacher_id'], amount=r['amount'], credit=r['credit'], at=r['paid_at'] or r['created_at'],
                             ref=r['nedarim_id'], status=r['status']) for r in c.execute("SELECT * FROM payments WHERE status='paid' ORDER BY id DESC LIMIT 300")]
            for r in c.execute("SELECT * FROM lead_reports ORDER BY at DESC LIMIT 200"):
                reports.append(dict(id=r['id'], leadId=r['lead_id'], reason=r['reason'], text=r['text'], status=r['status'], at=r['at']))
                lr = c.execute('SELECT * FROM leads WHERE id=?', (r['lead_id'],)).fetchone()
                if lr and lr['id'] not in {l['id'] for l in leads}:
                    d = J(lr['data'])
                    d.update(id=lr['id'], courseId=lr['course_id'], requestId=lr['request_id'], studentId=lr['student_id'], teacherId=lr['teacher_id'],
                             status=lr['status'], progress=lr['progress'], price=lr['price'], createdAt=lr['created_at'], openedAt=lr['opened_at'])
                    leads.append(d)
    stats = {}
    if admin:
        day = (datetime.utcnow() - timedelta(days=1)).isoformat(timespec='seconds') + 'Z'
        month = (datetime.utcnow() - timedelta(days=30)).isoformat(timespec='seconds') + 'Z'
        stats = dict(leadsToday=c.execute('SELECT COUNT(*) FROM leads WHERE created_at>?', (day,)).fetchone()[0],
                     leadsMonth=c.execute('SELECT COUNT(*) FROM leads WHERE created_at>?', (month,)).fetchone()[0],
                     incomeMonth=c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='paid' AND paid_at>?", (month,)).fetchone()[0])
    for d in users.values():
        d.pop('_lvl', None)
    return dict(fields=fields, users=list(users.values()), courses=courses, reviews=reviews, requests=reqs, leads=leads,
                messages=messages, lessons=lessons, ledger=ledger_rows, payments=payments, leadReports=reports,
                settings=settings(), packs=PACKS, payOnline=_nedarim_ready(), stats=stats, ice=_ice_servers(),
                session={'userId': uid}, mailReady=_mail_ready())


# ------------------------------------------------------------------
# page + api plumbing
# ------------------------------------------------------------------
@bp.route('/')
def index():
    with open(os.path.join(HERE, 'app.html'), encoding='utf-8') as f:
        html = f.read()
    html = html.replace('__CSRF__', csrf_token()).replace('__BASE__', url_for('miktzoa.index').rstrip('/'))
    resp = Response(html, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@bp.route('/api/state')
def api_state():
    return jsonify(build_state())


ACTIONS = {}


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


@bp.route('/api/<path:name>', methods=['POST'])
def api(name):
    fn = ACTIONS.get(name)
    if not fn:
        return jsonify(ok=False, error='פעולה לא קיימת'), 404
    body = request.get_json(silent=True)
    if body is None:
        body = J(request.form.get('json'), {})
    try:
        extra = fn(body) or {}
        db().commit()
    except ApiError as e:
        db().rollback()
        return jsonify(ok=False, error=str(e)), 400
    g.pop('mk_me', None)
    return jsonify(ok=True, state=build_state(), **extra)


def txt(v, n):
    return (str(v or '')).strip()[:n]


def num(v, lo=0, hi=10 ** 7):
    try:
        x = int(float(v))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, x))


# ------------------------------------------------------------------
# files (credentials, message attachments)
# ------------------------------------------------------------------
ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf', '.heic'}


def save_upload(fs, owner, kind, lead_id=None):
    if not fs or not fs.filename:
        return None
    ext = os.path.splitext(fs.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        fail('אפשר לצרף רק תמונה או PDF')
    data = fs.read()
    if len(data) > 10 * 1024 * 1024:
        fail('הקובץ גדול מ-10MB')
    name = uuid.uuid4().hex + ext
    with open(os.path.join(FILES_DIR, name), 'wb') as f:
        f.write(data)
    db().execute('INSERT INTO files VALUES (?,?,?,?,?,?)', (name, owner, kind, lead_id, txt(fs.filename, 120), now()))
    return name


@bp.route('/file/<name>')
def file(name):
    r = db().execute('SELECT * FROM files WHERE name=?', (name,)).fetchone()
    u = me()
    if not r or not u:
        abort(404)
    ok = is_admin(u) or r['owner_id'] == u['id']
    if r['lead_id']:
        l = db().execute('SELECT * FROM leads WHERE id=?', (r['lead_id'],)).fetchone()
        ok = ok or (l and u['id'] in (l['teacher_id'], l['student_id']) and l['status'] == 'open')
    if not ok:
        abort(403)
    return send_from_directory(FILES_DIR, name, download_name=r['original'] or name)


# ------------------------------------------------------------------
# auth
# ------------------------------------------------------------------
@action('auth/register')
def a_register(b):
    name, email, phone = txt(b.get('name'), 60), txt(b.get('email'), 120).lower(), txt(b.get('phone'), 20)
    gender, city, pw = b.get('gender'), txt(b.get('city'), 40), str(b.get('password') or '')
    if not name or gender not in ('m', 'f') or '@' not in email or not city:
        fail('צריך שם, מגדר, מייל ועיר')
    digits = ''.join(ch for ch in phone if ch.isdigit())
    if not (len(digits) == 10 and digits.startswith('05')):
        fail('מספר נייד לא תקין (05XXXXXXXX)')
    if len(pw) < 8:
        fail('סיסמה של 8 תווים לפחות')
    if not b.get('terms'):
        fail('צריך לאשר את התקנון')
    if db().execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
        fail('המייל כבר רשום. אפשר להתחבר או לשחזר סיסמה.')
    uid = nid('u')
    db().execute('INSERT INTO users (id,name,gender,email,phone,city,password_hash,role,wallet,news,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                 (uid, name, gender, email, digits, city, generate_password_hash(pw), 'admin' if email in _admin_emails() else 'student', 0,
                  1 if b.get('news') else 0, now()))
    session['mk_uid'] = uid
    session.permanent = True


@action('auth/login')
def a_login(b):
    ip = _client_ip()
    lst = [x for x in _fails.get(ip, []) if time.time() - x < 600]
    _fails[ip] = lst
    if len(lst) >= 10:
        fail('יותר מדי ניסיונות. נסה שוב בעוד כמה דקות.')
    email = txt(b.get('email'), 120).lower()
    r = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not r or not r['password_hash'] or not check_password_hash(r['password_hash'], str(b.get('password') or '')):
        _fails.setdefault(ip, []).append(time.time())
        fail('המייל או הסיסמה לא נכונים')
    if r['blocked']:
        fail('החשבון חסום. פנה למנהל האתר.')
    session['mk_uid'] = r['id']
    session.permanent = True
    g.pop('mk_me', None)


@action('auth/logout')
def a_logout(b):
    session.pop('mk_uid', None)
    g.pop('mk_me', None)


@action('auth/forgot')
def a_forgot(b):
    email = txt(b.get('email'), 120).lower()
    r = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not _mail_ready():
        fail('שחזור סיסמה במייל עדיין לא הופעל. פנה למנהל האתר.')
    if r:
        tok = secrets.token_urlsafe(32)
        db().execute('INSERT INTO tokens VALUES (?,?,?,?)', (tok, r['id'], 'reset', (datetime.utcnow() + timedelta(hours=2)).isoformat()))
        send_mail(email, 'שחזור סיסמה - מקצוע ביד', 'שחזור סיסמה', 'קיבלנו בקשה לבחור סיסמה חדשה. הקישור בתוקף לשעתיים.',
                  link('#/reset?token=' + tok), 'בחירת סיסמה חדשה')


@action('auth/reset')
def a_reset(b):
    tok = str(b.get('token') or '')
    r = db().execute("SELECT * FROM tokens WHERE token=? AND kind='reset'", (tok,)).fetchone()
    if not r or r['expires'] < datetime.utcnow().isoformat():
        fail('הקישור פג או אינו תקין')
    pw = str(b.get('password') or '')
    if len(pw) < 8:
        fail('סיסמה של 8 תווים לפחות')
    db().execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(pw), r['user_id']))
    db().execute('DELETE FROM tokens WHERE token=?', (tok,))
    session['mk_uid'] = r['user_id']


@action('profile/update')
def a_profile(b):
    u = need_me()
    email = txt(b.get('email'), 120).lower() or u['email']
    if email != u['email'] and db().execute('SELECT 1 FROM users WHERE email=? AND id<>?', (email, u['id'])).fetchone():
        fail('המייל כבר בשימוש')
    digits = ''.join(ch for ch in txt(b.get('phone'), 20) if ch.isdigit()) or u['phone']
    db().execute('UPDATE users SET name=?, phone=?, email=?, city=?, news=?, phone_verified=CASE WHEN phone=? THEN phone_verified ELSE 0 END WHERE id=?',
                 (txt(b.get('name'), 60) or u['name'], digits, email, txt(b.get('city'), 40) or u['city'], 1 if b.get('news') else 0, digits, u['id']))


@action('profile/password')
def a_password(b):
    u = need_me()
    if not check_password_hash(u['password_hash'], str(b.get('old') or '')):
        fail('הסיסמה הנוכחית לא נכונה')
    if len(str(b.get('new') or '')) < 8:
        fail('סיסמה חדשה של 8 תווים לפחות')
    db().execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(str(b['new'])), u['id']))


# ------------------------------------------------------------------
# leads (student -> teacher)
# ------------------------------------------------------------------
def same_gender_or_fail(a, b):
    if not a or not b or a['gender'] != b['gender']:
        fail('פנייה אפשרית רק בין גבר לגבר ואישה לאישה')


@action('lead/create')
def a_lead_create(b):
    u = need_me()
    c = db().execute("SELECT * FROM courses WHERE id=? AND status='active'", (b.get('courseId'),)).fetchone()
    if not c:
        fail('הקורס לא זמין')
    t = user_row(c['teacher_id'])
    if not is_teacher(t):
        fail('המורה לא זמין כרגע')
    if t['id'] == u['id']:
        fail('זה הקורס שלך')
    same_gender_or_fail(u, t)
    if db().execute("SELECT 1 FROM leads WHERE course_id=? AND student_id=? AND status IN ('new','open')", (c['id'], u['id'])).fetchone():
        fail('כבר שלחת פנייה לקורס הזה')
    cd = J(c['data'])
    mode = b.get('mode') if b.get('mode') in cd.get('modes', []) else (cd.get('modes') or ['front'])[0]
    contact = [x for x in (b.get('contact') or []) if x in ('phone', 'whatsapp', 'site')]
    want = txt(b.get('want'), 300)
    if not want or not contact or b.get('level') not in SLEVEL:
        fail('חסרים פרטים בפנייה')
    f = db().execute('SELECT lead_price FROM fields WHERE id=?', (cd.get('fieldId'),)).fetchone()
    price = f['lead_price'] if f else 25
    data = dict(want=want, level=b['level'], mode=mode, start=b.get('start') if b.get('start') in STARTS else 'flex',
                hours=txt(b.get('hours'), 80), contact=contact)
    db().execute('INSERT INTO leads (id,course_id,request_id,student_id,teacher_id,data,status,progress,price,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (nid('l'), c['id'], None, u['id'], t['id'], json.dumps(data, ensure_ascii=False), 'new', '', price, now()))
    send_mail(t['email'], 'פנייה חדשה - מקצוע ביד', 'פנייה חדשה לקורס שלך',
              f"תלמיד חדש פנה לקורס \"{cd.get('title', '')}\". אפשר לראות את הפנייה ולפתוח אותה בלוח המורה.", link('#/leads'), 'לפנייה')


@action('lead/cancel')
def a_lead_cancel(b):
    u = need_me()
    db().execute("DELETE FROM leads WHERE id=? AND student_id=? AND status='new'", (b.get('id'), u['id']))


def own_lead(b, teacher=True):
    u = need_teacher() if teacher else need_me()
    l = db().execute('SELECT * FROM leads WHERE id=?', (b.get('id'),)).fetchone()
    if not l or (teacher and l['teacher_id'] != u['id']):
        fail('הליד לא נמצא')
    return u, l


@action('lead/unlock')
def a_lead_unlock(b):
    u, l = own_lead(b)
    if l['status'] != 'new':
        fail('אפשר לפתוח רק ליד חדש')
    stu = user_row(l['student_id'])
    same_gender_or_fail(u, stu)
    cur = db().execute('UPDATE users SET wallet = wallet - ? WHERE id=? AND wallet >= ?', (l['price'], u['id'], l['price']))
    if cur.rowcount != 1:
        fail('אין מספיק יתרה. אפשר לטעון בעמוד "יתרה ותשלומים".')
    db().execute("UPDATE leads SET status='open', opened_at=? WHERE id=? AND status='new'", (now(), l['id']))
    ledger(u['id'], 'lead', -l['price'], 'פתיחת ליד: ' + short(stu['name']))
    send_mail(stu['email'], 'המורה קיבל את הפנייה שלך', 'המורה קיבל את הפנייה',
              f"{u['name']} פתח את הפנייה שלך ויחזור אליך בקרוב. אפשר גם לכתוב לו הודעה באתר.", link('#/messages/' + l['id']), 'להודעות')


@action('lead/decline')
def a_lead_decline(b):
    u, l = own_lead(b)
    if l['status'] == 'new':
        db().execute("UPDATE leads SET status='declined' WHERE id=?", (l['id'],))


@action('lead/progress')
def a_lead_progress(b):
    u, l = own_lead(b)
    if b.get('progress') in PROGRESS:
        db().execute('UPDATE leads SET progress=? WHERE id=?', (b['progress'], l['id']))


@action('lead/report')
def a_lead_report(b):
    u = need_me()
    l = db().execute('SELECT * FROM leads WHERE id=?', (b.get('id'),)).fetchone()
    if not l or u['id'] not in (l['teacher_id'], l['student_id']):
        fail('הליד לא נמצא')
    reason = b.get('reason') if b.get('reason') in ('wrong', 'joke', 'dup', 'other', 'chat') else 'other'
    if reason != 'chat' and l['opened_at'] and (datetime.utcnow() - datetime.fromisoformat(l['opened_at'].rstrip('Z'))).days > 7:
        fail('אפשר לדווח עד 7 ימים מפתיחת הליד')
    if reason != 'chat' and db().execute("SELECT 1 FROM lead_reports WHERE lead_id=? AND reason<>'chat'", (l['id'],)).fetchone():
        fail('כבר דיווחת על הליד הזה')
    db().execute('INSERT INTO lead_reports VALUES (?,?,?,?,?,?,?)', (nid('x'), l['id'], u['id'], reason, txt(b.get('text'), 500), 'pending', now()))


# ------------------------------------------------------------------
# request board
# ------------------------------------------------------------------
@action('request/create')
def a_req_create(b):
    u = need_me()
    if not db().execute('SELECT 1 FROM fields WHERE id=?', (b.get('fieldId'),)).fetchone():
        fail('בחר תחום')
    modes = [m for m in (b.get('modes') or []) if m in MODES]
    text = txt(b.get('text'), 500)
    if not text or not modes:
        fail('חסרים פרטים')
    data = dict(text=text, level=b.get('level') if b.get('level') in SLEVEL else 'none', modes=modes, area=txt(b.get('area'), 40),
                budget=num(b.get('budget')) or 0, start=b.get('start') if b.get('start') in STARTS else 'flex')
    db().execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)', (nid('q'), u['id'], b['fieldId'], json.dumps(data, ensure_ascii=False), '[]', 'open', now()))


@action('request/close')
def a_req_close(b):
    u = need_me()
    db().execute("UPDATE requests SET status='closed' WHERE id=? AND student_id=?", (b.get('id'), u['id']))


@action('request/open')
def a_req_open(b):
    u = need_teacher()
    r = db().execute("SELECT * FROM requests WHERE id=? AND status='open'", (b.get('id'),)).fetchone()
    if not r:
        fail('הבקשה כבר לא פתוחה')
    if r['field_id'] not in (teacher_of(u) or {}).get('fields', []):
        fail('הבקשה לא בתחומים שלך')
    stu = user_row(r['student_id'])
    same_gender_or_fail(u, stu)
    opens = J(r['opens'], [])
    if u['id'] in opens:
        fail('כבר פתחת את הבקשה')
    if len(opens) >= settings()['maxOpensPerRequest']:
        fail('הבקשה כבר נפתחה על ידי המספר המרבי של מורים')
    price = db().execute('SELECT lead_price FROM fields WHERE id=?', (r['field_id'],)).fetchone()['lead_price']
    cur = db().execute('UPDATE users SET wallet = wallet - ? WHERE id=? AND wallet >= ?', (price, u['id'], price))
    if cur.rowcount != 1:
        fail('אין מספיק יתרה')
    opens.append(u['id'])
    db().execute('UPDATE requests SET opens=? WHERE id=?', (json.dumps(opens), r['id']))
    d = J(r['data'])
    data = dict(want=d.get('text'), level=d.get('level'), mode=(d.get('modes') or ['front'])[0], start=d.get('start'), hours='', contact=['phone', 'site'])
    lid = nid('l')
    db().execute('INSERT INTO leads (id,course_id,request_id,student_id,teacher_id,data,status,progress,price,created_at,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                 (lid, None, r['id'], stu['id'], u['id'], json.dumps(data, ensure_ascii=False), 'open', '', price, r['created_at'], now()))
    ledger(u['id'], 'lead', -price, 'פתיחת בקשה מהלוח')
    send_mail(stu['email'], 'מורה פנה אליך - מקצוע ביד', 'מורה פנה לבקשה שלך', f"{u['name']} פתח את הבקשה שלך ויחזור אליך בקרוב.",
              link('#/messages/' + lid), 'להודעות')
    return {'leadId': lid}


# ------------------------------------------------------------------
# teachers & courses
# ------------------------------------------------------------------
@bp.route('/api/teacher/apply', methods=['POST'])
def teacher_apply():
    """Multipart (credential file), so it has its own route; same CSRF check applies."""
    try:
        u = need_me()
        b = J(request.form.get('json'), {})
        t = teacher_of(u)
        if t and t.get('status') in ('approved', 'pending'):
            fail('כבר הגשת בקשה')
        fields = [f for f in (b.get('fields') or []) if db().execute('SELECT 1 FROM fields WHERE id=?', (f,)).fetchone()][:5]
        modes = [m for m in (b.get('modes') or []) if m in MODES]
        if (not fields and not txt(b.get('otherField'), 60)) or not modes or not txt(b.get('about'), 800):
            fail('חסרים פרטים בפרופיל')
        fname = save_upload(request.files.get('file'), u['id'], 'credential')
        creds = [{'name': txt(b.get('credName'), 80) or 'תעודה', 'pending': True, 'file': fname, 'license': txt(b.get('license'), 40)}] if (fname or b.get('credName')) else []
        t = dict(status='pending', verified=False, years=num(b.get('years'), 0, 70) or 0, workplace=txt(b.get('workplace'), 80),
                 about=txt(b.get('about'), 800), availability=txt(b.get('availability'), 80), modes=modes,
                 areas=[txt(a, 40) for a in (b.get('areas') or [])][:12], fields=fields, otherField=txt(b.get('otherField'), 60),
                 credentials=creds, joined=now())
        db().execute('UPDATE users SET teacher=? WHERE id=?', (json.dumps(t, ensure_ascii=False), u['id']))
        db().commit()
    except ApiError as e:
        db().rollback()
        return jsonify(ok=False, error=str(e)), 400
    g.pop('mk_me', None)
    return jsonify(ok=True, state=build_state())


@action('teacher/profile')
def a_teacher_profile(b):
    u = need_teacher()
    t = teacher_of(u)
    t.update(years=num(b.get('years'), 0, 70) or t.get('years', 0), workplace=txt(b.get('workplace'), 80), about=txt(b.get('about'), 800) or t.get('about'),
             availability=txt(b.get('availability'), 80) or t.get('availability'),
             modes=[m for m in (b.get('modes') or []) if m in MODES] or t.get('modes'))
    db().execute('UPDATE users SET teacher=? WHERE id=?', (json.dumps(t, ensure_ascii=False), u['id']))


@action('course/save')
def a_course_save(b):
    u = need_me()
    t = teacher_of(u)
    if not t or t.get('status') not in ('approved', 'pending'):
        fail('קודם צריך להצטרף כמורה')
    cid = b.get('id')
    row = db().execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone() if cid else None
    if cid and (not row or (row['teacher_id'] != u['id'] and not is_admin(u))):
        fail('הקורס לא נמצא')
    field = b.get('fieldId')
    if field not in t.get('fields', []) and not is_admin(u):
        fail('אפשר לפרסם רק בתחומים שלך')
    syl = [{'title': txt(s.get('title'), 80), 'desc': txt(s.get('desc'), 160)} for s in (b.get('syllabus') or []) if txt(s.get('title'), 80)][:40]
    data = dict(title=txt(b.get('title'), 80), fieldId=field, track=b.get('track') if b.get('track') in TRACKS else 'practical',
                level=b.get('level') if b.get('level') in LEVELS else 'begin',
                outcomes=[txt(o, 90) for o in (b.get('outcomes') or []) if txt(o, 90)][:6], description=txt(b.get('description'), 1500),
                syllabus=syl, bring=txt(b.get('bring'), 120), modes=[m for m in (b.get('modes') or []) if m in MODES],
                sessions=len(syl), duration=num(b.get('duration'), 15, 480) or 60, group='group' if b.get('group') == 'group' else 'single',
                groupMax=num(b.get('groupMax'), 2, 30) or 4, priceType='course' if b.get('priceType') == 'course' else 'lesson',
                price=num(b.get('price'), 0, 100000) or 0,
                trial={'on': bool((b.get('trial') or {}).get('on')), 'price': num((b.get('trial') or {}).get('price'), 0, 10000) or 0})
    send = b.get('as') == 'send'
    if send:
        if not data['title'] or len(data['outcomes']) < 3 or not syl or not data['modes'] or not data['price'] or not data['description']:
            fail('כדי לשלוח לאישור צריך: שם, 3 שורות "מה תדע לעשות", תיאור, סילבוס, אופן לימוד ומחיר')
    if row:
        status = row['status']
        if not send:
            status = 'draft' if status != 'active' else 'active'
        elif status in ('draft', 'rejected'):
            status = 'pending' if is_teacher(u) or t.get('status') == 'pending' else 'draft'
        db().execute('UPDATE courses SET data=?, status=? WHERE id=?', (json.dumps(data, ensure_ascii=False), status, row['id']))
    else:
        status = 'pending' if send else 'draft'
        db().execute('INSERT INTO courses VALUES (?,?,?,?,?,?,?)', (nid('c'), u['id'], status, None, 0, now(), json.dumps(data, ensure_ascii=False)))
    return {'status': status}


@action('course/visibility')
def a_course_vis(b):
    u = need_teacher()
    st = 'hidden' if b.get('hide') else 'active'
    db().execute("UPDATE courses SET status=? WHERE id=? AND teacher_id=? AND status IN ('active','hidden')", (st, b.get('id'), u['id']))


@action('course/view')
def a_course_view(b):
    db().execute("UPDATE courses SET views = views + 1 WHERE id=? AND status='active' AND teacher_id <> ?", (b.get('id'), session.get('mk_uid') or ''))


@action('course/highlight')
def a_course_highlight(b):
    u = need_teacher()
    s = settings()
    c = db().execute("SELECT * FROM courses WHERE id=? AND teacher_id=? AND status='active'", (b.get('id'), u['id'])).fetchone()
    if not c:
        fail('הקורס לא נמצא')
    price = s['highlightPrice']
    cur = db().execute('UPDATE users SET wallet = wallet - ? WHERE id=? AND wallet >= ?', (price, u['id'], price))
    if cur.rowcount != 1:
        fail('אין מספיק יתרה להדגשה')
    start = max(c['highlighted_until'] or '', now())
    until = (datetime.fromisoformat(start.rstrip('Z')) + timedelta(days=s['highlightDays'])).isoformat(timespec='seconds') + 'Z'
    db().execute('UPDATE courses SET highlighted_until=? WHERE id=?', (until, c['id']))
    ledger(u['id'], 'highlight', -price, f"הדגשת קורס ל-{s['highlightDays']} יום: {J(c['data']).get('title', '')}")


# ------------------------------------------------------------------
# messages, lessons, reviews
# ------------------------------------------------------------------
def open_lead_for(u, lead_id):
    l = db().execute("SELECT * FROM leads WHERE id=? AND status='open'", (lead_id,)).fetchone()
    if not l or u['id'] not in (l['teacher_id'], l['student_id']):
        fail('השיחה לא זמינה')
    return l


@bp.route('/api/message/send', methods=['POST'])
def message_send():
    try:
        u = need_me()
        b = J(request.form.get('json'), None) or request.get_json(silent=True) or {}
        l = open_lead_for(u, b.get('leadId'))
        text = txt(b.get('text'), 2000)
        fs = request.files.get('file')
        fname = save_upload(fs, u['id'], 'message', l['id']) if fs and fs.filename else None
        if not text and not fname:
            fail('ההודעה ריקה')
        other = l['student_id'] if u['id'] == l['teacher_id'] else l['teacher_id']
        last = db().execute('SELECT at FROM messages WHERE lead_id=? AND sender=? ORDER BY at DESC LIMIT 1', (l['id'], u['id'])).fetchone()
        db().execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,0)', (nid('m'), l['id'], u['id'], other, text, fname,
                                                                        txt(fs.filename, 120) if fname else '', now()))
        db().commit()
        if not last or last['at'] < (datetime.utcnow() - timedelta(hours=1)).isoformat():
            o = user_row(other)
            send_mail(o['email'], 'הודעה חדשה - מקצוע ביד', f"הודעה חדשה מ{u['name']}", (text or 'נשלח קובץ')[:200], link('#/messages/' + l['id']), 'לקריאה ולתשובה')
    except ApiError as e:
        db().rollback()
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, state=build_state())


@action('message/read')
def a_msg_read(b):
    u = need_me()
    db().execute('UPDATE messages SET read=1 WHERE lead_id=? AND recipient=?', (b.get('leadId'), u['id']))


@action('lesson/create')
def a_lesson_create(b):
    u = need_teacher()
    l = open_lead_for(u, b.get('leadId'))
    if l['teacher_id'] != u['id']:
        fail('רק המורה קובע שיעור')
    try:
        at = datetime.fromisoformat(str(b.get('at')).replace('Z', ''))
    except ValueError:
        fail('תאריך לא תקין')
    offset = num(b.get('tzOffset'), -900, 900) or 0          # browser minutes offset, to store UTC
    at_utc = at + timedelta(minutes=offset)
    n = 4 if b.get('repeat') == 'weekly' else 1
    dur = num(b.get('duration'), 15, 300) or 60
    for i in range(n):
        db().execute('INSERT INTO lessons VALUES (?,?,?,?,?,?,?)', (nid('v'), l['id'], (at_utc + timedelta(days=7 * i)).isoformat(timespec='seconds') + 'Z',
                                                                    dur, txt(b.get('topic'), 80), '[]', 'miktzoa-' + secrets.token_hex(10)))
    if l['progress'] in ('', 'contacted'):
        db().execute("UPDATE leads SET progress='scheduled' WHERE id=?", (l['id'],))
    stu = user_row(l['student_id'])
    send_mail(stu['email'], 'נקבע שיעור בווידאו - מקצוע ביד', 'נקבע לך שיעור בווידאו',
              f"{u['name']} קבע שיעור. נכנסים לחדר השיעור מהאתר, 10 דקות לפני השעה.", link('#/me?tab=lessons'), 'לשיעורים שלי')


@action('lesson/done')
def a_lesson_done(b):
    u = need_teacher()
    v = db().execute('SELECT * FROM lessons WHERE id=?', (b.get('id'),)).fetchone()
    if not v:
        fail('השיעור לא נמצא')
    l = open_lead_for(u, v['lead_id'])
    if l['teacher_id'] != u['id']:
        fail('רק המורה מסמן')
    done = sorted({int(x) for x in (b.get('done') or []) if str(x).isdigit()})
    db().execute('UPDATE lessons SET done=? WHERE id=?', (json.dumps(done), v['id']))


@action('review/create')
def a_review(b):
    u = need_me()
    l = db().execute("SELECT * FROM leads WHERE id=? AND student_id=? AND status='open' AND course_id IS NOT NULL", (b.get('leadId'), u['id'])).fetchone()
    if not l:
        fail('אפשר לכתוב ביקורת רק על קורס שלמדת בו')
    past_lesson = db().execute('SELECT 1 FROM lessons WHERE lead_id=? AND at < ?', (l['id'], now())).fetchone()
    opened = datetime.fromisoformat(l['opened_at'].rstrip('Z'))
    if not past_lesson and datetime.utcnow() - opened < timedelta(days=7):
        fail('אפשר לכתוב ביקורת 7 ימים אחרי שהמורה פתח את הפנייה, או אחרי שיעור')
    if db().execute('SELECT 1 FROM reviews WHERE course_id=? AND student_id=?', (l['course_id'], u['id'])).fetchone():
        fail('כבר כתבת ביקורת על הקורס')
    stars = num(b.get('stars'), 1, 5)
    if not stars:
        fail('בחר דירוג')
    db().execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,0)', (nid('r'), l['course_id'], u['id'], l['id'], stars,
                 b.get('delivered') if b.get('delivered') in ('yes', 'partly', 'no') else 'yes', num(b.get('patience'), 0, 5) or 0,
                 num(b.get('punctual'), 0, 5) or 0, txt(b.get('text'), 600), '', now()))
    return {'courseId': l['course_id']}


@action('review/reply')
def a_review_reply(b):
    u = need_teacher()
    r = db().execute('SELECT r.* FROM reviews r JOIN courses c ON c.id=r.course_id WHERE r.id=? AND c.teacher_id=?', (b.get('id'), u['id'])).fetchone()
    if not r or r['reply']:
        fail('אפשר להגיב פעם אחת')
    db().execute('UPDATE reviews SET reply=? WHERE id=?', (txt(b.get('text'), 400), r['id']))


# ------------------------------------------------------------------
# video lessons: WebRTC between the two browsers; this server only passes the
# connection messages (offer/answer/candidates). Nothing is recorded.
# ------------------------------------------------------------------
def _ice_servers():
    out = [{'urls': u.strip()} for u in os.environ.get('MIKTZOA_STUN', 'stun:stun.l.google.com:19302').split(',') if u.strip()]
    if os.environ.get('MIKTZOA_TURN_URL'):
        out.append({'urls': [u.strip() for u in os.environ['MIKTZOA_TURN_URL'].split(',')],
                    'username': os.environ.get('MIKTZOA_TURN_USER', ''), 'credential': os.environ.get('MIKTZOA_TURN_PASS', '')})
    return out


def _lesson_for(u, lesson_id):
    v = db().execute('SELECT * FROM lessons WHERE id=?', (lesson_id,)).fetchone()
    if not v:
        fail('השיעור לא נמצא')
    open_lead_for(u, v['lead_id'])
    return v


@bp.route('/rtc/<lesson_id>', methods=['GET', 'POST'])
def rtc(lesson_id):
    try:
        u = need_me()
        _lesson_for(u, lesson_id)
    except ApiError as e:
        return jsonify(ok=False, error=str(e)), 403
    c = db()
    if request.method == 'POST':
        if not secrets.compare_digest(request.headers.get('X-CSRF', ''), session.get('mk_csrf', '')):
            return jsonify(ok=False), 400
        b = request.get_json(silent=True) or {}
        if b.get('type') not in ('hello', 'here', 'offer', 'answer', 'ice', 'bye'):
            return jsonify(ok=False), 400
        data = json.dumps(b.get('data'))[:20000]
        c.execute('DELETE FROM rtc WHERE at < ?', (time.time() - 6 * 3600,))
        c.execute('INSERT INTO rtc (lesson_id, sender, type, data, at) VALUES (?,?,?,?,?)', (lesson_id, u['id'], b['type'], data, time.time()))
        c.commit()
        return jsonify(ok=True)
    since = num(request.args.get('since'), 0) or 0
    if since == 0:   # first poll: only the latest id, so old messages are not replayed
        last = c.execute('SELECT COALESCE(MAX(id),0) FROM rtc WHERE lesson_id=?', (lesson_id,)).fetchone()[0]
        return jsonify(ok=True, last=last, items=[])
    rows = c.execute('SELECT * FROM rtc WHERE lesson_id=? AND id>? AND sender<>? ORDER BY id', (lesson_id, since, u['id'])).fetchall()
    last = c.execute('SELECT COALESCE(MAX(id),?) FROM rtc WHERE lesson_id=?', (since, lesson_id)).fetchone()[0]
    return jsonify(ok=True, last=last, items=[{'type': r['type'], 'data': J(r['data'], None)} for r in rows])


# ------------------------------------------------------------------
# wallet top-up via Nedarim Plus
# ------------------------------------------------------------------
def _clean(text, limit):
    t = (text or '').replace('<', '(').replace('>', ')').replace('&#', '')
    for bad in ('http', 'www', 'src='):
        t = t.replace(bad, '')
    return t.strip()[:limit]


@action('pay/create')
def a_pay_create(b):
    u = need_teacher()
    if not _nedarim_ready():
        fail('תשלום מקוון עדיין לא הופעל. פנה למנהל האתר.')
    i = num(b.get('pack'), 0, len(PACKS) - 1)
    if i is None:
        fail('בחר סכום')
    p = PACKS[i]
    token = secrets.token_urlsafe(24)
    import requests as rq
    name = (u['name'] or '').split()
    admins = sorted(_admin_emails())
    data = {'Mosad': os.environ['NEDARIM_MOSAD'], 'ApiValid': os.environ['NEDARIM_API_VALID'], 'PaymentType': 'Ragil',
            'Amount': str(p['amount']), 'Currency': '1', 'Tashlumim': '1',
            'FirstName': _clean(name[0] if name else '', 50), 'LastName': _clean(' '.join(name[1:]), 50),
            'Phone': (u['phone'] or '')[:20], 'Mail': (u['email'] or '')[:50],
            'Zeout': ''.join(ch for ch in str(b.get('zeout') or '') if ch.isdigit())[:9],
            'Groupe': _clean(os.environ.get('MIKTZOA_CATEGORY', 'מקצוע ביד'), 100),
            'Comment': _clean(f"טעינת יתרה למורה {u['name']}", 300), 'Param2': 'MKZ-' + token[:16],
            'CallBack': _base_url() + url_for('miktzoa.pay_callback', token=token),
            'CallBackMailError': os.environ.get('NEDARIM_CALLBACK_MAIL') or (admins[0] if admins else ''),
            'AjaxId': str(int(time.time() * 1000))}
    try:
        j = rq.post(NEDARIM_CREATE_URL, data=data, timeout=20).json()
    except Exception:
        fail('לא הצלחנו להתחבר לנדרים פלוס. נסה שוב בעוד רגע.')
    if j.get('Status') != 'OK' or not j.get('ID'):
        msg = j.get('Message') or 'שגיאה מנדרים פלוס'
        if 'זהות' in msg:
            return {'needId': True}
        fail(msg)
    db().execute('INSERT INTO payments (token,teacher_id,amount,credit,nedarim_id,nedarim_key,status,created_at) VALUES (?,?,?,?,?,?,?,?)',
                 (token, u['id'], p['amount'], p['amount'] + p['bonus'], str(j['ID']), j.get('Key', ''), 'created', now()))
    from urllib.parse import urlencode
    return {'token': token, 'iframe': NEDARIM_IFRAME + '?' + urlencode({'language': 'he', 'TransactionId': j['ID'], 'Key': j.get('Key', ''), 'ButtonText': 'תשלום'})}


@bp.route('/pay/status/<token>')
def pay_status(token):
    u = me()
    p = db().execute('SELECT status, teacher_id FROM payments WHERE token=?', (token,)).fetchone()
    if not u or not p or p['teacher_id'] != u['id']:
        abort(404)
    return jsonify(status=p['status'])


def _signature_state(raw):
    keys = [x.strip() for x in os.environ.get('NEDARIM_WEBHOOK_SECRET', '').split(',') if x.strip()]
    ts = request.headers.get('X-Nedarim-Timestamp', '')
    sig = request.headers.get('X-Nedarim-Signature', '').replace('v1=', '')
    if not keys or not ts or not sig:
        return 'absent'
    try:
        if abs(time.time() - int(ts)) > 600:
            return 'invalid'
    except ValueError:
        return 'invalid'
    for k in keys:
        if hmac.compare_digest(hmac.new(k.encode(), ts.encode() + b'.' + raw, hashlib.sha256).hexdigest(), sig):
            return 'valid'
    return 'invalid'


@bp.route('/pay/callback/<token>', methods=['POST'])
def pay_callback(token):
    """Server-to-server update from Nedarim. The address carries a random secret per payment;
    on top of that: HMAC signature (when enabled), sender IP (logged) and the amount."""
    raw = request.get_data(cache=True)
    try:
        data = json.loads(raw.decode('utf-8') or '{}')
    except ValueError:
        data = request.form.to_dict()
    c = db()
    p = c.execute('SELECT * FROM payments WHERE token=?', (token,)).fetchone()
    ip = _client_ip()
    if not p:
        return jsonify(ok=False), 404
    sig = _signature_state(raw)
    if sig == 'invalid':
        log.warning(f'miktzoa callback bad signature, payment {p["id"]}, ip {ip}')
        return jsonify(ok=False), 401
    if ip not in NEDARIM_IPS:
        log.warning(f'miktzoa callback from unlisted IP {ip} (payment {p["id"]})')
    c.execute('UPDATE payments SET raw=? WHERE id=?', (raw.decode('utf-8', 'replace')[:4000], p['id']))
    if p['status'] == 'paid':
        c.commit()
        return jsonify(WEBDocID=p['id'])
    if str(data.get('Status', '')).upper() != 'OK':
        c.execute('UPDATE payments SET last_error=? WHERE id=?', (str(data.get('Message', ''))[:300], p['id']))
        c.commit()
        return jsonify(ok=True)
    amount = data.get('Amount')
    if amount not in (None, ''):
        try:
            if round(float(str(amount).replace(',', ''))) != int(p['amount']):
                c.execute('UPDATE payments SET last_error=? WHERE id=?', (f'סכום לא תואם: {amount}', p['id']))
                c.commit()
                return jsonify(ok=False), 400
        except ValueError:
            pass
    tid = str(data.get('TransactionId') or data.get('ID') or p['nedarim_id'])
    cur = c.execute("UPDATE payments SET status='paid', confirmation=?, verified_by=?, paid_at=?, nedarim_id=? WHERE id=? AND status<>'paid'",
                    (str(data.get('Confirmation', '')), 'signature' if sig == 'valid' else ('ip' if ip in NEDARIM_IPS else 'secret-url'), now(), tid, p['id']))
    if cur.rowcount == 1:
        c.execute('UPDATE users SET wallet = wallet + ? WHERE id=?', (p['credit'], p['teacher_id']))
        bonus = p['credit'] - p['amount']
        c.execute('INSERT INTO ledger VALUES (?,?,?,?,?,?)', (nid('g'), p['teacher_id'], 'topup', p['credit'], now(),
                                                             f"טעינה ₪{p['amount']}" + (f" (+₪{bonus} מתנה)" if bonus else '') + f" · עסקה {tid}"))
    c.commit()
    return jsonify(WEBDocID=p['id'])


# ------------------------------------------------------------------
# admin
# ------------------------------------------------------------------
def set_teacher(uid, fn):
    r = user_row(uid)
    t = teacher_of(r)
    if not t:
        fail('המשתמש אינו מורה')
    fn(t)
    db().execute('UPDATE users SET teacher=? WHERE id=?', (json.dumps(t, ensure_ascii=False), uid))
    return r


@action('admin/teacher')
def a_admin_teacher(b):
    need_admin()
    if b.get('action') == 'approve':
        r = set_teacher(b.get('id'), lambda t: t.update(status='approved', rejectReason=''))
        db().execute("UPDATE courses SET status='pending' WHERE teacher_id=? AND status='draft' AND 0", (r['id'],))
        send_mail(r['email'], 'אושרת כמורה - מקצוע ביד', 'ברוך הבא לצוות', 'הבקשה שלך אושרה. אפשר לפרסם קורסים ולקבל פניות.', link('#/dash'), 'ללוח המורה')
    else:
        reason = txt(b.get('reason'), 300)
        r = set_teacher(b.get('id'), lambda t: t.update(status='rejected', rejectReason=reason))
        send_mail(r['email'], 'הבקשה להצטרף כמורה', 'הבקשה לא אושרה', reason or 'אפשר לפנות אלינו לפרטים.', link('#/join'), 'לפרטים')


@action('admin/cred')
def a_admin_cred(b):
    need_admin()
    ok = bool(b.get('ok'))

    def upd(t):
        if ok:
            for c in t.get('credentials', []):
                c['pending'] = False
            t['verified'] = True
        else:
            t['credentials'] = [c for c in t.get('credentials', []) if not c.get('pending')]
    set_teacher(b.get('id'), upd)


@action('admin/course')
def a_admin_course(b):
    need_admin()
    c = db().execute('SELECT * FROM courses WHERE id=?', (b.get('id'),)).fetchone()
    if not c:
        fail('הקורס לא נמצא')
    st = {'approve': 'active', 'back': 'rejected', 'hide': 'hidden'}.get(b.get('action'))
    if not st:
        fail('פעולה לא מוכרת')
    db().execute('UPDATE courses SET status=? WHERE id=?', (st, c['id']))
    t = user_row(c['teacher_id'])
    title = J(c['data']).get('title', '')
    if st == 'active':
        send_mail(t['email'], 'הקורס אושר - מקצוע ביד', 'הקורס שלך באוויר', f'הקורס "{title}" אושר ומופיע עכשיו בחיפוש.', link('#/course/' + c['id']), 'לקורס')
    if st == 'rejected':
        send_mail(t['email'], 'הקורס הוחזר לתיקון', 'הקורס הוחזר לתיקון', f'הקורס "{title}" הוחזר: {txt(b.get("reason"), 300)}', link('#/course/' + c['id'] + '/edit'), 'לעריכה')


@action('admin/field')
def a_admin_field(b):
    need_admin()
    if b.get('new'):
        name, price = txt(b.get('name'), 60), num(b.get('leadPrice'), 1, 1000)
        if not name or not price:
            fail('צריך שם ומחיר ליד')
        db().execute('INSERT INTO fields VALUES (?,?,?,?,?)', (nid('f'), name, 0, price, 999))
        return
    f = db().execute('SELECT * FROM fields WHERE id=?', (b.get('id'),)).fetchone()
    if not f:
        fail('התחום לא נמצא')
    db().execute('UPDATE fields SET name=?, regulated=?, lead_price=? WHERE id=?',
                 (txt(b.get('name'), 60) or f['name'], 1 if b.get('regulated') else 0, num(b.get('leadPrice'), 1, 1000) or f['lead_price'], f['id']))


@action('admin/report')
def a_admin_report(b):
    need_admin()
    r = db().execute("SELECT * FROM lead_reports WHERE id=? AND status='pending'", (b.get('id'),)).fetchone()
    if not r:
        fail('הדיווח כבר טופל')
    if b.get('action') == 'refund':
        l = db().execute('SELECT * FROM leads WHERE id=?', (r['lead_id'],)).fetchone()
        db().execute('UPDATE users SET wallet = wallet + ? WHERE id=?', (l['price'], l['teacher_id']))
        ledger(l['teacher_id'], 'refund', l['price'], 'החזר על ליד לא תקין')
        db().execute("UPDATE lead_reports SET status='refunded' WHERE id=?", (r['id'],))
    else:
        db().execute("UPDATE lead_reports SET status='closed' WHERE id=?", (r['id'],))


@action('admin/user')
def a_admin_user(b):
    a = need_admin()
    r = user_row(b.get('id'))
    if not r:
        fail('המשתמש לא נמצא')
    if b.get('gender') in ('m', 'f'):
        db().execute('UPDATE users SET gender=? WHERE id=?', (b['gender'], r['id']))
    if b.get('credit'):
        v = num(b.get('credit'), -100000, 100000)
        db().execute('UPDATE users SET wallet = wallet + ? WHERE id=?', (v, r['id']))
        ledger(r['id'], 'manual', v, txt(b.get('note'), 120) or 'עדכון ידני מהמנהל')
    if 'block' in b and r['id'] != a['id']:
        db().execute('UPDATE users SET blocked=? WHERE id=?', (1 if b['block'] else 0, r['id']))


@action('admin/review')
def a_admin_review(b):
    need_admin()
    db().execute('UPDATE reviews SET hidden=? WHERE id=?', (1 if b.get('hide') else 0, b.get('id')))


@action('admin/settings')
def a_admin_settings(b):
    need_admin()
    for k in DEFAULT_SETTINGS:
        if k in b and num(b[k]) is not None:
            db().execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (k, str(num(b[k], 0, 100000))))


# ------------------------------------------------------------------
def init_miktzoa(app):
    if not app.secret_key:
        seed = os.environ.get('LUACH_SECRET_KEY') or os.environ.get('SECRET_KEY')
        if not seed and os.environ.get('LAB_ACCESS_CODE'):
            seed = hashlib.sha256(('luach-session|' + os.environ['LAB_ACCESS_CODE']).encode()).hexdigest()
        app.secret_key = seed or secrets.token_hex(32)
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(days=60))
    os.makedirs(DATA_DIR, exist_ok=True)
    _init_db()
    app.teardown_appcontext(_close)
    app.register_blueprint(bp)
