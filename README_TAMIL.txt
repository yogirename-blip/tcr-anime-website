TCR × YOGI ADMIN STARTER

Files: server.py, admin.html (UI file to add), requirements.txt, Procfile.

Render environment variables:
DATABASE_URL = tcr-movies-db Internal Database URL
FLASK_SECRET_KEY = long random secret
SUPER_ADMIN_EMAIL = your primary admin email
ADMIN_PASSWORD_HASH = Werkzeug password hash (not plain password)
ADMIN_EMAILS = optional comma-separated initial admin emails

Create password hash locally:
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('CHANGE_THIS_PASSWORD'))"

IMPORTANT:
- Keep database URL, bot token and passwords out of public GitHub.
- This starter uses an allowlisted email plus shared admin password, not email OTP.
- Existing index.html is not yet connected to /api/public-posts; frontend integration remains.
- Telegram file list assumes the bot's yogi_files table exists; schema may need adjustment.
- Back up the database before deploying changes.
