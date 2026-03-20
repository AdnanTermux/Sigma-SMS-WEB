"""
panel_client.py — SigmaFetcher V10
Robust scraper for /ints panels, timesms.net, and ivas-style panels.
Re-logins automatically on session expiry.
"""
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging

log     = logging.getLogger(__name__)
TIMEOUT = 20

# Regex to extract any 4-8 digit OTP from a message
OTP_RE = re.compile(r'\b\d{4,8}\b')


class PanelClient:
    def __init__(self, panel):
        self.panel   = panel
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        self._logged_in = False
        self.ptype = panel.panel_type  # "ints" | "timesms" | "ivas"

    # ── SESSION CHECK ──────────────────────────────────────────────────
    def _is_session_valid(self, html: str) -> bool:
        """Detect if we've been redirected to the login page."""
        low = html.lower()
        triggers = ["please login", "please log in", "sign in", "login required",
                    '<input.*?name="password"', "session expired", "unauthorized"]
        for t in triggers:
            if re.search(t, low):
                return False
        return True

    def _ensure_logged_in(self) -> bool:
        if self._logged_in:
            return True
        return self.login()

    # ── LOGIN ──────────────────────────────────────────────────────────
    def login(self) -> bool:
        try:
            if self.ptype == "timesms":
                ok = self._login_timesms()
            elif self.ptype == "ivas":
                ok = self._login_ivas()
            else:
                ok = self._login_ints()
            self._logged_in = ok
            if not ok:
                log.warning("[%s] Login FAILED", self.panel.name)
            else:
                log.info("[%s] Login OK", self.panel.name)
            return ok
        except Exception as e:
            log.warning("[%s] Login exception: %s", self.panel.name, e)
            self._logged_in = False
            return False

    def _login_ints(self) -> bool:
        """Login to /ints panel (standard Sigma panel type)."""
        base = self.panel.base_url.rstrip("/")
        # Try POST to /login
        try:
            r = self.session.post(
                f"{base}/login",
                data={"username": self.panel.username, "password": self.panel.password},
                timeout=TIMEOUT, allow_redirects=True)
            html = r.text.lower()
            # Success indicators
            if any(x in html for x in ["logout", "sign out", "dashboard", "welcome",
                                        "smscdr", "inbox", "numbers"]):
                return True
            # Some panels redirect to dashboard on success
            if r.url and r.url != f"{base}/login" and "login" not in r.url.lower():
                return True
        except Exception as e:
            log.warning("[%s] _login_ints error: %s", self.panel.name, e)
        return False

    def _login_timesms(self) -> bool:
        """Login to timesms.net."""
        base = self.panel.base_url.rstrip("/")
        # First GET to get any CSRF tokens
        try:
            self.session.get(f"{base}/", timeout=TIMEOUT)
        except Exception:
            pass
        # Try both /index.php and /login.php
        for path in ["/index.php", "/login.php", "/login"]:
            try:
                r = self.session.post(
                    f"{base}{path}",
                    data={
                        "username": self.panel.username,
                        "password": self.panel.password,
                        "submit": "Login",
                        "action": "login",
                    },
                    timeout=TIMEOUT, allow_redirects=True)
                html = r.text.lower()
                if any(x in html for x in ["logout", "dashboard", "sign out",
                                            "numbers", "inbox", "welcome"]):
                    return True
                if r.status_code == 200 and "login" not in r.url.lower():
                    return True
            except Exception:
                continue
        return False

    def _login_ivas(self) -> bool:
        """Login to ivas-style panel."""
        base = self.panel.base_url.rstrip("/")
        for path in ["/login", "/ivas/login", "/admin/login", "/"]:
            try:
                r = self.session.post(
                    f"{base}{path}",
                    data={"username": self.panel.username, "password": self.panel.password},
                    timeout=TIMEOUT, allow_redirects=True)
                html = r.text.lower()
                if any(x in html for x in ["logout", "dashboard", "numbers", "sms"]):
                    return True
            except Exception:
                continue
        return False

    # ── FETCH NUMBERS ──────────────────────────────────────────────────
    def fetch_numbers(self) -> list[dict]:
        if not self._ensure_logged_in():
            return []
        try:
            if self.ptype == "timesms":
                return self._numbers_timesms()
            elif self.ptype == "ivas":
                return self._numbers_ivas()
            else:
                return self._numbers_ints()
        except Exception as e:
            log.warning("[%s] fetch_numbers error: %s", self.panel.name, e)
            return []

    def _numbers_ints(self) -> list[dict]:
        """Fetch number list from /ints panel — tries multiple endpoints."""
        base = self.panel.base_url.rstrip("/")
        out  = []

        for path in ["/SMSCDRStats", "/numbers", "/inbox", "/sms"]:
            try:
                r = self.session.get(f"{base}{path}", timeout=TIMEOUT)
                if not self._is_session_valid(r.text):
                    # Session expired — re-login
                    self._logged_in = False
                    if not self.login():
                        return []
                    r = self.session.get(f"{base}{path}", timeout=TIMEOUT)

                soup = BeautifulSoup(r.text, "lxml")
                rows = soup.select("table tr")
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    cols = row.find_all("td")
                    if not cols:
                        continue
                    num = cols[0].get_text(strip=True)
                    # Phone numbers: start with + or digit, 8-15 chars
                    num = re.sub(r"[^\d+]", "", num)
                    if not num or len(num) < 7:
                        continue
                    cc = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                    out.append({"number": num, "country_code": cc,
                                "country_name": cc, "country_flag": ""})
                if out:
                    return out
            except Exception as e:
                log.warning("[%s] _numbers_ints %s: %s", self.panel.name, path, e)
                continue
        return out

    def _numbers_timesms(self) -> list[dict]:
        base = self.panel.base_url.rstrip("/")
        out  = []
        for path in ["/numbers.php", "/numbers", "/api/numbers"]:
            try:
                r = self.session.get(f"{base}{path}", timeout=TIMEOUT)
                if not self._is_session_valid(r.text):
                    self._logged_in = False
                    if not self.login():
                        return []
                    r = self.session.get(f"{base}{path}", timeout=TIMEOUT)
                # Try JSON first
                try:
                    data = r.json()
                    if isinstance(data, list):
                        for item in data:
                            num = str(item.get("number", item.get("msisdn", item.get("phone", ""))))
                            if num and len(num) >= 7:
                                out.append({"number": num, "country_code": item.get("country",""),
                                            "country_name": item.get("country",""), "country_flag": ""})
                        if out:
                            return out
                except Exception:
                    pass
                # HTML fallback
                soup = BeautifulSoup(r.text, "lxml")
                for row in soup.select("table tr")[1:]:
                    cols = row.find_all("td")
                    if not cols:
                        continue
                    num = re.sub(r"[^\d+]", "", cols[0].get_text(strip=True))
                    if num and len(num) >= 7:
                        cc = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                        out.append({"number": num, "country_code": cc,
                                    "country_name": cc, "country_flag": ""})
                if out:
                    return out
            except Exception:
                continue
        return out

    def _numbers_ivas(self) -> list[dict]:
        base = self.panel.base_url.rstrip("/")
        out  = []
        for path in ["/numbers", "/api/numbers", "/ivas/numbers", "/inbox"]:
            try:
                r = self.session.get(f"{base}{path}", timeout=TIMEOUT)
                soup = BeautifulSoup(r.text, "lxml")
                for row in soup.select("table tr")[1:]:
                    cols = row.find_all("td")
                    if not cols:
                        continue
                    num = re.sub(r"[^\d+]", "", cols[0].get_text(strip=True))
                    if num and len(num) >= 7:
                        out.append({"number": num, "country_code": "",
                                    "country_name": "", "country_flag": ""})
                if out:
                    return out
            except Exception:
                continue
        return out

    # ── FETCH SMS ──────────────────────────────────────────────────────
    def fetch_sms(self, number: str) -> list[dict]:
        if not self._ensure_logged_in():
            return []
        try:
            if self.ptype == "timesms":
                return self._sms_timesms(number)
            elif self.ptype == "ivas":
                return self._sms_ivas(number)
            else:
                return self._sms_ints(number)
        except Exception as e:
            log.warning("[%s] fetch_sms error: %s", self.panel.name, e)
            return []

    def _parse_sms_rows(self, soup: BeautifulSoup, number: str) -> list[dict]:
        """
        Generic table parser — tries to find sender/message/timestamp columns.
        Works across different /ints panel versions.
        """
        out   = []
        rows  = soup.select("table tr")[1:]
        if not rows:
            # Try any table
            rows = soup.select("tr")[1:]

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue

            # Column heuristic: look for a column that looks like a message
            # Typical layout: [number/sender] [message] [timestamp]
            #               or [timestamp] [sender] [message]
            texts = [c.get_text(strip=True) for c in cols]

            # Find the longest text — likely the message body
            msg_idx = max(range(len(texts)), key=lambda i: len(texts[i]))
            msg     = texts[msg_idx]
            if not msg or len(msg) < 2:
                continue

            # Sender = first short field that isn't the message
            sender = ""
            for i, t in enumerate(texts):
                if i != msg_idx and t and len(t) < 30:
                    sender = t
                    break

            # Timestamp — try to parse any field
            ts = datetime.utcnow()
            for t in texts:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                            "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S"):
                    try:
                        ts = datetime.strptime(t, fmt)
                        break
                    except Exception:
                        continue

            out.append({"sender": sender, "message": msg, "received_at": ts})
        return out

    def _sms_ints(self, number: str) -> list[dict]:
        base = self.panel.base_url.rstrip("/")
        # Multiple endpoint patterns used by different /ints versions
        attempts = [
            (f"{base}/SMSCDRStats", {"number": number}),
            (f"{base}/SMSCDRStats", {"msisdn": number, "limit": 50}),
            (f"{base}/sms",         {"number": number}),
            (f"{base}/inbox",       {"number": number}),
            (f"{base}/messages",    {"number": number}),
        ]
        for url, params in attempts:
            try:
                r = self.session.get(url, params=params, timeout=TIMEOUT)
                if not self._is_session_valid(r.text):
                    self._logged_in = False
                    if not self.login():
                        return []
                    r = self.session.get(url, params=params, timeout=TIMEOUT)

                # Try JSON response first
                try:
                    data = r.json()
                    msgs = data if isinstance(data, list) else data.get("messages", data.get("data", []))
                    if msgs:
                        out = []
                        for m in msgs:
                            out.append({
                                "sender":      str(m.get("sender", m.get("from", m.get("msisdn", "")))),
                                "message":     str(m.get("message", m.get("text", m.get("body", "")))),
                                "received_at": datetime.utcnow(),
                            })
                        return out
                except Exception:
                    pass

                # HTML scrape
                soup = BeautifulSoup(r.text, "lxml")
                out  = self._parse_sms_rows(soup, number)
                if out:
                    return out
            except Exception as e:
                log.debug("[%s] _sms_ints %s: %s", self.panel.name, url, e)
                continue
        return []

    def _sms_timesms(self, number: str) -> list[dict]:
        base = self.panel.base_url.rstrip("/")
        for path, params in [
            ("/sms.php",    {"number": number}),
            ("/api/sms",    {"number": number}),
            ("/inbox.php",  {"number": number}),
            ("/messages",   {"msisdn": number}),
        ]:
            try:
                r = self.session.get(f"{base}{path}", params=params, timeout=TIMEOUT)
                if not self._is_session_valid(r.text):
                    self._logged_in = False
                    if not self.login():
                        return []
                    r = self.session.get(f"{base}{path}", params=params, timeout=TIMEOUT)
                try:
                    data = r.json()
                    msgs = data if isinstance(data, list) else data.get("messages", [])
                    if msgs:
                        return [{"sender": str(m.get("sender", m.get("from", ""))),
                                 "message": str(m.get("message", m.get("text", ""))),
                                 "received_at": datetime.utcnow()} for m in msgs]
                except Exception:
                    pass
                soup = BeautifulSoup(r.text, "lxml")
                out  = self._parse_sms_rows(soup, number)
                if out:
                    return out
            except Exception:
                continue
        return []

    def _sms_ivas(self, number: str) -> list[dict]:
        base = self.panel.base_url.rstrip("/")
        for path in ["/sms", "/messages", "/ivas/sms", "/inbox"]:
            try:
                r = self.session.get(f"{base}{path}", params={"number": number}, timeout=TIMEOUT)
                soup = BeautifulSoup(r.text, "lxml")
                out  = self._parse_sms_rows(soup, number)
                if out:
                    return out
            except Exception:
                continue
        return []

    # ── PING ───────────────────────────────────────────────────────────
    def ping(self) -> bool:
        try:
            r = self.session.get(self.panel.base_url, timeout=8)
            return r.status_code < 500
        except Exception:
            return False


# ── Module-level client cache ──────────────────────────────────────────
_clients: dict[int, "PanelClient"] = {}

def get_client(panel) -> PanelClient:
    if panel.id not in _clients:
        _clients[panel.id] = PanelClient(panel)
    return _clients[panel.id]

def evict_client(panel_id: int):
    _clients.pop(panel_id, None)
