"""
panel_client.py — SigmaFetcher V10
Exact panel logic ported from bot.py for all three panel types:
  login — /ints-style login form → sesskey discovery → aaData fetch
  api   — token-based GET, returns JSON records
  ivas  — WebSocket (not used for web polling; stored as type marker)
"""
import re, logging, requests
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

log     = logging.getLogger("sigmafetcher")
TIMEOUT = 20


class PanelClient:
    def __init__(self, panel):
        self.panel      = panel
        self.ptype      = panel.panel_type   # "login" | "api" | "ivas"
        self.session    = requests.Session()
        self.session.headers.update({
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        self.api_url  = None   # discovered after login (login panels)
        self.sesskey  = None   # discovered after login (login panels)
        self.stats_url= None   # Referer URL for XHR calls
        self._logged_in = False

    # ── LOGIN PANEL ────────────────────────────────────────────────────
    # Mirrors bot.py login_to_panel() exactly
    def login(self) -> bool:
        if self.ptype == "api":
            return self._test_api()
        if self.ptype == "ivas":
            return True   # IVAS uses WebSocket; web platform just shows status
        return self._login_form()

    def _login_form(self) -> bool:
        """Scrape login form, POST credentials, discover sAjaxSource endpoint."""
        base      = self.panel.base_url.rstrip("/")
        login_url = f"{base}/login"
        try:
            r = self.session.get(login_url, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code != 200:
                log.warning("[%s] Login page HTTP %s", self.panel.name, r.status_code)
                return False

            soup = BeautifulSoup(r.text, "html.parser")
            form = soup.find("form")
            if not form:
                log.warning("[%s] No <form> found at %s", self.panel.name, login_url)
                return False

            # Build payload — mirrors bot.py field detection
            payload = {}
            for tag in form.find_all("input"):
                nm  = tag.get("name")
                val = tag.get("value", "")
                ph  = (tag.get("placeholder", "") + " " + (nm or "")).lower()
                tp  = tag.get("type", "text").lower()
                if not nm:
                    continue
                if tp == "hidden":
                    payload[nm] = val
                elif any(k in ph for k in ("user","email","login","uname","username")):
                    payload[nm] = self.panel.username or ""
                elif any(k in ph for k in ("pass","pwd","secret","password")):
                    payload[nm] = self.panel.password or ""
                elif any(k in ph for k in ("ans","captcha","answer","result","sum","calc")):
                    # Arithmetic captcha solver (same as bot.py)
                    cap = re.search(r"(\d+)\s*([+\-*])\s*(\d+)", form.get_text() or r.text)
                    if cap:
                        n1, op, n2 = int(cap.group(1)), cap.group(2), int(cap.group(3))
                        ans = n1+n2 if op=="+" else (n1-n2 if op=="-" else n1*n2)
                        payload[nm] = str(ans)
                else:
                    payload[nm] = val

            # Resolve form action (same as bot.py urljoin logic)
            raw_action = (form.get("action") or "").strip()
            if raw_action:
                action = raw_action if raw_action.startswith("http") else urljoin(login_url, raw_action)
            else:
                action = login_url

            origin = login_url.split("/ints/")[0] if "/ints/" in login_url else "/".join(login_url.split("/")[:3])
            pr = self.session.post(
                action, data=payload,
                headers={"Referer": login_url, "Origin": origin},
                timeout=TIMEOUT, allow_redirects=True)

            final_url = pr.url
            body_l    = pr.text.lower()

            # Detect success/failure — mirrors bot.py _OK_BODY / _FAIL_BODY
            _OK_BODY   = {"logout","log out","sign out","signout","dashboard",
                          "smscdr","sms log","sms report","smscdrstats","welcome",
                          "my account","sms dashboard","smsdashboard"}
            _FAIL_BODY = {"invalid","incorrect","wrong password","failed","error","invalid credentials"}
            _OK_URL    = {"dashboard","smscdr","smscdrstats","welcome","inbox","report","home"}

            body_ok   = any(k in body_l for k in _OK_BODY)
            body_fail = any(k in body_l for k in _FAIL_BODY)
            url_ok    = any(k in final_url.lower() for k in _OK_URL)

            if body_fail and not body_ok:
                log.warning("[%s] Login FAILED (bad credentials)", self.panel.name)
                return False
            if not body_ok and not url_ok:
                log.warning("[%s] Login FAILED (no success signal)", self.panel.name)
                return False

            # Discover API endpoint via sAjaxSource — mirrors bot.py discovery
            parsed_final = urlparse(final_url)
            path_parts   = parsed_final.path.rstrip("/").rsplit("/", 1)
            redirect_dir = path_parts[0] if len(path_parts) > 1 else ""
            redirect_base = f"{parsed_final.scheme}://{parsed_final.netloc}{redirect_dir}"

            candidate_bases = []
            if redirect_base and redirect_base != base:
                candidate_bases.append(redirect_base)
            candidate_bases.append(base)

            for disc_base in candidate_bases:
                for stats_path in ["/SMSCDRStats","/client/SMSCDRStats",
                                   "/smscdrstats","/sms/log","/smslogs","/sms"]:
                    try:
                        stats_url = disc_base + stats_path
                        sr = self.session.get(stats_url, timeout=10)
                        if sr.status_code != 200:
                            continue
                        for sc in BeautifulSoup(sr.text, "html.parser").find_all("script"):
                            if not sc.string:
                                continue
                            m = re.search(r'sAjaxSource["\'\s]*:\s*["\']([^"\']+)["\']', sc.string)
                            if m:
                                found = m.group(1)
                                if not found.startswith("http"):
                                    found = disc_base + "/" + found.lstrip("/")
                                if "sesskey=" in found:
                                    parts         = found.split("?", 1)
                                    self.api_url  = parts[0]
                                    sk = re.search(r"sesskey=([^&]+)", parts[1])
                                    if sk: self.sesskey = sk.group(1)
                                else:
                                    self.api_url = found
                                self.stats_url  = stats_url
                                self._logged_in = True
                                log.info("[%s] Endpoint: %s", self.panel.name, self.api_url)
                                return True
                    except Exception as e:
                        log.debug("[%s] discovery %s: %s", self.panel.name, stats_url, e)

            # Fallback — mirrors bot.py fallback
            best_base       = candidate_bases[0]
            self.api_url    = f"{best_base}/res/data_smscdr.php"
            self.stats_url  = f"{best_base}/SMSCDRStats"
            self._logged_in = True
            log.info("[%s] Fallback endpoint: %s", self.panel.name, self.api_url)
            return True

        except requests.RequestException as e:
            log.error("[%s] Login network error: %s", self.panel.name, e)
            return False

    # ── API PANEL ──────────────────────────────────────────────────────
    # Mirrors bot.py test_api_panel() and fetch_panel_sms(api)
    def _test_api(self) -> bool:
        try:
            now  = datetime.now()
            prev = now - timedelta(hours=24)
            params = {
                "token":   self.panel.token,
                "dt1":     prev.strftime("%Y-%m-%d %H:%M:%S"),
                "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
                "records": 1,
            }
            r = self.session.get(self.panel.base_url, params=params, timeout=15)
            if r.status_code != 200:
                return False
            data = r.json()
            if isinstance(data, list):
                self._logged_in = True
                return True
            if isinstance(data, dict):
                st = str(data.get("status","")).lower()
                if st == "error":
                    return False
                self._logged_in = True
                return st == "success" or any(k in data for k in ("data","records","sms"))
        except Exception as e:
            log.warning("[%s] API test error: %s", self.panel.name, e)
        return False

    # ── FETCH SMS ──────────────────────────────────────────────────────
    def fetch_sms(self, number: str) -> list[dict]:
        if self.ptype == "api":
            return self._fetch_api(number)
        if self.ptype == "login":
            if not self._logged_in:
                self.login()
            return self._fetch_login(number)
        return []   # IVAS: live WebSocket, not HTTP-polled

    def _fetch_api(self, number: str) -> list[dict]:
        """Mirrors bot.py fetch_panel_sms() for api type."""
        try:
            now  = datetime.now()
            prev = now - timedelta(days=1)
            params = {
                "token":   self.panel.token,
                "dt1":     prev.strftime("%Y-%m-%d %H:%M:%S"),
                "dt2":     now.strftime("%Y-%m-%d %H:%M:%S"),
                "records": 200,
            }
            r    = self.session.get(self.panel.base_url, params=params, timeout=20)
            if r.status_code != 200:
                return []
            data    = r.json()
            records = []
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                if str(data.get("status","")).lower() == "error":
                    return []
                records = (data.get("data") or data.get("records") or
                           data.get("sms")  or data.get("messages") or [])
            out = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                num = str(rec.get("num") or rec.get("number") or
                          rec.get("recipient") or rec.get("phone") or "").replace("+","").strip()
                # Filter to the requested number if provided
                if number and number.replace("+","").strip() not in num:
                    continue
                msg = str(rec.get("message") or rec.get("text") or
                          rec.get("body") or rec.get("content") or "")
                sender = str(rec.get("cli") or rec.get("sender") or
                             rec.get("originator") or rec.get("service") or "")
                dt_raw = str(rec.get("dt") or rec.get("date") or rec.get("timestamp") or "")
                ts = self._parse_dt(dt_raw)
                if msg:
                    out.append({"sender": sender, "message": msg, "received_at": ts})
            return out
        except Exception as e:
            log.warning("[%s] API fetch error: %s", self.panel.name, e)
            return []

    def _fetch_login(self, number: str) -> list[dict]:
        """Mirrors bot.py fetch_panel_sms() for login type — uses aaData."""
        if not self.api_url:
            return []
        try:
            now  = datetime.now()
            prev = now - timedelta(days=1)
            params = {
                "fdate1":        prev.strftime("%Y-%m-%d %H:%M:%S"),
                "fdate2":        now.strftime("%Y-%m-%d %H:%M:%S"),
                "sEcho":         "1",
                "iDisplayStart": "0",
                "iDisplayLength":"200",
                "iSortCol_0":    "0",
                "sSortDir_0":    "desc",
            }
            if self.sesskey:
                params["sesskey"] = self.sesskey
            referer = self.stats_url or f"{self.panel.base_url}/SMSCDRStats"
            headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          referer,
                "Accept":           "application/json, text/javascript, */*; q=0.01",
            }
            r = self.session.get(self.api_url, params=params, headers=headers, timeout=15)
            if r.status_code != 200:
                # Session may have expired — re-login once
                self._logged_in = False
                if self.login():
                    return self._fetch_login(number)
                return []
            data = r.json()
            if "aaData" not in data:
                return []
            out = []
            for row in data["aaData"]:
                # aaData columns: [dt, number, cli/service, ?, ?, message, ...]
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                num = str(row[1]).replace("+","").strip()
                if number and number.replace("+","").strip() not in num:
                    continue
                msg    = ""
                sender = str(row[2]) if len(row) > 2 else ""
                # Find the message body — longest text field
                for idx in [5, 4, 3]:
                    if len(row) > idx and str(row[idx]).strip() not in {"0","0.00","€","$","null","None",""}:
                        msg = str(row[idx]).strip()
                        if len(msg) > 1:
                            break
                ts = self._parse_dt(str(row[0])) if row else datetime.utcnow()
                if msg:
                    out.append({"sender": sender, "message": msg, "received_at": ts})
            return out
        except Exception as e:
            log.warning("[%s] Login-panel fetch error: %s", self.panel.name, e)
            return []

    # ── FETCH NUMBERS (from login panel stats page) ────────────────────
    def fetch_numbers(self) -> list[dict]:
        if self.ptype == "api":
            return self._numbers_api()
        if self.ptype == "login":
            if not self._logged_in:
                self.login()
            return self._numbers_login()
        return []  # IVAS: numbers come from WebSocket live

    def _numbers_api(self) -> list[dict]:
        try:
            now  = datetime.now()
            prev = now - timedelta(days=1)
            params = {
                "token": self.panel.token,
                "dt1":   prev.strftime("%Y-%m-%d %H:%M:%S"),
                "dt2":   now.strftime("%Y-%m-%d %H:%M:%S"),
                "records": 200,
            }
            r    = self.session.get(self.panel.base_url, params=params, timeout=20)
            data = r.json()
            records = data if isinstance(data, list) else (
                data.get("data") or data.get("records") or [])
            seen = set()
            out  = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                num = str(rec.get("num") or rec.get("number") or
                          rec.get("recipient") or "").replace("+","").strip()
                if num and num not in seen:
                    seen.add(num)
                    out.append({"number": num, "country_code": "",
                                "country_name": "", "country_flag": ""})
            return out
        except Exception as e:
            log.warning("[%s] API numbers error: %s", self.panel.name, e)
            return []

    def _numbers_login(self) -> list[dict]:
        """Pull distinct numbers from aaData for login panels."""
        if not self.api_url:
            return []
        try:
            now  = datetime.now()
            prev = now - timedelta(days=1)
            params = {
                "fdate1":        prev.strftime("%Y-%m-%d %H:%M:%S"),
                "fdate2":        now.strftime("%Y-%m-%d %H:%M:%S"),
                "sEcho":         "1",
                "iDisplayStart": "0",
                "iDisplayLength":"200",
                "iSortCol_0":    "0",
                "sSortDir_0":    "desc",
            }
            if self.sesskey:
                params["sesskey"] = self.sesskey
            referer = self.stats_url or f"{self.panel.base_url}/SMSCDRStats"
            r = self.session.get(self.api_url, params=params, timeout=15,
                                 headers={"X-Requested-With":"XMLHttpRequest",
                                          "Referer": referer})
            if r.status_code != 200:
                return []
            data = r.json()
            rows = data.get("aaData", [])
            seen = set(); out = []
            for row in rows:
                if len(row) < 2: continue
                num = str(row[1]).replace("+","").strip()
                if num and num not in seen:
                    seen.add(num)
                    out.append({"number": num, "country_code": "",
                                "country_name": "", "country_flag": ""})
            return out
        except Exception as e:
            log.warning("[%s] Login numbers error: %s", self.panel.name, e)
            return []

    # ── PING ───────────────────────────────────────────────────────────
    def ping(self) -> bool:
        try:
            r = self.session.get(self.panel.base_url, timeout=8, allow_redirects=True)
            return r.status_code < 500
        except Exception:
            return False

    # ── HELPER ─────────────────────────────────────────────────────────
    @staticmethod
    def _parse_dt(s: str) -> datetime:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
            try:
                return datetime.strptime(s.strip(), fmt)
            except Exception:
                pass
        return datetime.utcnow()


# Module-level client cache
_clients: dict[int, PanelClient] = {}

def get_client(panel) -> PanelClient:
    if panel.id not in _clients:
        _clients[panel.id] = PanelClient(panel)
    return _clients[panel.id]

def evict_client(panel_id: int):
    _clients.pop(panel_id, None)
