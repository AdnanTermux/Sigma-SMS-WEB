"""
app.py — SigmaFetcher V10  (auth fixed)
Key fixes:
  - logging at MODULE level (not inside create_app) so handlers can always use it
  - login/signup routes do NOT have try/except — exceptions propagate to the
    global @errorhandler(Exception) which always returns JSON for those paths
  - DB engine options to avoid Railway/Postgres connection drops
"""
import os, csv, io, logging, traceback
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   session, jsonify, flash)

# ── Module-level logger — available everywhere including error handlers ──
log = logging.getLogger("sigmafetcher")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from models import db, User, Panel, VirtualNumber, SMSMessage, Order
from panel_client import get_client, evict_client

# ── Country map ────────────────────────────────────────────────────────
COUNTRY_MAP = {
    "+93":("Afghanistan","🇦🇫"),"+355":("Albania","🇦🇱"),"+213":("Algeria","🇩🇿"),
    "+54":("Argentina","🇦🇷"),"+61":("Australia","🇦🇺"),"+43":("Austria","🇦🇹"),
    "+994":("Azerbaijan","🇦🇿"),"+880":("Bangladesh","🇧🇩"),"+32":("Belgium","🇧🇪"),
    "+591":("Bolivia","🇧🇴"),"+55":("Brazil","🇧🇷"),"+1":("United States","🇺🇸"),
    "+44":("United Kingdom","🇬🇧"),"+49":("Germany","🇩🇪"),"+33":("France","🇫🇷"),
    "+91":("India","🇮🇳"),"+62":("Indonesia","🇮🇩"),"+98":("Iran","🇮🇷"),
    "+964":("Iraq","🇮🇶"),"+353":("Ireland","🇮🇪"),"+972":("Israel","🇮🇱"),
    "+39":("Italy","🇮🇹"),"+81":("Japan","🇯🇵"),"+962":("Jordan","🇯🇴"),
    "+7":("Russia","🇷🇺"),"+966":("Saudi Arabia","🇸🇦"),"+92":("Pakistan","🇵🇰"),
    "+971":("UAE","🇦🇪"),"+90":("Turkey","🇹🇷"),"+86":("China","🇨🇳"),
    "+82":("South Korea","🇰🇷"),"+60":("Malaysia","🇲🇾"),"+63":("Philippines","🇵🇭"),
    "+66":("Thailand","🇹🇭"),"+84":("Vietnam","🇻🇳"),"+20":("Egypt","🇪🇬"),
    "+234":("Nigeria","🇳🇬"),"+27":("South Africa","🇿🇦"),"+254":("Kenya","🇰🇪"),
    "+48":("Poland","🇵🇱"),"+40":("Romania","🇷🇴"),"+31":("Netherlands","🇳🇱"),
    "+46":("Sweden","🇸🇪"),"+47":("Norway","🇳🇴"),"+45":("Denmark","🇩🇰"),
    "+358":("Finland","🇫🇮"),"+41":("Switzerland","🇨🇭"),"+34":("Spain","🇪🇸"),
    "+351":("Portugal","🇵🇹"),"+30":("Greece","🇬🇷"),"+52":("Mexico","🇲🇽"),
    "+57":("Colombia","🇨🇴"),"+56":("Chile","🇨🇱"),"+51":("Peru","🇵🇪"),
    "+58":("Venezuela","🇻🇪"),"+380":("Ukraine","🇺🇦"),"+974":("Qatar","🇶🇦"),
    "+965":("Kuwait","🇰🇼"),"+968":("Oman","🇴🇲"),"+961":("Lebanon","🇱🇧"),
    "+963":("Syria","🇸🇾"),"+94":("Sri Lanka","🇱🇰"),"+977":("Nepal","🇳🇵"),
    "+95":("Myanmar","🇲🇲"),"+64":("New Zealand","🇳🇿"),"+65":("Singapore","🇸🇬"),
    "+886":("Taiwan","🇹🇼"),"+852":("Hong Kong","🇭🇰"),"+212":("Morocco","🇲🇦"),
    "+216":("Tunisia","🇹🇳"),"+249":("Sudan","🇸🇩"),"+251":("Ethiopia","🇪🇹"),
    "+255":("Tanzania","🇹🇿"),"+256":("Uganda","🇺🇬"),"+233":("Ghana","🇬🇭"),
    "+221":("Senegal","🇸🇳"),"+252":("Somalia","🇸🇴"),"+263":("Zimbabwe","🇿🇼"),
    "+998":("Uzbekistan","🇺🇿"),"+992":("Tajikistan","🇹🇯"),"+993":("Turkmenistan","🇹🇲"),
    "+996":("Kyrgyzstan","🇰🇬"),"+976":("Mongolia","🇲🇳"),"+995":("Georgia","🇬🇪"),
    "+375":("Belarus","🇧🇾"),"+370":("Lithuania","🇱🇹"),"+371":("Latvia","🇱🇻"),
    "+372":("Estonia","🇪🇪"),"+421":("Slovakia","🇸🇰"),"+420":("Czech Republic","🇨🇿"),
    "+36":("Hungary","🇭🇺"),"+385":("Croatia","🇭🇷"),"+381":("Serbia","🇷🇸"),
    "+387":("Bosnia","🇧🇦"),"+386":("Slovenia","🇸🇮"),"+359":("Bulgaria","🇧🇬"),
}

def dial_to_country(number: str):
    n = number if number.startswith("+") else "+" + number
    for length in (4, 3, 2):
        if n[:length + 1] in COUNTRY_MAP:
            return COUNTRY_MAP[n[:length + 1]]
    return ("Unknown", "🌐")


# ── App factory ────────────────────────────────────────────────────────
def create_app():
    app = Flask(__name__, template_folder=BASE_DIR, static_folder=None)

    # Config
    app.secret_key = os.getenv("SECRET_KEY", "sigma-v10-xK9mP2qRjL7wNdQ4zB8s")
    app.config["PERMANENT_SESSION_LIFETIME"]  = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"]     = True
    app.config["SESSION_COOKIE_SAMESITE"]     = "Lax"
    app.config["SESSION_COOKIE_SECURE"]       = False
    app.config["MAX_CONTENT_LENGTH"]          = 5 * 1024 * 1024

    # Database
    db_url = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'sigma.db')}")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"]        = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Keep connections alive on Railway (avoids "SSL connection has been closed" errors)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle":  300,
    }

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _seed_admin()

    # ── Global error handler — MUST return JSON for fetch() routes ─────
    # This runs when any unhandled exception escapes a view function.
    # login/signup/api routes intentionally have no try/except so the
    # real error reaches here and gets logged properly.
    @app.errorhandler(Exception)
    def on_exception(e):
        tb = traceback.format_exc()
        log.error("Unhandled exception on %s:\n%s", request.path, tb)
        msg = str(e)
        # For all fetch() endpoints return JSON
        if (request.path in ("/login", "/signup")
                or request.path.startswith("/api/")
                or request.path.startswith("/admin/")
                and request.method == "POST"):
            return jsonify({"ok": False, "error": msg}), 500
        return (f"<pre style='padding:2rem;color:#ef4444'>"
                f"500 Error: {msg}\n\n{tb}</pre>"), 500

    @app.errorhandler(404)
    def on_404(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Not found"}), 404
        return "<h2 style='padding:2rem;color:#ef4444'>404 — Not found. <a href='/'>← Home</a></h2>", 404

    # ── Auth helpers ───────────────────────────────────────────────────
    def current_user():
        uid = session.get("user_id")
        if not uid:
            return None
        u = db.session.get(User, uid)
        if u is None:
            session.clear()
        return u

    def login_required(f):
        @wraps(f)
        def w(*a, **kw):
            if not current_user():
                return jsonify({"ok": False, "error": "Please log in first"}), 401
            return f(*a, **kw)
        return w

    def admin_required(f):
        @wraps(f)
        def w(*a, **kw):
            u = current_user()
            if not u:
                return redirect("/")
            if not u.is_admin:
                return "<h2 style='padding:2rem;color:#ef4444'>Admins only. <a href='/'>← Home</a></h2>", 403
            return f(*a, **kw)
        return w

    # ── Public pages ───────────────────────────────────────────────────
    @app.route("/")
    def index():
        panels, numbers = [], []
        try:
            panels  = Panel.query.filter_by(active=True).all()
            numbers = (VirtualNumber.query
                       .filter_by(is_available=True)
                       .join(Panel).filter(Panel.active == True)
                       .order_by(VirtualNumber.created_at.desc())
                       .limit(200).all())
        except Exception as exc:
            log.warning("index query failed: %s", exc)
        user = current_user()
        return render_template("index.html", panels=panels, numbers=numbers, user=user)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/")

    # ── AUTH — no try/except; let on_exception handle & log real errors ─
    @app.route("/login", methods=["POST"])
    def login_route():
        # Parse body — accept both JSON and form POST
        data  = (request.get_json(force=True, silent=True)
                 or request.form.to_dict()
                 or {})

        email = str(data.get("email", "")).strip().lower()
        pw    = str(data.get("password", ""))

        if not email or not pw:
            return jsonify({"ok": False, "error": "Email and password are required"})

        u = User.query.filter_by(email=email).first()
        if u is None or not u.check_password(pw):
            return jsonify({"ok": False, "error": "Wrong email or password"})

        session.permanent  = True
        session["user_id"] = u.id
        log.info("Login OK: %s", email)
        return jsonify({"ok": True, "is_admin": u.is_admin, "username": u.username})

    @app.route("/signup", methods=["POST"])
    def signup_route():
        data = (request.get_json(force=True, silent=True)
                or request.form.to_dict()
                or {})

        username = str(data.get("username", "")).strip()
        email    = str(data.get("email",    "")).strip().lower()
        password = str(data.get("password", ""))

        # Validation
        if not username or not email or not password:
            return jsonify({"ok": False, "error": "All fields are required"})
        if len(username) < 3:
            return jsonify({"ok": False, "error": "Username must be at least 3 characters"})
        if len(password) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"})
        if "@" not in email or "." not in email:
            return jsonify({"ok": False, "error": "Enter a valid email address"})
        if User.query.filter_by(email=email).first():
            return jsonify({"ok": False, "error": "Email already registered"})
        if User.query.filter_by(username=username).first():
            return jsonify({"ok": False, "error": "Username already taken"})

        u = User(username=username, email=email)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()

        session.permanent  = True
        session["user_id"] = u.id
        log.info("New user: %s <%s>", username, email)
        return jsonify({"ok": True, "username": u.username})

    # ── API: current user ──────────────────────────────────────────────
    @app.route("/api/me")
    def api_me():
        u = current_user()
        if not u:
            return jsonify({"logged_in": False})
        return jsonify({"logged_in": True, "username": u.username,
                        "email": u.email, "is_admin": u.is_admin})

    # ── API: numbers + stats ───────────────────────────────────────────
    @app.route("/api/numbers")
    def api_numbers():
        country = request.args.get("country", "")
        q = (VirtualNumber.query.filter_by(is_available=True)
             .join(Panel).filter(Panel.active == True))
        if country:
            q = q.filter(VirtualNumber.country_code == country)
        nums = q.order_by(VirtualNumber.created_at.desc()).limit(120).all()
        return jsonify([n.to_dict() for n in nums])

    @app.route("/api/stats")
    def api_stats():
        return jsonify(
            panels  = Panel.query.filter_by(active=True).count(),
            numbers = VirtualNumber.query.filter_by(is_available=True).count(),
            messages= SMSMessage.query.count(),
            users   = User.query.count(),
        )

    # ── API: get a number (user) ───────────────────────────────────────
    @app.route("/api/get_number/<int:nid>", methods=["POST"])
    @login_required
    def api_get_number(nid):
        vn = db.session.get(VirtualNumber, nid)
        if not vn:
            return jsonify({"ok": False, "error": "Number not found"})
        if not vn.is_available:
            return jsonify({"ok": False, "error": "Number just got taken — pick another"})
        u     = current_user()
        vn.is_available = False
        order = Order(user_id=u.id, virtual_number_id=vn.id,
                      expires_at=datetime.utcnow() + timedelta(minutes=20))
        db.session.add(order)
        db.session.commit()
        return jsonify({"ok": True, "order": order.to_dict()})

    # ── API: poll SMS ──────────────────────────────────────────────────
    @app.route("/api/sms/<int:nid>")
    @login_required
    def api_sms(nid):
        vn = db.session.get(VirtualNumber, nid)
        if not vn:
            return jsonify({"ok": False, "error": "Number not found"})
        u = current_user()
        if not u.is_admin:
            order = Order.query.filter_by(
                user_id=u.id, virtual_number_id=nid, status="active").first()
            if not order:
                return jsonify({"ok": False, "error": "Not your number"})

        if vn.panel and vn.panel.active:
            try:
                client = get_client(vn.panel)
                for m in client.fetch_sms(vn.number):
                    if not SMSMessage.query.filter_by(
                            virtual_number_id=nid,
                            message=m["message"], sender=m["sender"]).first():
                        db.session.add(SMSMessage(
                            virtual_number_id=nid,
                            sender=m.get("sender", ""),
                            message=m["message"],
                            received_at=m.get("received_at", datetime.utcnow())))
                db.session.commit()
            except Exception as exc:
                log.warning("fetch_sms %s: %s", vn.number, exc)

        msgs = (SMSMessage.query.filter_by(virtual_number_id=nid)
                .order_by(SMSMessage.received_at.desc()).limit(20).all())
        return jsonify({"ok": True, "messages": [m.to_dict() for m in msgs]})

    @app.route("/api/release/<int:oid>", methods=["POST"])
    @login_required
    def api_release(oid):
        u     = current_user()
        order = Order.query.filter_by(id=oid, user_id=u.id).first()
        if not order:
            return jsonify({"ok": False, "error": "Order not found"})
        order.status = "cancelled"
        if order.virtual_number:
            order.virtual_number.is_available = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/api/my_orders")
    @login_required
    def api_my_orders():
        u      = current_user()
        orders = Order.query.filter_by(user_id=u.id).order_by(
            Order.created_at.desc()).limit(20).all()
        return jsonify([o.to_dict() for o in orders])

    # ── Admin pages ────────────────────────────────────────────────────
    @app.route("/admin")
    @admin_required
    def admin_route():
        stats = dict(
            panels   = Panel.query.count(),
            online   = Panel.query.filter_by(status="online").count(),
            numbers  = VirtualNumber.query.count(),
            available= VirtualNumber.query.filter_by(is_available=True).count(),
            users    = User.query.count(),
            orders   = Order.query.count(),
            messages = SMSMessage.query.count(),
        )
        panels  = Panel.query.order_by(Panel.created_at.desc()).all()
        numbers = VirtualNumber.query.order_by(
            VirtualNumber.created_at.desc()).limit(60).all()
        users  = User.query.order_by(User.created_at.desc()).all()
        orders = Order.query.order_by(Order.created_at.desc()).limit(40).all()
        for n in numbers:
            n._panel_name = n.panel.name if n.panel else "—"
        for o in orders:
            o._uname   = o.user.username           if o.user            else "—"
            o._num     = o.virtual_number.number    if o.virtual_number  else "—"
            o._country = ((o.virtual_number.country_flag or "") + " " +
                          (o.virtual_number.country_name or "?")) if o.virtual_number else "—"
            o._panel   = (o.virtual_number.panel.name
                          if o.virtual_number and o.virtual_number.panel else "—")
        return render_template("admin.html", stats=stats, panels=panels,
                               numbers=numbers, users=users, orders=orders)

    # ── Admin: panel CRUD ──────────────────────────────────────────────
    @app.route("/admin/panels/add", methods=["POST"])
    @admin_required
    def admin_add_panel():
        d    = request.form
        name = d.get("name","").strip()
        url  = d.get("base_url","").strip().rstrip("/")
        uname= d.get("username","").strip()
        pw   = d.get("password","")
        ptype= d.get("panel_type","ints")
        if not all([name, url, uname, pw]):
            flash("All fields are required.", "error")
            return redirect("/admin#panels")
        p = Panel(name=name, base_url=url, username=uname, password=pw, panel_type=ptype)
        db.session.add(p)
        db.session.commit()
        flash(f"Panel '{p.name}' added.", "success")
        return redirect("/admin#panels")

    @app.route("/admin/panels/<int:pid>/toggle", methods=["POST"])
    @admin_required
    def admin_panel_toggle(pid):
        p = db.session.get(Panel, pid)
        if not p:
            return jsonify({"ok": False})
        p.active = not p.active
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True, "active": p.active})

    @app.route("/admin/panels/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_panel_delete(pid):
        p = db.session.get(Panel, pid)
        if not p:
            return jsonify({"ok": False})
        db.session.delete(p)
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True})

    @app.route("/admin/panels/<int:pid>/ping", methods=["POST"])
    @admin_required
    def admin_panel_ping(pid):
        p = db.session.get(Panel, pid)
        if not p:
            return jsonify({"ok": False})
        try:
            p.status = "online" if get_client(p).ping() else "offline"
        except Exception:
            p.status = "offline"
        p.last_checked = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True, "status": p.status})

    @app.route("/admin/panels/<int:pid>/fetch", methods=["POST"])
    @admin_required
    def admin_panel_fetch(pid):
        p = db.session.get(Panel, pid)
        if not p:
            return jsonify({"ok": False, "error": "Panel not found"})
        fetched = get_client(p).fetch_numbers()
        added   = 0
        for row in fetched:
            num = row.get("number", "").strip()
            if not num:
                continue
            if VirtualNumber.query.filter_by(number=num, panel_id=p.id).first():
                continue
            name, flag = dial_to_country(num)
            db.session.add(VirtualNumber(
                number=num, country_code=name[:2].upper(),
                country_name=name, country_flag=flag, panel_id=p.id))
            added += 1
        db.session.commit()
        return jsonify({"ok": True, "added": added, "total": len(fetched)})

    @app.route("/admin/panels/<int:pid>/login_test", methods=["POST"])
    @admin_required
    def admin_panel_login_test(pid):
        p = db.session.get(Panel, pid)
        if not p:
            return jsonify({"ok": False, "error": "Not found"})
        evict_client(pid)
        ok = get_client(p).login()
        return jsonify({"ok": ok,
                        "message": "Login successful" if ok else "Login FAILED — check credentials/URL"})

    # ── Admin: numbers ─────────────────────────────────────────────────
    @app.route("/admin/numbers/upload", methods=["POST"])
    @admin_required
    def admin_numbers_upload():
        panel_id = request.form.get("panel_id", type=int)
        file     = request.files.get("file")
        if not file or not file.filename:
            flash("Please select a CSV file.", "error")
            return redirect("/admin#numbers")
        text   = file.read().decode("utf-8", errors="ignore")
        reader = csv.reader(io.StringIO(text))
        added = skipped = 0
        for row in reader:
            if not row:
                continue
            num = row[0].strip().replace(" ", "")
            if not num or len(num) < 7:
                continue
            pid = int(row[1].strip()) if len(row) > 1 and row[1].strip().isdigit() else panel_id
            if not pid:
                skipped += 1
                continue
            panel = db.session.get(Panel, pid)
            if not panel:
                skipped += 1
                continue
            if VirtualNumber.query.filter_by(number=num, panel_id=pid).first():
                skipped += 1
                continue
            name, flag = dial_to_country(num)
            db.session.add(VirtualNumber(
                number=num, country_code=name[:2].upper(),
                country_name=name, country_flag=flag, panel_id=pid))
            added += 1
        db.session.commit()
        flash(f"✓ Uploaded {added} numbers. {skipped} skipped.", "success")
        return redirect("/admin#numbers")

    @app.route("/admin/numbers/<int:nid>/delete", methods=["POST"])
    @admin_required
    def admin_number_delete(nid):
        n = db.session.get(VirtualNumber, nid)
        if n:
            db.session.delete(n)
            db.session.commit()
        return jsonify({"ok": True})

    @app.route("/admin/numbers/<int:nid>/toggle", methods=["POST"])
    @admin_required
    def admin_number_toggle(nid):
        n = db.session.get(VirtualNumber, nid)
        if not n:
            return jsonify({"ok": False})
        n.is_available = not n.is_available
        db.session.commit()
        return jsonify({"ok": True, "available": n.is_available})

    # ── Admin: users ───────────────────────────────────────────────────
    @app.route("/admin/users/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_user_toggle_admin(uid):
        u = db.session.get(User, uid)
        if not u:
            return jsonify({"ok": False})
        u.is_admin = not u.is_admin
        db.session.commit()
        return jsonify({"ok": True, "is_admin": u.is_admin})

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_user_delete(uid):
        u = db.session.get(User, uid)
        if u:
            db.session.delete(u)
            db.session.commit()
        return jsonify({"ok": True})

    return app


# ── Seed admin account ─────────────────────────────────────────────────
def _seed_admin():
    email = os.getenv("ADMIN_EMAIL",    "adnannoordogar01@gmail.com")
    pw    = os.getenv("ADMIN_PASSWORD", "Adnan#100400")
    if not User.query.filter_by(email=email).first():
        u = User(username="admin", email=email, is_admin=True)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        log.info("Admin account created: %s", email)
    else:
        log.info("Admin account exists: %s", email)


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
