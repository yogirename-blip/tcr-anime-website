import os
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, redirect
from psycopg import connect
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SUPER_ADMIN_EMAIL = os.environ.get("SUPER_ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
if not all([app.secret_key, DATABASE_URL, SUPER_ADMIN_EMAIL, ADMIN_PASSWORD_HASH]):
    raise RuntimeError("Set FLASK_SECRET_KEY, DATABASE_URL, SUPER_ADMIN_EMAIL, ADMIN_PASSWORD_HASH in Render.")

def db():
    return connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)

def init_db():
    with db() as conn:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_admin_users(email TEXT PRIMARY KEY,is_super_admin BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_posts(id BIGSERIAL PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT 'Anime',description TEXT NOT NULL DEFAULT '',poster_url TEXT NOT NULL DEFAULT '',season TEXT NOT NULL DEFAULT '',episode TEXT NOT NULL DEFAULT '',quality TEXT NOT NULL DEFAULT '',audio TEXT NOT NULL DEFAULT '',telegram_chat_id TEXT NOT NULL DEFAULT '',telegram_message_id TEXT NOT NULL DEFAULT '',watching_url TEXT NOT NULL DEFAULT '',short_url TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'published',created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            c.execute("""CREATE TABLE IF NOT EXISTS tcr_admin_urls(id BIGSERIAL PRIMARY KEY,label TEXT NOT NULL,url TEXT NOT NULL,enabled BOOLEAN NOT NULL DEFAULT TRUE,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
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
                c.execute("SELECT filename,title,category,season,episode,quality,audio,telegram_chat_id,telegram_message_id,created_at FROM yogi_files ORDER BY created_at DESC LIMIT 500"); rows=c.fetchall()
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
@app.get("/api/public-posts")
def public_posts():
    with db() as conn:
        with conn.cursor() as c: c.execute("SELECT id,title,category,description,poster_url,season,episode,quality,audio,created_at FROM tcr_posts WHERE status='published' ORDER BY created_at DESC LIMIT 200"); rows=c.fetchall()
    for r in rows: r["watching_url"]="/watch/"+str(r["id"])
    return jsonify(rows)
with app.app_context(): init_db()
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
      
