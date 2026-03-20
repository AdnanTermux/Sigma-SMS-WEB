"""
app.py — SigmaFetcher Ultimate  (fixed)
All files in one directory. No .env needed.
"""
import os, csv, io
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, abort)

# Absolute path to this file's directory  ← KEY FIX #1
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from models import db, User, Panel, VirtualNumber, SMSMessage, Order
from panel_client import get_client, evict_client

# ── Country lookup ─────────────────────────────────────────────────────
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
        prefix = n[:length + 1]
        if prefix in COUNTRY_MAP:
            return COUNTRY_MAP[prefix]
    return ("Unknown", "🌐")


# ── App factory ────────────────────────────────────────────────────────
def create_app():
    app = Flask(
        __name__,
        template_folder=BASE_DIR,   # ← KEY FIX #1: absolute path, finds index.html & admin.html
        static_folder=os.path.join(BASE_DIR, "static") if os.path.isdir(os.path.join(BASE_DIR, "static")) else None,
    )

    app.secret_key = os.getenv("SECRET_KEY", "sigma-ultrav10-secret-xK9mP2qR")

    # ── KEY FIX #2: session cookie settings so login sticks after redirect ──
    app.config["SESSION_COOKIE_HTTPONLY"]  = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"]   = False  # set True if HTTPS only
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

    # Use DATABASE_URL from env (Railway Postgres) or fall back to local SQLite
    db_url = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'sigma.db')}")
    # Railway sometimes gives postgres:// — SQLAlchemy needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    db.init_app(app)

    # ── KEY FIX #3: seed inside app context properly ─────────────────────
    with app.app_context():
        db.create_all()
        _seed_admin(app)

    # ── Error pages ───────────────────────────────────────────────────────
    @app.errorhandler(403)
    def err403(e):
        return "<h2 style='font-family:monospace;color:#ef4444;padding:2rem'>403 — Access denied.<br><a href='/'>← Home</a></h2>", 403

    @app.errorhandler(404)
    def err404(e):
        return "<h2 style='font-family:monospace;color:#ef4444;padding:2rem'>404 — Not found.<br><a href='/'>← Home</a></h2>", 404

    @app.errorhandler(500)
    def err500(e):
        return f"<h2 style='font-family:monospace;color:#ef4444;padding:2rem'>500 — Server error.<br><code>{e}</code><br><a href='/'>← Home</a></h2>", 500

    # ── Auth decorators ───────────────────────────────────────────────────
    def login_required(f):
        @wraps(f)
        def w(*a, **kw):
            if "user_id" not in session:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Login required"}), 401
                return redirect("/")
            return f(*a, **kw)
        return w

    def admin_required(f):
        @wraps(f)
        def w(*a, **kw):
            if "user_id" not in session:
                return redirect("/")
            u = db.session.get(User, session["user_id"])
            if not u or not u.is_admin:
                return "<h2 style='font-family:monospace;color:#ef4444;padding:2rem'>403 — Admins only.<br><a href='/'>← Home</a></h2>", 403
            return f(*a, **kw)
        return w

    # ── PUBLIC ROUTES ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        try:
            numbers = (VirtualNumber.query.filter_by(is_available=True)
                       .join(Panel).filter(Panel.active == True)
                       .order_by(VirtualNumber.created_at.desc()).limit(200).all())
            panels  = Panel.query.filter_by(active=True).all()
        except Exception:
            numbers, panels = [], []
        user = db.session.get(User, session["user_id"]) if "user_id" in session else None
        if user is None and "user_id" in session:
            session.clear()   # stale session — clear it
        return render_template("index.html", numbers=numbers, panels=panels, user=user)

    @app.route("/login", methods=["GET", "POST"])
    def login_route():
        # GET just goes home (no separate login page)
        if request.method == "GET":
            return redirect("/")
        data  = request.get_json(silent=True) or request.form
        email = data.get("email", "").strip().lower()
        pw    = data.get("password", "")
        if not email or not pw:
            return jsonify({"ok": False, "error": "Email and password are required"})
        u = User.query.filter_by(email=email).first()
        if not u or not u.check_password(pw):
            return jsonify({"ok": False, "error": "Wrong email or password"})
        session.permanent = True
        session["user_id"] = u.id
        return jsonify({"ok": True, "is_admin": u.is_admin})

    @app.route("/signup", methods=["POST"])
    def signup_route():
        data     = request.get_json(silent=True) or request.form
        username = data.get("username", "").strip()
        email    = data.get("email",    "").strip().lower()
        password = data.get("password", "")
        if not username or not email or not password:
            return jsonify({"ok": False, "error": "All fields are required"})
        if len(password) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"})
        if User.query.filter_by(email=email).first():
            return jsonify({"ok": False, "error": "Email already registered"})
        if User.query.filter_by(username=username).first():
            return jsonify({"ok": False, "error": "Username already taken"})
        u = User(username=username, email=email)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        session.permanent = True
        session["user_id"] = u.id
        return jsonify({"ok": True})

    @app.route("/logout")
    def logout_route():
        session.clear()
        return redirect("/")

    # ── USER API ──────────────────────────────────────────────────────────
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

    @app.route("/api/me")
    def api_me():
        if "user_id" not in session:
            return jsonify({"logged_in": False})
        u = db.session.get(User, session["user_id"])
        if not u:
            session.clear()
            return jsonify({"logged_in": False})
        return jsonify({"logged_in": True, "username": u.username,
                        "email": u.email, "is_admin": u.is_admin})

    @app.route("/api/get_number/<int:nid>", methods=["POST"])
    @login_required
    def api_get_number(nid):
        vn = db.session.get(VirtualNumber, nid)
        if not vn:
            return jsonify({"ok": False, "error": "Number not found"})
        if not vn.is_available:
            return jsonify({"ok": False, "error": "Number just got taken — try another"})
        user = db.session.get(User, session["user_id"])
        vn.is_available = False
        order = Order(
            user_id           = user.id,
            virtual_number_id = vn.id,
            expires_at        = datetime.utcnow() + timedelta(minutes=20),
        )
        db.session.add(order)
        db.session.commit()
        return jsonify({"ok": True, "order": order.to_dict()})

    @app.route("/api/sms/<int:nid>")
    @login_required
    def api_sms(nid):
        vn   = db.session.get(VirtualNumber, nid)
        if not vn:
            return jsonify({"ok": False, "error": "Number not found"})
        user = db.session.get(User, session["user_id"])
        # Verify ownership unless admin
        if not user.is_admin:
            order = Order.query.filter_by(
                user_id=user.id, virtual_number_id=nid, status="active").first()
            if not order:
                return jsonify({"ok": False, "error": "Not your number"})
        # Live fetch from panel
        if vn.panel and vn.panel.active:
            try:
                client = get_client(vn.panel)
                for m in client.fetch_sms(vn.number):
                    if not SMSMessage.query.filter_by(
                            virtual_number_id=nid,
                            message=m["message"],
                            sender=m["sender"]).first():
                        db.session.add(SMSMessage(
                            virtual_number_id=nid,
                            sender=m["sender"],
                            message=m["message"],
                            received_at=m["received_at"]))
                db.session.commit()
            except Exception as e:
                pass  # don't crash if panel is unreachable
        msgs = (SMSMessage.query.filter_by(virtual_number_id=nid)
                .order_by(SMSMessage.received_at.desc()).limit(20).all())
        return jsonify({"ok": True, "messages": [m.to_dict() for m in msgs]})

    @app.route("/api/release/<int:oid>", methods=["POST"])
    @login_required
    def api_release(oid):
        user  = db.session.get(User, session["user_id"])
        order = Order.query.filter_by(id=oid, user_id=user.id).first()
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
        user   = db.session.get(User, session["user_id"])
        orders = (Order.query.filter_by(user_id=user.id)
                  .order_by(Order.created_at.desc()).limit(20).all())
        return jsonify([o.to_dict() for o in orders])

    # ── ADMIN ─────────────────────────────────────────────────────────────
    @app.route("/admin")
    @admin_required
    def admin_route():
        # Pre-compute counts safely
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
        numbers = VirtualNumber.query.order_by(VirtualNumber.created_at.desc()).limit(60).all()
        users   = User.query.order_by(User.created_at.desc()).all()
        orders  = Order.query.order_by(Order.created_at.desc()).limit(40).all()
        # Enrich numbers with panel name safely
        for n in numbers:
            n._panel_name = n.panel.name if n.panel else "—"
        # Enrich orders safely
        for o in orders:
            o._username  = o.user.username                if o.user              else "—"
            o._number    = o.virtual_number.number         if o.virtual_number   else "—"
            o._country   = (o.virtual_number.country_flag or "") + " " + (o.virtual_number.country_name or "—") if o.virtual_number else "—"
            o._panel     = o.virtual_number.panel.name    if o.virtual_number and o.virtual_number.panel else "—"
        return render_template("admin.html", stats=stats, panels=panels,
                               numbers=numbers, users=users, orders=orders)

    # ── Admin: Panels ─────────────────────────────────────────────────────
    @app.route("/admin/panels/add", methods=["POST"])
    @admin_required
    def admin_add_panel():
        d = request.form
        name = d.get("name","").strip()
        url  = d.get("base_url","").strip().rstrip("/")
        user = d.get("username","").strip()
        pw   = d.get("password","")
        ptype= d.get("panel_type","ints")
        if not name or not url or not user or not pw:
            flash("All fields required.", "error")
            return redirect("/admin#panels")
        p = Panel(name=name, base_url=url, username=user,
                  password=pw, panel_type=ptype)
        db.session.add(p)
        db.session.commit()
        flash(f"Panel '{p.name}' added.", "success")
        return redirect("/admin#panels")

    @app.route("/admin/panels/<int:pid>/toggle", methods=["POST"])
    @admin_required
    def admin_panel_toggle(pid):
        p = db.session.get(Panel, pid)
        if not p: return jsonify({"ok": False, "error": "Not found"})
        p.active = not p.active
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True, "active": p.active})

    @app.route("/admin/panels/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_panel_delete(pid):
        p = db.session.get(Panel, pid)
        if not p: return jsonify({"ok": False, "error": "Not found"})
        db.session.delete(p)
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True})

    @app.route("/admin/panels/<int:pid>/ping", methods=["POST"])
    @admin_required
    def admin_panel_ping(pid):
        p = db.session.get(Panel, pid)
        if not p: return jsonify({"ok": False, "error": "Not found"})
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
        if not p: return jsonify({"ok": False, "error": "Not found"})
        try:
            fetched = get_client(p).fetch_numbers()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
        added = 0
        for row in fetched:
            num = row["number"]
            if not VirtualNumber.query.filter_by(number=num, panel_id=p.id).first():
                name, flag = dial_to_country(num)
                db.session.add(VirtualNumber(
                    number=num, country_code=name[:2].upper(),
                    country_name=name, country_flag=flag, panel_id=p.id))
                added += 1
        db.session.commit()
        return jsonify({"ok": True, "added": added, "total": len(fetched)})

    # ── Admin: Numbers ────────────────────────────────────────────────────
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
            if not row: continue
            num = row[0].strip().replace(" ", "")
            if not num or not (num.startswith("+") or num.isdigit()): continue
            pid = int(row[1].strip()) if len(row) > 1 and row[1].strip().isdigit() else panel_id
            if not pid:
                skipped += 1; continue
            panel = db.session.get(Panel, pid)
            if not panel:
                skipped += 1; continue
            if VirtualNumber.query.filter_by(number=num, panel_id=pid).first():
                skipped += 1; continue
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
        if not n: return jsonify({"ok": False})
        db.session.delete(n)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/admin/numbers/<int:nid>/toggle", methods=["POST"])
    @admin_required
    def admin_number_toggle(nid):
        n = db.session.get(VirtualNumber, nid)
        if not n: return jsonify({"ok": False})
        n.is_available = not n.is_available
        db.session.commit()
        return jsonify({"ok": True, "available": n.is_available})

    # ── Admin: Users ──────────────────────────────────────────────────────
    @app.route("/admin/users/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_user_toggle_admin(uid):
        u = db.session.get(User, uid)
        if not u: return jsonify({"ok": False})
        u.is_admin = not u.is_admin
        db.session.commit()
        return jsonify({"ok": True, "is_admin": u.is_admin})

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_user_delete(uid):
        u = db.session.get(User, uid)
        if not u: return jsonify({"ok": False})
        db.session.delete(u)
        db.session.commit()
        return jsonify({"ok": True})

    return app


# ── Admin seed ─────────────────────────────────────────────────────────
def _seed_admin(app):
    """Create default admin account if it doesn't exist."""
    with app.app_context():
        email = os.getenv("ADMIN_EMAIL",    "adnannoordogar01@gmail.com")
        pw    = os.getenv("ADMIN_PASSWORD", "Adnan#100400")
        if not User.query.filter_by(email=email).first():
            u = User(username="admin", email=email, is_admin=True)
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            print(f"[SigmaFetcher] Admin account created: {email}")
        else:
            print(f"[SigmaFetcher] Admin account exists: {email}")


if __name__ == "__main__":
    application = create_app()
    application.run(
        host  = "0.0.0.0",
        port  = int(os.getenv("PORT", 5000)),
        debug = False,
    )
