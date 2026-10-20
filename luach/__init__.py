"""
לוח המודעות - לוח נדל"ן מרוכז לציבור החרדי (לפי SPEC שהוכן ב-Base44).

הכל רץ בתוך אפליקציית המעבדה, תחת הכתובת /luach:
  /luach              הלוח (ציבורי)
  /luach/publish      פרסום מודעה (מחייב התחברות)
  /luach/marketers    אזור משווקים (מחייב התחברות)
  /luach/admin        ניהול (מנהלים בלבד)
  /luach/login ...    הרשמה, התחברות, שחזור סיסמה, התחברות עם גוגל

הגדרות (משתני סביבה, כולם לא חובה):
  LUACH_ADMIN_EMAILS   מיילים של מנהלים, מופרדים בפסיק (ברירת מחדל: LAB_DEFAULT_EMAIL)
  LUACH_DATA_DIR       תיקייה לשמירת מסד הנתונים והתמונות (ברירת מחדל: /data אם קיימת, אחרת תיקיית הפרויקט)
  LUACH_SECRET_KEY     מפתח לחתימת ההתחברות (ברירת מחדל: נגזר מ-LAB_ACCESS_CODE)
  GOOGLE_CLIENT_ID     מזהה OAuth של גוגל - אם מוגדר, מופיע כפתור "התחברות עם Google"
  SENDGRID_API_KEY / SENDGRID_FROM_EMAIL  לשליחת מייל אימות ושחזור סיסמה (בלעדיהם: אין אימות במייל)
"""
import os
import io
import json
import time
import uuid
import hashlib
import secrets
import sqlite3
import logging
import functools
from datetime import datetime, timedelta

from flask import (Blueprint, request, session, redirect, url_for, render_template,
                   jsonify, abort, send_from_directory, flash, g)
from werkzeug.security import generate_password_hash, check_password_hash

log = logging.getLogger(__name__)

bp = Blueprint('luach', __name__, url_prefix='/luach',
               template_folder='templates', static_folder='static', static_url_path='/static')

# ------------------------------------------------------------------
# settings & constants
# ------------------------------------------------------------------
_DEFAULT_DIR = '/data' if os.path.isdir('/data') else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('LUACH_DATA_DIR', _DEFAULT_DIR)
DB_PATH = os.path.join(DATA_DIR, 'luach.db')
IMG_DIR = os.path.join(DATA_DIR, 'luach_images')

PRICE_COLOR = 20
PRICE_MOTION = 40
PRICE_EXTRA = 30
EXTRA_SLOTS = 5
BASE_SLOTS = 2
SLOTS_CAP = 20
LIKES_PER_SLOT = 5

DEAL_TYPES = {'sale': 'מכירה', 'rent': 'השכרה', 'vacation': 'נופש', 'commercial': 'מסחרי'}
HIGHLIGHTS = {'none': 'רגילה', 'color': 'הדגשה בצבע', 'motion': 'צבע ותנועה'}
STORES = {'aliexpress': 'עלי אקספרס', 'temu': 'טמו', 'shein': 'שיין', 'amazon': 'אמזון', 'other': 'אחר'}
PAPERS = ['יתד נאמן', 'המודיע', 'המבשר', 'בקהילה', 'משפחה', 'פלס', 'שעה טובה', 'מרווה לצמא']
CITIES = ['ירושלים', 'בני ברק', 'בית שמש', 'מודיעין עילית', 'ביתר עילית', 'אלעד', 'אשדוד', 'חיפה',
          'צפת', 'טבריה', 'רכסים', 'עמנואל', 'חריש', 'נתיבות', 'ערד', 'קרית גת', 'פתח תקווה',
          'רחובות', 'נתניה', 'קרית יערים', 'כרמיאל', 'אופקים']


def _admin_emails():
    raw = os.environ.get('LUACH_ADMIN_EMAILS') or os.environ.get('LAB_DEFAULT_EMAIL', '')
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def _mail_ready():
    return bool(os.environ.get('SENDGRID_API_KEY') and os.environ.get('SENDGRID_FROM_EMAIL'))


# ------------------------------------------------------------------
# database
# ------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT,
  password_hash TEXT,
  google_sub TEXT,
  verified INTEGER DEFAULT 0,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  user_id INTEGER,
  kind TEXT,
  expires TEXT
);
CREATE TABLE IF NOT EXISTS ads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, deal_type TEXT, city TEXT, neighborhood TEXT,
  price INTEGER, rooms REAL, floor TEXT, size_sqm INTEGER, description TEXT,
  source_paper TEXT, gilyon TEXT, published_date TEXT,
  contact_name TEXT, phone TEXT, image TEXT,
  highlight TEXT DEFAULT 'none', origin TEXT DEFAULT 'user', status TEXT DEFAULT 'active',
  user_id INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS marketers (
  user_id INTEGER PRIMARY KEY,
  name TEXT, phone TEXT,
  extra_slots INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, url TEXT, description TEXT, store TEXT,
  marketer_id INTEGER, marketer_name TEXT, phone TEXT,
  likes INTEGER DEFAULT 0, dislikes INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active', sample INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS votes (
  link_id INTEGER, user_id INTEGER, value INTEGER,
  PRIMARY KEY (link_id, user_id)
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, ad_id INTEGER, ad_title TEXT, highlight TEXT, slots INTEGER, amount INTEGER,
  requester_id INTEGER, requester_name TEXT, phone TEXT, note TEXT,
  status TEXT DEFAULT 'pending', created_at TEXT
);
CREATE INDEX IF NOT EXISTS ads_status ON ads(status);
CREATE INDEX IF NOT EXISTS links_status ON links(status);
"""


def db():
    if 'luach_db' not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        g.luach_db = conn
    return g.luach_db


def _close_db(exc=None):
    conn = g.pop('luach_db', None)
    if conn is not None:
        conn.close()


def now():
    return datetime.now().isoformat(timespec='seconds')


def _init_db():
    os.makedirs(IMG_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    # first run only (deleted ads stay in the table with status 'removed', so this never re-seeds)
    if conn.execute('SELECT COUNT(*) FROM ads').fetchone()[0] == 0:
        _seed(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    """12 sample ads and 6 sample links so the board is not empty. Marked as samples; admin can delete them all."""
    today = datetime.now().date()
    samples = [
        ('דירת 4 חדרים מרווחת עם מרפסת סוכה', 'sale', 'בני ברק', 'רמת אלחנן', 2650000, 4, '3', 98, 'משפצת, מעלית שבת, קרובה לבתי כנסת ומוסדות.', 'יתד נאמן', 'none'),
        ('3 חדרים להשכרה, כניסה מיידית', 'rent', 'ירושלים', 'רמות', 5200, 3, '1', 72, 'מוארת ושקטה, חניה בטאבו, ליד תחנת אוטובוס.', 'המודיע', 'color'),
        ('יחידת נופש לבין הזמנים', 'vacation', 'צפת', 'העיר העתיקה', 900, 2, 'קרקע', 55, 'ללילה, עד 6 נפשות, נוף לירון, ריהוט מלא.', 'בקהילה', 'motion'),
        ('חנות במרכז מסחרי סואן', 'commercial', 'מודיעין עילית', 'ברכפלד', 1350000, 1, 'קרקע', 40, 'חזית לרחוב ראשי, מתאימה לכל עסק.', 'המבשר', 'none'),
        ('5 חדרים בבניין חדש', 'sale', 'בית שמש', 'רמת בית שמש ג', 2380000, 5, '2', 120, 'מחסן וחניה, מעלית שבת, קרוב לתלמודי תורה.', 'משפחה', 'color'),
        ('דירת 2 חדרים לזוג צעיר', 'rent', 'ביתר עילית', 'גבעה ב', 3400, 2, '4', 50, 'משופצת, מרוהטת חלקית, כניסה בחודש הבא.', 'פלס', 'none'),
        ('וילה לנופש עם בריכה פרטית', 'vacation', 'טבריה', 'קרית שמואל', 2500, 6, 'קרקע', 220, 'לסופי שבוע, בריכה מגודרת בהפרדה, קרוב לקברי צדיקים.', 'שעה טובה', 'motion'),
        ('4 חדרים עם נוף פתוח', 'sale', 'אלעד', 'רחוב רבי עקיבא', 1890000, 4, '6', 95, 'מעלית שבת, שתי מרפסות, חניה.', 'יתד נאמן', 'none'),
        ('משרד להשכרה', 'commercial', 'ירושלים', 'גבעת שאול', 6800, 3, '2', 85, 'מתאים לכולל, משרד או קליניקה. מזגנים בכל החדרים.', 'המודיע', 'none'),
        ('3.5 חדרים עם מרפסת סוכה', 'rent', 'אשדוד', 'רובע ז', 4300, 3.5, '3', 80, 'קרוב למוסדות, נגיש לעגלות.', 'מרווה לצמא', 'none'),
        ('דופלקס 6 חדרים', 'sale', 'חיפה', 'הדר', 1750000, 6, '5', 150, 'מרווח מאוד, מתאים למשפחה גדולה.', 'בקהילה', 'none'),
        ('צימר זוגי לשבת', 'vacation', 'רכסים', 'מרכז', 600, 1, 'קרקע', 35, 'ללילה, מטבחון כשר, גינה פרטית.', 'משפחה', 'none'),
    ]
    for i, (title, deal, city, hood, price, rooms, floor, size, desc, paper, hl) in enumerate(samples):
        conn.execute("""INSERT INTO ads (title, deal_type, city, neighborhood, price, rooms, floor, size_sqm, description,
                        source_paper, gilyon, published_date, contact_name, phone, image, highlight, origin, status, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (title, deal, city, hood, price, rooms, floor, size, desc, paper, 'פרשת השבוע',
                      str(today - timedelta(days=i % 6)), 'מודעת דוגמה', '000-0000000', None, hl, 'sample', 'active', now()))
    links = [
        ('מארגני מגירות למטבח', 'https://www.aliexpress.com/', 'aliexpress', 'סט 6 מארגנים שקופים, נוח לניקוי.', 14, 1),
        ('כיסויי שולחן חד פעמיים לשבת', 'https://www.temu.com/', 'temu', 'מארז חסכוני, עמיד ונוח.', 9, 0),
        ('תיק לגן ילדים עם שם', 'https://www.aliexpress.com/', 'aliexpress', 'בד עמיד, אפשר לכבס במכונה.', 6, 1),
        ('מנורת לילה לחדר ילדים', 'https://www.amazon.com/', 'amazon', 'אור עמום ונעים, טעינה ב-USB.', 11, 0),
        ('קופסאות אחסון לבגדי חורף', 'https://www.temu.com/', 'temu', 'נערמות זו על זו, חוסכות מקום.', 4, 0),
        ('מתלה לסידורים ולספרים קטנים', 'https://www.aliexpress.com/', 'aliexpress', 'מתקפל, קל ונייד.', 7, 2),
    ]
    for title, url, store, desc, likes, dislikes in links:
        conn.execute("""INSERT INTO links (title, url, description, store, marketer_id, marketer_name, likes, dislikes,
                        status, sample, created_at) VALUES (?,?,?,?,NULL,'משווק לדוגמה',?,?,'active',1,?)""",
                     (title, url, desc, store, likes, dislikes, now()))


# ------------------------------------------------------------------
# auth helpers
# ------------------------------------------------------------------
def current_user():
    if 'luach_user' in g:
        return g.luach_user
    uid = session.get('luach_uid')
    user = None
    if uid:
        row = db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if row:
            user = dict(row)
            user['is_admin'] = row['email'].lower() in _admin_emails()
    g.luach_user = user
    return user


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            if request.is_json or request.path.startswith('/luach/api/'):
                return jsonify(ok=False, reason='login'), 401
            return redirect(url_for('luach.login', next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for('luach.login', next=request.path))
        if not u['is_admin']:
            return render_template('luach/admin_denied.html'), 403
        return fn(*a, **kw)
    return wrapper


def csrf_token():
    if 'luach_csrf' not in session:
        session['luach_csrf'] = secrets.token_urlsafe(24)
    return session['luach_csrf']


@bp.before_request
def _check_csrf():
    if request.method == 'POST':
        sent = request.form.get('csrf') or request.headers.get('X-CSRF')
        if not sent or not secrets.compare_digest(sent, session.get('luach_csrf', '')):
            if request.path.startswith('/luach/api/'):
                return jsonify(ok=False, reason='csrf'), 400
            flash('פג תוקף הטופס. נסה שוב.', 'err')
            return redirect(request.path)


@bp.context_processor
def _inject():
    return dict(user=current_user(), csrf=csrf_token(), DEAL_TYPES=DEAL_TYPES, HIGHLIGHTS=HIGHLIGHTS,
                STORES=STORES, PAPERS=PAPERS, CITIES=CITIES, PRICE_COLOR=PRICE_COLOR,
                PRICE_MOTION=PRICE_MOTION, PRICE_EXTRA=PRICE_EXTRA, EXTRA_SLOTS=EXTRA_SLOTS,
                google_client_id=os.environ.get('GOOGLE_CLIENT_ID', ''), mail_ready=_mail_ready())


# simple brute-force protection for login
_fails = {}


def _too_many(ip):
    t = time.time()
    lst = [x for x in _fails.get(ip, []) if t - x < 600]
    _fails[ip] = lst
    return len(lst) >= 10


def _safe_next(nxt):
    return nxt if nxt and nxt.startswith('/luach') and not nxt.startswith('//') else url_for('luach.board')


def _base_url():
    return (os.environ.get('APP_BASE_URL') or request.url_root).rstrip('/')


def _send_mail(to, subject, html):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        sg.send(Mail(from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'לוח המודעות'),
                     to_emails=to, subject=subject, html_content=html))
        return True
    except Exception as e:
        log.error(f'luach mail error: {e}')
        return False


def _make_token(user_id, kind, hours):
    tok = secrets.token_urlsafe(32)
    db().execute('INSERT INTO tokens (token, user_id, kind, expires) VALUES (?,?,?,?)',
                 (tok, user_id, kind, (datetime.now() + timedelta(hours=hours)).isoformat()))
    db().commit()
    return tok


def _use_token(tok, kind):
    row = db().execute('SELECT * FROM tokens WHERE token=? AND kind=?', (tok, kind)).fetchone()
    if not row or row['expires'] < datetime.now().isoformat():
        return None
    return row


def _mail_html(title, text, link, button):
    return f"""<div dir="rtl" style="font-family:Arial,sans-serif;max-width:520px;margin:auto;color:#1a1c1e">
<h2 style="margin:0 0 12px">{title}</h2><p style="line-height:1.6">{text}</p>
<p><a href="{link}" style="display:inline-block;background:#1a1c1e;color:#fff;padding:12px 26px;border-radius:999px;text-decoration:none">{button}</a></p>
<p style="color:#666;font-size:13px">אם לא ביקשת את זה, אפשר להתעלם מההודעה.</p></div>"""


# ------------------------------------------------------------------
# images
# ------------------------------------------------------------------
def _save_image(fs):
    if not fs or not fs.filename:
        return None
    data = fs.read()
    if len(data) > 10 * 1024 * 1024:
        raise ValueError('התמונה גדולה מדי (עד 10MB)')
    from PIL import Image, ImageOps
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert('RGB')
    except Exception:
        raise ValueError('הקובץ שנבחר אינו תמונה')
    img.thumbnail((1400, 1400))
    name = uuid.uuid4().hex + '.jpg'
    img.save(os.path.join(IMG_DIR, name), 'JPEG', quality=82, optimize=True)
    return name


@bp.route('/img/<name>')
def image(name):
    if not name.endswith('.jpg') or '/' in name or '..' in name:
        abort(404)
    resp = send_from_directory(IMG_DIR, name)
    resp.headers['Cache-Control'] = 'public, max-age=604800'
    return resp


# ------------------------------------------------------------------
# quota logic (single shared module, per spec)
# ------------------------------------------------------------------
def marketer_stats(user_id):
    m = db().execute('SELECT * FROM marketers WHERE user_id=?', (user_id,)).fetchone()
    agg = db().execute("""SELECT COALESCE(SUM(likes),0) l, COALESCE(SUM(dislikes),0) d,
                          SUM(CASE WHEN status != 'removed' THEN 1 ELSE 0 END) used
                          FROM links WHERE marketer_id=?""", (user_id,)).fetchone()
    net = (agg['l'] or 0) - (agg['d'] or 0)
    from_votes = max(0, net) // LIKES_PER_SLOT
    extra = (m['extra_slots'] if m else 0) or 0
    total = min(SLOTS_CAP, BASE_SLOTS + from_votes + extra)
    return dict(base=BASE_SLOTS, from_votes=from_votes, extra=extra, net=net, likes=agg['l'] or 0,
                total=total, used=agg['used'] or 0, cap=SLOTS_CAP,
                name=(m['name'] if m else ''), phone=(m['phone'] if m else ''))


def _ad_dict(r):
    d = dict(r)
    d['deal_label'] = DEAL_TYPES.get(d['deal_type'], '')
    d['image_url'] = url_for('luach.image', name=d['image']) if d['image'] else ''
    return d


# ------------------------------------------------------------------
# board
# ------------------------------------------------------------------
def _board_data():
    ads = [_ad_dict(r) for r in db().execute(
        "SELECT * FROM ads WHERE status='active' ORDER BY COALESCE(published_date, created_at) DESC, id DESC").fetchall()]
    links = [dict(r) for r in db().execute(
        "SELECT id, title, url, description, store, marketer_name, likes, dislikes, sample FROM links WHERE status='active' ORDER BY id DESC").fetchall()]
    top = [dict(r) for r in db().execute(
        """SELECT marketer_name AS name, SUM(likes) AS likes, COUNT(*) AS links FROM links
           WHERE status='active' AND marketer_id IS NOT NULL GROUP BY marketer_id ORDER BY likes DESC LIMIT 4""").fetchall()]
    my_votes = {}
    u = current_user()
    if u:
        my_votes = {r['link_id']: r['value'] for r in db().execute('SELECT link_id, value FROM votes WHERE user_id=?', (u['id'],))}
    for l in links:
        l['store_label'] = STORES.get(l['store'], 'אחר')
        l['my_vote'] = my_votes.get(l['id'], 0)
    stats = dict(ads=len(ads), cities=len({a['city'] for a in ads if a['city']}),
                 papers=len({a['source_paper'] for a in ads if a['source_paper']}))
    return dict(ads=ads, links=links, top=top, stats=stats)


@bp.route('/')
def board():
    return render_template('luach/board.html', data=_board_data())


@bp.route('/api/board')
def api_board():
    return jsonify(_board_data())


@bp.route('/api/vote', methods=['POST'])
@login_required
def api_vote():
    """voteLink: one vote per user per link; clicking the same vote again removes it."""
    body = request.get_json(silent=True) or {}
    link_id, value = int(body.get('link_id', 0)), int(body.get('value', 0))
    if value not in (1, -1):
        return jsonify(ok=False), 400
    u = current_user()
    conn = db()
    link = conn.execute("SELECT id FROM links WHERE id=? AND status='active'", (link_id,)).fetchone()
    if not link:
        return jsonify(ok=False, reason='missing'), 404
    old = conn.execute('SELECT value FROM votes WHERE link_id=? AND user_id=?', (link_id, u['id'])).fetchone()
    if old and old['value'] == value:
        conn.execute('DELETE FROM votes WHERE link_id=? AND user_id=?', (link_id, u['id']))
        mine = 0
    else:
        conn.execute('INSERT OR REPLACE INTO votes (link_id, user_id, value) VALUES (?,?,?)', (link_id, u['id'], value))
        mine = value
    conn.execute("""UPDATE links SET likes=(SELECT COUNT(*) FROM votes WHERE link_id=? AND value=1),
                    dislikes=(SELECT COUNT(*) FROM votes WHERE link_id=? AND value=-1) WHERE id=?""",
                 (link_id, link_id, link_id))
    conn.commit()
    r = conn.execute('SELECT likes, dislikes FROM links WHERE id=?', (link_id,)).fetchone()
    return jsonify(ok=True, likes=r['likes'], dislikes=r['dislikes'], mine=mine)


# ------------------------------------------------------------------
# publish
# ------------------------------------------------------------------
def _num(v, kind=int):
    try:
        return kind(str(v).replace(',', '').strip()) if str(v).strip() else None
    except ValueError:
        return None


def _ad_from_form(f):
    return dict(
        title=f.get('title', '').strip()[:120], deal_type=f.get('deal_type') if f.get('deal_type') in DEAL_TYPES else 'sale',
        city=f.get('city', '').strip()[:60], neighborhood=f.get('neighborhood', '').strip()[:80],
        price=_num(f.get('price')), rooms=_num(f.get('rooms'), float), floor=f.get('floor', '').strip()[:20],
        size_sqm=_num(f.get('size_sqm')), description=f.get('description', '').strip()[:1500],
        contact_name=f.get('contact_name', '').strip()[:60], phone=f.get('phone', '').strip()[:30])


def _insert_ad(d):
    cols = ', '.join(d.keys())
    q = ', '.join('?' * len(d))
    cur = db().execute(f'INSERT INTO ads ({cols}) VALUES ({q})', tuple(d.values()))
    return cur.lastrowid


@bp.route('/publish', methods=['GET', 'POST'])
@login_required
def publish():
    u = current_user()
    form = {}
    if request.method == 'POST':
        form = request.form.to_dict()
        d = _ad_from_form(request.form)
        hl = request.form.get('highlight') if request.form.get('highlight') in HIGHLIGHTS else 'none'
        missing = [n for k, n in (('title', 'כותרת'), ('city', 'ריכוז'), ('phone', 'טלפון')) if not d[k]]
        if missing:
            flash('חסר: ' + ', '.join(missing), 'err')
            return render_template('luach/publish.html', form=form, my_ads=_my_ads(u['id']))
        try:
            d['image'] = _save_image(request.files.get('image'))
        except ValueError as e:
            flash(str(e), 'err')
            return render_template('luach/publish.html', form=form, my_ads=_my_ads(u['id']))
        d.update(highlight='none', origin='user', status='active', user_id=u['id'], created_at=now(),
                 published_date=str(datetime.now().date()))
        ad_id = _insert_ad(d)
        if hl != 'none':
            db().execute("""INSERT INTO requests (kind, ad_id, ad_title, highlight, amount, requester_id, requester_name, phone, status, created_at)
                            VALUES ('highlight',?,?,?,?,?,?,?,'pending',?)""",
                         (ad_id, d['title'], hl, PRICE_COLOR if hl == 'color' else PRICE_MOTION, u['id'],
                          d['contact_name'] or u['name'], d['phone'], now()))
        db().commit()
        return redirect(url_for('luach.publish', ok=ad_id, hl=hl))
    return render_template('luach/publish.html', form=form, my_ads=_my_ads(u['id']))


def _my_ads(uid):
    rows = db().execute("""SELECT a.*, (SELECT status FROM requests r WHERE r.ad_id=a.id AND r.kind='highlight'
                           ORDER BY r.id DESC LIMIT 1) AS req_status,
                           (SELECT highlight FROM requests r WHERE r.ad_id=a.id AND r.kind='highlight'
                           ORDER BY r.id DESC LIMIT 1) AS req_highlight
                           FROM ads a WHERE user_id=? AND status!='removed' ORDER BY id DESC""", (uid,)).fetchall()
    return [_ad_dict(r) for r in rows]


@bp.route('/ads/<int:ad_id>/delete', methods=['POST'])
@login_required
def delete_ad(ad_id):
    u = current_user()
    ad = db().execute('SELECT * FROM ads WHERE id=?', (ad_id,)).fetchone()
    if not ad or not (u['is_admin'] or ad['user_id'] == u['id']):
        abort(403)
    db().execute("UPDATE ads SET status='removed' WHERE id=?", (ad_id,))
    db().execute("UPDATE requests SET status='rejected' WHERE ad_id=? AND status='pending'", (ad_id,))
    db().commit()
    flash('המודעה נמחקה.', 'ok')
    return redirect(request.form.get('back') or url_for('luach.publish'))


# ------------------------------------------------------------------
# marketers
# ------------------------------------------------------------------
@bp.route('/marketers')
@login_required
def marketers():
    u = current_user()
    links = [dict(r) for r in db().execute(
        "SELECT * FROM links WHERE marketer_id=? AND status!='removed' ORDER BY id DESC", (u['id'],)).fetchall()]
    reqs = [dict(r) for r in db().execute(
        "SELECT * FROM requests WHERE requester_id=? ORDER BY id DESC LIMIT 20", (u['id'],)).fetchall()]
    return render_template('luach/marketers.html', stats=marketer_stats(u['id']), links=links, reqs=reqs)


@bp.route('/marketers/links', methods=['POST'])
@login_required
def add_link():
    """addMarketerLink: adds a link only when the marketer still has quota."""
    u = current_user()
    f = request.form
    title, url = f.get('title', '').strip()[:100], f.get('url', '').strip()[:600]
    name, phone = f.get('name', '').strip()[:60], f.get('phone', '').strip()[:30]
    if not title or not url.startswith(('http://', 'https://')):
        flash('צריך כותרת וקישור תקין שמתחיל ב-https://', 'err')
        return redirect(url_for('luach.marketers'))
    st = marketer_stats(u['id'])
    if st['used'] >= st['total']:
        flash(f"הגעת למכסה ({st['total']} קישורים). אפשר לקבל עוד עם תגובות חיוביות או ברכישת תוספת.", 'err')
        return redirect(url_for('luach.marketers'))
    store = f.get('store') if f.get('store') in STORES else 'other'
    conn = db()
    conn.execute("""INSERT INTO marketers (user_id, name, phone, extra_slots) VALUES (?,?,?,0)
                    ON CONFLICT(user_id) DO UPDATE SET name=COALESCE(NULLIF(excluded.name,''), name),
                    phone=COALESCE(NULLIF(excluded.phone,''), phone)""", (u['id'], name or u['name'], phone))
    m = conn.execute('SELECT name, phone FROM marketers WHERE user_id=?', (u['id'],)).fetchone()
    conn.execute("""INSERT INTO links (title, url, description, store, marketer_id, marketer_name, phone, status, created_at)
                    VALUES (?,?,?,?,?,?,?,'active',?)""",
                 (title, url, f.get('description', '').strip()[:240], store, u['id'], m['name'] or u['name'], m['phone'], now()))
    conn.commit()
    flash('הקישור נוסף ועולה עכשיו בסרגל הקישורים.', 'ok')
    return redirect(url_for('luach.marketers'))


@bp.route('/marketers/links/<int:link_id>/delete', methods=['POST'])
@login_required
def delete_link(link_id):
    u = current_user()
    link = db().execute('SELECT * FROM links WHERE id=?', (link_id,)).fetchone()
    if not link or not (u['is_admin'] or link['marketer_id'] == u['id']):
        abort(403)
    db().execute("UPDATE links SET status='removed' WHERE id=?", (link_id,))
    db().commit()
    flash('הקישור נמחק.', 'ok')
    return redirect(request.form.get('back') or url_for('luach.marketers'))


@bp.route('/marketers/extra', methods=['POST'])
@login_required
def request_extra():
    u = current_user()
    st = marketer_stats(u['id'])
    pending = db().execute("SELECT 1 FROM requests WHERE requester_id=? AND kind='extra_links' AND status='pending'", (u['id'],)).fetchone()
    if pending:
        flash('כבר יש לך בקשה לתוספת שממתינה לאישור.', 'err')
    else:
        db().execute("""INSERT INTO requests (kind, slots, amount, requester_id, requester_name, phone, note, status, created_at)
                        VALUES ('extra_links',?,?,?,?,?,?,'pending',?)""",
                     (EXTRA_SLOTS, PRICE_EXTRA, u['id'], st['name'] or u['name'], request.form.get('phone', '').strip() or st['phone'],
                      request.form.get('note', '').strip()[:200], now()))
        db().commit()
        flash('הבקשה נשלחה. אחרי התשלום והאישור המכסה תעלה ב-5 קישורים.', 'ok')
    return redirect(url_for('luach.marketers'))


# ------------------------------------------------------------------
# admin
# ------------------------------------------------------------------
@bp.route('/admin')
@admin_required
def admin():
    tab = request.args.get('tab', 'paper')
    q = request.args.get('q', '').strip()
    ctx = dict(tab=tab, q=q, today=str(datetime.now().date()))
    pend = db().execute("SELECT COUNT(*) FROM requests WHERE status='pending'").fetchone()[0]
    ctx['pending_count'] = pend
    if tab == 'ads':
        sql = "SELECT * FROM ads WHERE status='active'"
        args = []
        if q:
            sql += " AND (title LIKE ? OR city LIKE ? OR neighborhood LIKE ? OR phone LIKE ? OR description LIKE ?)"
            args = [f'%{q}%'] * 5
        ctx['ads'] = [_ad_dict(r) for r in db().execute(sql + ' ORDER BY id DESC LIMIT 300', args).fetchall()]
        ctx['sample_count'] = db().execute("SELECT COUNT(*) FROM ads WHERE origin='sample' AND status='active'").fetchone()[0] + \
            db().execute("SELECT COUNT(*) FROM links WHERE sample=1 AND status='active'").fetchone()[0]
    elif tab == 'requests':
        ctx['reqs'] = [dict(r) for r in db().execute(
            "SELECT * FROM requests ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 200").fetchall()]
    elif tab == 'links':
        ctx['links'] = [dict(r) for r in db().execute(
            "SELECT * FROM links WHERE status!='removed' ORDER BY id DESC LIMIT 300").fetchall()]
    else:
        ctx['recent'] = [_ad_dict(r) for r in db().execute(
            "SELECT * FROM ads WHERE origin='paper' AND status='active' ORDER BY id DESC LIMIT 8").fetchall()]
    return render_template('luach/admin.html', **ctx)


@bp.route('/admin/paper', methods=['POST'])
@admin_required
def admin_paper():
    d = _ad_from_form(request.form)
    if not d['title'] or not d['city'] or not d['phone']:
        flash('חובה: כותרת, ריכוז וטלפון.', 'err')
        return redirect(url_for('luach.admin', tab='paper'))
    try:
        d['image'] = _save_image(request.files.get('image'))
    except ValueError as e:
        flash(str(e), 'err')
        return redirect(url_for('luach.admin', tab='paper'))
    d.update(source_paper=request.form.get('source_paper', '').strip()[:40], gilyon=request.form.get('gilyon', '').strip()[:40],
             published_date=request.form.get('published_date') or str(datetime.now().date()),
             highlight=request.form.get('highlight') if request.form.get('highlight') in HIGHLIGHTS else 'none',
             origin='paper', status='active', user_id=current_user()['id'], created_at=now())
    _insert_ad(d)
    db().commit()
    flash(f"המודעה \"{d['title']}\" עלתה ללוח.", 'ok')
    return redirect(url_for('luach.admin', tab='paper', paper=d['source_paper'], gilyon=d['gilyon'], date=d['published_date']))


@bp.route('/admin/ads/<int:ad_id>/highlight', methods=['POST'])
@admin_required
def admin_highlight(ad_id):
    hl = request.form.get('highlight')
    if hl in HIGHLIGHTS:
        db().execute('UPDATE ads SET highlight=? WHERE id=?', (hl, ad_id))
        db().commit()
        flash('ההדגשה עודכנה.', 'ok')
    return redirect(url_for('luach.admin', tab='ads', q=request.form.get('q', '')))


@bp.route('/admin/requests/<int:req_id>/<action>', methods=['POST'])
@admin_required
def admin_request(req_id, action):
    r = db().execute('SELECT * FROM requests WHERE id=?', (req_id,)).fetchone()
    if not r or r['status'] != 'pending' or action not in ('approve', 'reject'):
        abort(400)
    if action == 'approve':
        if r['kind'] == 'highlight':
            db().execute('UPDATE ads SET highlight=? WHERE id=?', (r['highlight'], r['ad_id']))
        else:
            db().execute("""INSERT INTO marketers (user_id, name, phone, extra_slots) VALUES (?,?,?,?)
                            ON CONFLICT(user_id) DO UPDATE SET extra_slots = extra_slots + excluded.extra_slots""",
                         (r['requester_id'], r['requester_name'], r['phone'], r['slots'] or EXTRA_SLOTS))
    db().execute('UPDATE requests SET status=? WHERE id=?', ('approved' if action == 'approve' else 'rejected', req_id))
    db().commit()
    flash('הבקשה אושרה.' if action == 'approve' else 'הבקשה נדחתה.', 'ok')
    return redirect(url_for('luach.admin', tab='requests'))


@bp.route('/admin/links/<int:link_id>/<action>', methods=['POST'])
@admin_required
def admin_link(link_id, action):
    status = {'hide': 'hidden', 'show': 'active', 'delete': 'removed'}.get(action)
    if not status:
        abort(400)
    db().execute('UPDATE links SET status=? WHERE id=?', (status, link_id))
    db().commit()
    flash({'hidden': 'הקישור הוסתר.', 'active': 'הקישור מוצג שוב.', 'removed': 'הקישור נמחק.'}[status], 'ok')
    return redirect(url_for('luach.admin', tab='links'))


@bp.route('/admin/samples/delete', methods=['POST'])
@admin_required
def admin_delete_samples():
    db().execute("UPDATE ads SET status='removed' WHERE origin='sample'")
    db().execute("UPDATE links SET status='removed' WHERE sample=1")
    db().commit()
    flash('כל מודעות וקישורי הדוגמה נמחקו.', 'ok')
    return redirect(url_for('luach.admin', tab='ads'))


# ------------------------------------------------------------------
# auth routes
# ------------------------------------------------------------------
def _login_user(uid):
    session['luach_uid'] = uid
    session.permanent = True


@bp.route('/login', methods=['GET', 'POST'])
def login():
    nxt = request.values.get('next', '')
    if request.method == 'POST':
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0]
        if _too_many(ip):
            flash('יותר מדי ניסיונות. נסה שוב בעוד כמה דקות.', 'err')
            return render_template('luach/auth.html', mode='login', next=nxt)
        email = request.form.get('email', '').strip().lower()
        row = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if not row or not row['password_hash'] or not check_password_hash(row['password_hash'], request.form.get('password', '')):
            _fails.setdefault(ip, []).append(time.time())
            flash('המייל או הסיסמה לא נכונים.', 'err')
            return render_template('luach/auth.html', mode='login', next=nxt, email=email)
        if not row['verified']:
            flash('צריך לאשר את כתובת המייל קודם. שלחנו לך קישור אימות.', 'err')
            return render_template('luach/auth.html', mode='login', next=nxt, email=email)
        _login_user(row['id'])
        return redirect(_safe_next(nxt))
    return render_template('luach/auth.html', mode='login', next=nxt)


@bp.route('/register', methods=['GET', 'POST'])
def register():
    nxt = request.values.get('next', '')
    if request.method == 'POST':
        name = request.form.get('name', '').strip()[:60]
        email = request.form.get('email', '').strip().lower()[:120]
        pw = request.form.get('password', '')
        if not name or '@' not in email or len(pw) < 8:
            flash('צריך שם, מייל תקין וסיסמה של 8 תווים לפחות.', 'err')
            return render_template('luach/auth.html', mode='register', next=nxt, email=email, name=name)
        if db().execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            flash('המייל הזה כבר רשום. אפשר להתחבר או לשחזר סיסמה.', 'err')
            return render_template('luach/auth.html', mode='login', next=nxt, email=email)
        verified = 0 if _mail_ready() else 1
        cur = db().execute('INSERT INTO users (email, name, password_hash, verified, created_at) VALUES (?,?,?,?,?)',
                           (email, name, generate_password_hash(pw), verified, now()))
        db().commit()
        if verified:
            _login_user(cur.lastrowid)
            return redirect(_safe_next(nxt))
        tok = _make_token(cur.lastrowid, 'verify', 72)
        link = _base_url() + url_for('luach.verify', token=tok)
        _send_mail(email, 'אימות כתובת המייל - לוח המודעות',
                   _mail_html(f'שלום {name},', 'כדי להשלים את ההרשמה ללוח המודעות, לחץ על הכפתור:', link, 'אימות המייל'))
        return render_template('luach/auth.html', mode='sent', email=email)
    return render_template('luach/auth.html', mode='register', next=nxt)


@bp.route('/verify/<token>')
def verify(token):
    row = _use_token(token, 'verify')
    if not row:
        flash('קישור האימות פג או אינו תקין.', 'err')
        return redirect(url_for('luach.login'))
    db().execute('UPDATE users SET verified=1 WHERE id=?', (row['user_id'],))
    db().execute('DELETE FROM tokens WHERE token=?', (token,))
    db().commit()
    _login_user(row['user_id'])
    flash('המייל אומת. ברוך הבא!', 'ok')
    return redirect(url_for('luach.board'))


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        row = db().execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if row and _mail_ready():
            tok = _make_token(row['id'], 'reset', 2)
            link = _base_url() + url_for('luach.reset', token=tok)
            _send_mail(email, 'שחזור סיסמה - לוח המודעות',
                       _mail_html('שחזור סיסמה', 'קיבלנו בקשה לבחור סיסמה חדשה. הקישור בתוקף לשעתיים.', link, 'בחירת סיסמה חדשה'))
        return render_template('luach/auth.html', mode='sent_reset', email=email)
    return render_template('luach/auth.html', mode='forgot')


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset(token):
    row = _use_token(token, 'reset')
    if not row:
        flash('הקישור פג או אינו תקין. אפשר לבקש קישור חדש.', 'err')
        return redirect(url_for('luach.forgot'))
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if len(pw) < 8:
            flash('הסיסמה צריכה להיות 8 תווים לפחות.', 'err')
            return render_template('luach/auth.html', mode='reset', token=token)
        db().execute('UPDATE users SET password_hash=?, verified=1 WHERE id=?', (generate_password_hash(pw), row['user_id']))
        db().execute('DELETE FROM tokens WHERE token=?', (token,))
        db().commit()
        _login_user(row['user_id'])
        flash('הסיסמה עודכנה.', 'ok')
        return redirect(url_for('luach.board'))
    return render_template('luach/auth.html', mode='reset', token=token)


@bp.route('/google', methods=['POST'])
def google_login():
    cid = os.environ.get('GOOGLE_CLIENT_ID', '')
    cred = request.form.get('credential', '')
    if not cid or not cred:
        abort(400)
    import requests as rq
    try:
        info = rq.get('https://oauth2.googleapis.com/tokeninfo', params={'id_token': cred}, timeout=10).json()
    except Exception:
        info = {}
    if info.get('aud') != cid or info.get('email_verified') not in ('true', True) or not info.get('email'):
        flash('ההתחברות עם Google נכשלה.', 'err')
        return redirect(url_for('luach.login'))
    email = info['email'].lower()
    conn = db()
    row = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if row:
        conn.execute('UPDATE users SET google_sub=?, verified=1 WHERE id=?', (info.get('sub'), row['id']))
        uid = row['id']
    else:
        uid = conn.execute('INSERT INTO users (email, name, google_sub, verified, created_at) VALUES (?,?,?,1,?)',
                           (email, info.get('name') or email.split('@')[0], info.get('sub'), now())).lastrowid
    conn.commit()
    _login_user(uid)
    return redirect(_safe_next(request.form.get('next', '')))


@bp.route('/logout', methods=['POST'])
def logout():
    session.pop('luach_uid', None)
    return redirect(url_for('luach.board'))


# ------------------------------------------------------------------
# template filters
# ------------------------------------------------------------------
@bp.app_template_filter('shekel')
def _shekel(v):
    if v is None or v == '':
        return 'מחיר לפי בקשה'
    return '₪' + f'{int(v):,}'


@bp.app_template_filter('rooms')
def _rooms(v):
    if v is None:
        return ''
    return str(int(v)) if float(v).is_integer() else str(v)


# ------------------------------------------------------------------
def init_luach(app):
    if not app.secret_key:
        seed = os.environ.get('LUACH_SECRET_KEY') or os.environ.get('SECRET_KEY')
        if not seed and os.environ.get('LAB_ACCESS_CODE'):
            seed = hashlib.sha256(('luach-session|' + os.environ['LAB_ACCESS_CODE']).encode()).hexdigest()
        app.secret_key = seed or secrets.token_hex(32)
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(days=60))
    app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')
    os.makedirs(DATA_DIR, exist_ok=True)
    _init_db()
    app.teardown_appcontext(_close_db)
    app.register_blueprint(bp)
