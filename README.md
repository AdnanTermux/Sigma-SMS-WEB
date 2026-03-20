# SigmaFetcher Ultimate — Fixed Edition

## Files (all in one flat directory)
- app.py          ← Flask backend (ALL bugs fixed)
- models.py       ← DB models
- panel_client.py ← Panel HTTP client
- index.html      ← Public site
- admin.html      ← Admin dashboard
- requirements.txt
- Procfile

## What was fixed
1. template_folder now uses absolute path (BASE_DIR) — was breaking on Railway
2. SESSION_COOKIE_SAMESITE="Lax" — session now persists after login redirect
3. session.permanent=True — session lasts 7 days, not just browser session
4. credentials:'same-origin' on all fetch() calls — cookies sent with every request
5. All route names fixed (login_route, signup_route, logout_route, admin_route)
6. Safe None checks in templates — no more 500 errors from missing panel/user
7. postgres:// → postgresql:// fix for Railway DATABASE_URL
8. _seed_admin() gets app context properly
9. Error pages for 403/404/500 instead of blank crashes

## Deploy to Railway
1. Push to private GitHub repo
2. railway.app → New Project → Deploy from GitHub
3. Add PostgreSQL plugin (sets DATABASE_URL automatically)
4. Add env var: SECRET_KEY = any random string (optional but recommended)
5. Deploy

## Admin Login
URL:      /admin
Email:    adnannoordogar01@gmail.com
Password: Adnan#100400

## Local Dev
pip install -r requirements.txt
python app.py
# → http://localhost:5000
