# SigmaFetcher Web — Railway Deployment Guide

## Project Structure
```
sigmaweb/
├── app.py            ← Flask app + all routes
├── models.py         ← SQLAlchemy DB models
├── panel_client.py   ← /ints + timesms panel HTTP client
├── requirements.txt
├── Procfile          ← Railway start command
├── .env.example      ← Copy to .env for local dev
├── .gitignore        ← Keep .env and dex.txt out of git
└── templates/
    ├── index.html    ← Public site
    └── admin.html    ← Admin dashboard
```

## Deploy to Railway

1. Push this folder to a GitHub repo (private recommended)
2. Go to railway.app → New Project → Deploy from GitHub
3. Select the repo
4. Add a **PostgreSQL** plugin (Railway auto-sets `DATABASE_URL`)
5. Set these Environment Variables in Railway dashboard:
   ```
   SECRET_KEY      = <generate a random 32-char string>
   ADMIN_EMAIL     = adnannoordogar01@gmail.com
   ADMIN_PASSWORD  = Adnan#100400
   ```
6. Deploy — Railway runs: `gunicorn "app:create_app()"`

## First Login
- Go to `yourapp.railway.app/admin`
- Email: `adnannoordogar01@gmail.com`
- Password: `Adnan#100400`

## Adding Panels (Admin Dashboard)
1. Go to **Panels** tab → fill in the form
2. Name, Base URL (e.g. `http://185.2.83.39/ints`), username, password, type (`ints` or `timesms`)
3. Click **Ping** to test connectivity
4. Click **Fetch nums** to pull numbers directly from the panel into the DB

## Uploading Numbers Manually
1. Go to **Numbers** tab → Upload section
2. Create a CSV file — one number per line:
   ```
   +923001234567,1
   +923219876543,1
   +14155551234,2
   ```
   Column 1 = phone number (with country code), Column 2 = Panel ID
3. Or select a default panel and just list numbers with no second column

## How SMS Fetching Works
- When a user clicks **Get** on a number, it creates an order
- The SMS modal opens and polls `/api/sms/<id>` every 5 seconds
- Each poll logs into the panel, scrapes the SMS inbox for that number
- New messages are saved to the DB and shown instantly
- User clicks **Release number** when done — number becomes available again

## Local Development
```bash
pip install -r requirements.txt
cp .env.example .env
python app.py
# Open http://localhost:5000
```
