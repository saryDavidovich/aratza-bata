"""
השוואת תמלול מקצועי - שלושה מנועים במקביל, מחולק לקטעים לפי ההקלטה
=====================================================================
דף /compare: מעלים קובץ אודיו/וידאו, או מדביקים קישור להקלטה קיימת (הקישור
"להורדת ההקלטה" מהמייל של תמלול פון, או כל קישור ישיר לקובץ).

שלוש עמודות, על אותו קובץ בדיוק:
  1. AlefBot - בדיוק כמו "תמלול מקצועי" במערכת הראשית (model_tier='standard').
  2. OpenAI רגיל - gpt-transcribe עם תיאור קצר בלבד.
  3. OpenAI משופר - gpt-transcribe עם רקע + רשימת מילים צפויות + שפות (אפשרות 1),
     ואחריו שלב תיקון במודל חושב (אפשרות 2): מתקן מילים שנשמעו לא נכון וציטוטי
     פסוקים, בלי לשנות תוכן. התיקון נעשה קטע-קטע (עם הקטע שלפני ואחרי כהקשר),
     כך שהחלוקה לקטעים נשמרת בדיוק.

חלוקה לקטעים: ההקלטה נחתכת לקטעים של כ-40 שניות, בנקודה השקטה ביותר שבין 30
ל-50 שניות (כדי לא לחתוך באמצע מילה). כל קטע של OpenAI מתומלל בנפרד, ולכן הוא
מתאים בדיוק לזמן בהקלטה. AlefBot מתמלל את כל ההקלטה ברצף (כמו במערכת), והטקסט
שלו מחולק לאותם קטעים לפי השוואת מילים מול התמלול של OpenAI (קירוב טוב, לא
מדויק במאה אחוז במילים שבגבול).

בדף: נגן קבוע למעלה, ושורה לכל קטע עם שלוש העמודות - כל שורה מסתיימת ביחד,
ולחיצה על הזמן מנגנת מאותו מקום. רוחב כל עמודה יחסי לאורך הטקסט שלה, כך
ששלוש העמודות "נגמרות" בערך באותו גובה. המייל: אותה טבלה + 3 קובצי Word.

משתנים (אופציונלי): LAB_OPENAI_TRANSCRIBE_MODEL (ברירת מחדל gpt-transcribe),
LAB_OPENAI_FIX_MODEL (ברירת מחדל gpt-5), LAB_PRICE_ALEFBOT_PER_MIN /
LAB_PRICE_OPENAI_PER_MIN (שקלים לדקה, להערכת עלות).

נפרד לגמרי מהמערכת הראשית: לא נוגע ב-DB שלה, בלקוחות או בחיובים.
"""
import os
import io
import re
import json
import time
import uuid
import wave
import base64
import shutil
import sqlite3
import difflib
import logging
import tempfile
import threading
import subprocess
from datetime import datetime
from html import escape
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import request, render_template_string, redirect, send_from_directory, abort

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COMPARE_DIR = os.path.join(BASE_DIR, 'compare_files')
os.makedirs(COMPARE_DIR, exist_ok=True)
COMPARE_DB = os.path.join(BASE_DIR, 'compare.db')

ALEFBOT_BASE = 'https://alef-bot.top/api/v1'
OPENAI_MODEL = os.environ.get('LAB_OPENAI_TRANSCRIBE_MODEL', 'gpt-transcribe')
FIX_MODEL = os.environ.get('LAB_OPENAI_FIX_MODEL', 'gpt-5')
MAX_FILE_SIZE = 300 * 1024 * 1024
SEG_TARGET, SEG_MIN, SEG_MAX = 40, 30, 50     # שניות
WORKERS = 6

ENGINES = ('alefbot', 'openai', 'plus')
ENGINE_NAMES = {'alefbot': 'AlefBot (התמלול המקצועי היום)',
                'openai': 'OpenAI רגיל',
                'plus': 'OpenAI עם רקע + תיקון'}
LANG_NAMES = {'he': 'עברית', 'yi': 'אידיש', 'en': 'אנגלית'}

BASIC_PROMPT = 'תמלול שיחה או שיעור בעברית.'
DEFAULT_CONTEXT = ('שיעור תורה או שיחה בעברית, לעתים עם ארמית. יש ציטוטים מפסוקי התנ"ך, '
                   'מהגמרא ומהמשנה, ראשי תיבות ומינוח תורני. יש לכתוב ראשי תיבות עם גרשיים.')
DEFAULT_KEYWORDS = '\n'.join([
    'גמרא', 'משנה', 'ברייתא', 'סוגיא', 'רש"י', 'תוספות', 'רמב"ם', 'שולחן ערוך', 'משנה ברורה',
    'קל וחומר', 'גזירה שווה', 'קושיא', 'תירוץ', 'הקב"ה', 'פסוק', 'תנא', 'אמורא', 'מאי טעמא',
    'תא שמע', 'איתמר', 'אמר רבי', 'דאורייתא', 'דרבנן', 'מצוה', 'הלכה', 'בראשית', 'שמות',
    'ויקרא', 'במדבר', 'דברים', 'תהלים', 'משלי',
])
FIX_INSTRUCTIONS = """אתה מגיה תמלול אוטומטי של הקלטה בעברית (לעתים עם ארמית ומינוח תורני).
תקבל קטע אחד לתיקון, ולצידו את הקטע שלפניו ואת הקטע שאחריו - רק כהקשר, אל תחזיר אותם.

מה מותר לתקן:
- מילים שנשמעו לא נכון ונכתבו כמילה אחרת שנשמעת דומה, כשההקשר מראה בבירור מה נאמר.
- ציטוט של פסוק או של משפט ידוע מהגמרא/משנה שנכתב בשיבוש - לנוסח המדויק, אבל רק את
  המילים שהדובר באמת אמר. אם ציטט רק חלק מהפסוק - אל תשלים את השאר.
- ראשי תיבות לכתיב המקובל עם גרשיים, ופיסוק בסיסי.

מה אסור:
- לשנות סגנון, סדר מילים, או ניסוח של הדובר. לא לסכם, לא להוסיף, לא להשמיט.
- "לתקן" כשלא ברור מה נאמר - במקרה כזה השאר כמו שהוא.

החזר אך ורק את הטקסט המתוקן של הקטע לתיקון, בלי הסברים ובלי מירכאות סביבו."""


# ---------------------------------------------------------------- DB

_lock = threading.Lock()
_COLS = {
    'id': 'TEXT PRIMARY KEY', 'created_at': 'TEXT', 'source': 'TEXT', 'email': 'TEXT',
    'language': 'TEXT', 'output_language': 'TEXT', 'duration_sec': 'REAL',
    'context': 'TEXT', 'keywords': 'TEXT', 'fix_effort': 'TEXT',
    'segments': 'TEXT', 'audio_file': 'TEXT', 'emailed': 'INTEGER DEFAULT 0',
}
for _e in ENGINES:
    _COLS.update({f'{_e}_status': 'TEXT', f'{_e}_text': 'TEXT', f'{_e}_error': 'TEXT', f'{_e}_secs': 'REAL'})
_COLS['plus_raw'] = 'TEXT'


def _db():
    conn = sqlite3.connect(COMPARE_DB)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS comparisons (id TEXT PRIMARY KEY)')
    have = {r[1] for r in conn.execute('PRAGMA table_info(comparisons)')}
    for col, typ in _COLS.items():
        if col not in have:
            conn.execute(f'ALTER TABLE comparisons ADD COLUMN {col} {typ}')
    return conn


def _update(cid, **fields):
    with _lock:
        conn = _db()
        conn.execute(f'UPDATE comparisons SET {", ".join(f"{k} = ?" for k in fields)} WHERE id = ?',
                     (*fields.values(), cid))
        conn.commit()
        conn.close()


def _get(cid):
    conn = _db()
    row = conn.execute('SELECT * FROM comparisons WHERE id = ?', (cid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _list(limit=50):
    conn = _db()
    rows = conn.execute('SELECT * FROM comparisons ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- אודיו

def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return 'ffmpeg'


def _to_wav16k(src, dst):
    subprocess.run([_ffmpeg(), '-y', '-v', 'error', '-i', src, '-vn', '-ac', '1', '-ar', '16000',
                    '-sample_fmt', 's16', dst], check=True, timeout=1800)
    with wave.open(dst, 'rb') as w:
        return w.getnframes() / float(w.getframerate())


def _to_mp3(src_wav, dst):
    """עותק קטן לנגן בדף (64kbps)."""
    subprocess.run([_ffmpeg(), '-y', '-v', 'error', '-i', src_wav, '-ac', '1', '-b:a', '64k', dst],
                   check=True, timeout=1800)


def _cut_points(wav_path):
    """נקודות חיתוך (בשניות): כל ~40 שניות, בחלון השקט ביותר (0.2 שנ') בין 30 ל-50."""
    with wave.open(wav_path, 'rb') as w:
        rate, total = w.getframerate(), w.getnframes()
        win = rate // 5
        # עוצמה לכל חלון של 0.2 שנ' - נקרא בבלוקים, בלי לטעון את כל הקובץ
        rms = []
        while True:
            data = w.readframes(win)
            if not data:
                break
            import numpy as np
            x = np.frombuffer(data, dtype='<i2').astype(np.float32)
            rms.append(float(np.sqrt(np.mean(x * x))) if x.size else 0.0)
    dur = total / float(rate)
    points, start = [], 0.0
    while dur - start > SEG_MAX:
        lo, hi = int((start + SEG_MIN) * 5), int((start + SEG_MAX) * 5)
        cand = rms[lo:hi]
        if not cand:
            break
        best = lo + min(range(len(cand)), key=lambda i: (cand[i], abs((lo + i) / 5.0 - start - SEG_TARGET)))
        cut = best / 5.0 + 0.1
        points.append(cut)
        start = cut
    return [0.0] + points + [dur]


def _write_segments(wav_path, bounds, out_dir):
    paths = []
    with wave.open(wav_path, 'rb') as w:
        params, rate = w.getparams(), w.getframerate()
        for i in range(len(bounds) - 1):
            w.setpos(int(bounds[i] * rate))
            data = w.readframes(int((bounds[i + 1] - bounds[i]) * rate))
            p = os.path.join(out_dir, f'seg_{i:04d}.wav')
            with wave.open(p, 'wb') as o:
                o.setparams(params)
                o.writeframes(data)
            paths.append(p)
    return paths


# ---------------------------------------------------------------- מנועים

def _run_alefbot(wav_path, output_language):
    key = os.environ.get('ALEFBOT_API_KEY')
    if not key:
        raise RuntimeError('לא הוגדר ALEFBOT_API_KEY')
    h = {'Authorization': f'Bearer {key}'}
    up = requests.post(f'{ALEFBOT_BASE}/uploads', headers={**h, 'Content-Type': 'application/json'},
                       json={'filename': f'compare_{uuid.uuid4().hex[:8]}.wav', 'content_type': 'audio/wav',
                             'size_bytes': os.path.getsize(wav_path)}, timeout=30)
    up.raise_for_status()
    upload_id = up.json().get('upload_id')
    with open(wav_path, 'rb') as fh:
        requests.put(f'{ALEFBOT_BASE}/uploads/{upload_id}/binary', headers=h, data=fh, timeout=900).raise_for_status()
    tr = requests.post(f'{ALEFBOT_BASE}/transcriptions', headers={**h, 'Content-Type': 'application/json'},
                       json={'upload_id': upload_id, 'output_format': 'plain_text', 'model_tier': 'standard',
                             'translate_to_hebrew': output_language == 'he'}, timeout=30)
    tr.raise_for_status()
    job_id = tr.json().get('job_id') or tr.json().get('id')
    if not job_id:
        raise RuntimeError('AlefBot לא החזיר מספר עבודה')
    for _ in range(240):   # עד שעתיים
        time.sleep(30)
        try:
            st = requests.get(f'{ALEFBOT_BASE}/transcriptions/{job_id}', headers=h, timeout=15).json().get('status', '')
        except Exception as e:
            log.warning(f'compare alefbot poll: {e}')
            continue
        if st == 'completed':
            art = requests.get(f'{ALEFBOT_BASE}/transcriptions/{job_id}/artifact?format=txt', headers=h, timeout=60)
            art.raise_for_status()
            return art.text.strip()
        if st in ('failed', 'cancelled'):
            raise RuntimeError(f'AlefBot: העבודה {st}')
    raise RuntimeError('AlefBot: לא הסתיים אחרי שעתיים')


def _openai():
    from openai import OpenAI
    return OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))


def _transcribe_seg(client, path, prompt, language, keywords=None):
    """קטע אחד ב-gpt-transcribe. languages/keywords נשלחים ב-extra_body כדי לעבוד
    בכל גרסה של ספריית openai."""
    extra = {'languages': [language] if language in ('he', 'en', 'yi') else ['he']}
    if keywords:
        extra['keywords'] = keywords
    last = None
    for attempt in range(3):
        try:
            with open(path, 'rb') as fh:
                res = client.audio.transcriptions.create(
                    model=OPENAI_MODEL, file=(os.path.basename(path), fh, 'audio/wav'),
                    response_format='text', prompt=prompt, extra_body=extra)
            return (res if isinstance(res, str) else getattr(res, 'text', '') or '').strip()
        except Exception as e:
            last = e
            log.warning(f'compare transcribe {os.path.basename(path)} attempt {attempt + 1}: {e}')
            time.sleep(4)
    raise RuntimeError(f'OpenAI נכשל בקטע {os.path.basename(path)}: {last}')


def _fix_seg(client, prev_text, text, next_text, effort):
    if not text.strip():
        return text
    msg = (f'הקטע שלפני (הקשר בלבד):\n{prev_text or "(אין)"}\n\n'
           f'הקטע לתיקון:\n{text}\n\n'
           f'הקטע שאחרי (הקשר בלבד):\n{next_text or "(אין)"}')
    last = None
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=FIX_MODEL, reasoning_effort=effort,
                messages=[{'role': 'system', 'content': FIX_INSTRUCTIONS}, {'role': 'user', 'content': msg}])
            out = (r.choices[0].message.content or '').strip()
            return out or text
        except Exception as e:
            last = e
            log.warning(f'compare fix attempt {attempt + 1}: {e}')
            time.sleep(4)
    raise RuntimeError(f'שלב התיקון נכשל: {last}')


def _keywords_list(raw):
    out = []
    for line in (raw or '').splitlines():
        k = re.sub(r'[<>\r\n]', '', line).strip()
        if k and k not in out:
            out.append(k)
    return out[:200]


# ---------------------------------------------------------------- חלוקת AlefBot לקטעים

def split_by_reference(text, ref_segments):
    """מחלק טקסט רציף לפי חלוקת קטעים של תמלול אחר (ref_segments - רשימת טקסטים),
    בעזרת התאמת מילים. מחזיר רשימה באותו אורך."""
    n = len(ref_segments)
    words = (text or '').split()
    if n <= 1 or not words:
        return [text or ''] + [''] * (n - 1) if n else []
    norm = lambda w: re.sub(r'[^\w]', '', w)
    ref_words, seg_of = [], []
    for i, s in enumerate(ref_segments):
        for w in (s or '').split():
            ref_words.append(norm(w))
            seg_of.append(i)
    if not ref_words:   # אין במה להיעזר - חלוקה לפי מספר מילים שווה
        per = max(1, -(-len(words) // n))
        return [' '.join(words[i * per:(i + 1) * per]) for i in range(n)]
    sm = difflib.SequenceMatcher(None, ref_words, [norm(w) for w in words], autojunk=False)
    pairs = [(b.a + k, b.b + k) for b in sm.get_matching_blocks() for k in range(b.size)]
    # לכל קטע: מאיזו מילה בטקסט הוא מתחיל (לפי ההתאמה הראשונה שבתוכו)
    starts = [None] * n
    for a, b in pairs:
        s = seg_of[a]
        if starts[s] is None:
            starts[s] = b
    # השלמת קטעים בלי התאמה בכלל: אינטרפולציה בין השכנים, ושמירה על סדר עולה
    starts[0] = 0
    for i in range(1, n):
        if starts[i] is None:
            nxt = next((starts[j] for j in range(i + 1, n) if starts[j] is not None), len(words))
            starts[i] = starts[i - 1] + max(0, (nxt - starts[i - 1]) // (sum(1 for j in range(i, n) if starts[j] is None) + 1))
        starts[i] = max(starts[i], starts[i - 1])
    starts.append(len(words))
    return [' '.join(words[starts[i]:starts[i + 1]]) for i in range(n)]


# ---------------------------------------------------------------- תהליך

def _process(cid, src_path, link):
    row = _get(cid)
    work = tempfile.mkdtemp(prefix='cmp_', dir=COMPARE_DIR)
    try:
        if link:
            src_path = os.path.join(work, 'source')
            with requests.get(link, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(src_path, 'wb') as fh:
                    for chunk in r.iter_content(1024 * 1024):
                        fh.write(chunk)
        wav = os.path.join(work, 'audio16k.wav')
        dur = _to_wav16k(src_path, wav)
        mp3_name = f'{cid}.mp3'
        _to_mp3(wav, os.path.join(COMPARE_DIR, mp3_name))
        bounds = _cut_points(wav)
        seg_paths = _write_segments(wav, bounds, work)
        segs = [{'start': round(bounds[i], 2), 'end': round(bounds[i + 1], 2)} for i in range(len(seg_paths))]
        _update(cid, duration_sec=dur, audio_file=mp3_name, segments=json.dumps(segs, ensure_ascii=False))
        log.info(f'compare {cid}: {dur:.0f}s, {len(segs)} קטעים')

        client = _openai()
        keywords = _keywords_list(row['keywords'])
        context = (row['context'] or '').strip() or DEFAULT_CONTEXT
        results = {}

        def timed(name, fn):
            t0 = time.time()
            try:
                results[name] = fn()
                _update(cid, **{f'{name}_status': 'done', f'{name}_secs': time.time() - t0})
            except Exception as e:
                log.error(f'compare {cid} {name}: {e}', exc_info=True)
                results[name] = None
                _update(cid, **{f'{name}_status': 'error', f'{name}_error': f'{type(e).__name__}: {e}',
                                f'{name}_secs': time.time() - t0})

        def run_openai():
            with ThreadPoolExecutor(WORKERS) as ex:
                return list(ex.map(lambda p: _transcribe_seg(client, p, BASIC_PROMPT, row['language']), seg_paths))

        def run_plus():
            with ThreadPoolExecutor(WORKERS) as ex:
                raw = list(ex.map(lambda p: _transcribe_seg(client, p, context, row['language'], keywords), seg_paths))
            _update(cid, plus_raw=json.dumps(raw, ensure_ascii=False))
            with ThreadPoolExecutor(WORKERS) as ex:
                return list(ex.map(lambda i: _fix_seg(client, raw[i - 1] if i else '', raw[i],
                                                       raw[i + 1] if i + 1 < len(raw) else '', row['fix_effort'] or 'high'),
                                   range(len(raw))))

        threads = [threading.Thread(target=timed, args=('alefbot', lambda: _run_alefbot(wav, row['output_language']))),
                   threading.Thread(target=timed, args=('openai', run_openai)),
                   threading.Thread(target=timed, args=('plus', run_plus))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # שמירה: כל מנוע מחולק לקטעים. AlefBot מחולק לפי ההתאמה מול OpenAI הרגיל
        ref = results.get('openai') or results.get('plus') or [''] * len(segs)
        if results.get('alefbot') is not None:
            results['alefbot'] = split_by_reference(results['alefbot'], ref)
        for i, s in enumerate(segs):
            for e in ENGINES:
                s[e] = (results.get(e) or [''] * len(segs))[i] if results.get(e) is not None else ''
        upd = {'segments': json.dumps(segs, ensure_ascii=False)}
        for e in ENGINES:
            if results.get(e) is not None:
                upd[f'{e}_text'] = '\n\n'.join(t for t in results[e] if t)
        _update(cid, **upd)
    except Exception as e:
        log.error(f'compare {cid} prepare: {e}', exc_info=True)
        msg = f'הכנת הקובץ נכשלה: {type(e).__name__}: {e}'
        _update(cid, **{k: v for e2 in ENGINES for k, v in ((f'{e2}_status', 'error'), (f'{e2}_error', msg))})
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if src_path and not link and os.path.exists(src_path):
            os.remove(src_path)
    try:
        _send_email(_get(cid))
        _update(cid, emailed=1)
    except Exception as e:
        log.error(f'compare {cid} email: {e}', exc_info=True)


# ---------------------------------------------------------------- מדדים ומייל

def _words(t):
    return re.findall(r'\w+', t or '')


def similarity(a, b):
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return None
    return round(difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio() * 100, 1)


def _price(name, dur):
    key = 'ALEFBOT' if name == 'alefbot' else 'OPENAI'
    v = os.environ.get(f'LAB_PRICE_{key}_PER_MIN')
    try:
        return round(float(v) * (dur or 0) / 60.0, 2) if v else None
    except ValueError:
        return None


def summary(row):
    out = {}
    for n in ENGINES:
        text = row.get(f'{n}_text') or ''
        out[n] = {'status': row.get(f'{n}_status'), 'text': text, 'error': row.get(f'{n}_error'),
                  'secs': row.get(f'{n}_secs'), 'words': len(_words(text)), 'chars': len(text),
                  'price': _price(n, row.get('duration_sec'))}
    out['sim'] = {'alefbot_openai': similarity(out['alefbot']['text'], out['openai']['text']),
                  'alefbot_plus': similarity(out['alefbot']['text'], out['plus']['text']),
                  'openai_plus': similarity(out['openai']['text'], out['plus']['text'])}
    try:
        out['segments'] = json.loads(row.get('segments') or '[]')
    except ValueError:
        out['segments'] = []
    # רוחב עמודה יחסי לאורך הטקסט - כדי ששלוש העמודות ייגמרו בערך באותו גובה (מינימום 20%)
    lens = {n: max(1, out[n]['chars']) if out[n]['status'] == 'done' else 1 for n in ENGINES}
    total = sum(lens.values())
    raw = {n: max(20.0, 100.0 * lens[n] / total) for n in ENGINES}
    scale = 100.0 / sum(raw.values())
    out['widths'] = {n: round(raw[n] * scale, 1) for n in ENGINES}
    return out


def _fmt_secs(s):
    if s is None:
        return '-'
    s = int(round(s))
    return f'{s // 60}:{s % 60:02d}'


def _docx(title, segments, key, full_text):
    from docx import Document
    from docx.oxml import OxmlElement

    def rtl_par(doc, text, bold=False, small=False):
        p = doc.add_paragraph()
        p._p.get_or_add_pPr().append(OxmlElement('w:bidi'))
        run = p.add_run(text)
        rPr = run._r.get_or_add_rPr()
        rPr.append(OxmlElement('w:rtl'))
        if bold:
            run.bold = True
            rPr.append(OxmlElement('w:bCs'))
        if small:
            from docx.shared import Pt
            run.font.size = Pt(9)
    doc = Document()
    rtl_par(doc, title, bold=True)
    if segments:
        for s in segments:
            rtl_par(doc, f'[{_fmt_secs(s["start"])}]', small=True)
            rtl_par(doc, s.get(key) or '')
    else:
        for line in (full_text or '').split('\n'):
            rtl_par(doc, line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _send_email(row):
    import sendgrid
    from sendgrid.helpers.mail import Mail, Email, Attachment, FileContent, FileName, FileType, Disposition
    if not row or not row.get('email'):
        return
    s = summary(row)
    head_cells = ''
    for n in ENGINES:
        e = s[n]
        info = (f'זמן עיבוד {_fmt_secs(e["secs"])} · {e["words"]} מילים'
                + (f' · ₪{e["price"]:.2f}' if e['price'] is not None else '')) if e['status'] == 'done' \
            else f'<span style="color:#b91c1c">שגיאה: {escape(e["error"] or "")}</span>'
        head_cells += (f'<th style="width:{s["widths"][n]}%;padding:8px;border:1px solid #cfe0fb;background:#e3edfc;'
                       f'text-align:right;vertical-align:top"><div style="color:#0a58ca">{escape(ENGINE_NAMES[n])}</div>'
                       f'<div style="font-weight:normal;font-size:12px;color:#43546b">{info}</div></th>')
    rows = ''
    for seg in s['segments']:
        rows += (f'<tr><td style="padding:6px;border:1px solid #cfe0fb;color:#43546b;font-size:12px;vertical-align:top;'
                 f'white-space:nowrap">{_fmt_secs(seg["start"])}</td>'
                 + ''.join(f'<td style="padding:6px 8px;border:1px solid #cfe0fb;vertical-align:top;line-height:1.7;'
                           f'font-size:14px;text-align:justify;background:#ffffff">{escape(seg.get(n) or "")}</td>'
                           for n in ENGINES) + '</tr>')
    sim = s['sim']
    pct = lambda v: f'{v}%' if v is not None else '-'
    link = os.environ.get('APP_BASE_URL', '').rstrip('/')
    view = f'<p><a href="{link}/compare/{row["id"]}">לצפייה בדף עם נגן</a> (צריך קוד גישה)</p>' if link else ''
    html = (f'<div dir="rtl" style="font-family:Arial,sans-serif;max-width:1200px;margin:0 auto;background:#eef4fb;padding:16px">'
            f'<h2 style="color:#0a58ca;margin:0 0 6px">השוואת תמלול - שלושה מנועים</h2>'
            f'<div style="color:#43546b;font-size:14px">{escape(row["source"] or "")} · אורך {_fmt_secs(row["duration_sec"])} · '
            f'{len(s["segments"])} קטעים</div>'
            f'<div style="color:#43546b;font-size:14px">דמיון: AlefBot מול OpenAI רגיל {pct(sim["alefbot_openai"])} · '
            f'AlefBot מול המשופר {pct(sim["alefbot_plus"])} · רגיל מול המשופר {pct(sim["openai_plus"])}</div>{view}'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:12px">'
            f'<tr><th style="width:44px;border:1px solid #cfe0fb;background:#e3edfc">זמן</th>{head_cells}</tr>{rows}</table></div>')
    msg = Mail(from_email=Email(os.environ.get('SENDGRID_FROM_EMAIL', ''), 'מעבדת בדיקות'),
               to_emails=row['email'], subject=f'🧪 השוואת תמלול - {row["source"]}', html_content=html)
    for n in ENGINES:
        if s[n]['status'] == 'done':
            try:
                data = _docx(f'{ENGINE_NAMES[n]} - {row["source"]}', s['segments'], n, s[n]['text'])
                msg.add_attachment(Attachment(FileContent(base64.b64encode(data).decode()), FileName(f'{n}.docx'),
                                              FileType('application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
                                              Disposition('attachment')))
            except Exception as e:
                log.warning(f'compare docx {n}: {e}')
    sendgrid.SendGridAPIClient(api_key=os.environ.get('SENDGRID_API_KEY')).send(msg)


# ---------------------------------------------------------------- דפים

PAGE_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>השוואת תמלול</title>
{% if refresh %}<meta http-equiv="refresh" content="20">{% endif %}
<style>
body{font-family:Arial,sans-serif;margin:0;color:#0b1f3a;background:#eef4fb}
.wrap{max-width:1500px;margin:0 auto;padding:20px 16px 60px}
h2{color:#0a58ca;margin:0 0 6px}
.card{background:#fff;border:1px solid #cfe0fb;border-radius:12px;padding:18px;margin-top:16px}
.narrow{max-width:760px}
label{display:block;margin-top:14px;font-weight:bold;font-size:14px}
input,select,textarea{width:100%;padding:10px;margin-top:4px;box-sizing:border-box;border:1px solid #cfe0fb;border-radius:8px;font-size:15px;font-family:Arial}
button{margin-top:18px;padding:12px 28px;background:#0a58ca;color:#fff;border:none;border-radius:24px;font-size:16px;cursor:pointer}
.note{color:#43546b;font-size:13px}
.err{color:#991b1b;background:#fef2f2;border:1px solid #fecaca;padding:10px;border-radius:8px;margin-top:12px}
.ok{color:#065f46;background:#f0fdf4;border:1px solid #bbf7d0;padding:10px;border-radius:8px;margin-top:12px}
.player{position:sticky;top:0;z-index:5;background:#0a58ca;color:#fff;padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.player audio{flex:1;min-width:260px}
.player b{white-space:nowrap}
.grid{display:grid;grid-template-columns:62px {{ cols }};margin-top:14px;border:1px solid #cfe0fb;border-radius:10px;background:#fff}
.grid > div{padding:8px 10px;border-bottom:1px solid #e3ebf6;border-left:1px solid #e3ebf6;min-width:0}
.hd{background:#e3edfc;font-weight:bold;color:#0a58ca;position:sticky;top:var(--ph,0px);z-index:4}
.hd small{display:block;font-weight:normal;color:#43546b;font-size:12px;margin-top:2px}
.t{font-size:12px;color:#43546b;cursor:pointer;white-space:nowrap}
.t:hover{color:#0a58ca;text-decoration:underline}
.c{white-space:pre-wrap;line-height:1.8;font-size:15px;text-align:justify}
.row-on{background:#fff8d6 !important}
@media (max-width:760px){.grid{grid-template-columns:1fr}.hd{position:static}.grid > div.c:before{content:attr(data-n);display:block;font-size:11px;color:#43546b}}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{border-bottom:1px solid #e3ebf6;padding:8px;text-align:right}
a{color:#0a58ca}
.or{text-align:center;color:#43546b;margin-top:10px;font-size:13px}
</style></head><body>
{% if row and row.audio_file %}
<div class="player"><b>{{ row.source }}</b><audio id="aud" controls preload="metadata" src="/compare/audio/{{ row.id }}?access_code={{ access_code }}"></audio></div>
{% endif %}
<div class="wrap">
<h2>השוואת תמלול - שלושה מנועים</h2>
<p class="note">AlefBot (כמו היום) · OpenAI רגיל · OpenAI עם רקע ומילים צפויות + תיקון במודל חושב. מחולק לקטעים לפי ההקלטה. <a href="/compare?access_code={{ access_code }}">השוואה חדשה</a> · <a href="/">חזרה למעבדה</a></p>
{% if message %}<div class="{{ 'ok' if ok else 'err' }}">{{ message }}</div>{% endif %}

{% if row %}
  <div class="card">
    <div class="note">נוצר {{ row.created_at }} · אורך {{ fmt(row.duration_sec) }} · {{ s.segments|length }} קטעים · שפה {{ langs.get(row.language, row.language) }}
      {% if refresh %} · <b>עדיין רץ - הדף מתרענן לבד</b>{% endif %}</div>
    <div class="note">דמיון: AlefBot מול OpenAI רגיל {{ pct(s.sim.alefbot_openai) }} · AlefBot מול המשופר {{ pct(s.sim.alefbot_plus) }} · רגיל מול המשופר {{ pct(s.sim.openai_plus) }}</div>
  </div>
  <div class="grid" id="grid">
    <div class="hd">זמן</div>
    {% for n in engines %}{% set e = s[n] %}<div class="hd">{{ names[n] }}
      <small>{% if e.status == 'done' %}זמן עיבוד {{ fmt(e.secs) }} · {{ e.words }} מילים{% if e.price is not none %} · ₪{{ '%.2f'|format(e.price) }}{% endif %}
      {% elif e.status == 'error' %}<span style="color:#b91c1c">שגיאה: {{ e.error }}</span>{% else %}רץ...{% endif %}</small></div>{% endfor %}
    {% for seg in s.segments %}
      <div class="t" data-start="{{ seg.start }}" data-end="{{ seg.end }}" title="לנגן מכאן">▶ {{ fmt(seg.start) }}</div>
      {% for n in engines %}<div class="c" data-n="{{ names[n] }}" data-start="{{ seg.start }}">{{ seg.get(n, '') }}</div>{% endfor %}
    {% endfor %}
  </div>
  <script>
  (function(){
    var pl=document.querySelector('.player');
    function ph(){ document.documentElement.style.setProperty('--ph',(pl?pl.offsetHeight:0)+'px'); }
    ph(); window.addEventListener('resize',ph);
    var a=document.getElementById('aud'); if(!a) return;
    document.querySelectorAll('.t').forEach(function(t){t.addEventListener('click',function(){a.currentTime=parseFloat(t.dataset.start);a.play();});});
    a.addEventListener('timeupdate',function(){
      var now=a.currentTime;
      document.querySelectorAll('.t').forEach(function(t){
        var on=now>=parseFloat(t.dataset.start)&&now<parseFloat(t.dataset.end);
        var el=t; for(var i=0;i<4;i++){ if(!el) break; el.classList.toggle('row-on',on); el=el.nextElementSibling; }
      });
    });
  })();
  </script>
{% else %}
  <form class="card narrow" method="post" action="/compare/run" enctype="multipart/form-data">
    <input type="hidden" name="access_code" value="{{ access_code }}">
    <label>קובץ אודיו / וידאו</label>
    <input type="file" name="file" accept="audio/*,video/*">
    <div class="or">או</div>
    <label>קישור להקלטה קיימת</label>
    <input type="url" name="link" placeholder="למשל הקישור 'להורדת ההקלטה' מהמייל של תמלול פון">
    <label>שפת ההקלטה</label>
    <select name="language"><option value="he">עברית</option><option value="yi">אידיש</option><option value="en">אנגלית</option></select>
    <label>שפת הפלט (ל-AlefBot)</label>
    <select name="output_language"><option value="he">עברית (תרגום לעברית)</option><option value="original">שפת ההקלטה</option></select>
    <label>רקע על ההקלטה (לעמודה המשופרת)</label>
    <textarea name="context" rows="3">{{ default_context }}</textarea>
    <label>מילים צפויות - מילה או ביטוי בכל שורה (לעמודה המשופרת)</label>
    <textarea name="keywords" rows="7">{{ default_keywords }}</textarea>
    <p class="note">כדאי להשאיר רק מילים שבאמת נאמרות - מילה ברשימה שלא נאמרה עלולה "להישמע" בטעות.</p>
    <label>עומק החשיבה בשלב התיקון</label>
    <select name="fix_effort"><option value="high">גבוה (מדויק יותר, איטי ויקר יותר)</option><option value="medium">בינוני</option><option value="low">נמוך</option></select>
    <label>שליחת ההשוואה למייל</label>
    <input type="email" name="email" value="{{ default_email }}" required>
    <button type="submit">הרץ השוואה</button>
  </form>
  {% if history %}
  <div class="card"><b>השוואות קודמות</b>
    <table><tr><th>תאריך</th><th>קובץ</th><th>אורך</th>{% for n in engines %}<th>{{ names[n] }}</th>{% endfor %}</tr>
    {% for h in history %}<tr><td>{{ h.created_at }}</td>
      <td><a href="/compare/{{ h.id }}?access_code={{ access_code }}">{{ h.source }}</a></td>
      <td>{{ fmt(h.duration_sec) }}</td>{% for n in engines %}<td>{{ st.get(h[n ~ '_status'], h[n ~ '_status'] or '-') }}</td>{% endfor %}</tr>{% endfor %}
    </table></div>
  {% endif %}
{% endif %}
</div>
</body></html>"""

STATUS_NAMES = {'pending': 'רץ', 'done': 'הסתיים', 'error': 'שגיאה'}

GATE_HTML = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>השוואת תמלול</title>
<style>body{font-family:Arial,sans-serif;max-width:400px;margin:80px auto;padding:0 16px;color:#0b1f3a}
input{width:100%;padding:10px;margin-top:8px;box-sizing:border-box;border:1px solid #cfe0fb;border-radius:8px;font-size:15px}
button{margin-top:16px;padding:10px 24px;background:#0a58ca;color:#fff;border:none;border-radius:24px;cursor:pointer}
.err{color:#991b1b;background:#fef2f2;padding:10px;border-radius:8px;margin-top:12px}</style></head><body>
<h2>השוואת תמלול</h2>
<form method="get" action="{{ action }}"><label>קוד גישה</label>
<input type="password" name="access_code" required autofocus><button type="submit">כניסה</button></form>
{% if wrong %}<div class="err">קוד גישה שגוי</div>{% endif %}
</body></html>"""


def init_transcompare(app, *, require_access, default_email):

    def render(**kw):
        base = dict(message=None, ok=True, row=None, s=None, refresh=False, history=None, access_code='',
                    default_email=default_email, fmt=_fmt_secs, names=ENGINE_NAMES, langs=LANG_NAMES,
                    st=STATUS_NAMES, engines=ENGINES, cols='1fr 1fr 1fr', default_context=DEFAULT_CONTEXT,
                    default_keywords=DEFAULT_KEYWORDS, pct=lambda v: f'{v}%' if v is not None else '-')
        base.update(kw)
        return render_template_string(PAGE_HTML, **base)

    def gate():
        code = request.values.get('access_code', '')
        if not code:
            return render_template_string(GATE_HTML, wrong=False, action=request.path), 200
        if not require_access(code):
            return render_template_string(GATE_HTML, wrong=True, action=request.path), 403
        return None

    @app.route('/compare', methods=['GET'])
    def compare_page():
        g = gate()
        if g:
            return g
        return render(access_code=request.args['access_code'], history=_list())

    @app.route('/compare/audio/<cid>', methods=['GET'])
    def compare_audio(cid):
        if not require_access(request.args.get('access_code', '')):
            abort(403)
        row = _get(cid)
        if not row or not row.get('audio_file'):
            abort(404)
        return send_from_directory(COMPARE_DIR, row['audio_file'], mimetype='audio/mpeg', conditional=True)

    @app.route('/compare/<cid>', methods=['GET'])
    def compare_view(cid):
        g = gate()
        if g:
            return g
        row = _get(cid)
        if not row:
            return render(access_code=request.args['access_code'], message='ההשוואה לא נמצאה', ok=False,
                          history=_list()), 404
        s = summary(row)
        running = 'pending' in (row.get(f'{n}_status') for n in ENGINES)
        cols = ' '.join(f'{s["widths"][n]}fr' for n in ENGINES)
        return render(access_code=request.args['access_code'], row=row, s=s, refresh=running, cols=cols)

    @app.route('/compare/run', methods=['POST'])
    def compare_run():
        code = request.form.get('access_code', '')
        if not require_access(code):
            return 'קוד גישה שגוי', 403
        f = request.files.get('file')
        link = (request.form.get('link') or '').strip()
        has_file = bool(f and f.filename)
        if has_file == bool(link):
            return render(access_code=code, history=_list(), ok=False,
                          message='יש לבחור קובץ או להדביק קישור (אחד מהשניים)'), 400
        if link and not re.match(r'^https?://', link):
            return render(access_code=code, history=_list(), ok=False, message='הקישור צריך להתחיל ב-http'), 400
        effort = request.form.get('fix_effort', 'high')
        if effort not in ('low', 'medium', 'high'):
            effort = 'high'
        cid = uuid.uuid4().hex[:12]
        src_path = None
        if has_file:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(0)
            if size > MAX_FILE_SIZE:
                return render(access_code=code, history=_list(), ok=False,
                              message=f'הקובץ גדול מדי ({size // 1024 // 1024}MB, מקסימום 300MB)'), 400
            src_path = os.path.join(COMPARE_DIR, f'{cid}_src{os.path.splitext(f.filename)[1][:10]}')
            f.save(src_path)
            source = f.filename
        else:
            source = 'קישור: ' + link.split('?')[0][-60:]
        vals = {'id': cid, 'created_at': datetime.now().strftime('%d/%m/%Y %H:%M'), 'source': source,
                'email': (request.form.get('email') or '').strip(), 'language': request.form.get('language', 'he'),
                'output_language': request.form.get('output_language', 'he'),
                'context': (request.form.get('context') or '').strip()[:2000],
                'keywords': (request.form.get('keywords') or '')[:8000], 'fix_effort': effort}
        vals.update({f'{n}_status': 'pending' for n in ENGINES})
        with _lock:
            conn = _db()
            conn.execute(f'INSERT INTO comparisons ({", ".join(vals)}) VALUES ({", ".join("?" * len(vals))})',
                         tuple(vals.values()))
            conn.commit()
            conn.close()
        threading.Thread(target=_process, args=(cid, src_path, link or None), daemon=True).start()
        return redirect(f'/compare/{cid}?access_code={code}')
