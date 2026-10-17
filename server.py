import os
import re
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, redirect
from psycopg import connect
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "") or (generate_password_hash(ADMIN_PASSWORD) if ADMIN_PASSWORD else "")
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
MEDIA_BOT_USERNAME = os.environ.get("MEDIA_BOT_USERNAME", "").strip().lstrip("@")
if not all([app.secret_key, DATABASE_URL, SUPER_ADMIN_EMAIL, ADMIN_PASSWORD_HASH]):
    raise RuntimeError("Set FLASK_SECRET_KEY, DATABASE_URL, SUPER_ADMIN_EMAIL, and either ADMIN_PASSWORD or ADMIN_PASSWORD_HASH in Render.")

def db():
    return connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)

def init_db():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_admin_users(email TEXT PRIMARY KEY,is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_posts(id BIGSERIAL PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT 'Anime',description TEXT NOT NULL DEFAULT '',poster_url TEXT NOT NULL DEFAULT '',season TEXT NOT NULL DEFAULT '',episode TEXT NOT NULL DEFAULT '',quality TEXT NOT NULL DEFAULT '',audio TEXT NOT NULL DEFAULT '',telegram_chat_id TEXT NOT NULL DEFAULT '',telegram_message_id TEXT NOT NULL DEFAULT '',watching_url TEXT NOT NULL DEFAULT '',short_url TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'published',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_admin_urls(id BIGSERIAL PRIMARY KEY,label TEXT NOT NULL,url TEXT NOT NULL,enabled BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            # Create/upgrade the Telegram files table without deleting existing rows.
            c.execute("""CREATE TABLE IF NOT EXISTS yogi_files (
                id BIGSERIAL PRIMARY KEY, filename TEXT, telegram_file_id TEXT, caption TEXT,
                title TEXT, season TEXT, episode TEXT, quality TEXT, audio TEXT, category TEXT,
                telegram_chat_id TEXT, telegram_message_id BIGINT, created_at TIMESTAMPTZ DEFAULT NOW()
            )""")
            for statement in (
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS file_size BIGINT",
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS poster_url TEXT",
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS overview TEXT",
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS tmdb_id BIGINT",
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS tmdb_media_type TEXT",
                "ALTER TABLE yogi_files ADD COLUMN IF NOT EXISTS source_caption TEXT",
            ):
                c.execute(statement)
            c.execute("INSERT INTO tcr_admin_users(email,is_super_admin) VALUES(%s,TRUE) ON CONFLICT(email) DO UPDATE SET is_super_admin=TRUE",(SUPER_ADMIN_EMAIL,))
            for e in ADMIN_EMAILS:
                c.execute("INSERT INTO tcr_admin_users(email,is_super_admin) VALUES(%s,FALSE) ON CONFLICT(email) DO NOTHING",(e,))

def required(fn):
    @wraps(fn)
    def inner(*a,**kw):
        email=str(session.get("admin_email","")).lower()
        if not email: return jsonify(error="Login required"),401
        with db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT email,is_super_admin FROM tcr_admin_users WHERE email=%s",(email,))
                user=c.fetchone()
        if not user:
            session.clear()
            return jsonify(error="Admin access revoked"),403
        request.admin_user=user
        return fn(*a,**kw)
    return inner

@app.get("/")
def home(): return send_from_directory(".", "index.html")
@app.get("/admin")
@app.get("/admin.html")
def admin(): return send_from_directory(".", "admin.html")
@app.get("/api/health")
def health(): return jsonify(ok=True,service="TCR YOGI Admin API")
@app.post("/api/login")
def login():
    d=request.get_json(silent=True) or {}; email=str(d.get("email","")).strip().lower(); password=str(d.get("password",""))
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT email FROM tcr_admin_users WHERE email=%s",(email,)); user=c.fetchone()
    if not user or not check_password_hash(ADMIN_PASSWORD_HASH,password): return jsonify(error="Invalid email or password"),401
    session.clear(); session["admin_email"]=email; session.permanent=True
    return jsonify(ok=True,email=email)
@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)
@app.get("/api/me")
@required
def me(): return jsonify(email=request.admin_user["email"],is_super_admin=request.admin_user["is_super_admin"])
@app.get("/api/dashboard")
@required
def dashboard():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) AS n FROM tcr_posts"); posts=c.fetchone()["n"]
            c.execute("SELECT COUNT(*) AS n FROM tcr_admin_users"); admins=c.fetchone()["n"]
            try: c.execute("SELECT COUNT(*) AS n FROM yogi_files"); files=c.fetchone()["n"]
            except Exception: files=0
    return jsonify(posts=posts,admins=admins,telegram_files=files)
@app.get("/api/posts")
@required
def posts():
    with db() as conn:
        with conn.cursor() as c: c.execute("SELECT * FROM tcr_posts ORDER BY created_at DESC LIMIT 500"); rows=c.fetchall()
    return jsonify(rows)
@app.post("/api/posts")
@required
def create_post():
    d=request.get_json(silent=True) or {}; title=str(d.get("title","")).strip()
    if not title: return jsonify(error="Title is required"),400
    keys=["title","category","description","poster_url","season","episode","quality","audio","telegram_chat_id","telegram_message_id","watching_url","short_url","status"]
    vals=[title]+[str(d.get(k,"") or "").strip() for k in keys[1:]]
    if vals[1] not in ("Anime","Movies","Web Series","Cartoons"): vals[1]="Anime"
    if vals[-1] not in ("draft","published"): vals[-1]="published"
    with db() as conn:
        with conn.cursor() as c:
            c.execute("INSERT INTO tcr_posts(title,category,description,poster_url,season,episode,quality,audio,telegram_chat_id,telegram_message_id,watching_url,short_url,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",vals); pid=c.fetchone()["id"]
    return jsonify(ok=True,id=pid)
@app.put("/api/posts/<int:pid>")
@required
def edit_post(pid):
    d=request.get_json(silent=True) or {}; allowed=["title","category","description","poster_url","season","episode","quality","audio","telegram_chat_id","telegram_message_id","watching_url","short_url","status"]
    fields=[(k,str(d[k] or "").strip()) for k in allowed if k in d]
    if not fields: return jsonify(error="No fields"),400
    sql=", ".join(k+"=%s" for k,v in fields)+", updated_at=NOW()"
    with db() as conn:
        with conn.cursor() as c:
            c.execute("UPDATE tcr_posts SET "+sql+" WHERE id=%s",[v for k,v in fields]+[pid])
            if not c.rowcount: return jsonify(error="Post not found"),404
    return jsonify(ok=True)
@app.delete("/api/posts/<int:pid>")
@required
def delete_post(pid):
    with db() as conn:
        with conn.cursor() as c:
            c.execute("DELETE FROM tcr_posts WHERE id=%s",(pid,))
            if not c.rowcount: return jsonify(error="Post not found"),404
    return jsonify(ok=True)
@app.get("/api/telegram-files")
@required
def telegram_files():
    try:
        with db() as conn:
            with conn.cursor() as c:
                c.execute("SELECT filename,title,category,season,episode,quality,audio,telegram_chat_id,telegram_message_id,created_at,file_size,poster_url,overview,tmdb_id,tmdb_media_type,source_caption FROM yogi_files ORDER BY created_at DESC LIMIT 500"); rows=c.fetchall()
        return jsonify(rows)
    except Exception: return jsonify(error="Cannot read yogi_files; check the bot table columns."),500
@app.get("/api/admins")
@required
def admins():
    if not request.admin_user["is_super_admin"]: return jsonify(error="Primary admin only"),403
    with db() as conn:
        with conn.cursor() as c: c.execute("SELECT email,is_super_admin FROM tcr_admin_users ORDER BY is_super_admin DESC,email"); rows=c.fetchall()
    return jsonify(rows)
@app.post("/api/admins")
@required
def add_admin():
    if not request.admin_user["is_super_admin"]: return jsonify(error="Primary admin only"),403
    email=str((request.get_json(silent=True) or {}).get("email","")).strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]: return jsonify(error="Invalid email"),400
    with db() as conn:
        with conn.cursor() as c: c.execute("INSERT INTO tcr_admin_users(email,is_super_admin) VALUES(%s,FALSE) ON CONFLICT(email) DO NOTHING",(email,))
    return jsonify(ok=True)
@app.delete("/api/admins")
@required
def remove_admin():
    if not request.admin_user["is_super_admin"]: return jsonify(error="Primary admin only"),403
    email=str((request.get_json(silent=True) or {}).get("email","")).strip().lower()
    if email==SUPER_ADMIN_EMAIL: return jsonify(error="Cannot remove primary admin"),400
    with db() as conn:
        with conn.cursor() as c: c.execute("DELETE FROM tcr_admin_users WHERE email=%s AND is_super_admin=FALSE",(email,))
    return jsonify(ok=True)
@app.get("/api/urls")
@required
def urls():
    with db() as conn:
        with conn.cursor() as c: c.execute("SELECT * FROM tcr_admin_urls ORDER BY created_at DESC"); rows=c.fetchall()
    return jsonify(rows)
@app.post("/api/urls")
@required
def add_url():
    d=request.get_json(silent=True) or {}; label=str(d.get("label","")).strip(); url=str(d.get("url","")).strip()
    if not label or not url.startswith(("https://","http://")): return jsonify(error="Enter label and valid URL"),400
    with db() as conn:
        with conn.cursor() as c: c.execute("INSERT INTO tcr_admin_urls(label,url) VALUES(%s,%s) RETURNING id",(label,url)); uid=c.fetchone()["id"]
    return jsonify(ok=True,id=uid)
@app.delete("/api/urls/<int:uid>")
@required
def delete_url(uid):
    with db() as conn:
        with conn.cursor() as c: c.execute("DELETE FROM tcr_admin_urls WHERE id=%s",(uid,))
    return jsonify(ok=True)
@app.get("/watch/<int:pid>")
def watch(pid):
    with db() as conn:
        with conn.cursor() as c: c.execute("SELECT watching_url,short_url FROM tcr_posts WHERE id=%s AND status='published'",(pid,)); row=c.fetchone()
    if not row: return "Link not found",404
    target=row["watching_url"] or row["short_url"]
    return redirect(target,302) if target.startswith(("https://","http://")) else ("Watching URL not configured",404)
def clean_display_title(value):
    """Strip release metadata from titles without keeping quality/audio in the title."""
    text = str(value or '').strip()
    text = re.sub(r'https?://\S+|@\w+', ' ', text)
    text = re.sub(r'(?i)\b(?:Tamil Cartoon Rare|TCR|YOGI ANIME|YoGi Linkz(?: Colony)?|Powered By)\b', ' ', text)
    text = re.sub(r'(?i)\.(?:mkv|mp4|avi|mov|webm|m4v|zip|rar|7z|mp3|m4a|aac|flac)\b', ' ', text)
    text = re.sub(r'(?i)\bS(?:eason)?[\s._-]*\d{1,2}[\s._-]*E(?:P(?:isode)?)?[\s._-]*\d{1,3}\b', ' ', text)
    text = re.sub(r'(?i)\bSeason[\s._-]*\d{1,2}\b|\bEpisode[\s._#:-]*\d{1,3}\b|\bEp[\s._#:-]*\d{1,3}\b', ' ', text)
    text = re.sub(r'(?i)\b\d{2,3}\s?kbps\b|\b\d+(?:\.\d+)?\s?(?:GB|MB|TB)\b', ' ', text)
    text = re.sub(r'(?i)\b(?:4K|2160p|UHD|2K|1440p|1080p|720p|480p|x264|x265|h264|h265|hevc|aac|ac3|ddp\d*|webrip|web[\s._-]?dl|bluray|blu-ray|brrip|hdrip|hdtv|dvdrip|10bit|8bit|esub|e-sub|proper|repack|remux)\b', ' ', text)
    # Remove parenthesized/bracketed release notes, but keep year in title such as (2026).
    text = re.sub(r'\[[^\]]*\]|\{[^}]*\}', ' ', text)
    text = re.sub(r'\((?=[^)]*(?:AAC|Audio|ESub|Subtitle|GB|MB|Tamil\s*[+&,/]|Telugu\s*[+&,/]|Hindi\s*[+&,/]))[^)]*\)', ' ', text, flags=re.I)
    # Strip language lists only when they occur after a release separator.
    text = re.sub(r'(?i)\s[-–]\s*\[?(?:Tamil|Telugu|Hindi|English|Korean|Japanese|Malayalam|Kannada)(?:\s*[+&,/]\s*(?:Tamil|Telugu|Hindi|English|Korean|Japanese|Malayalam|Kannada))+(?:\s+Audio)?\]?$', ' ', text)
    text = re.sub(r'[._]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -_|:;,.+')
    if not text or re.fullmatch(r'(?i)(?:1080p|720p|480p|4k|x264|x265|tamil|audio|kbps|multi)+', text):
        return ''
    return text


def extract_title(row):
    source = str(row.get('source_caption') or row.get('caption') or '')
    first = next((line.strip() for line in source.splitlines() if line.strip()), '')
    # The channel release line is the source of truth. Take only the show title
    # before S01E01 / S02EP03, not the episode subtitle or codec tags after it.
    if first:
        before_episode = re.split(r'(?i)\bS\d{1,2}\s*E(?:P)?\d{1,3}\b', first, maxsplit=1)[0]
        cleaned = clean_display_title(before_episode)
        if cleaned and len(cleaned) >= 2:
            return cleaned
    for candidate in (row.get('title'), row.get('filename')):
        cleaned = clean_display_title(candidate)
        if cleaned and len(cleaned) >= 2 and not re.search(r'(?i)\b(?:kbps|x264|x265|1080p|720p|480p)\b', cleaned):
            # Filename can include show title then episode marker and subtitle.
            cleaned = re.split(r'(?i)\bS\d{1,2}\s*E(?:P)?\d{1,3}\b', cleaned, maxsplit=1)[0].strip(' -_|:;,.')
            if cleaned:
                return cleaned
    return 'Untitled'


def category_for(row, title):
    raw = ' '.join(str(row.get(k) or '') for k in ('filename','source_caption','caption'))
    cat = str(row.get('category') or 'Anime')
    if cat.lower() in ('movie','movies'):
        return 'Movies'
    if cat.lower() == 'web series':
        return 'Web Series'
    if re.search(r'(?i)\bS\d{1,2}E\d{1,3}\b|\bSeason\s*\d+\b|\bEpisode\s*\d+\b', raw):
        return 'Anime' if cat.lower() not in ('cartoon','cartoons') else 'Cartoons'
    if re.search(r'\b(?:19|20)\d{2}\b', title):
        return 'Movies'
    return cat if cat in ('Anime','Movies','Web Series','Cartoons') else 'Anime'


def public_rows():
    rows=[]
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id,title,category,description,poster_url,season,episode,quality,audio,created_at,watching_url,short_url FROM tcr_posts WHERE status='published' ORDER BY created_at DESC LIMIT 500")
            for row in c.fetchall():
                row.update(source_type='manual',filename='',file_size=None)
                row['display_title']=clean_display_title(row.get('title')) or row.get('title') or 'Untitled'
                row['category']=category_for(row,row['display_title'])
                row['watching_url']='/watch/'+str(row['id'])
                rows.append(row)
            c.execute("SELECT id,filename,title,caption,category,season,episode,quality,audio,telegram_chat_id,telegram_message_id,created_at,file_size,poster_url,overview,tmdb_id,tmdb_media_type,source_caption FROM yogi_files ORDER BY created_at DESC LIMIT 1000")
            for row in c.fetchall():
                title=extract_title(row)
                # Channel publishing is disabled: direct the website's Get TG File action to this bot.
                url=f"https://t.me/{MEDIA_BOT_USERNAME}?start=file_{row['id']}" if MEDIA_BOT_USERNAME else ''
                row.update(source_type='telegram',display_title=title,category=category_for(row,title),description=row.get('overview') or row.get('caption') or '',watching_url=url,telegram_url=url,play_url='',download_url=url)
                rows.append(row)
            # New standalone bot stores uploads in tcr_media_files. Read it separately
            # so the existing website tables and older bot records remain untouched.
            try:
                c.execute("""SELECT id,filename,title,caption,media_type,file_size,season,episode,quality,audio,play_url,watching_url,download_url,created_at
                             FROM tcr_media_files WHERE is_active=TRUE ORDER BY created_at DESC LIMIT 1000""")
                for item in c.fetchall():
                    title=clean_display_title(item.get('title')) or clean_display_title(item.get('filename')) or 'Untitled'
                    raw=' '.join(str(item.get(k) or '') for k in ('filename','caption','title'))
                    cat='Movies' if re.search(r'\b(?:19|20)\d{2}\b', title) and not re.search(r'(?i)\bS\d{1,2}E\d{1,3}\b|\bSeason\s*\d+\b', raw) else 'Anime'
                    if re.search(r'(?i)\b(?:web series|season\s*\d+)\b', raw): cat='Web Series' if 'web series' in raw.lower() else 'Anime'
                    sn=item.get('season'); en=item.get('episode')
                    deep_link=f"https://t.me/{MEDIA_BOT_USERNAME}?start=file_{item['id']}" if MEDIA_BOT_USERNAME else ''
                    row={'id':item['id'],'filename':item.get('filename') or '', 'title':title, 'caption':item.get('caption') or '',
                         'source_caption':title, 'category':cat, 'season':str(sn) if sn else '', 'episode':str(en) if en else '',
                         'quality':item.get('quality') or 'Unknown', 'audio':item.get('audio') or 'Unknown', 'file_size':item.get('file_size'),
                         'created_at':item.get('created_at'), 'poster_url':'', 'overview':'', 'tmdb_id':None, 'tmdb_media_type':None,
                         'source_type':'new_bot', 'display_title':title, 'description':item.get('caption') or '',
                         'watching_url':deep_link, 'telegram_url':deep_link, 'play_url':item.get('play_url') or '',
                         'download_url':item.get('download_url') or ''}
                    rows.append(row)
            except Exception:
                # Bot may not have created its table yet; existing site stays online.
                pass
    rows.sort(key=lambda x:str(x.get('created_at') or ''),reverse=True)
    return rows


def slugify(value):
    return re.sub(r'[^a-z0-9]+','-',str(value or '').lower()).strip('-') or 'untitled'


def episode_number(row):
    value=str(row.get('episode') or '')
    match=re.search(r'\d+',value)
    if match: return int(match.group())
    raw=' '.join(str(row.get(k) or '') for k in ('filename','source_caption','caption'))
    match=re.search(r'(?i)\bS\d{1,2}E(\d{1,3})\b|\bEpisode\s*(\d{1,3})\b|\bEp\s*(\d{1,3})\b',raw)
    return int(next(x for x in match.groups() if x)) if match else 0


def season_number(row):
    value=str(row.get('season') or '')
    match=re.search(r'\d+',value)
    if match: return int(match.group())
    raw=' '.join(str(row.get(k) or '') for k in ('filename','source_caption','caption'))
    match=re.search(r'(?i)\bS(\d{1,2})E\d{1,3}\b|\bSeason\s*(\d{1,2})\b',raw)
    return int(next(x for x in match.groups() if x)) if match else 0


def group_rows(rows):
    # One Home card per show + season. Episode files/ audio/quality versions stay inside it.
    groups={}
    for row in rows:
        cat=row.get('category') or 'Anime'
        title=str(row.get('display_title') or 'Untitled').strip()
        sn=season_number(row); en=episode_number(row)
        season_label=f'Season {sn:02d}' if sn else ''
        norm_title=re.sub(r'[^a-z0-9]+',' ',title.lower()).strip()
        key=(cat,norm_title,sn if cat!='Movies' else 0)
        if key not in groups:
            slug=slugify(title + (f' season {sn:02d}' if sn and cat!='Movies' else ''))
            groups[key]={'slug':slug,'title':title,'category':cat,'season':season_label,'description':row.get('overview') or row.get('description') or '', 'poster_url':row.get('poster_url') or '', 'latest_at':str(row.get('created_at') or ''),'episodes':[]}
        g=groups[key]
        if not g['poster_url'] and row.get('poster_url'): g['poster_url']=row['poster_url']
        if not g['description']: g['description']=row.get('overview') or row.get('description') or ''
        g['latest_at']=max(g['latest_at'],str(row.get('created_at') or ''))
        episode_key=(sn,en) if en else (sn,0)
        episode=next((e for e in g['episodes'] if e['_key']==episode_key),None)
        if episode is None:
            ep_title=f'Episode {en:02d}' if en else (str(row.get('episode') or '').strip() or ('Movie' if cat=='Movies' else 'Episode not identified'))
            episode={'_key':episode_key,'season_number':sn,'episode_number':en,'title':ep_title,'variants':[]}
            g['episodes'].append(episode)
        variant={'id':row.get('id'),'quality':str(row.get('quality') or 'Quality not specified').strip(),'audio':str(row.get('audio') or 'Audio not specified').strip(),'file_size':row.get('file_size'),'watching_url':row.get('watching_url') or row.get('telegram_url') or '', 'play_url':row.get('play_url') or '', 'download_url':row.get('download_url') or '', 'filename':row.get('filename') or '', 'created_at':row.get('created_at')}
        # Distinct qualities/audio variants are kept, exact duplicate imports are removed.
        unique=(variant['watching_url'],variant['quality'].lower(),variant['audio'].lower(),variant['filename'].lower())
        if not any((v.get('watching_url'),str(v.get('quality') or '').lower(),str(v.get('audio') or '').lower(),str(v.get('filename') or '').lower())==unique for v in episode['variants']):
            episode['variants'].append(variant)
    result=[]
    for g in groups.values():
        g['episodes'].sort(key=lambda e:(e['season_number'],e['episode_number']))
        for e in g['episodes']:
            e['variants'].sort(key=lambda v:(str(v.get('audio') or '').lower(),str(v.get('quality') or '').lower()))
        g['episode_count']=len(g['episodes'])
        g['variant_count']=sum(len(e['variants']) for e in
        g['episodes'])
        result.append(g)
    return sorted(result,key=lambda x:x['latest_at'],reverse=True)


@app.get('/api/public-posts')
def public_posts():
    return jsonify([{k:v for k,v in g.items() if k!='episodes'} for g in group_rows(public_rows())])


@app.get('/api/public-posts/<path:slug>')
def public_series_detail(slug):
    for group in group_rows(public_rows()):
        if group['slug']==slug:
            return jsonify(group)
    return jsonify(error='Title not found'),404

with app.app_context(): init_db()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
    
