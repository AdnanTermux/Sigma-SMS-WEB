# SigmaFetcher Ultimate — Railway Deploy

## Files (all in one directory)
```
app.py            ← Flask app + all routes
models.py         ← DB models (User, Panel, VirtualNumber, SMSMessage, Order)
panel_client.py   ← /ints + timesms HTTP session client
index.html        ← Public site (animated)
admin.html        ← Admin dashboard
requirements.txt
Procfile          ← Railway start command
```

## Deploy to Railway

1. Push this folder to a **private** GitHub repo
2. railway.app → New Project → Deploy from GitHub
3. Add **PostgreSQL** plugin (auto-sets DATABASE_URL)
4. Set Environment Variables:
   - `SECRET_KEY` = any long random string
5. Deploy

Admin credentials are baked in:
- Email: `adnannoordogar01@gmail.com`
- Password: `Adnan#100400`
- URL: `yourdomain.railway.app/admin`

## Local Dev
```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

## Adding Numbers
**Option A — CSV Upload** (Admin → Numbers tab):
```
+923001234567,1
+923219876543,1
+14155552671,2
```
Column 1 = number with country code, Column 2 = Panel ID

**Option B — Fetch from panel** (Admin → Panels tab → click "Fetch"):
Logs into the panel and pulls all numbers automatically.

## SMS Flow
1. User clicks "Get" on a number
2. Order created, number marked in-use
3. SMS modal opens, polls panel every 5s
4. OTPs highlighted automatically
5. User clicks "Release" when done → number available again
