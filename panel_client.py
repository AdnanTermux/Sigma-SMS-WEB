"""
panel_client.py — Sigma Fetcher Web
Handles login + SMS fetching for /ints panels and timesms.net panels.
"""
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging

log = logging.getLogger(__name__)

TIMEOUT = 15


class PanelClient:
    """HTTP session-based client for a single panel."""

    def __init__(self, panel):
        self.panel    = panel
        self.session  = requests.Session()
        self.session.headers.update({"User-Agent": "SigmaFetcher/10"})
        self._logged_in = False

    # ── LOGIN ──────────────────────────────────────────────────────────────

    def login(self) -> bool:
        try:
            if self.panel.panel_type == "timesms":
                return self._login_timesms()
            return self._login_ints()
        except Exception as e:
            log.warning("Panel %s login error: %s", self.panel.name, e)
            return False

    def _login_ints(self) -> bool:
        url = f"{self.panel.base_url.rstrip('/')}/login"
        r = self.session.post(url, data={
            "username": self.panel.username,
            "password": self.panel.password,
        }, timeout=TIMEOUT, allow_redirects=True)
        ok = r.status_code == 200 and "logout" in r.text.lower()
        self._logged_in = ok
        return ok

    def _login_timesms(self) -> bool:
        url = f"{self.panel.base_url.rstrip('/')}/index.php"
        r = self.session.post(url, data={
            "username": self.panel.username,
            "password": self.panel.password,
            "submit":   "Login",
        }, timeout=TIMEOUT, allow_redirects=True)
        ok = r.status_code == 200 and ("logout" in r.text.lower()
                                        or "dashboard" in r.text.lower())
        self._logged_in = ok
        return ok

    # ── FETCH NUMBERS ──────────────────────────────────────────────────────

    def fetch_numbers(self) -> list[dict]:
        """Return list of {number, country_code, country_name} dicts."""
        if not self._logged_in and not self.login():
            return []
        try:
            if self.panel.panel_type == "timesms":
                return self._fetch_numbers_timesms()
            return self._fetch_numbers_ints()
        except Exception as e:
            log.warning("Panel %s fetch_numbers error: %s", self.panel.name, e)
            return []

    def _fetch_numbers_ints(self) -> list[dict]:
        url = f"{self.panel.base_url.rstrip('/')}/SMSCDRStats"
        r   = self.session.get(url, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "lxml")
        numbers = []
        for row in soup.select("table tr")[1:]:
            cols = row.find_all("td")
            if len(cols) >= 2:
                num = cols[0].get_text(strip=True)
                cc  = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                if num:
                    numbers.append({"number": num, "country_code": cc,
                                    "country_name": cc, "country_flag": ""})
        return numbers

    def _fetch_numbers_timesms(self) -> list[dict]:
        url  = f"{self.panel.base_url.rstrip('/')}/numbers.php"
        r    = self.session.get(url, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "lxml")
        numbers = []
        for row in soup.select("table tr")[1:]:
            cols = row.find_all("td")
            if len(cols) >= 1:
                num = cols[0].get_text(strip=True)
                cc  = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                if num:
                    numbers.append({"number": num, "country_code": cc,
                                    "country_name": cc, "country_flag": ""})
        return numbers

    # ── FETCH SMS ──────────────────────────────────────────────────────────

    def fetch_sms(self, number: str) -> list[dict]:
        """Return list of {sender, message, received_at} dicts for a number."""
        if not self._logged_in and not self.login():
            return []
        try:
            if self.panel.panel_type == "timesms":
                return self._fetch_sms_timesms(number)
            return self._fetch_sms_ints(number)
        except Exception as e:
            log.warning("Panel %s fetch_sms error: %s", self.panel.name, e)
            return []

    def _fetch_sms_ints(self, number: str) -> list[dict]:
        url = f"{self.panel.base_url.rstrip('/')}/SMSCDRStats"
        r   = self.session.get(url, params={"number": number}, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "lxml")
        msgs = []
        for row in soup.select("table tr")[1:]:
            cols = row.find_all("td")
            if len(cols) >= 3:
                sender  = cols[0].get_text(strip=True)
                message = cols[1].get_text(strip=True)
                ts_raw  = cols[2].get_text(strip=True)
                try:
                    ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts = datetime.utcnow()
                if message:
                    msgs.append({"sender": sender, "message": message,
                                 "received_at": ts})
        return msgs

    def _fetch_sms_timesms(self, number: str) -> list[dict]:
        url = f"{self.panel.base_url.rstrip('/')}/sms.php"
        r   = self.session.get(url, params={"number": number}, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "lxml")
        msgs = []
        for row in soup.select("table tr")[1:]:
            cols = row.find_all("td")
            if len(cols) >= 2:
                sender  = cols[0].get_text(strip=True)
                message = cols[1].get_text(strip=True)
                ts_raw  = cols[2].get_text(strip=True) if len(cols) > 2 else ""
                try:
                    ts = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    ts = datetime.utcnow()
                if message:
                    msgs.append({"sender": sender, "message": message,
                                 "received_at": ts})
        return msgs

    # ── PING ───────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Quick reachability check."""
        try:
            r = self.session.get(self.panel.base_url, timeout=8)
            return r.status_code < 500
        except Exception:
            return False


# module-level cache: panel_id → PanelClient
_clients: dict[int, PanelClient] = {}


def get_client(panel) -> PanelClient:
    if panel.id not in _clients:
        _clients[panel.id] = PanelClient(panel)
    return _clients[panel.id]


def evict_client(panel_id: int):
    _clients.pop(panel_id, None)
