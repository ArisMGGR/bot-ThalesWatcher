import sys
import requests
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

from General_Items.connection_const import HEADERS,CAS_ENTRY,ECLASS_USER,ECLASS_PASS,ROOT

# Always in the project folder (where .gitignore and ownership.give_back expect
# it), whatever directory the bot was started from.
DEBUG_FILE = ROOT / "cas_debug.html"


def build_session():
    """A requests Session that rides out transient network hiccups.

    The watcher runs on a schedule against a university server that
    occasionally has brief DNS/connection failures. Instead of crashing the
    whole run on a momentary blip, retry a few times with exponential
    backoff. `connect` retries also cover DNS resolution failures, since
    those surface while establishing the connection.
    """
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=4,
        connect=4,             # covers DNS resolution / connection failures
        read=4,
        backoff_factor=1,      # waits 0s, 2s, 4s, 8s between attempts
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=None,  # retry POSTs too (CAS login, DataTables fetch)
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def login():
    s = build_session()

    # Hit the eClass CAS entry point
    # Following redirects lands us on the SSO login page (with the service param).
    page = s.get(CAS_ENTRY, timeout=30, allow_redirects=True)
    cas_login_url = page.url
    soup = BeautifulSoup(page.text, "html.parser")

    form = soup.find("form")
    if form is None:
        DEBUG_FILE.write_text(page.text, encoding="utf-8")
        sys.exit(f"No CAS login form found - see {DEBUG_FILE} (2FA? different page?).")

    # Grab EVERY hidden field (execution token, csrf, etc.), then add creds.
    # Grabbing them generically means it works whether CAS uses 'execution',
    # 'lt', '_csrf', or something else.
    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            data[name] = inp.get("value", "")
    data["username"] = ECLASS_USER
    data["password"] = ECLASS_PASS
    data.setdefault("_eventId", "submit")

    # POST back to the form's action
    action = urljoin(cas_login_url, form.get("action") or cas_login_url)
    resp = s.post(action, data=data, timeout=30, allow_redirects=True)

    # On success CAS redirected us back through cas.php?ticket=... and we now hold an eClass session cookie. If we're still on SSO, it failed.
    if still_on_sso(resp):
        DEBUG_FILE.write_text(resp.text, encoding="utf-8")
        sys.exit(f"CAS login failed — check credentials / 2FA (see {DEBUG_FILE}).")
    return s


def still_on_sso(resp):
    # If we're still on the SSO host, or a password field is present, login failed.
    if "sso.unipi.gr" in resp.url:
        return True
    return bool(BeautifulSoup(resp.text, "html.parser").find("input", {"name": "password"}))
