"""
app.py — Sigma Fetcher Web Platform
"""
import os
import csv
import io
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash, abort)
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

from models import db, User, Panel, VirtualNumber, SMSMessage, Order
from panel_client import get_client, evict_client

# ── Country data (dial_code → name, flag) built from countries.json ──────
COUNTRY_MAP = {
    "+93":  ("Afghanistan",    "🇦🇫"), "+355": ("Albania",       "🇦🇱"),
    "+213": ("Algeria",        "🇩🇿"), "+54":  ("Argentina",      "🇦🇷"),
    "+61":  ("Australia",      "🇦🇺"), "+43":  ("Austria",        "🇦🇹"),
    "+994": ("Azerbaijan",     "🇦🇿"), "+880": ("Bangladesh",     "🇧🇩"),
    "+32":  ("Belgium",        "🇧🇪"), "+55":  ("Brazil",         "🇧🇷"),
    "+1":   ("United States",  "🇺🇸"), "+44":  ("United Kingdom", "🇬🇧"),
    "+49":  ("Germany",        "🇩🇪"), "+33":  ("France",         "🇫🇷"),
    "+91":  ("India",          "🇮🇳"), "+92":  ("Pakistan",       "🇵🇰"),
    "+971": ("UAE",            "🇦🇪"), "+966": ("Saudi Arabia",   "🇸🇦"),
    "+90":  ("Turkey",         "🇹🇷"), "+7":   ("Russia",         "🇷🇺"),
    "+86":  ("China",          "🇨🇳"), "+81":  ("Japan",          "🇯🇵"),
    "+82":  ("South Korea",    "🇰🇷"), "+62":  ("Indonesia",      "🇮🇩"),
    "+60":  ("Malaysia",       "🇲🇾"), "+63":  ("Philippines",    "🇵🇭"),
    "+66":  ("Thailand",       "🇹🇭"), "+84":  ("Vietnam",        "🇻🇳"),
    "+20":  ("Egypt",          "🇪🇬"), "+234": ("Nigeria",        "🇳🇬"),
    "+27":  ("South Africa",   "🇿🇦"), "+254": ("Kenya",          "🇰🇪"),
    "+98":  ("Iran",           "🇮🇷"), "+964": ("Iraq",           "🇮🇶"),
    "+962": ("Jordan",         "🇯🇴"), "+965": ("Kuwait",         "🇰🇼"),
    "+974": ("Qatar",          "🇶🇦"), "+968": ("Oman",           "🇴🇲"),
    "+961": ("Lebanon",        "🇱🇧"), "+963": ("Syria",          "🇸🇾"),
    "+880": ("Bangladesh",     "🇧🇩"), "+94":  ("Sri Lanka",      "🇱🇰"),
    "+977": ("Nepal",          "🇳🇵"), "+95":  ("Myanmar",        "🇲🇲"),
    "+52":  ("Mexico",         "🇲🇽"), "+57":  ("Colombia",       "🇨🇴"),
    "+56":  ("Chile",          "🇨🇱"), "+51":  ("Peru",           "🇵🇪"),
    "+58":  ("Venezuela",      "🇻🇪"), "+380": ("Ukraine",        "🇺🇦"),
    "+48":  ("Poland",         "🇵🇱"), "+40":  ("Romania",        "🇷🇴"),
    "+31":  ("Netherlands",    "🇳🇱"), "+46":  ("Sweden",         "🇸🇪"),
    "+47":  ("Norway",         "🇳🇴"), "+45":  ("Denmark",        "🇩🇰"),
    "+358": ("Finland",        "🇫🇮"), "+41":  ("Switzerland",    "🇨🇭"),
    "+39":  ("Italy",          "🇮🇹"), "+34":  ("Spain",          "🇪🇸"),
    "+351": ("Portugal",       "🇵🇹"), "+30":  ("Greece",         "🇬🇷"),
}


def dial_to_country(number: str):
    """Return (name, flag) by matching longest dial code prefix."""
    n = number if number.startswith("+") else "+" + number
    for length in (4, 3, 2):
        prefix = n[:length + 1]
        if prefix in COUNTRY_MAP:
            return COUNTRY_MAP[prefix]
    return ("Unknown", "🌐")


# ── App factory ───────────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "changeme-use-a-real-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///sigma.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024   # 2 MB upload cap

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _seed_admin()

    # ── Auth helpers ─────────────────────────────────────────────────────

    def login_required(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login_page"))
            return f(*args, **kwargs)
        return wrapped

    def admin_required(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login_page"))
            u = User.query.get(session["user_id"])
            if not u or not u.is_admin:
                abort(403)
            return f(*args, **kwargs)
        return wrapped

    # ── PUBLIC ROUTES ────────────────────────────────────────────────────

    @app.route("/")
    def index():
        numbers = (VirtualNumber.query
                   .filter_by(is_available=True)
                   .join(Panel).filter(Panel.active == True)
                   .order_by(VirtualNumber.created_at.desc())
                   .limit(200).all())
        panels  = Panel.query.filter_by(active=True).all()
        user    = User.query.get(session["user_id"]) if "user_id" in session else None
        return render_template("index.html",
                               numbers=numbers, panels=panels, user=user)

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if request.method == "POST":
            data = request.get_json(silent=True) or request.form
            u = User.query.filter_by(email=data.get("email", "")).first()
            if u and u.check_password(data.get("password", "")):
                session["user_id"] = u.id
                return (jsonify({"ok": True, "is_admin": u.is_admin})
                        if request.is_json
                        else redirect(url_for("admin_dashboard") if u.is_admin else url_for("index")))
            return (jsonify({"ok": False, "error": "Invalid credentials"})
                    if request.is_json else redirect(url_for("login_page") + "?err=1"))
        return render_template("index.html")

    @app.route("/signup", methods=["POST"])
    def signup():
        data = request.get_json(silent=True) or request.form
        username = data.get("username", "").strip()
        email    = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not username or not email or not password:
            return jsonify({"ok": False, "error": "All fields required"})
        if User.query.filter((User.email == email) | (User.username == username)).first():
            return jsonify({"ok": False, "error": "Email or username already taken"})
        u = User(username=username, email=email)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        session["user_id"] = u.id
        return jsonify({"ok": True})

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ── USER API ──────────────────────────────────────────────────────────

    @app.route("/api/numbers")
    def api_numbers():
        country = request.args.get("country", "")
        q = (VirtualNumber.query.filter_by(is_available=True)
             .join(Panel).filter(Panel.active == True))
        if country:
            q = q.filter(VirtualNumber.country_code == country)
        nums = q.order_by(VirtualNumber.created_at.desc()).limit(100).all()
        return jsonify([n.to_dict() for n in nums])

    @app.route("/api/get_number/<int:number_id>", methods=["POST"])
    @login_required
    def get_number(number_id):
        vn = VirtualNumber.query.get_or_404(number_id)
        if not vn.is_available:
            return jsonify({"ok": False, "error": "Number already taken"})
        user = User.query.get(session["user_id"])
        vn.is_available = False
        order = Order(user_id=user.id, virtual_number_id=vn.id,
                      expires_at=datetime.utcnow() + timedelta(minutes=20))
        db.session.add(order)
        db.session.commit()
        return jsonify({"ok": True, "order": order.to_dict()})

    @app.route("/api/sms/<int:number_id>")
    @login_required
    def api_sms(number_id):
        """Poll live SMS for a number — fetches from the panel in real time."""
        vn = VirtualNumber.query.get_or_404(number_id)
        # Check ownership
        user = User.query.get(session["user_id"])
        if not user.is_admin:
            order = Order.query.filter_by(
                user_id=user.id, virtual_number_id=number_id,
                status="active").first()
            if not order:
                return jsonify({"ok": False, "error": "Not your number"})

        # Fetch from panel
        panel = vn.panel
        if panel and panel.active:
            client = get_client(panel)
            live   = client.fetch_sms(vn.number)
            for m in live:
                exists = SMSMessage.query.filter_by(
                    virtual_number_id=vn.id, message=m["message"],
                    sender=m["sender"]).first()
                if not exists:
                    db.session.add(SMSMessage(
                        virtual_number_id=vn.id,
                        sender=m["sender"],
                        message=m["message"],
                        received_at=m["received_at"]))
            db.session.commit()

        msgs = (SMSMessage.query.filter_by(virtual_number_id=vn.id)
                .order_by(SMSMessage.received_at.desc()).limit(20).all())
        return jsonify({"ok": True, "messages": [m.to_dict() for m in msgs]})

    @app.route("/api/my_orders")
    @login_required
    def my_orders():
        user   = User.query.get(session["user_id"])
        orders = (Order.query.filter_by(user_id=user.id)
                  .order_by(Order.created_at.desc()).limit(30).all())
        return jsonify([o.to_dict() for o in orders])

    @app.route("/api/release/<int:order_id>", methods=["POST"])
    @login_required
    def release_number(order_id):
        user  = User.query.get(session["user_id"])
        order = Order.query.filter_by(id=order_id, user_id=user.id).first_or_404()
        order.status = "cancelled"
        if order.virtual_number:
            order.virtual_number.is_available = True
        db.session.commit()
        return jsonify({"ok": True})

    # ── ADMIN DASHBOARD ───────────────────────────────────────────────────

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        stats = dict(
            panels=Panel.query.count(),
            numbers=VirtualNumber.query.count(),
            available=VirtualNumber.query.filter_by(is_available=True).count(),
            users=User.query.count(),
            orders=Order.query.count(),
            messages=SMSMessage.query.count(),
        )
        panels  = Panel.query.order_by(Panel.created_at.desc()).all()
        numbers = (VirtualNumber.query.order_by(
                   VirtualNumber.created_at.desc()).limit(50).all())
        users   = User.query.order_by(User.created_at.desc()).limit(30).all()
        return render_template("admin.html", stats=stats,
                               panels=panels, numbers=numbers, users=users)

    # ── ADMIN: PANELS ─────────────────────────────────────────────────────

    @app.route("/admin/panels/add", methods=["POST"])
    @admin_required
    def admin_add_panel():
        d = request.form
        panel = Panel(
            name       = d["name"],
            base_url   = d["base_url"].rstrip("/"),
            username   = d["username"],
            password   = d["password"],
            panel_type = d.get("panel_type", "ints"),
            active     = True,
        )
        db.session.add(panel)
        db.session.commit()
        flash(f"Panel '{panel.name}' added.", "success")
        return redirect(url_for("admin_dashboard") + "#panels")

    @app.route("/admin/panels/<int:pid>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_panel(pid):
        p = Panel.query.get_or_404(pid)
        p.active = not p.active
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True, "active": p.active})

    @app.route("/admin/panels/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_panel(pid):
        p = Panel.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True})

    @app.route("/admin/panels/<int:pid>/ping", methods=["POST"])
    @admin_required
    def admin_ping_panel(pid):
        p = Panel.query.get_or_404(pid)
        client = get_client(p)
        ok = client.ping()
        p.status       = "online" if ok else "offline"
        p.last_checked = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True, "status": p.status})

    @app.route("/admin/panels/<int:pid>/fetch", methods=["POST"])
    @admin_required
    def admin_fetch_numbers(pid):
        """Pull numbers directly from a panel and store them."""
        p      = Panel.query.get_or_404(pid)
        client = get_client(p)
        fetched = client.fetch_numbers()
        added = 0
        for row in fetched:
            num = row["number"]
            if not VirtualNumber.query.filter_by(number=num, panel_id=p.id).first():
                name, flag = dial_to_country(num)
                vn = VirtualNumber(
                    number       = num,
                    country_code = row.get("country_code") or name[:2].upper(),
                    country_name = row.get("country_name") or name,
                    country_flag = flag,
                    panel_id     = p.id,
                )
                db.session.add(vn)
                added += 1
        db.session.commit()
        return jsonify({"ok": True, "added": added, "total": len(fetched)})

    # ── ADMIN: NUMBERS UPLOAD ─────────────────────────────────────────────

    @app.route("/admin/numbers/upload", methods=["POST"])
    @admin_required
    def admin_upload_numbers():
        """
        CSV upload: each line is  number,panel_id
        OR just a list of numbers (panel picked from dropdown).
        """
        panel_id = request.form.get("panel_id", type=int)
        file     = request.files.get("file")
        if not file:
            flash("No file uploaded.", "error")
            return redirect(url_for("admin_dashboard") + "#numbers")

        text    = file.read().decode("utf-8", errors="ignore")
        reader  = csv.reader(io.StringIO(text))
        added   = 0
        skipped = 0
        for row in reader:
            if not row:
                continue
            num = row[0].strip()
            if not num:
                continue
            pid = int(row[1].strip()) if len(row) > 1 and row[1].strip().isdigit() else panel_id
            if pid is None:
                skipped += 1
                continue
            panel = Panel.query.get(pid)
            if not panel:
                skipped += 1
                continue
            if VirtualNumber.query.filter_by(number=num, panel_id=pid).first():
                skipped += 1
                continue
            name, flag = dial_to_country(num)
            vn = VirtualNumber(
                number       = num,
                country_code = name[:2].upper(),
                country_name = name,
                country_flag = flag,
                panel_id     = pid,
            )
            db.session.add(vn)
            added += 1
        db.session.commit()
        flash(f"Uploaded {added} numbers ({skipped} skipped).", "success")
        return redirect(url_for("admin_dashboard") + "#numbers")

    @app.route("/admin/numbers/<int:nid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_number(nid):
        n = VirtualNumber.query.get_or_404(nid)
        db.session.delete(n)
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/admin/numbers/<int:nid>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle_number(nid):
        n = VirtualNumber.query.get_or_404(n_id := nid)
        n.is_available = not n.is_available
        db.session.commit()
        return jsonify({"ok": True, "available": n.is_available})

    # ── ADMIN: USERS ──────────────────────────────────────────────────────

    @app.route("/admin/users/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_toggle_admin(uid):
        u = User.query.get_or_404(uid)
        u.is_admin = not u.is_admin
        db.session.commit()
        return jsonify({"ok": True, "is_admin": u.is_admin})

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_delete_user(uid):
        u = User.query.get_or_404(uid)
        db.session.delete(u)
        db.session.commit()
        return jsonify({"ok": True})

    # ── ADMIN: STATS API ──────────────────────────────────────────────────

    @app.route("/api/admin/stats")
    @admin_required
    def api_admin_stats():
        return jsonify(dict(
            panels   = Panel.query.count(),
            online   = Panel.query.filter_by(status="online").count(),
            numbers  = VirtualNumber.query.count(),
            available= VirtualNumber.query.filter_by(is_available=True).count(),
            users    = User.query.count(),
            orders   = Order.query.count(),
            messages = SMSMessage.query.count(),
        ))

    return app


# ── Seed admin account ────────────────────────────────────────────────────

def _seed_admin():
    email    = os.getenv("ADMIN_EMAIL",    "adnannoordogar01@gmail.com")
    password = os.getenv("ADMIN_PASSWORD", "Adnan#100400")
    if not User.query.filter_by(email=email).first():
        u = User(username="admin", email=email, is_admin=True)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
