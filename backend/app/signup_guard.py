"""Bot-resistant signup with no external service: honeypot, disposable-email blocklist, per-IP and global signup caps,
optional stateless proof-of-work. Honest scope: this raises the cost of scripted signups; a patient attacker with many IPs
and real mailboxes still gets through (there is no email verification). See docs/SECURITY.md.

Env: MIRAGE_SIGNUP_POW_BITS (default 0 = off; 18-20 is ~0.3-3 s in a browser), MIRAGE_SIGNUP_PER_IP_DAY (prod 10, dev 0=off),
MIRAGE_SIGNUP_GLOBAL_HOUR (prod 300, dev 0=off), MIRAGE_BLOCKED_EMAIL_DOMAINS (extra, comma list),
MIRAGE_SIGNUP_UNIQUE_EMAIL (prod on: one account per normalized address), MIRAGE_BLOCK_DISPOSABLE_EMAIL (default on).
"""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from fastapi import HTTPException

from . import settings
from .safety import limiter

HONEYPOT_FIELD = "website"
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,24}$")

DISPOSABLE = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org guerrillamail.biz guerrillamail.de sharklasers.com grr.la
guerrillamailblock.com pokemail.net spam4.me 10minutemail.com 10minutemail.net 10minemail.com 20minutemail.com tempmail.com
temp-mail.org temp-mail.io tempmail.net tempmailo.com tempail.com tempr.email throwawaymail.com trashmail.com trashmail.net
trashmail.de trash-mail.com yopmail.com yopmail.fr yopmail.net cool.fr.nf jetable.org nospam.ze.tc nomail.xl.cx mega.zik.dj
speed.1s.fr courriel.fr.nf moncourrier.fr.nf monemail.fr.nf monmail.fr.nf getnada.com nada.email maildrop.cc mailnesia.com
mailcatch.com mintemail.com mytemp.email mohmal.com dispostable.com fakeinbox.com fakemail.net fakemailgenerator.com
getairmail.com harakirimail.com incognitomail.org instantemailaddress.com mailexpire.com mailforspam.com mailmoat.com
mailnull.com mailsac.com mailtothis.com meltmail.com mt2015.com mvrht.net my10minutemail.com noclickemail.com oneoffmail.com
owlymail.com pjjkp.com put2.net quickinbox.com rcpt.at receiveee.com rmqkr.net sendspamhere.com sogetthis.com spambog.com
spambox.us spamex.com spamfree24.org spamgourmet.com spamherelots.com spamhole.com spamify.com spaml.com spamspot.com
supergreatmail.com superrito.com suremail.info teleworm.us tempinbox.com tempomail.fr thankyou2010.com thisisnotmyrealemail.com
tmail.ws tmailinator.com tradermail.info trbvm.com twinmail.de uroid.com veryrealemail.com vomoto.com wegwerfmail.de
wegwerfmail.net wh4f.org yepmail.net yuurok.com zehnminutenmail.com zippymail.info burnermail.io emailondeck.com
33mail.com anonbox.net armyspy.com cuvox.de dayrep.com einrot.com fleckens.hu gustr.com jourrapide.com rhyta.com
dropmail.me emltmp.com mail.tm moakt.com tmpmail.org tmpmail.net 1secmail.com 1secmail.net 1secmail.org
discard.email discardmail.com discardmail.de emkei.cz inboxkitten.com linshiyouxiang.net luxusmail.org
""".split())


def normalize_email(email: str) -> str:
    """Dedupe key: lowercase; for gmail/googlemail drop dots and +tag; for everyone drop +tag."""
    e = email.strip().lower()
    local, _, dom = e.partition("@")
    local = local.split("+", 1)[0]
    if dom in ("gmail.com", "googlemail.com"):
        local, dom = local.replace(".", ""), "gmail.com"
    return f"{local}@{dom}"


def is_disposable(email: str) -> bool:
    dom = email.strip().lower().rpartition("@")[2]
    extra = {d.strip().lower() for d in os.getenv("MIRAGE_BLOCKED_EMAIL_DOMAINS", "").split(",") if d.strip()}
    blocked = DISPOSABLE | extra
    parts = dom.split(".")
    return any(".".join(parts[i:]) in blocked for i in range(len(parts) - 1))  # subdomains of a blocked domain too


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def pow_bits() -> int:
    return max(0, min(_int("MIRAGE_SIGNUP_POW_BITS", 0), 28))


# ---- proof of work (stateless challenge, single use) ----
POW_TTL = 300
_used: dict[str, float] = {}
_lock = threading.Lock()


def _mac(payload: str) -> str:
    return hmac.new(settings.secret_key(), b"signup-pow:" + payload.encode(), hashlib.sha256).hexdigest()[:32]


def new_challenge() -> dict:
    bits = pow_bits()
    payload = f"{int(time.time()) + POW_TTL}.{secrets.token_hex(8)}.{bits}"
    return {"challenge": f"{payload}.{_mac(payload)}", "bits": bits, "algorithm": "sha256(challenge + ':' + nonce) has `bits` leading zero bits"}


def _leading_zero_bits(d: bytes) -> int:
    n = 0
    for b in d:
        if b == 0:
            n += 8
            continue
        return n + (8 - b.bit_length())
    return n


def verify_pow(challenge: str, nonce: str) -> bool:
    try:
        exp, rnd, bits, mac = challenge.split(".")
        payload = f"{exp}.{rnd}.{bits}"
        if not hmac.compare_digest(mac, _mac(payload)) or int(exp) < time.time() or int(bits) < pow_bits():
            return False
        if len(nonce) > 64 or _leading_zero_bits(hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()) < int(bits):
            return False
    except (ValueError, TypeError):
        return False
    now = time.time()
    with _lock:
        for k in [k for k, v in _used.items() if v < now]:
            del _used[k]
        if challenge in _used:
            return False
        _used[challenge] = now + POW_TTL
    return True


def solve(challenge: str, bits: int) -> str:
    """Reference solver (tests, scripts). Browsers do the same loop with crypto.subtle."""
    i = 0
    while True:
        if _leading_zero_bits(hashlib.sha256(f"{challenge}:{i}".encode()).digest()) >= bits:
            return str(i)
        i += 1


# ---- the gate ----
def _reject(status: int, code: str, msg: str):
    raise HTTPException(status, {"error": code, "message": msg})


def is_honeypot(value) -> bool:
    return bool(value and str(value).strip())


def check(email: str, ip: str, pow_challenge: str = "", pow_nonce: str = "") -> None:
    """Raise HTTPException unless the signup may proceed. (Honeypot is handled by the caller so bots get a fake success.)"""
    email = (email or "").strip()
    if len(email) > 254 or not EMAIL_RE.match(email):
        _reject(422, "invalid_email", "Enter a valid email address.")
    if os.getenv("MIRAGE_BLOCK_DISPOSABLE_EMAIL", "1").strip().lower() not in ("0", "false", "no", "off") and is_disposable(email):
        _reject(422, "disposable_email", "Disposable email addresses are not accepted. Use a real address.")
    if pow_bits() and not verify_pow(pow_challenge or "", pow_nonce or ""):
        _reject(400, "pow_required", "Proof of work missing or invalid. GET /v1/signup/challenge, solve it, and resend.")
    prod = settings.is_production()
    per_ip = _int("MIRAGE_SIGNUP_PER_IP_DAY", 10 if prod else 0)
    if per_ip and not limiter.check(f"signup:day:{ip}", per_ip, 86400):
        _reject(429, "signup_limit", "Too many signups from this network today. Try again tomorrow.")
    glob = _int("MIRAGE_SIGNUP_GLOBAL_HOUR", 300 if prod else 0)
    if glob and not limiter.check("signup:global", glob, 3600):
        _reject(429, "signup_busy", "Signups are temporarily limited. Try again later.")


def unique_email_enforced() -> bool:
    v = os.getenv("MIRAGE_SIGNUP_UNIQUE_EMAIL", "")
    return settings.is_production() if v == "" else v.strip().lower() in ("1", "true", "yes", "on")
