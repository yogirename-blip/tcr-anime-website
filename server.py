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
    text = str(value or '').strip()
    text = re.sub(r'https?://\S+|@\w+', ' ', text)
    text = re.sub(r'(?i)\b(?:Tamil Cartoon Rare|TCR|YOGI ANIME|YoGi Linkz(?: Colony)?|Powered By)\b', ' ', text)
    text = re.sub(r'(?i)\.(?:mkv|mp4|avi|mov|webm|m4v|zip|rar|7z|mp3|m4a|aac|flac)\b', ' ', text)
    text = re.sub(r'(?i)\bS(?:eason)?[\s._-]*\d{1,2}[\s._-]*E(?:P(?:isode)?)?[\s._-]*\d{1,3}\b', ' ', text)
    text = re.sub(r'(?i)\bSeason[\s._-]*\d{1,2}\b|\bEpisode[\s._#:-]*\d{1,3}\b|\bEp[\s._#:-]*\d{1,3}\b', ' ', text)
    text = re.sub(r'(?i)\b\d{2,3}\s?kbps\b|\b\d+(?:\.\d+)?\s?(?:GB|MB|TB)\b', ' ', text)
    text = re.sub(r'(?i)\b(?:4K|2160p|UHD|2K|1440p|1080p|720p|480p|x264|x265|h264|h265|hevc|aac|ac3|ddp\d*|webrip|web[\s._-]?dl|bluray|blu-ray|brrip|hdrip|hdtv|dvdrip|10bit|8bit|esub|e-sub|proper|repack|remux)\b', ' ', text)
    text = re.sub(r'\[[^\]]*\]|\{[^}]*\}', ' ', text)
    text = re.sub(r'(?i)\((?=[^)]*(?:AAC|Audio|ESub|Subtitle|GB|MB))[^)]*\)', ' ', text)
    text = re.sub(r'[._]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -_|:;,.+')
    if not text or re.fullmatch(r'(?i)(?:1080p|720p|480p|4k|x264|x265|tamil|audio|kbps)+', text):
        return ''
    return text


def category_for(row, title):
    raw = ' '.join(str(row.get(k) or '') for k in ('filename','source_caption','caption'))
    cat = str(row.get('category') or 'Anime')
    if cat.lower() in ('movie','movies'):
        return 'Movies'
    if cat.lower() == 'web series':
        return 'Web Series'
    if row.get('season') or row.get('episode') or re.search(r'(?i)\bS\d{1,2}E\d{1,3}\b|\bEpisode\s*\d+\b', raw):
        return 'Anime'
    if re.search(r'\b(?:19|20)\d{2}\b', title) and not re.search(r'(?i)\b(?:season|episode|S\d+E\d+)\b', raw):
        return 'Movies'
    return cat if cat in ('Anime','Movies','Web Series','Cartoons') else 'Anime'


def public_rows():
    rows=[]
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT id,title,category,description,poster_url,season,episode,quality,audio,created_at,watching_url,short_url FROM tcr_posts WHERE status='published' ORDER BY created_at DESC LIMIT 500")
            for row in c.fetchall():
                row['source_type']='manual'; row['filename']=''; row['file_size']=None
                row['display_title']=clean_display_title(row.get('title')) or row.get('title') or 'Untitled'
                row['category']=category_for(row,row['display_title'])
                row['watching_url']='/watch/'+str(row['id'])
                rows.append(row)
            c.execute("SELECT id,filename,title,caption,category,season,episode,quality,audio,telegram_chat_id,telegram_message_id,created_at,file_size,poster_url,overview,tmdb_id,tmdb_media_type,source_caption FROM yogi_files ORDER BY created_at DESC LIMIT 1000")
            for row in c.fetchall():
                source=str(row.get('source_caption') or row.get('caption') or '')
                first=next((line.strip() for line in source.splitlines() if line.strip()),'')
                title=clean_display_title(row.get('title')) or clean_display_title(first) or clean_display_title(row.get('filename')) or 'Untitled'
                if re.search(r'(?i)\b\d{2,3}\s?kbps\b|\b(?:x264|x265|1080p|720p|480p)\b',title):
                    title=clean_display_title(first) or clean_display_title(row.get('filename')) or title
                chat=str(row.get('telegram_chat_id') or ''); mid=row.get('telegram_message_id'); url=''
                if mid and chat.startswith('@'): url=f"https://t.me/{chat[1:]}/{mid}"
                elif mid and chat.lstrip('-').isdigit(): url=f"https://t.me/c/{chat.removeprefix('-100').removeprefix('-')}/{mid}"
                row.update(source_type='telegram',display_title=title,category=category_for(row,title),description=row.get('overview') or row.get('caption') or '',watching_url=url,telegram_url=url)
                rows.append(row)
    rows.sort(key=lambda x:str(x.get('created_at') or ''),reverse=True)
    return rows


def row_group_key(row):
    cat=row.get('category') or 'Anime'; title=str(row.get('display_title') or 'Untitled').strip().lower()
    season=str(row.get('season') or '').strip().lower()
    # All episodes of the same anime season go under one title card. Movies group by title only.
    return (cat,title,'' if cat=='Movies' else season)


def slugify(value):
    return re.sub(r'[^a-z0-9]+','-',str(value or '').lower()).strip('-') or 'untitled'


def group_rows(rows):
    groups={}
    for row in rows:
        key=row_group_key(row)
        if key not in groups:
            season=str(row.get('season') or '').strip()
            groups[key]={'slug':slugify(key[1]+(('-'+season.lower()) if season and key[0]!='Movies' else '')),
                'title':row.get('display_title') or 'Untitled','category':row.get('category') or 'Anime','season':season,
                'description':row.get('overview') or row.get('description') or '', 'poster_url':row.get('poster_url') or '',
                'qualities':set(),'audios':set(),'episodes':[],'latest_at':str(row.get('created_at') or '')}
        g=groups[key]
        if not g['poster_url'] and row.get('poster_url'): g['poster_url']=row['poster_url']
        if not g['description']: g['description']=row.get('overview') or row.get('description') or ''
        if row.get('quality') and row['quality']!='Not specified': g['qualities'].add(str(row['quality']))
        if row.get('audio') and row['audio']!='Not specified': g['audios'].add(str(row['audio']))
        g['episodes'].append(row)
        g['latest_at']=max(g['latest_at'],str(row.get('created_at') or ''))
    result=[]
    for g in groups.values():
        g['qualities']=sorted(g['qualities']); g['audios']=sorted(g['audios']); g['episode_count']=len(g['episodes'])
        g['episodes'].sort(key=lambda x:(int(re.search(r'\d+',str(x.get('season') or '')).group()) if re.search(r'\d+',str(x.get('season') or '')) else 0,int(re.search(r'\d+',str(x.get('episode') or '')).group()) if re.search(r'\d+',str(x.get('episode') or '')) else 0,str(x.get('created_at') or '')))
        result.append(g)
    return sorted(result,key=lambda x:x['latest_at'],reverse=True)


@app.get('/api/public-posts')
def public_posts():
    # Home shows one card per series/season; episodes are intentionally hidden until title click.
    return jsonify([{k:v for k,v in g.items() if k!='episodes'} for g in group_rows(public_rows())])


@app.get('/api/public-posts/<path:slug>')
def public_series_detail(slug):
    rows=public_rows()
    for group in group_rows(rows):
        if group['slug']==slug:
            key=(group['category'],group['title'].strip().lower(),'' if group['category']=='Movies' else str(group.get('season') or '').strip().lower())
            group['episodes']=[r for r in rows if row_group_key(r)==key]
            group['episodes'].sort(key=lambda x:(int(re.search(r'\d+',str(x.get('episode') or '')).group()) if re.search(r'\d+',str(x.get('episode') or '')) else 0,str(x.get('created_at') or '')))
            return jsonify(group)
    return jsonify(error='Title not found'),404

with app.app_context(): init_db()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
      
