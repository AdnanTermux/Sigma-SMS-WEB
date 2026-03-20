"""
app.py — SigmaFetcher Ultimate
All files in one directory. No .env needed — defaults built in.
"""
import os, csv, io
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, abort)
from models import db, User, Panel, VirtualNumber, SMSMessage, Order
from panel_client import get_client, evict_client

# ── Country lookup (dial prefix → name, flag) ─────────────────────────
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


# ── App ────────────────────────────────────────────────────────────────
def create_app():
    app = Flask(__name__,
                template_folder=".",   # HTML files live in same dir
                static_folder="static")

    app.secret_key = os.getenv("SECRET_KEY", "sigma-secret-key-ultrav10-change-in-prod")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///sigma.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

    db.init_app(app)
    with app.app_context():
        db.create_all()
        _seed_admin()

    # ── decorators ────────────────────────────────────────────────────
    def login_required(f):
        @wraps(f)
        def w(*a, **kw):
            if "user_id" not in session:
                return (jsonify({"ok": False, "error": "Login required"})
                        if request.is_json else redirect(url_for("index")))
            return f(*a, **kw)
        return w

    def admin_required(f):
        @wraps(f)
        def w(*a, **kw):
            if "user_id" not in session:
                return redirect(url_for("index"))
            u = User.query.get(session["user_id"])
            if not u or not u.is_admin:
                abort(403)
            return f(*a, **kw)
        return w

    # ── PUBLIC ────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        numbers = (VirtualNumber.query.filter_by(is_available=True)
                   .join(Panel).filter(Panel.active == True)
                   .order_by(VirtualNumber.created_at.desc()).limit(200).all())
        panels  = Panel.query.filter_by(active=True).all()
        user    = User.query.get(session["user_id"]) if "user_id" in session else None
        return render_template("index.html", numbers=numbers, panels=panels, user=user)

    @app.route("/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True) or request.form
        u = User.query.filter_by(email=data.get("email", "").lower()).first()
        if u and u.check_password(data.get("password", "")):
            session["user_id"] = u.id
            return jsonify({"ok": True, "is_admin": u.is_admin})
        return jsonify({"ok": False, "error": "Invalid email or password"})

    @app.route("/signup", methods=["POST"])
    def signup():
        data     = request.get_json(silent=True) or request.form
        username = data.get("username", "").strip()
        email    = data.get("email",    "").strip().lower()
        password = data.get("password", "")
        if not username or not email or not password:
            return jsonify({"ok": False, "error": "All fields are required"})
        if len(password) < 6:
            return jsonify({"ok": False, "error": "Password must be at least 6 characters"})
        if User.query.filter((User.email==email)|(User.username==username)).first():
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

    # ── USER API ──────────────────────────────────────────────────────
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
            panels   = Panel.query.filter_by(active=True).count(),
            numbers  = VirtualNumber.query.filter_by(is_available=True).count(),
            messages = SMSMessage.query.count(),
            users    = User.query.count(),
        )

    @app.route("/api/get_number/<int:nid>", methods=["POST"])
    @login_required
    def api_get_number(nid):
        vn = VirtualNumber.query.get_or_404(nid)
        if not vn.is_available:
            return jsonify({"ok": False, "error": "Number just got taken — try another"})
        user = User.query.get(session["user_id"])
        vn.is_available = False
        order = Order(user_id=user.id, virtual_number_id=vn.id,
                      expires_at=datetime.utcnow() + timedelta(minutes=20))
        db.session.add(order)
        db.session.commit()
        return jsonify({"ok": True, "order": order.to_dict()})

    @app.route("/api/sms/<int:nid>")
    @login_required
    def api_sms(nid):
        vn   = VirtualNumber.query.get_or_404(nid)
        user = User.query.get(session["user_id"])
        if not user.is_admin:
            order = Order.query.filter_by(user_id=user.id,
                                          virtual_number_id=nid,
                                          status="active").first()
            if not order:
                return jsonify({"ok": False, "error": "Not your number"})
        # Live fetch from panel
        if vn.panel and vn.panel.active:
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
        msgs = (SMSMessage.query.filter_by(virtual_number_id=nid)
                .order_by(SMSMessage.received_at.desc()).limit(20).all())
        return jsonify({"ok": True, "messages": [m.to_dict() for m in msgs]})

    @app.route("/api/release/<int:oid>", methods=["POST"])
    @login_required
    def api_release(oid):
        user  = User.query.get(session["user_id"])
        order = Order.query.filter_by(id=oid, user_id=user.id).first_or_404()
        order.status = "cancelled"
        if order.virtual_number:
            order.virtual_number.is_available = True
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/api/my_orders")
    @login_required
    def api_my_orders():
        user   = User.query.get(session["user_id"])
        orders = (Order.query.filter_by(user_id=user.id)
                  .order_by(Order.created_at.desc()).limit(20).all())
        return jsonify([o.to_dict() for o in orders])

    # ── ADMIN ─────────────────────────────────────────────────────────
    @app.route("/admin")
    @admin_required
    def admin():
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
        return render_template("admin.html", stats=stats, panels=panels,
                               numbers=numbers, users=users, orders=orders)

    @app.route("/admin/panels/add", methods=["POST"])
    @admin_required
    def admin_add_panel():
        d = request.form
        p = Panel(name=d["name"], base_url=d["base_url"].rstrip("/"),
                  username=d["username"], password=d["password"],
                  panel_type=d.get("panel_type", "ints"))
        db.session.add(p)
        db.session.commit()
        flash(f"Panel '{p.name}' added successfully.", "success")
        return redirect("/admin#panels")

    @app.route("/admin/panels/<int:pid>/toggle",  methods=["POST"])
    @admin_required
    def admin_panel_toggle(pid):
        p = Panel.query.get_or_404(pid)
        p.active = not p.active
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True, "active": p.active})

    @app.route("/admin/panels/<int:pid>/delete",  methods=["POST"])
    @admin_required
    def admin_panel_delete(pid):
        db.session.delete(Panel.query.get_or_404(pid))
        db.session.commit()
        evict_client(pid)
        return jsonify({"ok": True})

    @app.route("/admin/panels/<int:pid>/ping",    methods=["POST"])
    @admin_required
    def admin_panel_ping(pid):
        p = Panel.query.get_or_404(pid)
        p.status       = "online" if get_client(p).ping() else "offline"
        p.last_checked = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True, "status": p.status})

    @app.route("/admin/panels/<int:pid>/fetch",   methods=["POST"])
    @admin_required
    def admin_panel_fetch(pid):
        p       = Panel.query.get_or_404(pid)
        client  = get_client(p)
        fetched = client.fetch_numbers()
        added   = 0
        for row in fetched:
            num = row["number"]
            if not VirtualNumber.query.filter_by(number=num, panel_id=p.id).first():
                name, flag = dial_to_country(num)
                db.session.add(VirtualNumber(
                    number=num,
                    country_code=name[:2].upper(),
                    country_name=name,
                    country_flag=flag,
                    panel_id=p.id))
                added += 1
        db.session.commit()
        return jsonify({"ok": True, "added": added, "total": len(fetched)})

    @app.route("/admin/numbers/upload",           methods=["POST"])
    @admin_required
    def admin_numbers_upload():
        panel_id = request.form.get("panel_id", type=int)
        file     = request.files.get("file")
        if not file:
            flash("No file selected.", "error")
            return redirect("/admin#numbers")
        text    = file.read().decode("utf-8", errors="ignore")
        reader  = csv.reader(io.StringIO(text))
        added = skipped = 0
        for row in reader:
            if not row: continue
            num = row[0].strip()
            if not num: continue
            pid = int(row[1].strip()) if len(row) > 1 and row[1].strip().isdigit() else panel_id
            if not pid or not Panel.query.get(pid):
                skipped += 1; continue
            if VirtualNumber.query.filter_by(number=num, panel_id=pid).first():
                skipped += 1; continue
            name, flag = dial_to_country(num)
            db.session.add(VirtualNumber(
                number=num, country_code=name[:2].upper(),
                country_name=name, country_flag=flag, panel_id=pid))
            added += 1
        db.session.commit()
        flash(f"Uploaded {added} numbers. {skipped} skipped.", "success")
        return redirect("/admin#numbers")

    @app.route("/admin/numbers/<int:nid>/delete", methods=["POST"])
    @admin_required
    def admin_number_delete(nid):
        db.session.delete(VirtualNumber.query.get_or_404(nid))
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/admin/numbers/<int:nid>/toggle", methods=["POST"])
    @admin_required
    def admin_number_toggle(nid):
        n = VirtualNumber.query.get_or_404(nid)
        n.is_available = not n.is_available
        db.session.commit()
        return jsonify({"ok": True, "available": n.is_available})

    @app.route("/admin/users/<int:uid>/toggle_admin", methods=["POST"])
    @admin_required
    def admin_user_toggle_admin(uid):
        u = User.query.get_or_404(uid)
        u.is_admin = not u.is_admin
        db.session.commit()
        return jsonify({"ok": True, "is_admin": u.is_admin})

    @app.route("/admin/users/<int:uid>/delete",   methods=["POST"])
    @admin_required
    def admin_user_delete(uid):
        db.session.delete(User.query.get_or_404(uid))
        db.session.commit()
        return jsonify({"ok": True})

    @app.route("/api/admin/stats")
    @admin_required
    def api_admin_stats():
        return jsonify(
            panels   = Panel.query.count(),
            online   = Panel.query.filter_by(status="online").count(),
            numbers  = VirtualNumber.query.count(),
            available= VirtualNumber.query.filter_by(is_available=True).count(),
            users    = User.query.count(),
            orders   = Order.query.count(),
            messages = SMSMessage.query.count(),
        )

    return app


def _seed_admin():
    email = os.getenv("ADMIN_EMAIL", "adnannoordogar01@gmail.com")
    pw    = os.getenv("ADMIN_PASSWORD", "Adnan#100400")
    if not User.query.filter_by(email=email).first():
        u = User(username="admin", email=email, is_admin=True)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
