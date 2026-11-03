# SPDX-License-Identifier: GPL-3.0-or-later
"""Canon PIXMA "Remote UI" client — an SDK for the printer's undocumented admin HTTP API.

Usable three ways, all from this one file:
  * Ansible:  `from ansible_collections.richard_lynch.home.plugins.module_utils.canon_rui import ...`
              (plugins/modules/canon_printer_settings.py, roles/canon_printer)
  * Python:   `from canon_rui import CanonRuiClient` with this directory on sys.path — for
              ad-hoc scripts, exporters and other glue.
  * CLI:      `python canon_rui.py --host printer.example.test read`

WHY A CLIENT OF ITS OWN. There is no Canon SDK and no published API: the Remote UI is a single-page
app whose only contract is its JavaScript. Everything below is derived from firmware 4.010 (TS8700
series) — the JS served at /rui/JS/impl.js and /rui/JS/jobkick.js — and checked against a real
device. See roles/canon_printer/README.md for the derivation and the endpoint catalogue.

THE PROTOCOL, as measured:
  login     POST /rui/sendpw.cgi  NAMAE=<sha256 hex of password>&IDTYPE=2 (admin)
            -> sets cookie SCID (path /rui) and returns a self-posting form carrying SBID, the CSRF
               token. SBID is then appended to every request. The hash is UNSALTED, so it is
               password-equivalent: treat it exactly like the password (never log it).
  read      POST /rui/<page>.cgi  GETINFO=0&SBID=...   -> XML; values are leaf elements
  write     POST /rui/<page>.cgi  SETINFO=0&F1=..&SBID=...  -> XML <RESULT>:
              RW_OK       accepted
              RW_NG       rejected (validation failed on the device)
              RW_POLLING  accepted, in progress: poll get_job_status.cgi <getJobState>
                          1=in progress, 2=done, 5=error
              LOCKED      the session lost admin rights; log in again
            A write sends EVERY field of its page, as the UI does — never a partial page. This
            client therefore always reads, merges, then writes the whole group.
  expiry    any reply containing <SES_ERR_URL> means the session is gone.
  404       the JS is shared across Canon models; endpoints for features a model lacks return
            404 (on the TS8700: power_schedule.cgi, power_mng_mode.cgi, chk_secp_onoff.cgi).
            That is UnsupportedError, a normal answer, not a failure.

Pure logic (hashing, parsing, validation, payload building, diffing) is kept in plain functions so
it is unit-testable without a printer (tests/unit/test_canon_rui.py). Only CanonRuiClient touches
the network. Requires `requests` on the controller.

XML PARSING uses the stdlib on purpose: on Python >= 3.7.1 expat never fetches external entities
(no XXE), and expat >= 2.4.1 (bundled with every supported Python) caps entity amplification
(billion laughs). defusedxml would add a dependency to defend against what the parser already
refuses, for replies from a device on the local network.
"""

from __future__ import annotations

import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import quote

__all__ = [
    "CHMP_POWER_OFF",
    "CLEANING_PATTERNS",
    "GROUPS",
    "MAINTENANCE",
    "AuthError",
    "CanonRuiClient",
    "CanonRuiError",
    "MaintenanceJob",
    "RejectedError",
    "SettingGroup",
    "UnsupportedError",
    "ValidationError",
    "accepted_password",
    "build_payload",
    "fetch_ca",
    "job_poll",
    "job_state",
    "maintenance_body",
    "parse_response",
    "parse_status_model_js",
    "password_hash",
    "plan_write",
    "power_off",
    "validate",
    "validate_admin_password",
]


class CanonRuiError(Exception):
    """The printer answered in a way the caller must handle."""


class AuthError(CanonRuiError):
    """Login failed, or the session was lost (SES_ERR_URL / LOCKED)."""


class UnsupportedError(CanonRuiError):
    """This model does not implement the endpoint (HTTP 404). A normal answer, not a fault."""


class RejectedError(CanonRuiError):
    """The device refused a write (RW_NG) or a polled job ended in error."""


class ValidationError(CanonRuiError):
    """A desired value would be refused by the device; caught before sending it."""


ON_OFF = ("ON", "OFF")

# Device-side validation, transcribed from the UI's own canSet() checks so a bad value fails HERE
# with a reason rather than as an opaque RW_NG.
_PRINTER_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{0,13}[0-9A-Za-z]$")  # 2-15, no edge '-'
_BONJOUR_NAME_RE = re.compile(
    r"^[\x21-\x7E][\x20-\x7E]{0,47}$"
)  # printable ASCII, no leading space


@dataclass(frozen=True)
class SettingGroup:
    """One Remote UI page: what it reads, what it writes, and what each field may hold.

    `fields` maps a WRITABLE field to its allowed values (a tuple), a regex, or None (free text).
    `read_only` groups are reported but never written by this client — either because a bad write
    could take the printer off the network (ipv4) or because the write path is not yet verified.
    `disruptive` marks writes that make the printer re-register on the network (expect a brief
    disappearance and an mDNS re-announce).
    """

    name: str
    cgi: str
    description: str
    fields: dict = field(default_factory=dict)
    read_only: bool = False
    disruptive: bool = False


GROUPS = {
    g.name: g
    for g in (
        SettingGroup(
            "wifi_power_save",
            "lan_intermittent_reception.cgi",
            "Wi-Fi DRX / 'intermittent reception': the radio sleeps between beacons. Saves power, "
            "and can miss or delay multicast (mDNS/Bonjour).",
            {"WIRELESS_INTERMITTENT": ON_OFF, "WIRED_INTERMITTENT": ON_OFF},
        ),
        SettingGroup(
            "ipv6",
            "lan_ipv6_data.cgi",
            "IPv4-only or dual-stack. Dual-stack doubles the Bonjour address records.",
            {"CONNECT_TYPE": ("IPV4V6_AUTO", "IPV4_MODE")},
            disruptive=True,
        ),
        SettingGroup(
            "printer_name",
            "lan_set_printer_name.cgi",
            "The device (host) name — also its mDNS hostname <name>.local.",
            {"PRINTER_NAME": _PRINTER_NAME_RE},
            disruptive=True,
        ),
        SettingGroup(
            "bonjour",
            "lan_bonjour_data.cgi",
            "Bonjour on/off and the advertised service name (what AirPrint clients list).",
            {"BON_MODE": ON_OFF, "BONNAME": _BONJOUR_NAME_RE},
            disruptive=True,
        ),
        SettingGroup(
            "print_protocols",
            "lan_print_protocol_data.cgi",
            "LPR (515), RAW (9100) and LLMNR.",
            {"LPR_MODE": ON_OFF, "LLMNR_MODE": ON_OFF, "RAW_MODE": ON_OFF},
        ),
        SettingGroup(
            "wsd",
            "lan_wsd_data.cgi",
            "Windows WS-Discovery printing/scanning.",
            {
                "WSD_MODE": ON_OFF,
                "WSD_OPTIMIZE": ON_OFF,
                "WSD_SCAN_MODE": ON_OFF,
                "WSD_TIMEOUT": re.compile(r"^[0-9]{1,3}$"),
            },
        ),
        SettingGroup(
            "ipp",
            "lan_ipp_data.cgi",
            "IPP printing (AirPrint needs it).",
            {"IPP_MODE": ON_OFF},
        ),
        SettingGroup(
            "ipv4",
            "lan_ipv4_data.cgi",
            "Address mode, address, DNS, proxy. READ ONLY here: addressing belongs to the DHCP "
            "server's reservation, and a wrong write strands the printer.",
            read_only=True,
        ),
        SettingGroup(
            "power",
            "ut_data.cgi",
            "Auto power off/on (POWER_SAVE_TIME 0 = never) and quiet mode. Read only until the "
            "write path is verified.",
            read_only=True,
        ),
        SettingGroup(
            "firmware",
            "firm_version.cgi",
            "Installed and latest firmware.",
            read_only=True,
        ),
        SettingGroup(
            "firmware_auto_update",
            "get_firm_auto_update_info.cgi",
            "Whether the printer updates its own firmware.",
            read_only=True,
        ),
        SettingGroup(
            "airprint",
            "app_data.cgi",
            "AirPrint page: Bonjour name, location, geo-location, firmware.",
            read_only=True,
        ),
        SettingGroup(
            "capabilities",
            "chk_func_onoff.cgi",
            "Which interfaces/features this MODEL has (WFD=ON means Wireless Direct exists, not that "
            "it is enabled).",
            read_only=True,
        ),
    )
}

# Envelope elements every reply carries: navigation chrome and status, not settings.
_ENVELOPE = {"RESULT", "ERR_DISP_STATE", "WARNING_ID", "BST_STATE"}
_ENVELOPE_CONTAINERS = ("HEADER_2PAIN", "MENU_2PAIN")
# Leaf elements whose values are secrets the device will happily echo back.
_SECRET_RE = re.compile(r"PASS|PASSWORD|PSK|KEY|_PW$", re.I)


def password_hash(password):
    """The login proof: lowercase hex SHA-256 of the password, exactly as the UI's CryptoJS does."""
    if not password:
        raise AuthError("password is required")
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def parse_response(xml_text):
    """Parse a Remote UI XML reply into (result, values).

    `values` holds every leaf element outside the header/menu chrome, with secret-looking fields
    replaced by '<redacted>' — the device returns e.g. IPP_PASSWORD and PRX_USER_PASS in reads, and
    a value that never leaves this function cannot reach a log.
    """
    if "<SES_ERR_URL>" in xml_text:
        raise AuthError("session expired or not logged in (SES_ERR_URL)")
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        raise CanonRuiError("reply is not XML (wrong endpoint, or an HTML login page?): %s" % exc)
    for name in _ENVELOPE_CONTAINERS:
        for el in root.iter(name):
            el.clear()
    result = (root.findtext("RESULT") or "").strip()
    values = {}
    for el in root.iter():
        if len(el) or el.tag in _ENVELOPE or el.tag in _ENVELOPE_CONTAINERS or el is root:
            continue
        text = (el.text or "").strip()
        values[el.tag] = "<redacted>" if (_SECRET_RE.search(el.tag) and text) else text
    return result, values


_ADMIN_PASSWORD_RE = re.compile(r"^[0-9A-Za-z]{4,32}$")


def validate_admin_password(password):
    """The device's rule (rui/JS/utility.js isInvalidUserPassword): 4-32 chars of [0-9A-Za-z].

    Models with `g_is_supported_extended_password` accept a few more characters; the TS8700 does
    not, so anything else would be refused — or worse, accepted by one code path and unusable at
    the login screen.
    """
    if not _ADMIN_PASSWORD_RE.match(password or ""):
        raise ValidationError("admin password must be 4-32 characters of [0-9A-Za-z]")


def validate(group, desired):
    """Raise ValidationError unless every desired field is writable and holds an allowed value."""
    if group.read_only:
        raise ValidationError("group '%s' is read-only in this client" % group.name)
    for key, value in desired.items():
        if key not in group.fields:
            raise ValidationError(
                "%s: '%s' is not a writable field (writable: %s)"
                % (group.name, key, ", ".join(sorted(group.fields)))
            )
        rule = group.fields[key]
        value = str(value)
        if isinstance(rule, tuple) and value not in rule:
            raise ValidationError("%s.%s=%r: must be one of %s" % (group.name, key, value, rule))
        if hasattr(rule, "match") and not rule.match(value):
            raise ValidationError(
                "%s.%s=%r: does not match %s" % (group.name, key, value, rule.pattern)
            )


def plan_write(group, current, desired):
    """Return (changes, merged): which fields differ, and the full page to send.

    The device wants the WHOLE page on every write. Which fields make up "the page" depends on the
    MODEL: the UI sends a field only if the device has that interface (WIRED_INTERMITTENT exists in
    the JS, but a Wi-Fi-only TS8700 never reports it and the UI never sends it). So the page is
    every writable field the device REPORTED, overlaid with the desired values. Asking for a field
    the device did not report is an error — this model lacks it — never a guess.
    """
    validate(group, desired)
    desired = {k: str(v) for k, v in desired.items()}
    absent = sorted(k for k in desired if k not in current)
    if absent:
        raise UnsupportedError(
            "%s: this model does not report %s (it lacks that interface/feature)"
            % (group.name, ", ".join(absent))
        )
    merged = {f: desired.get(f, current[f]) for f in group.fields if f in current}
    changes = {
        k: {"before": current.get(k), "after": v} for k, v in desired.items() if current.get(k) != v
    }
    return changes, merged


def build_payload(merged, sbid):
    """Form-encode a write exactly as the UI does: SETINFO=0 first, values percent-encoded, SBID last."""
    parts = ["SETINFO=0"] + ["%s=%s" % (k, quote(str(v), safe="")) for k, v in merged.items()]
    parts.append("SBID=%s" % sbid)
    return "&".join(parts)


def job_state(xml_text):
    """The <getJobState> of a get_job_status.cgi reply: '1' running, '2' done, '5' failed.

    Raises AuthError on a session error (the reply then has no job state at all, and treating that
    as "still running" is how a finished job looks like a timeout). Searches at ANY depth, as the UI
    does with getElementsByTagName.
    """
    if "<SES_ERR_URL>" in xml_text or "RW_SES_NG" in xml_text:
        raise AuthError("job poll rejected: session/SBID mismatch (RW_SES_NG)")
    root = ET.fromstring(xml_text.encode("utf-8"))
    el = next(root.iter("getJobState"), None)
    return (el.text or "").strip() if el is not None else ""


@dataclass(frozen=True)
class MaintenanceJob:
    """One maintenance operation the Remote UI's Maintenance menu runs as a "job kick".

    Derived from firmware 4.010's JS (rui/JS/impl.js nozzle_chk / nor_cleaning /
    deep_cleaning, rui/JS/jobkick.js Jobkick_Closure): POST /rui/<cgi> with the job's body plus
    SBID; RW_OK or RW_POLLING (with <JOB_NUM>) means it started, and get_job_status.cgi is then
    polled with the SAME body plus JOB_NUM. Unlike a settings write, a job is an action, not a
    state: running it twice does it twice, and it uses ink and/or paper.
    """

    name: str
    cgi: str
    description: str
    patterns: bool = False  # takes CLN_PATTERN (which ink group to clean)
    timeout: int = 600  # seconds; a deep cleaning runs for minutes


# The UI's g_util_cleaning offers these three for CLN_PATTERN (impl.js: ALL, PAT1, PAT2). Which ink
# group PAT1/PAT2 are is the model's, so the role only defaults to ALL.
CLEANING_PATTERNS = ("ALL", "PAT1", "PAT2")

MAINTENANCE = {
    j.name: j
    for j in (
        MaintenanceJob(
            "nozzle_check",
            "ut_nozzle_check.cgi",
            "Prints the nozzle check pattern: one sheet of A4/Letter plain paper, a little ink. "
            "The cheap way to see whether cleaning is needed at all.",
            timeout=300,
        ),
        MaintenanceJob(
            "cleaning",
            "ut_normal_cln.cgi",
            "Print head cleaning. Uses ink (and fills the maintenance cartridge); run it when a "
            "nozzle check shows gaps, not on a schedule.",
            patterns=True,
        ),
        MaintenanceJob(
            "deep_cleaning",
            "ut_deep_cln.cgi",
            "Deep print head cleaning: several times the ink of a cleaning. Canon: only after a "
            "cleaning has not cleared the nozzle check, then leave the printer ON for 24h.",
            patterns=True,
        ),
    )
}


def maintenance_body(job, pattern=None):
    """The form body of a job kick, before SBID. Pattern-less kicks send an empty body, as the
    UI's nor_cleaning() does; a pattern is sent as the UI's g_util_cleaning does."""
    if pattern is None:
        return ""
    if not job.patterns:
        raise ValidationError("%s takes no cleaning pattern" % job.name)
    if pattern not in CLEANING_PATTERNS:
        raise ValidationError(
            "cleaning pattern %r: must be one of %s" % (pattern, CLEANING_PATTERNS)
        )
    return "SETINFO=0&CLN_PATTERN=%s" % pattern


# get_job_status.cgi <getJobState>, from jobkick.js replyJob().
JOB_STATES = {
    "1": "in_progress",
    "2": "done",
    "3": "error",
    "4": "cancelled",
    "5": "waiting_on_error",  # e.g. no paper: the job is held until someone fixes it at the printer
}


def job_poll(xml_text):
    """(state, warning) of a job poll: state from JOB_STATES ('' if absent), warning the device's
    <WARNING_ID> (or <SERVICE>) when it is not NO_ERR/OK — the reason a job is held or failed."""
    code = job_state(xml_text)
    root = ET.fromstring(xml_text.encode("utf-8"))
    warning = (root.findtext(".//WARNING_ID") or "").strip()
    service = (root.findtext(".//SERVICE") or "").strip()
    if warning in ("", "NO_ERR"):
        warning = "" if service in ("", "OK") else service
    return JOB_STATES.get(code, code), warning


# CHMP ("Canon Hardware Management Protocol", what Canon's IJ Printer Assistant Tool speaks) at
# http://<printer>/canon/ij/command2/port1. Body verbatim from that tool's Send_PowerOff, as
# captured by github.com/gapipro/canon-pixma-printers-mac-assistant (CHMPXML.swift). NOTE: it is
# plain HTTP and UNAUTHENTICATED — the printer accepts it from anyone on its network.
CHMP_PATH = "/canon/ij/command2/port1"
CHMP_POWER_OFF = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<cmd xmlns:ivec="http://www.canon.com/ns/cmd/2008/07/common/"><ivec:contents>'
    "<ivec:operation>PowerOff</ivec:operation>"
    '<ivec:param_set servicetype="device"></ivec:param_set>'
    "</ivec:contents></cmd>"
)


def power_off(host, timeout=10, confirm_within=90):
    """Soft power-off: the same shutdown as the ON button, which caps (parks) the print head.

    The printer usually drops the connection while it shuts down, so the reply is not the result.
    The result is the read-back: the device must STOP answering /JS_MDL/model.js within
    `confirm_within` seconds. Returns {'sent': reply summary, 'off_after_s': seconds}; raises
    CanonRuiError if it is still answering.
    """
    import requests

    # Without this, an already-off (or unreachable) printer would drop the POST and then "confirm"
    # the shutdown it never received. Unreachable is not proof of off, so it is an error here.
    try:
        requests.get("http://%s/JS_MDL/model.js" % host, timeout=timeout).raise_for_status()
    except requests.RequestException as exc:
        raise CanonRuiError(
            "%s is not answering before the PowerOff (%s): already off, or unreachable — nothing sent"
            % (host, type(exc).__name__)
        )
    try:
        r = requests.post(
            "http://%s%s" % (host, CHMP_PATH),
            data=CHMP_POWER_OFF.encode("utf-8"),
            headers={
                "Content-Type": "application/octet-stream",
                "X-CHMP-Version": "1.4.0",
                "Connection": "close",
            },
            timeout=timeout,
        )
        if r.status_code == 404:
            raise UnsupportedError(
                "%s answers 404 for %s: no CHMP on this model" % (host, CHMP_PATH)
            )
        if not 200 <= r.status_code < 300:
            raise RejectedError("PowerOff refused: HTTP %s" % r.status_code)
        sent = "HTTP %s" % r.status_code
    except requests.RequestException as exc:
        sent = "connection dropped (%s): the expected shape of a shutdown" % type(exc).__name__
    start = time.monotonic()
    while time.monotonic() - start < confirm_within:
        time.sleep(3)
        try:
            requests.get("http://%s/JS_MDL/model.js" % host, timeout=3)
        except requests.RequestException:
            return {"sent": sent, "off_after_s": round(time.monotonic() - start)}
    raise CanonRuiError(
        "PowerOff was sent (%s) but %s still answers after %ss — not powered off"
        % (sent, host, confirm_within)
    )


_MODEL_VAR_RE = re.compile(r"var\s+(g_[A-Za-z0-9_]+)\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([-0-9]+))")


def parse_status_model_js(js_text):
    """Extract the unauthenticated status from /JS_MDL/model.js.

    Returns the scalar `g_*` variables (signal and link quality, error state, Wi-Fi Direct and DRX
    flags, firmware) as strings, minus anything secret-looking. Needs no login — this is the cheap
    poll for monitoring.
    """
    out = {}
    for m in _MODEL_VAR_RE.finditer(js_text):
        name = m.group(1)
        if _SECRET_RE.search(name) or name.endswith("_url"):
            continue
        out[name] = next(v for v in m.group(2, 3, 4) if v is not None)
    return out


def fetch_ca(host, timeout=20):
    """Bootstrap: the printer's own CA certificate as PEM text, fetched WITHOUT credentials.

    Served unauthenticated at http://<host>/cert_root.der. Fetching it over plain HTTP is a
    trust-on-first-use step — do it once, on a network you trust, and commit the result (it is a
    public certificate); every later session is then pinned to it.
    """
    import ssl

    import requests

    r = requests.get("http://%s/cert_root.der" % host, timeout=timeout)
    r.raise_for_status()
    return ssl.DER_cert_to_PEM_cert(r.content)


def accepted_password(host, candidates, ca_cert=None, insecure=False, timeout=20):
    """Which of `candidates` (a dict label -> password) the device accepts NOW, or None.

    For recovery after a rotation went wrong part-way: whichever value logs in is the one the
    credential store must hold. One login attempt per candidate.
    """
    for label, pw in candidates.items():
        c = CanonRuiClient(host, pw, ca_cert=ca_cert, insecure=insecure, timeout=timeout)
        try:
            c.login()
        except AuthError:
            continue
        c.logout()
        return label
    return None


class CanonRuiClient:
    """A logged-in session against one printer.

        with CanonRuiClient("printer.example.test", password) as c:
            c.read("wifi_power_save")                    # {'WIRELESS_INTERMITTENT': 'ON', ...}
            c.write("wifi_power_save", {"WIRELESS_INTERMITTENT": "OFF"}, apply=False)  # plan only

    TLS — PIN THE PRINTER'S OWN CA, DO NOT CHECK THE HOSTNAME. The server
    certificate is issued by a per-device CA (CN=CanonIJProduct<MAC>0001, valid 2022-2041, public at
    http://<printer>/cert_root.der) and its only SAN is the printer's CURRENT IP — the device re-issues
    it automatically when the address changes. So:
      * `ca_cert` (PEM path) pins that CA: only the device holding its private key can complete the
        handshake, which is what stops someone harvesting the admin hash. ONLY that CA is trusted —
        not the system bundle, since with hostname checks off a public CA would vouch for anyone.
      * the hostname check is OFF, because the name the device certifies (its IP) is exactly what
        DHCP may change, and callers address it by DNS name anyway. Pinning the CA is the stronger check.
      * FAILS CLOSED: with no `ca_cert`, every HTTPS call raises unless `insecure=True` was passed
        explicitly. First contact does not need insecure mode — `fetch_ca()` gets the CA over plain
        HTTP WITHOUT sending any credential, and the admin hash only ever travels over a pinned link.
    """

    def __init__(
        self,
        host,
        password=None,
        ca_cert=None,
        insecure=False,
        timeout=20,
        poll_interval=2.0,
        poll_limit=60,
    ):
        import requests  # imported here so the pure functions above need no third-party packages
        from requests.adapters import HTTPAdapter

        self.host = host
        self.base = "https://%s" % host
        self._password = password
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_limit = poll_limit
        self._sbid = None
        self._s = requests.Session()
        if ca_cert:

            class _PinnedCA(HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    kwargs["assert_hostname"] = False
                    super().init_poolmanager(*args, **kwargs)

            self._s.verify = ca_cert  # requests passes ONLY this file as ca_certs
            self._s.mount(self.base, _PinnedCA())
        elif insecure:
            import urllib3

            self._s.verify = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._tls_ok = bool(ca_cert) or insecure

    # -- session -------------------------------------------------------------------------------
    def __enter__(self):
        if self._password:
            self.login()
        return self

    def __exit__(self, *exc):
        self.logout()

    def _post(self, path, body, accept_xml=True):
        if not self._tls_ok:
            raise CanonRuiError(
                "refusing HTTPS to %s without a pinned CA: pass ca_cert (see fetch_ca()) or, for a "
                "deliberate one-off, insecure=True" % self.host
            )
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if accept_xml:
            headers["Accept"] = "application/xml"
        r = self._s.post(self.base + path, data=body, headers=headers, timeout=self.timeout)
        if r.status_code == 404:
            raise UnsupportedError("%s is not implemented on this model (404)" % path)
        r.raise_for_status()
        return r.text

    def login(self):
        """Authenticate as admin and capture the SBID token. Raises AuthError on a wrong password."""
        if not self._tls_ok:
            self._post("", "")  # raises the fail-closed error before any credential is built
        self._s.get(self.base + "/rui/login_manager.html", timeout=self.timeout)
        body = "NAMAE=%s&IDTYPE=2" % password_hash(self._password)
        page = self._post("/rui/sendpw.cgi", body, accept_xml=False)
        m = re.search(r'name="SBID" value="([0-9a-f]+)"', page)
        if not m or "SCID" not in self._s.cookies:
            raise AuthError("login refused — wrong admin password, or the UI is locked")
        # The UI follows the self-posting form to the admin index; do the same so the session is in
        # the state the device expects before the first GETINFO.
        index = self._post("/rui/index.html", "SBID=%s" % m.group(1), accept_xml=False)
        m2 = re.search(
            r'id="SBID"[^>]*value="([0-9a-f]+)"|value="([0-9a-f]+)"[^>]*id="SBID"',
            index,
        )
        self._sbid = (m2.group(1) or m2.group(2)) if m2 else m.group(1)

    def logout(self):
        if self._sbid:
            try:
                self._s.post(
                    self.base + "/rui/logout.cgi",
                    data="SBID=%s" % self._sbid,
                    timeout=self.timeout,
                )
            except (
                Exception
            ):  # pragma: no cover - best effort; the session also times out on its own
                pass
            self._sbid = None

    def _need_login(self):
        if not self._sbid:
            if not self._password:
                raise AuthError(
                    "this call needs an admin session; construct the client with a password"
                )
            self.login()

    # -- unauthenticated -----------------------------------------------------------------------
    def status(self):
        """Signal/link quality, error state and flags from /JS_MDL/model.js. No login needed."""
        r = self._s.get("http://%s/JS_MDL/model.js" % self.host, timeout=self.timeout)
        r.raise_for_status()
        return parse_status_model_js(r.text)

    # -- settings ------------------------------------------------------------------------------
    def read_raw(self, cgi):
        self._need_login()
        result, values = parse_response(self._post("/rui/" + cgi, "GETINFO=0&SBID=%s" % self._sbid))
        if result == "LOCKED":
            raise AuthError("session lost admin rights (LOCKED)")
        return values

    def read(self, group_name):
        """Current values of one group. Raises UnsupportedError if the model lacks it."""
        return self.read_raw(GROUPS[group_name].cgi)

    def read_all(self, names=None):
        """Every group (or `names`), as {group: values | {'_unsupported': reason}}."""
        out = {}
        for name in names or GROUPS:
            try:
                out[name] = self.read(name)
            except UnsupportedError as exc:
                out[name] = {"_unsupported": str(exc)}
        return out

    def write(self, group_name, desired, apply=False):
        """Converge one group to `desired`. Returns {'changed': bool, 'changes': {...}, 'after': {...}}.

        Generate-don't-apply: with apply=False the plan is computed against the LIVE values and
        nothing is sent. With apply=True the whole page is written, then READ BACK — the result is
        what the device reports afterwards, not what was sent, and a field that did not take is an
        error (a 'RW_OK' is the device accepting the request, not proof of the new state).
        """
        group = GROUPS[group_name]
        current = self.read(group_name)
        changes, merged = plan_write(group, current, desired)
        if not changes or not apply:
            return {
                "changed": bool(changes),
                "changes": changes,
                "after": current,
                "applied": False,
            }
        result, reply = parse_response(
            self._post("/rui/" + group.cgi, build_payload(merged, self._sbid))
        )
        if result == "RW_POLLING":
            try:
                result = self._poll_job()
            except Exception as exc:
                # A DISRUPTIVE write restarts the printer's network stack while the job runs
                # (the ipv6 write drops the connection mid-poll, and the device is back about 5 s
                # later with the new value). Losing the socket or the session
                # here is the expected shape of success, not failure — so the read-back below, which
                # re-logs in and retries for 90 s, decides. Anything else still fails loudly.
                import requests

                if not group.disruptive or not isinstance(
                    exc, (requests.RequestException, AuthError)
                ):
                    raise
                self._sbid = None
                result = "JOB_UNCONFIRMED"
        if result == "LOCKED":
            raise AuthError("write refused: session lost admin rights (LOCKED)")
        if result not in ("RW_OK", "JOB_DONE", "JOB_UNCONFIRMED"):
            raise RejectedError(
                "%s: device answered %s to the write" % (group_name, result or "<no RESULT>")
            )
        after = self._read_back(group_name, desired, settle=group.disruptive)
        return {"changed": True, "changes": changes, "after": after, "applied": True}

    def change_admin_password(self, new_password):
        """Set a new admin password, then PROVE it: the new one logs in and the old one is refused.

        Protocol (from regist_admin_pass_sett() in the firmware JS): read the security page's SCOPE,
        then POST se_setting_restriction.cgi with ADMIN_PW=SET, SCOPE, PASSWORD=sha256(new) and
        CUR_PASSWORD=sha256(current). A 200/RW_OK says the device took the request, not that the
        credential changed, so success is defined by the two logins afterwards (a rotation is done
        only when the OLD value is proven rejected).
        """
        validate_admin_password(new_password)
        if new_password == self._password:
            raise ValidationError("new admin password equals the current one")
        self._need_login()
        _result, sec = parse_response(
            self._post("/rui/se_setting_restriction.cgi", "GETINFO=0&SBID=%s" % self._sbid)
        )
        scope = sec.get("SCOPE")
        if not scope:
            raise CanonRuiError("security page did not report SCOPE; refusing to guess it")
        body = "&".join(
            [
                "SETINFO=0",
                "ADMIN_PW=SET",
                "SCOPE=%s" % quote(scope, safe=""),
                "PASSWORD=%s" % password_hash(new_password),
                "CUR_PASSWORD=%s" % password_hash(self._password),
                "SBID=%s" % self._sbid,
            ]
        )
        result, _values = parse_response(self._post("/rui/se_setting_restriction.cgi", body))
        if result == "RW_POLLING":
            result = self._poll_job()
        if result not in ("RW_OK", "JOB_DONE"):
            raise RejectedError("password change refused: %s" % (result or "<no RESULT>"))
        old = self._password
        self._sbid = None  # the old session may be invalidated by the change; never reuse it
        self._password = new_password
        ca = self._s.verify if self._s.verify is not False else None
        fresh = CanonRuiClient(self.host, new_password, ca_cert=ca, insecure=ca is None)
        fresh.login()
        fresh.logout()
        stale = CanonRuiClient(self.host, old, ca_cert=ca, insecure=ca is None)
        try:
            stale.login()
        except AuthError:
            return {"changed": True, "new_login": "ok", "old_login": "rejected"}
        stale.logout()
        raise RejectedError("new password works but the OLD one still logs in — rotation not done")

    def run_job(self, name, pattern=None):
        """Start one MAINTENANCE job and wait for the device to report it finished.

        Returns {'job', 'result', 'job_num', 'seconds'}. Raises RejectedError on RW_NG (busy, no
        paper, ink too low, cover open — the device does not say which), a job error, a cancel, or
        a job still held on an error (e.g. out of paper) at the timeout — the warning id says why.
        """
        job = MAINTENANCE[name]
        body = maintenance_body(job, pattern)
        self._need_login()
        sep = "&" if body else ""
        result, reply = parse_response(
            self._post("/rui/" + job.cgi, "%s%sSBID=%s" % (body, sep, self._sbid))
        )
        if result == "LOCKED":
            raise AuthError("%s refused: session lost admin rights (LOCKED)" % name)
        if result not in ("RW_OK", "RW_POLLING"):
            raise RejectedError(
                "%s: device answered %s to the kick (busy, no paper, low ink or an open cover)"
                % (name, result or "<no RESULT>")
            )
        job_num = reply.get("JOB_NUM", "0")
        poll = "%s%sJOB_NUM=%s&SBID=%s" % (body, sep, job_num, self._sbid)
        start = time.monotonic()
        state, warning = "", ""
        while time.monotonic() - start < job.timeout:
            time.sleep(self.poll_interval)
            state, warning = job_poll(self._post("/rui/get_job_status.cgi", poll))
            if state == "done":
                return {
                    "job": name,
                    "result": "done",
                    "job_num": job_num,
                    "seconds": round(time.monotonic() - start),
                }
            if state in ("error", "cancelled"):
                raise RejectedError(
                    "%s: job %s%s" % (name, state, " (%s)" % warning if warning else "")
                )
            if state not in ("in_progress", "waiting_on_error"):
                raise CanonRuiError("%s: unexpected job state %r" % (name, state))
        raise RejectedError(
            "%s: still %s after %ss%s — check the printer's panel"
            % (name, state, job.timeout, " (%s)" % warning if warning else "")
        )

    def _poll_job(self):
        # The poll MUST carry SBID like every other call. Without it the device answers
        # <SES_ERR_URL>ses_err_unmatch.html</SES_ERR_URL><RESULT>RW_SES_NG</RESULT>, so a poll with
        # an empty body never sees a job state and times out while the device has in fact finished.
        for _attempt in range(self.poll_limit):
            time.sleep(self.poll_interval)
            state = job_state(self._post("/rui/get_job_status.cgi", "SBID=%s" % self._sbid))
            if state == "2":
                return "JOB_DONE"
            if state == "5":
                raise RejectedError("device reported the job as failed (getJobState=5)")
        raise RejectedError(
            "job still in progress after %ss" % (self.poll_limit * self.poll_interval)
        )

    def _read_back(self, group_name, desired, settle):
        # A disruptive write re-registers the printer on the network; reading straight away can hit
        # the old state or a dead socket. Retry briefly rather than sleeping a fixed time.
        deadline = time.monotonic() + (90 if settle else 10)
        last = None
        while time.monotonic() < deadline:
            try:
                last = self.read(group_name)
                if all(last.get(k) == str(v) for k, v in desired.items()):
                    return last
            except Exception as exc:  # the device may drop the session while it re-registers
                last = {"_error": str(exc)}
                self._sbid = None
            time.sleep(3)
        raise RejectedError(
            "%s: device accepted the write but reports %s, not %s" % (group_name, last, desired)
        )


def _cli(argv=None):  # pragma: no cover - thin argparse shell over the client
    """`canon_rui --host H [--password-env VAR] {status|groups|read [g..]|set g K=V.. [--apply]}`."""
    import argparse
    import json
    import os
    import sys

    def emit(text, end="\n"):
        # The CLI's only output channel. sys.stdout rather than print(): this file is also
        # module_utils, where a stray print would corrupt a module's JSON reply.
        sys.stdout.write(text + end)

    p = argparse.ArgumentParser(prog="canon_rui", description="Canon PIXMA Remote UI client")
    p.add_argument("--host", required=True)
    p.add_argument(
        "--password-env",
        default="CANON_RUI_PASSWORD",
        help="environment variable holding the admin password (never pass it as an argument)",
    )
    p.add_argument(
        "--ca",
        default=None,
        help="pin the printer's CA (from /cert_root.der, PEM-converted)",
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (one-off only; prefer `fetch-ca` then --ca)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("fetch-ca", help="print the printer's CA as PEM (no credentials sent)")
    sub.add_parser("groups")
    r = sub.add_parser("read")
    r.add_argument("group", nargs="*")
    s = sub.add_parser("set")
    s.add_argument("group")
    s.add_argument("pairs", nargs="+", metavar="FIELD=VALUE")
    s.add_argument("--apply", action="store_true", help="actually write (default: plan only)")
    j = sub.add_parser("job", help="run a maintenance job (uses ink/paper)")
    j.add_argument("name", choices=sorted(MAINTENANCE))
    j.add_argument("--pattern", choices=CLEANING_PATTERNS)
    j.add_argument("--apply", action="store_true", help="actually run it (default: plan only)")
    o = sub.add_parser("power-off", help="soft power-off over CHMP (no credentials)")
    o.add_argument("--apply", action="store_true", help="actually send it (default: plan only)")
    a = p.parse_args(argv)

    if a.cmd == "groups":
        emit(
            json.dumps(
                {
                    n: {
                        "cgi": g.cgi,
                        "writable": sorted(g.fields),
                        "read_only": g.read_only,
                        "disruptive": g.disruptive,
                        "about": g.description,
                    }
                    for n, g in GROUPS.items()
                },
                indent=2,
            )
        )
        return
    if a.cmd == "fetch-ca":
        emit(fetch_ca(a.host), end="")
        return
    if a.cmd == "power-off":
        emit(json.dumps(power_off(a.host) if a.apply else {"plan": "would power off"}, indent=2))
        return
    if a.cmd == "job" and not a.apply:
        emit(
            json.dumps({"plan": "would run %s" % a.name, "about": MAINTENANCE[a.name].description})
        )
        return
    client = CanonRuiClient(
        a.host, os.environ.get(a.password_env), ca_cert=a.ca, insecure=a.insecure
    )
    if a.cmd == "status":
        emit(json.dumps(client.status(), indent=2))
        return
    with client:
        if a.cmd == "read":
            emit(json.dumps(client.read_all(a.group or None), indent=2))
        elif a.cmd == "job":
            emit(json.dumps(client.run_job(a.name, a.pattern), indent=2))
        else:
            desired = dict(kv.split("=", 1) for kv in a.pairs)
            emit(json.dumps(client.write(a.group, desired, apply=a.apply), indent=2))


if __name__ == "__main__":  # pragma: no cover
    _cli()
