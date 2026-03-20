"""models.py — SigmaFetcher V10"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64),  unique=True, nullable=False)
    email         = db.Column(db.String(128), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False)
    avatar        = db.Column(db.String(8),   default="👤")   # emoji avatar
    display_name  = db.Column(db.String(64),  nullable=True)  # editable display name
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    orders = db.relationship("Order", backref="user", lazy="dynamic",
                             cascade="all, delete-orphan")

    def set_password(self, pw):  self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)

    def to_dict(self):
        return dict(id=self.id, username=self.username, email=self.email,
                    is_admin=self.is_admin, avatar=self.avatar or "👤",
                    display_name=self.display_name or self.username,
                    created_at=self.created_at.isoformat())


class Panel(db.Model):
    __tablename__ = "panels"
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(64),  nullable=False)
    base_url     = db.Column(db.String(256), nullable=True)   # null for IVAS
    username     = db.Column(db.String(128), nullable=True)   # null for API/IVAS
    password     = db.Column(db.String(128), nullable=True)   # null for API/IVAS
    panel_type   = db.Column(db.String(16),  default="login") # login | api | ivas
    token        = db.Column(db.String(256), nullable=True)   # API panels
    uri          = db.Column(db.String(512), nullable=True)   # IVAS WebSocket URI
    active       = db.Column(db.Boolean,     default=True)
    status       = db.Column(db.String(16),  default="unknown")
    last_checked = db.Column(db.DateTime,    nullable=True)
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)
    numbers      = db.relationship("VirtualNumber", backref="panel", lazy="dynamic",
                                   cascade="all, delete-orphan")

    def to_dict(self):
        return dict(
            id=self.id, name=self.name, base_url=self.base_url,
            username=self.username, panel_type=self.panel_type,
            active=self.active, status=self.status,
            has_token=bool(self.token), has_uri=bool(self.uri),
            last_checked=self.last_checked.isoformat() if self.last_checked else None,
            available=self.numbers.filter_by(is_available=True).count(),
            total=self.numbers.count(),
        )


class VirtualNumber(db.Model):
    __tablename__ = "virtual_numbers"
    id           = db.Column(db.Integer, primary_key=True)
    number       = db.Column(db.String(32),  nullable=False, index=True)
    country_code = db.Column(db.String(8))
    country_name = db.Column(db.String(64))
    country_flag = db.Column(db.String(8))
    panel_id     = db.Column(db.Integer, db.ForeignKey("panels.id"), nullable=False)
    is_available = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    messages     = db.relationship("SMSMessage", backref="virtual_number", lazy="dynamic",
                                   cascade="all, delete-orphan")
    orders       = db.relationship("Order",      backref="virtual_number", lazy="dynamic")

    def to_dict(self):
        return dict(id=self.id, number=self.number,
                    country_code=self.country_code, country_name=self.country_name,
                    country_flag=self.country_flag,
                    panel_name=self.panel.name if self.panel else "—",
                    panel_type=self.panel.panel_type if self.panel else "—",
                    panel_id=self.panel_id, is_available=self.is_available,
                    msg_count=self.messages.count())


class SMSMessage(db.Model):
    __tablename__ = "sms_messages"
    id                = db.Column(db.Integer, primary_key=True)
    virtual_number_id = db.Column(db.Integer, db.ForeignKey("virtual_numbers.id"), nullable=False)
    sender            = db.Column(db.String(64))
    message           = db.Column(db.Text, nullable=False)
    received_at       = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return dict(id=self.id, sender=self.sender, message=self.message,
                    received_at=self.received_at.isoformat())


class Order(db.Model):
    __tablename__ = "orders"
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey("users.id"),           nullable=False)
    virtual_number_id = db.Column(db.Integer, db.ForeignKey("virtual_numbers.id"), nullable=False)
    status            = db.Column(db.String(16), default="active")
    created_at        = db.Column(db.DateTime,   default=datetime.utcnow)
    expires_at        = db.Column(db.DateTime,   nullable=True)

    def to_dict(self):
        vn = self.virtual_number
        return dict(id=self.id, status=self.status,
                    number=vn.number       if vn else "—",
                    country=vn.country_name if vn else "—",
                    flag=vn.country_flag    if vn else "",
                    panel=vn.panel.name     if vn and vn.panel else "—",
                    number_id=vn.id         if vn else None,
                    created_at=self.created_at.isoformat(),
                    expires_at=self.expires_at.isoformat() if self.expires_at else None,
                    messages=[m.to_dict() for m in
                              vn.messages.order_by(SMSMessage.received_at.desc()).limit(20)]
                              if vn else [])
