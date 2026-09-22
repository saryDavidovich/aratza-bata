"""
צ'אט יצירת סרטונים - Gemini Omni Flash / Veo 3.1 Lite
=====================================================
דף /videochat - כותבים פרומפט (ואפשר להעלות תמונות/סרטונים), הסרטון נוצר ברקע,
מוצג בדף ונשלח למייל. ב-Omni Flash השיחה "זוכרת" את הסרטון הקודם
(previous_interaction_id), כך שאפשר לבקש תיקונים ("תשנה את הרקע ללילה")
בלי לתאר הכל מחדש. "שיחה חדשה" מתחילה בלי קשר לעבר.

מחירים (Gemini API, ספטמבר 2026 - לבדוק מדי פעם ב-ai.google.dev/gemini-api/docs/pricing):
  Omni Flash 720p  ≈ $0.10 לשנייה   (רשמי)
  Omni Flash 1080p ≈ $0.15 לשנייה   (הערכה - 1080p הוא upscale, המחיר הרשמי לא מפורט)
  Veo 3.1 Lite 720p  ≈ $0.05 לשנייה
  Veo 3.1 Lite 1080p ≈ $0.08 לשנייה
משלמים רק על סרטון שנוצר בהצלחה.
"""
import os
import re
import json
import time
import uuid
import base64
import sqlite3
import logging
import threading
from datetime import datetime

from flask import request, render_template_string, redirect, send_from_directory

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_CHAT_DIR = os.path.join(BASE_DIR, 'video_chat_files')
os.makedirs(VIDEO_CHAT_DIR, exist_ok=True)
VIDEO_CHAT_DB = os.path.join(BASE_DIR, 'video_chat.db')

OMNI_MODEL = 'gemini-omni-1.1-flash'           # GA (ה-preview נסגר ב-30.9.2026)
VEO_LITE_MODEL = 'veo-3.1-lite-generate-preview'
TRANSLATE_MODEL = 'gemini-3.1-flash-lite'      # תרגום הפרומפט לאנגלית - עולה פחות מאגורה

ENGINES = {
    'omni': 'Gemini Omni Flash (מומלץ - זוכר את הסרטון ומאפשר תיקונים)',
    'veo_lite': 'Veo 3.1 Lite (הכי זול - כל הודעה = סרטון חדש, בלי זיכרון)',
}
# $ לשנייה - לחישוב הערכת עלות בלבד
PRICE_PER_SECOND = {
    ('omni', '720p'): 0.10,
    ('omni', '1080p'): 0.15,
    ('veo_lite', '720p'): 0.05,
    ('veo_lite', '1080p'): 0.08,
}

MAX_IMAGE_SIZE = 15 * 1024 * 1024
MAX_VIDEO_SIZE = 100 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
IMAGE_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'heic', 'heif'}
VIDEO_EXT = {'mp4': 'video/mp4', 'mov': 'video/quicktime', 'webm': 'video/webm',
             'avi': 'video/x-msvideo', '3gp': 'video/3gpp', 'mkv': 'video/x-matroska'}
EMAIL_ATTACH_LIMIT = 20 * 1024 * 1024  # מעל זה נשלח רק קישור (SendGrid מגביל ל-30MB למייל)

_db_lock = threading.Lock()


# ------------------------------------------------------------------ DB
def _db():
    conn = sqlite3.connect(VIDEO_CHAT_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS video_chats (
        id TEXT PRIMARY KEY, email TEXT, engine TEXT, aspect TEXT, resolution TEXT,
        duration INTEGER, last_interaction_id TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS video_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, text TEXT,
        sent_prompt TEXT, attachments TEXT, video_filename TEXT, status TEXT,
        cost REAL, created_at TEXT)""")
    conn.commit()
    return conn


def _get_chat(chat_id):
    conn = _db()
    row = conn.execute("SELECT id, email, engine, aspect, resolution, duration, last_interaction_id "
                       "FROM video_chats WHERE id=?", (chat_id,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ['id', 'email', 'engine', 'aspect', 'resolution', 'duration', 'last_interaction_id']
    return dict(zip(keys, row))


def _load_messages(chat_id):
    conn = _db()
    rows = conn.execute("SELECT id, role, text, sent_prompt, attachments, video_filename, status, cost "
                        "FROM video_chat_messages WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()
    conn.close()
    keys = ['id', 'role', 'text', 'sent_prompt', 'attachments', 'video_filename', 'status', 'cost']
    msgs = []
    for r in rows:
        m = dict(zip(keys, r))
        m['attachments'] = json.loads(m['attachments'] or '[]')
        msgs.append(m)
    return msgs


def _insert_message(chat_id, role, text, attachments=None, status='done'):
    with _db_lock:
        conn = _db()
        cur = conn.execute(
            "INSERT INTO video_chat_messages (chat_id, role, text, attachments, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, role, text, json.dumps(attachments or []), status, datetime.utcnow().isoformat()))
        conn.commit()
        msg_id = cur.lastrowid
        conn.close()
    return msg_id


def _update_message(msg_id, **fields):
    with _db_lock:
        conn = _db()
        sets = ', '.join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE video_chat_messages SET {sets} WHERE id=?", (*fields.values(), msg_id))
        conn.commit()
        conn.close()


# ------------------------------------------------------------------ Gemini helpers
def _client():
    from google import genai
    return genai.Client(api_key=os.environ.get('GOOGLE_API_KEY'))


def _translate_to_english(client, text):
    """מודלי הווידאו תומכים רשמית רק באנגלית - מתרגמים את הפרומפט (בלי לשנות תוכן).
    אם התרגום נכשל - ממשיכים עם המקור."""
    if not re.search(r'[\u0590-\u05FF]', text):
        return text  # אין עברית - אין מה לתרגם
    try:
        resp = client.models.generate_content(
            model=TRANSLATE_MODEL,
            contents=("Translate the following video-generation instruction to English. "
                      "Keep every detail and quoted dialogue meaning exactly; do not add or remove ideas. "
                      "Keep tags like <IMAGE_REF_0>, <FIRST_FRAME> unchanged. "
                      "Return only the translation.\n\n" + text),
        )
        out = (resp.text or '').strip()
        return out or text
    except Exception as e:
        log.warning(f"videochat translate failed, using original: {e}")
        return text


def _wait_file_active(client, file_name, timeout=300):
    start = time.time()
    while True:
        f = client.files.get(name=file_name)
        state = getattr(getattr(f, 'state', None), 'name', str(getattr(f, 'state', '')))
        if state == 'ACTIVE':
            return f
        if state == 'FAILED':
            raise RuntimeError("עיבוד הקובץ בשרתי גוגל נכשל")
        if time.time() - start > timeout:
            raise RuntimeError("עיבוד הקובץ בשרתי גוגל לקח יותר מדי זמן")
        time.sleep(4)


def _extract_omni_video_bytes(client, interaction):
    video = getattr(interaction, 'output_video', None)
    if video is None:
        reason = getattr(interaction, 'output_text', '') or f"status={getattr(interaction, 'status', '?')}"
        raise RuntimeError(f"לא התקבל סרטון (ייתכן שנחסם במסנני הבטיחות). תגובת המודל: {reason}")
    if getattr(video, 'data', None):
        data = video.data
        return base64.b64decode(data) if isinstance(data, str) else bytes(data)
    uri = getattr(video, 'uri', None)
    if not uri:
        raise RuntimeError("התקבל סרטון בלי נתונים ובלי קישור")
    m = re.search(r'files/([A-Za-z0-9_-]+)', uri)
    if m:
        file_name = f"files/{m.group(1)}"
        _wait_file_active(client, file_name)
        try:
            data = client.files.download(file=file_name)
            if data:
                return data
        except Exception as e:
            log.warning(f"files.download failed, falling back to direct GET: {e}")
    import requests
    r = requests.get(uri, headers={'x-goog-api-key': os.environ.get('GOOGLE_API_KEY', '')},
                     timeout=300, allow_redirects=True)
    r.raise_for_status()
    return r.content


def _run_omni(client, chat, prompt, files):
    parts = []
    for f in files:
        if f['kind'] == 'image':
            with open(os.path.join(VIDEO_CHAT_DIR, f['name']), 'rb') as fh:
                parts.append({"type": "image", "data": base64.b64encode(fh.read()).decode(),
                              "mime_type": "image/png"})
        else:
            uploaded = client.files.upload(file=os.path.join(VIDEO_CHAT_DIR, f['name']),
                                           config={'mime_type': f['mime']})
            uploaded = _wait_file_active(client, uploaded.name)
            parts.append({"type": "video", "uri": uploaded.uri, "mime_type": f['mime']})
    parts.append({"type": "text", "text": prompt})

    kwargs = dict(
        model=OMNI_MODEL,
        input=parts if len(parts) > 1 else prompt,
        response_format={
            "type": "video",
            "delivery": "uri",  # סרטונים מעל 4MB לא עוברים inline
            "aspect_ratio": chat['aspect'],
            "resolution": chat['resolution'],
            "duration": f"{chat['duration']}s",
        },
        timeout=900,
    )
    if chat.get('last_interaction_id'):
        kwargs['previous_interaction_id'] = chat['last_interaction_id']

    interaction = client.interactions.create(**kwargs)
    video_bytes = _extract_omni_video_bytes(client, interaction)
    return video_bytes, getattr(interaction, 'id', None)


def _run_veo_lite(client, chat, prompt, files):
    from google.genai import types
    if any(f['kind'] == 'video' for f in files):
        raise RuntimeError("Veo 3.1 Lite לא מקבל סרטונים כקלט - לעריכת סרטון בחרו Omni Flash (שיחה חדשה)")
    images = [f for f in files if f['kind'] == 'image']

    duration = int(chat['duration'])
    if chat['resolution'] == '1080p':
        duration = 8  # ב-1080p Veo מחייב 8 שניות
    cfg = dict(aspect_ratio=chat['aspect'], resolution=chat['resolution'],
               duration_seconds=duration, number_of_videos=1)

    def _img(f):
        with open(os.path.join(VIDEO_CHAT_DIR, f['name']), 'rb') as fh:
            return types.Image(image_bytes=fh.read(), mime_type='image/png')

    first_image = _img(images[0]) if images else None
    if len(images) >= 2:
        cfg['last_frame'] = _img(images[1])  # תמונה ראשונה = פריים פתיחה, שנייה = פריים סיום

    operation = client.models.generate_videos(
        model=VEO_LITE_MODEL, prompt=prompt, image=first_image,
        config=types.GenerateVideosConfig(**cfg))
    start = time.time()
    while not operation.done:
        if time.time() - start > 900:
            raise RuntimeError("יצירת הסרטון לקחה יותר מ-15 דקות - בוטל")
        time.sleep(10)
        operation = client.operations.get(operation)

    if getattr(operation, 'error', None):
        raise RuntimeError(f"שגיאה מ-Veo: {operation.error}")
    resp = operation.response
    vids = getattr(resp, 'generated_videos', None) if resp else None
    if not vids:
        reasons = getattr(resp, 'rai_media_filtered_reasons', None) if resp else None
        raise RuntimeError(f"לא נוצר סרטון (כנראה נחסם במסנני הבטיחות). {reasons or ''}")
    gv = vids[0]
    data = client.files.download(file=gv.video)
    return (data or gv.video.video_bytes), None


def _send_video_email(to, prompt, video_bytes, video_filename, cost, app_base_url):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, Attachment, FileContent, FileName, FileType, Disposition
        link = f"{app_base_url}/videochat/file/{video_filename}" if app_base_url else ''
        size_mb = len(video_bytes) / 1024 / 1024
        attach = len(video_bytes) <= EMAIL_ATTACH_LIMIT
        html = f"""<div dir='rtl' style='font-family:Arial;max-width:600px'>
<h3>🎬 סרטון חדש מצ'אט הסרטונים</h3>
<p style='color:#6b7280'>הבקשה: {prompt}</p>
<p>{'הסרטון מצורף למייל.' if attach else f'הסרטון גדול מדי לצירוף ({size_mb:.1f}MB) - הורדה בקישור למטה.'}</p>
{f"<p><a href='{link}'>⬇️ הורדת הסרטון</a></p>" if link else ''}
<p style='color:#6b7280;font-size:12px'>עלות משוערת: ${cost:.2f}</p>
</div>"""
        sg = sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY'))
        message = Mail(
            from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
            to_emails=to, subject="🎬 מעבדה - סרטון חדש מ-Gemini", html_content=html)
        if attach:
            message.attachment = Attachment(
                FileContent(base64.b64encode(video_bytes).decode()), FileName('video.mp4'),
                FileType('video/mp4'), Disposition('attachment'))
        sg.send(message)
        log.info(f"video email sent to {to}")
    except Exception as e:
        log.error(f"video email error: {e}")


def _video_worker(chat_id, model_msg_id, prompt, files, translate, app_base_url):
    chat = _get_chat(chat_id)
    try:
        client = _client()
        sent_prompt = _translate_to_english(client, prompt) if translate else prompt
        _update_message(model_msg_id, sent_prompt=sent_prompt)

        if chat['engine'] == 'veo_lite':
            video_bytes, interaction_id = _run_veo_lite(client, chat, sent_prompt, files)
        else:
            video_bytes, interaction_id = _run_omni(client, chat, sent_prompt, files)

        video_filename = f"{uuid.uuid4().hex}.mp4"
        with open(os.path.join(VIDEO_CHAT_DIR, video_filename), 'wb') as fh:
            fh.write(video_bytes)

        seconds = 8 if (chat['engine'] == 'veo_lite' and chat['resolution'] == '1080p') else int(chat['duration'])
        cost = seconds * PRICE_PER_SECOND.get((chat['engine'], chat['resolution']), 0.10)
        _update_message(model_msg_id, video_filename=video_filename, status='done', cost=cost,
                        text=None)
        if interaction_id:
            with _db_lock:
                conn = _db()
                conn.execute("UPDATE video_chats SET last_interaction_id=? WHERE id=?", (interaction_id, chat_id))
                conn.commit()
                conn.close()
        if chat['email']:
            _send_video_email(chat['email'], prompt, video_bytes, video_filename, cost, app_base_url)
    except Exception as e:
        log.error(f"videochat generate error: {e}", exc_info=True)
        _update_message(model_msg_id, status='error', text=f"שגיאה ביצירת הסרטון: {e}")


# ------------------------------------------------------------------ HTML
VIDEO_CHAT_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{% if pending %}<meta http-equiv="refresh" content="10">{% endif %}
<title>צ'אט סרטונים - Gemini</title>
<style>
body{font-family:Arial,sans-serif;max-width:760px;margin:30px auto;padding:0 16px;color:#111}
.msg-row{display:flex;margin:14px 0}
.msg-row.user{justify-content:flex-end}
.bubble{max-width:80%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6}
.bubble.user{background:#2563eb;color:#fff}
.bubble.model{background:#f0f0f0;color:#111}
.bubble.err{background:#fef2f2;color:#991b1b}
.bubble video{max-width:100%;border-radius:8px;margin-top:8px;display:block}
.bubble img{max-width:160px;border-radius:6px;margin:6px 4px 0 0}
.sent{font-size:11px;opacity:.75;margin-top:6px}
.cost{font-size:12px;color:#6b7280;margin-top:6px}
textarea{flex:1;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;font-family:Arial}
input[type=email],select{width:100%;padding:9px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box}
button{padding:10px 20px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer}
button:hover{background:#1d4ed8}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
a.newchat{font-size:13px;color:#2563eb;text-decoration:none}
.note{color:#6b7280;font-size:12px;margin-top:6px}
label{display:block;font-weight:bold;font-size:13px;margin-top:10px}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}
.settings{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin-top:10px}
.pending{background:#fffbeb;border:1px solid #f59e0b;color:#92400e;padding:12px;border-radius:8px;margin:14px 0;font-size:14px}
.chatinfo{font-size:12px;color:#374151;background:#f9fafb;border-radius:6px;padding:6px 10px}
</style></head><body>
<div class="topbar">
  <h2>🎬 צ'אט סרטונים - Gemini</h2>
  <a class="newchat" href="/videochat?access_code={{ access_code }}">🆕 שיחה חדשה</a>
</div>
<p class="note">כותבים מה ליצור (אפשר גם להעלות תמונות/סרטונים). הסרטון נוצר ברקע (בדרך כלל 1-6 דקות), מופיע כאן ונשלח למייל.
ב-Omni Flash אפשר לבקש תיקונים בהמשך השיחה - הוא זוכר על איזה סרטון מדובר.</p>
<p><a href="/lab" style="font-size:13px;color:#2563eb;text-decoration:none">→ חזרה למעבדה</a></p>

{% if chat %}
<div class="chatinfo">מנוע: {{ engines[chat.engine] }} · {{ chat.resolution }} · {{ chat.aspect }} · {{ chat.duration }} שניות
{% if total_cost %} · עלות משוערת בשיחה עד עכשיו: ${{ '%.2f' % total_cost }}{% endif %}</div>
{% endif %}

{% if error %}<div style="color:#991b1b;background:#fef2f2;padding:10px;border-radius:6px;margin:10px 0">{{ error }}</div>{% endif %}

{% for m in messages %}
<div class="msg-row {{ 'user' if m.role == 'user' else '' }}">
  <div class="bubble {{ 'user' if m.role == 'user' else ('err' if m.status == 'error' else 'model') }}">
    {% if m.role == 'user' %}
      {{ m.text }}
      {% for a in m.attachments %}
        {% if a.kind == 'image' %}<img src="/videochat/file/{{ a.name }}">
        {% else %}<video src="/videochat/file/{{ a.name }}" controls style="max-width:200px"></video>{% endif %}
      {% endfor %}
    {% elif m.status == 'pending' %}
      ⏳ יוצר את הסרטון... (הדף מתרענן לבד)
    {% elif m.status == 'error' %}
      {{ m.text }}
    {% else %}
      {% if m.video_filename %}<video src="/videochat/file/{{ m.video_filename }}" controls></video>
      <a href="/videochat/file/{{ m.video_filename }}" download style="font-size:12px">⬇️ הורדה</a>{% endif %}
      {% if m.cost %}<div class="cost">עלות משוערת: ${{ '%.2f' % m.cost }}</div>{% endif %}
    {% endif %}
    {% if m.sent_prompt and m.role != 'user' %}<div class="sent">נשלח למודל: {{ m.sent_prompt }}</div>{% endif %}
  </div>
</div>
{% endfor %}

{% if pending %}
<div class="pending">⏳ סרטון בתהליך יצירה. אפשר לסגור את הדף - הוא יישלח למייל כשיהיה מוכן.</div>
{% else %}
<form method="post" action="/videochat/send" enctype="multipart/form-data">
  <input type="hidden" name="access_code" value="{{ access_code }}">
  <input type="hidden" name="chat_id" value="{{ chat_id }}">
  {% if not chat %}
  <div class="settings">
    <label>שלח את הסרטונים למייל</label>
    <input type="email" name="email" value="{{ default_email }}" required>
    <label>מנוע</label>
    <select name="engine">
      {% for k, v in engines.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}
    </select>
    <div class="grid">
      <div><label>איכות</label>
        <select name="resolution"><option value="720p">720p (מומלץ)</option><option value="1080p">1080p (יקר יותר)</option></select></div>
      <div><label>כיוון</label>
        <select name="aspect"><option value="16:9">לרוחב 16:9</option><option value="9:16">לאורך 9:16 (סטטוס/רילס)</option></select></div>
      <div><label>אורך</label>
        <select name="duration">
          <option value="4">4 שניות</option><option value="6">6 שניות</option>
          <option value="8" selected>8 שניות</option><option value="10">10 שניות (Omni בלבד)</option>
        </select></div>
    </div>
    <p class="note">הערכת עלות לסרטון 8 שניות: Omni 720p ≈ $0.80 · Omni 1080p ≈ $1.20 · Veo Lite 720p ≈ $0.40 · Veo Lite 1080p ≈ $0.64.
    ב-Veo Lite: ב-1080p האורך תמיד 8 שניות, ו-10 שניות לא נתמך (יורד ל-8).</p>
  </div>
  {% endif %}
  <label>העלאת קבצים (אופציונלי - תמונות ו/או סרטונים, עד {{ max_files }})</label>
  <input type="file" name="files" multiple accept="image/*,video/*">
  <p class="note" style="margin-top:4px">תמונה = פריים פתיחה / דמות / מוצר שיופיע בסרטון. סרטון (עד 10 שניות, Omni בלבד) = לעריכה או להמשך.
  ב-Omni אפשר לכתוב בפרומפט &lt;IMAGE_REF_0&gt; כדי להפנות לתמונה הראשונה, &lt;FIRST_FRAME&gt; לפריים פתיחה וכו'.</p>
  <label>{% if messages %}תיקון / המשך{% else %}מה ליצור{% endif %}</label>
  <div style="display:flex;gap:8px;align-items:flex-end">
    <textarea name="prompt" rows="3" placeholder="{% if messages %}למשל: תשנה את השעה ללילה, השאר את כל השאר אותו דבר{% else %}תאר את הסצנה: מה רואים, מה זז, תנועת מצלמה, תאורה, סאונד/מוזיקה/דיבור...{% endif %}"></textarea>
    <button type="submit">צור</button>
  </div>
  <label style="font-weight:normal"><input type="checkbox" name="translate" value="1" checked> לתרגם את הבקשה לאנגלית לפני השליחה (המודלים עובדים הכי טוב באנגלית)</label>
</form>
{% endif %}
</body></html>"""

ACCESS_GATE_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<title>צ'אט סרטונים - Gemini</title>
<style>
body{font-family:Arial,sans-serif;max-width:400px;margin:80px auto;padding:0 16px;color:#111}
input{width:100%;padding:10px;margin-top:8px;box-sizing:border-box;border:1px solid #ccc;border-radius:6px;font-size:15px}
button{margin-top:16px;padding:10px 24px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer}
.err{color:#991b1b;background:#fef2f2;padding:10px;border-radius:6px;margin-top:12px}
</style></head><body>
<h2>🎬 צ'אט סרטונים - Gemini</h2>
<form method="get" action="/videochat">
  <label>קוד גישה</label>
  <input type="password" name="access_code" required autofocus>
  <button type="submit">כניסה</button>
</form>
{% if wrong %}<div class="err">קוד גישה שגוי</div>{% endif %}
</body></html>"""


# ------------------------------------------------------------------ routes
def init_videochat(app, *, require_access, normalize_image, default_email, app_base_url):

    def _render(chat_id, access_code, error=None, status=200):
        chat = _get_chat(chat_id)
        messages = _load_messages(chat_id)
        pending = any(m['status'] == 'pending' for m in messages)
        total_cost = sum((m['cost'] or 0) for m in messages)
        return render_template_string(
            VIDEO_CHAT_HTML, messages=messages, chat=chat, chat_id=chat_id, access_code=access_code,
            default_email=default_email, engines=ENGINES, pending=pending, total_cost=total_cost,
            max_files=MAX_FILES_PER_MESSAGE, error=error), status

    @app.route('/videochat', methods=['GET'])
    def videochat_page():
        access_code = request.args.get('access_code', '')
        if not access_code:
            return render_template_string(ACCESS_GATE_HTML, wrong=False)
        if not require_access(access_code):
            return render_template_string(ACCESS_GATE_HTML, wrong=True), 403
        chat_id = request.args.get('chat_id', '') or uuid.uuid4().hex
        return _render(chat_id, access_code)

    @app.route('/videochat/send', methods=['POST'])
    def videochat_send():
        access_code = request.form.get('access_code', '')
        if not require_access(access_code):
            return "קוד גישה שגוי", 403
        chat_id = request.form.get('chat_id') or uuid.uuid4().hex
        prompt = (request.form.get('prompt') or '').strip()
        translate = request.form.get('translate') == '1'

        chat = _get_chat(chat_id)
        if chat and any(m['status'] == 'pending' for m in _load_messages(chat_id)):
            return redirect(f"/videochat?access_code={access_code}&chat_id={chat_id}")

        uploads = [f for f in request.files.getlist('files') if f and f.filename]
        if not prompt:
            return _render(chat_id, access_code, error="יש לכתוב מה ליצור / מה לשנות")
        if len(uploads) > MAX_FILES_PER_MESSAGE:
            return _render(chat_id, access_code, error=f"אפשר להעלות עד {MAX_FILES_PER_MESSAGE} קבצים בהודעה")

        # שמירת הקבצים שהועלו (תמונות מנורמלות ל-PNG כמו בצ'אט התמונות)
        saved = []
        for up in uploads:
            ext = os.path.splitext(up.filename)[1].lstrip('.').lower()
            try:
                if ext in IMAGE_EXT:
                    png = normalize_image(up)
                    name = f"{uuid.uuid4().hex}.png"
                    with open(os.path.join(VIDEO_CHAT_DIR, name), 'wb') as fh:
                        fh.write(png)
                    saved.append({'kind': 'image', 'name': name, 'mime': 'image/png'})
                elif ext in VIDEO_EXT:
                    raw = up.read()
                    if not raw:
                        raise ValueError("הקובץ ריק")
                    if len(raw) > MAX_VIDEO_SIZE:
                        raise ValueError(f"הסרטון גדול מדי (מקסימום {MAX_VIDEO_SIZE // 1024 // 1024}MB)")
                    name = f"{uuid.uuid4().hex}.{ext}"
                    with open(os.path.join(VIDEO_CHAT_DIR, name), 'wb') as fh:
                        fh.write(raw)
                    saved.append({'kind': 'video', 'name': name, 'mime': VIDEO_EXT[ext]})
                else:
                    raise ValueError(f"סוג קובץ לא נתמך: .{ext}")
            except Exception as e:
                return _render(chat_id, access_code, error=f"בעיה בקובץ {up.filename}: {e}")

        if not chat:
            engine = request.form.get('engine', 'omni')
            engine = engine if engine in ENGINES else 'omni'
            resolution = request.form.get('resolution', '720p')
            resolution = resolution if resolution in ('720p', '1080p') else '720p'
            aspect = request.form.get('aspect', '16:9')
            aspect = aspect if aspect in ('16:9', '9:16') else '16:9'
            try:
                duration = int(request.form.get('duration', '8'))
            except ValueError:
                duration = 8
            allowed = (4, 6, 8, 10) if engine == 'omni' else (4, 6, 8)
            duration = duration if duration in allowed else 8
            email = (request.form.get('email') or default_email or '').strip()
            with _db_lock:
                conn = _db()
                conn.execute("INSERT INTO video_chats (id, email, engine, aspect, resolution, duration, "
                             "last_interaction_id, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                             (chat_id, email, engine, aspect, resolution, duration, datetime.utcnow().isoformat()))
                conn.commit()
                conn.close()

        _insert_message(chat_id, 'user', prompt, attachments=saved)
        model_msg_id = _insert_message(chat_id, 'model', None, status='pending')
        threading.Thread(target=_video_worker,
                         args=(chat_id, model_msg_id, prompt, saved, translate, app_base_url),
                         daemon=True).start()
        return redirect(f"/videochat?access_code={access_code}&chat_id={chat_id}")

    @app.route('/videochat/file/<path:filename>')
    def videochat_file(filename):
        return send_from_directory(VIDEO_CHAT_DIR, filename)

    # שרת שהופעל מחדש באמצע יצירה - ההודעות שנשארו "בתהליך" לעולם לא יסתיימו
    try:
        with _db_lock:
            conn = _db()
            conn.execute("UPDATE video_chat_messages SET status='error', "
                         "text='השרת הופעל מחדש באמצע היצירה - נסו לשלוח שוב' WHERE status='pending'")
            conn.commit()
            conn.close()
    except Exception as e:
        log.error(f"videochat startup cleanup error: {e}")
