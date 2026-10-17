TCR × YOGI Admin Starter — இந்த ZIP-ல் 5 files உள்ளன.

Render Environment Variables:
- DATABASE_URL: Render PostgreSQL internal URL
- FLASK_SECRET_KEY: நீளமான random secret
- SUPER_ADMIN_EMAIL: முதன்மை Admin email
- ADMIN_PASSWORD_HASH: password hash (plain password அல்ல)
- ADMIN_EMAILS: விருப்பமான கூடுதல் email-கள், comma-separated

Password hash உருவாக்க:
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('CHANGE_TO_A_LONG_PASSWORD'))"

Render start command: gunicorn server:app
தற்போதைய index.html இன்னும் /api/public-posts-உடன் இணைக்கப்படவில்லை.
Telegram file list, yogi_files table column names-ஐ எதிர்பார்க்கிறது; வேறுபாடு இருந்தால் மாற்றம் தேவை.
Secret values, database URL, Telegram bot token ஆகியவற்றை public GitHub-ல் போடாதே.
