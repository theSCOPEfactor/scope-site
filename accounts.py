"""
Scope accounts: multi-user login (email OTP), trial / subscription, KYC details, admin.

Environment variables (WSGI file ma set karvana):
  SCOPE_PASSWORD   - owner (admin) password
  SECRET_KEY       - lambo random text (OTP hash pan aa thi bane che)
  BREVO_API_KEY    - Brevo (sendinblue) API key  (email OTP mate)   ... athva
  RESEND_API_KEY   - Resend API key
  MAIL_FROM        - verified sender email (Brevo/Resend ma verify kareli)
  UPI_ID           - tamaro UPI id (payment levaa mate), UPI_NAME - naam
  OTP_DEV=1        - FAKAT test mate: OTP screen par dekhay (real use ma na rakhvu)
"""
import os, re, json, hmac, hashlib, secrets, sqlite3, math, datetime as dt
import requests
from flask import request, session, redirect, url_for, render_template, g, abort

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("SCOPE_DATA_DIR", os.path.join(BASE, "userdata"))
USERS_DB = os.path.join(DATA_DIR, "users.db")
os.makedirs(DATA_DIR, exist_ok=True)

TRIAL_DAYS = 5
MONTHS = (1, 3, 6, 12)
DEFAULT_PRICE_PER_MONTH = 5000     # fakat shuruat nu; admin page ma badlay

# aa pages login vagar pan khulla che
PUBLIC = {"login", "logout", "register", "verify", "resend_otp", "static"}
# plan puru thayo hoy tyare pan aa pages chalse (profile, payment, help)
EXPIRED_OK = {"profile", "pay", "help_page", "logout", "support", "support_new"}


# ------------------------------------------------------------ helpers
def now_ist():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)


def conn():
    c = sqlite3.connect(USERS_DB)
    c.row_factory = sqlite3.Row
    return c


def init_users():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL, mobile TEXT UNIQUE NOT NULL,
      name TEXT,
      plan TEXT DEFAULT 'none',            -- none / trial / paid
      trial_used INTEGER DEFAULT 0,
      expires TEXT,                        -- IST, 'YYYY-MM-DD HH:MM'
      email_verified INTEGER DEFAULT 0,
      status TEXT DEFAULT 'active',        -- active / blocked
      note TEXT DEFAULT '',
      sess TEXT, last_login TEXT, created TEXT
    );
    CREATE TABLE IF NOT EXISTS tickets(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, subject TEXT, message TEXT,
      reply TEXT, status TEXT DEFAULT 'open',       -- open / answered / closed
      created TEXT, replied TEXT
    );
    CREATE TABLE IF NOT EXISTS plans(
      months INTEGER PRIMARY KEY, price INTEGER NOT NULL, active INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS otps(
      id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, purpose TEXT,
      code_hash TEXT, exp REAL, attempts INTEGER DEFAULT 0, created REAL
    );
    CREATE TABLE IF NOT EXISTS payments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, months INTEGER, amount INTEGER,
      utr TEXT UNIQUE, status TEXT DEFAULT 'pending', created TEXT, decided TEXT
    );
    """)
    for m in MONTHS:
        c.execute("INSERT OR IGNORE INTO plans(months,price,active) VALUES(?,?,1)", (m, m * DEFAULT_PRICE_PER_MONTH))
    c.commit(); c.close()


init_users()


def get_plans(only_active=True):
    c = conn()
    r = c.execute("SELECT * FROM plans ORDER BY months").fetchall()
    c.close()
    return [p for p in r if p["active"]] if only_active else r


def norm_email(e):
    return (e or "").strip().lower()


def norm_mobile(m):
    d = re.sub(r"\D", "", m or "")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d


def parse_dt(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def fmt_dt(s):
    d = parse_dt(s) if isinstance(s, str) else s
    return d.strftime("%d %b %Y") if d else "-"


def days_left(u):
    """None = koi plan nathi; 0 = puru; n = baki divas"""
    exp = parse_dt(u["expires"]) if u["expires"] else None
    if not exp:
        return None
    sec = (exp - now_ist()).total_seconds()
    return 0 if sec <= 0 else int(math.ceil(sec / 86400))


def plan_name(u):
    return "Trial" if u["plan"] == "trial" else "Subscription"


def status_msg(u):
    n = days_left(u)
    if n is None:
        return "No active plan. Please choose a plan to continue."
    if n == 0:
        return "Your %s ended on %s. Please renew to continue." % (plan_name(u).lower(), fmt_dt(u["expires"]))
    return "%s: %d day%s left (valid till %s)." % (plan_name(u), n, "" if n == 1 else "s", fmt_dt(u["expires"]))


def is_active(u):
    n = days_left(u)
    return bool(n) and u["status"] == "active"


# ------------------------------------------------------------ OTP + email
def _secret():
    return os.environ.get("SECRET_KEY", "change-me-please").encode()


def _hash(email, purpose, code):
    return hmac.new(_secret(), ("%s|%s|%s" % (email, purpose, code)).encode(), hashlib.sha256).hexdigest()


def send_mail(to, subject, html):
    """Brevo ke Resend (HTTPS API - PythonAnywhere free par pan chale). Return (ok, error)."""
    frm = os.environ.get("MAIL_FROM", "")
    try:
        if os.environ.get("BREVO_API_KEY"):
            r = requests.post("https://api.brevo.com/v3/smtp/email", timeout=20,
                              headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json"},
                              json={"sender": {"name": "Scope", "email": frm}, "to": [{"email": to}],
                                    "subject": subject, "htmlContent": html})
            return r.status_code < 300, "" if r.status_code < 300 else "mail error %s" % r.status_code
        if os.environ.get("RESEND_API_KEY"):
            r = requests.post("https://api.resend.com/emails", timeout=20,
                              headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"]},
                              json={"from": "Scope <%s>" % frm, "to": [to], "subject": subject, "html": html})
            return r.status_code < 300, "" if r.status_code < 300 else "mail error %s" % r.status_code
    except Exception as e:
        return False, "mail error"
    return False, "Email is not set up on the server yet"


def send_otp(email, purpose):
    """Return (ok, message, dev_code)"""
    c = conn()
    t = now_ist().timestamp()
    recent = c.execute("SELECT created FROM otps WHERE email=? AND created>? ORDER BY created DESC",
                       (email, t - 3600)).fetchall()
    if recent and t - recent[0]["created"] < 45:
        c.close(); return False, "Please wait a few seconds before asking for a new OTP.", None
    if len(recent) >= 5:
        c.close(); return False, "Too many OTP requests. Try again after an hour.", None
    code = "%06d" % secrets.randbelow(1000000)
    c.execute("DELETE FROM otps WHERE email=? AND purpose=?", (email, purpose))
    c.execute("INSERT INTO otps(email,purpose,code_hash,exp,created) VALUES(?,?,?,?,?)",
              (email, purpose, _hash(email, purpose, code), t + 600, t))
    c.commit(); c.close()
    if os.environ.get("OTP_DEV") == "1" and not (os.environ.get("BREVO_API_KEY") or os.environ.get("RESEND_API_KEY")):
        print("DEV OTP", email, code)
        return True, "Test mode: OTP shown below.", code
    ok, err = send_mail(email, "Your Scope OTP: " + code,
                        "<p>Your Scope one-time password is <b style='font-size:20px'>%s</b></p>"
                        "<p>It is valid for 10 minutes. Do not share it with anyone.</p>" % code)
    return ok, ("OTP sent to your email." if ok else err), None


def check_otp(email, purpose, code):
    c = conn()
    r = c.execute("SELECT * FROM otps WHERE email=? AND purpose=?", (email, purpose)).fetchone()
    if not r or r["exp"] < now_ist().timestamp() or r["attempts"] >= 5:
        c.close(); return False
    c.execute("UPDATE otps SET attempts=attempts+1 WHERE id=?", (r["id"],)); c.commit()
    ok = hmac.compare_digest(r["code_hash"], _hash(email, purpose, (code or "").strip()))
    if ok:
        c.execute("DELETE FROM otps WHERE id=?", (r["id"],)); c.commit()
    c.close()
    return ok


# ------------------------------------------------------------ session / guard
def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _csrf_ok():
    return hmac.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "x"))


def load(password_set):
    """before_request ma: g.admin, g.user set kare"""
    g.admin = False
    g.user = None
    g.kicked = False
    if not password_set:
        g.admin = True            # local test: koi login nahi
        return
    if session.get("admin"):
        g.admin = True
        return
    uid = session.get("uid")
    if uid:
        c = conn()
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        c.close()
        if u and u["status"] == "active" and u["email_verified"] and u["sess"] and \
                hmac.compare_digest(u["sess"], session.get("tok", "")):
            g.user = u
        else:
            g.kicked = bool(u and u["sess"] and u["status"] == "active")
            session.pop("uid", None); session.pop("tok", None)


def guard(password_set):
    ep = request.endpoint or ""
    if ep == "static":
        return None
    load(password_set)
    if request.method == "POST" and ep in FORM_EPS and not _csrf_ok():
        abort(400)
    if ep in PUBLIC or g.admin:
        return None
    if not g.user:
        return redirect(url_for("login", kicked=1) if g.kicked else url_for("login"))
    if ep.startswith("admin"):
        abort(403)
    if not is_active(g.user) and ep not in EXPIRED_OK:
        return redirect(url_for("profile"))
    return None


def user_db_path(uid):
    return os.path.join(DATA_DIR, "u_%d.db" % uid)


def template_ctx():
    u = g.get("user")
    d = {"csrf": csrf, "is_admin": bool(g.get("admin")) and bool(os.environ.get("SCOPE_PASSWORD")),
         "me": None, "acct_msg": "", "acct_left": None, "acct_short": "", "open_tickets": 0, "wa": os.environ.get("WHATSAPP_NUMBER", "")}
    if g.get("admin") and os.environ.get("SCOPE_PASSWORD"):
        try:
            c = conn()
            d["open_tickets"] = c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
            c.close()
        except Exception:
            pass
    if u:
        d["me"] = u
        d["acct_msg"] = status_msg(u)
        d["acct_left"] = days_left(u)
        n = d["acct_left"]
        d["acct_short"] = "No plan" if n is None else ("Plan ended" if n == 0 else "%s · %dd left" % (plan_name(u), n))
    return d


# ------------------------------------------------------------ routes
def _login_user(u):
    tok = secrets.token_hex(24)               # navo login => junu session aapo-aap bandh
    c = conn()
    c.execute("UPDATE users SET sess=?, last_login=? WHERE id=?",
              (tok, now_ist().strftime("%Y-%m-%d %H:%M"), u["id"]))
    c.commit(); c.close()
    session.pop("admin", None)
    session["uid"] = u["id"]; session["tok"] = tok
    session.permanent = True


def route_login():
    err = info = dev = ""
    owner = request.args.get("owner") == "1" or request.form.get("owner") == "1"
    if request.method == "POST":
        if owner:
            pw = os.environ.get("SCOPE_PASSWORD", "")
            if pw and hmac.compare_digest(request.form.get("password", ""), pw):
                session.clear(); session["admin"] = True; session.permanent = True
                return redirect(url_for("home"))
            err = "Wrong password"
        else:
            ident = (request.form.get("ident") or "").strip()
            em = norm_email(ident) if "@" in ident else None
            mo = norm_mobile(ident) if "@" not in ident else None
            c = conn()
            u = c.execute("SELECT * FROM users WHERE email_verified=1 AND (email=? OR mobile=?)",
                          (em or "-", mo or "-")).fetchone()
            c.close()
            if u and u["status"] == "blocked":
                err = "This account is blocked. Please contact the owner."
            else:
                if u:
                    ok, msg, dev = send_otp(u["email"], "login")
                    if not ok:
                        err = msg
                    else:
                        session["pend"] = {"email": u["email"], "purpose": "login"}
                        session["dev"] = dev or ""
                        return redirect(url_for("verify"))
                else:
                    err = "No account found for this email / mobile. Please register."
    note = session.pop("bye", "")
    kicked = request.args.get("kicked")
    return render_template("login.html", err=err, owner=owner, note=note, kicked=kicked)


def route_logout():
    msg = ""
    if g.get("user"):
        msg = status_msg(g.user)
        c = conn(); c.execute("UPDATE users SET sess=NULL WHERE id=?", (g.user["id"],)); c.commit(); c.close()
    session.clear()
    if msg:
        session["bye"] = msg
    return redirect(url_for("login"))


def route_register():
    err = ""
    f = request.form
    if request.method == "POST":
        name = re.sub(r"\s+", " ", f.get("name", "")).strip()
        email = norm_email(f.get("email"))
        mobile = norm_mobile(f.get("mobile"))
        want = f.get("plan", "trial")
        if len(name) < 2 or len(name) > 80: err = "Enter your name."
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): err = "Enter a valid email."
        elif not re.match(r"^[6-9]\d{9}$", mobile): err = "Enter a valid 10-digit mobile number."
        elif f.get("consent") != "1": err = "Please accept the terms."
        elif want not in ("trial", "paid"): err = "Choose a plan."
        if not err:
            c = conn()
            clash = c.execute("SELECT id,email_verified FROM users WHERE email=? OR mobile=?", (email, mobile)).fetchall()
            if any(r["email_verified"] for r in clash):
                err = "An account with this email or mobile already exists. Please log in."
            else:
                for r in clash:
                    c.execute("DELETE FROM users WHERE id=?", (r["id"],))
                c.execute("INSERT INTO users(email,mobile,name,created) VALUES(?,?,?,?)",
                          (email, mobile, name, now_ist().strftime("%Y-%m-%d %H:%M")))
                c.commit()
                ok, msg, dev = send_otp(email, "register")
                if not ok:
                    err = msg
                else:
                    session["pend"] = {"email": email, "purpose": "register", "want": want}
                    session["dev"] = dev or ""
                    c.close()
                    return redirect(url_for("verify"))
            c.close()
    return render_template("register.html", err=err, f=f, trial=TRIAL_DAYS, plans=get_plans())


def route_verify():
    p = session.get("pend")
    if not p:
        return redirect(url_for("login"))
    err = ""
    if request.method == "POST":
        if check_otp(p["email"], p["purpose"], request.form.get("otp", "")):
            c = conn()
            u = c.execute("SELECT * FROM users WHERE email=?", (p["email"],)).fetchone()
            if not u:
                c.close(); session.pop("pend", None); return redirect(url_for("register"))
            first = False
            if p["purpose"] == "register":
                first = True
                c.execute("UPDATE users SET email_verified=1 WHERE id=?", (u["id"],))
                if p.get("want") == "trial" and not u["trial_used"]:
                    exp = (now_ist() + dt.timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d %H:%M")
                    c.execute("UPDATE users SET plan='trial', trial_used=1, expires=? WHERE id=?", (exp, u["id"]))
                c.commit()
                u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            c.close()
            if u["status"] != "active":
                session.pop("pend", None)
                return render_template("login.html", err="This account is blocked.", owner=False, note="", kicked=None)
            session.pop("pend", None); session.pop("dev", None)
            _login_user(u)
            if first and not is_active(u):
                return redirect(url_for("pay"))
            return redirect(url_for("profile") if first else url_for("home"))
        err = "Wrong or expired OTP."
    masked = re.sub(r"^(.).*(@.*)$", r"\1***\2", p["email"])
    return render_template("verify.html", err=err, masked=masked, dev=session.get("dev", ""),
                           purpose=p["purpose"])


def route_resend():
    p = session.get("pend")
    if not p or request.method != "POST":
        return redirect(url_for("login"))
    ok, msg, dev = send_otp(p["email"], p["purpose"])
    session["dev"] = dev or ""
    masked = re.sub(r"^(.).*(@.*)$", r"\1***\2", p["email"])
    return render_template("verify.html", err="" if ok else msg, info=msg if ok else "", masked=masked,
                           dev=dev or "", purpose=p["purpose"])


def _payments(uid):
    c = conn()
    r = c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    c.close()
    return r


def route_profile():
    if g.get("admin"):
        return redirect(url_for("admin"))
    return render_template("profile.html", u=g.user, msg=status_msg(g.user), left=days_left(g.user),
                           plan_active=is_active(g.user), pays=_payments(g.user["id"]), fmt=fmt_dt,
                           plans=get_plans(), trial=TRIAL_DAYS,
                           upi=os.environ.get("UPI_ID", ""), upi_name=os.environ.get("UPI_NAME", "Scope"),
                           active="profile", err=session.pop("perr", ""), ok=session.pop("pok", ""))


def route_pay():
    if g.get("admin") or request.method != "POST":
        return redirect(url_for("profile"))
    try:
        months = int(request.form.get("months", "0"))
    except ValueError:
        months = 0
    utr = re.sub(r"\s+", "", request.form.get("utr", "")).upper()
    plan = {p["months"]: p for p in get_plans()}.get(months)
    if not plan:
        session["perr"] = "Choose a valid duration."
    elif not re.match(r"^[A-Z0-9]{8,30}$", utr):
        session["perr"] = "Enter the UPI transaction / UTR number (8-30 letters or digits)."
    else:
        c = conn()
        try:
            c.execute("INSERT INTO payments(user_id,months,amount,utr,created) VALUES(?,?,?,?,?)",
                      (g.user["id"], months, plan["price"], utr, now_ist().strftime("%Y-%m-%d %H:%M")))
            c.commit()
            session["pok"] = "Payment submitted. Your plan starts once the owner confirms it (usually within a few hours)."
        except sqlite3.IntegrityError:
            session["perr"] = "This transaction number was already submitted."
        c.close()
    return redirect(url_for("profile"))


# ------------------------------------------------------------ admin
def _need_admin():
    if not g.get("admin") or not os.environ.get("SCOPE_PASSWORD"):
        abort(403)


def route_admin():
    _need_admin()
    c = conn()
    users = c.execute("SELECT * FROM users WHERE email_verified=1 ORDER BY id DESC").fetchall()
    pend = c.execute("""SELECT p.*, u.name, u.email FROM payments p JOIN users u ON u.id=p.user_id
                        WHERE p.status='pending' ORDER BY p.id""").fetchall()
    c.close()
    rows = [(u, status_msg(u), days_left(u)) for u in users]
    return render_template("admin.html", rows=rows, pend=pend, active="admin", plans=get_plans(False))


def route_admin_user(uid):
    _need_admin()
    c = conn()
    u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    pays = c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    c.close()
    return render_template("admin_user.html", u=u, msg=status_msg(u), pays=pays, fmt=fmt_dt, active="admin")


def route_support():
    """user: apli queries + navi query. WHATSAPP_NUMBER set hoy to link pan dekhay."""
    if g.get("admin"):
        return redirect(url_for("admin_support"))
    c = conn()
    rows = c.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC", (g.user["id"],)).fetchall()
    c.close()
    return render_template("support.html", rows=rows, fmt=fmt_dt, active="support",
                           err=session.pop("serr", ""), ok=session.pop("sok", ""))


def route_support_new():
    if g.get("admin") or request.method != "POST":
        return redirect(url_for("support"))
    subj = re.sub(r"\s+", " ", request.form.get("subject", "")).strip()[:120]
    msg = request.form.get("message", "").strip()[:2000]
    c = conn()
    open_n = c.execute("SELECT COUNT(*) FROM tickets WHERE user_id=? AND status='open'", (g.user["id"],)).fetchone()[0]
    if len(subj) < 3 or len(msg) < 5:
        session["serr"] = "Please write a subject and your question."
    elif open_n >= 5:
        session["serr"] = "You already have 5 open questions. Please wait for a reply."
    else:
        c.execute("INSERT INTO tickets(user_id,subject,message,created) VALUES(?,?,?,?)",
                  (g.user["id"], subj, msg, now_ist().strftime("%Y-%m-%d %H:%M")))
        c.commit()
        session["sok"] = "Sent. You will see the reply here."
    c.close()
    return redirect(url_for("support"))


def route_admin_support():
    _need_admin()
    c = conn()
    rows = c.execute("""SELECT t.*, u.name, u.email, u.mobile FROM tickets t JOIN users u ON u.id=t.user_id
                        ORDER BY (t.status='open') DESC, t.id DESC LIMIT 200""").fetchall()
    c.close()
    return render_template("admin_support.html", rows=rows, fmt=fmt_dt, active="admin_support")


def route_admin_reply():
    _need_admin()
    f = request.form
    c = conn()
    try:
        tid = int(f.get("ticket_id", 0))
        if f.get("action") == "close":
            c.execute("UPDATE tickets SET status='closed' WHERE id=?", (tid,))
        else:
            reply = f.get("reply", "").strip()[:2000]
            if reply:
                c.execute("UPDATE tickets SET reply=?, status='answered', replied=? WHERE id=?",
                          (reply, now_ist().strftime("%Y-%m-%d %H:%M"), tid))
        c.commit()
    except Exception:
        c.rollback()
    c.close()
    return redirect(url_for("admin_support"))


def route_admin_act():
    """POST: action=approve|reject (pay_id), block|unblock|logout|extend|kyc (user_id)"""
    _need_admin()
    f = request.form
    a = f.get("action", "")
    c = conn()
    back = url_for("admin")
    try:
        if a in ("approve", "reject"):
            p = c.execute("SELECT * FROM payments WHERE id=? AND status='pending'", (f.get("pay_id"),)).fetchone()
            if p:
                if a == "approve":
                    u = c.execute("SELECT * FROM users WHERE id=?", (p["user_id"],)).fetchone()
                    cur = parse_dt(u["expires"]) if u["expires"] else None
                    start = cur if cur and cur > now_ist() else now_ist()
                    exp = (start + dt.timedelta(days=30 * p["months"])).strftime("%Y-%m-%d %H:%M")
                    c.execute("UPDATE users SET plan='paid', expires=? WHERE id=?", (exp, u["id"]))
                c.execute("UPDATE payments SET status=?, decided=? WHERE id=?",
                          ("approved" if a == "approve" else "rejected", now_ist().strftime("%Y-%m-%d %H:%M"), p["id"]))
        elif a == "plans":
            for m in MONTHS:
                price = max(0, min(10000000, int(float(f.get("price_%d" % m, "0") or 0))))
                c.execute("UPDATE plans SET price=?, active=? WHERE months=?", (price, 1 if f.get("on_%d" % m) else 0, m))
        else:
            uid = int(f.get("user_id", 0))
            back = url_for("admin_user", uid=uid)
            if a == "block":
                c.execute("UPDATE users SET status='blocked', sess=NULL WHERE id=?", (uid,))
            elif a == "unblock":
                c.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
            elif a == "logout":
                c.execute("UPDATE users SET sess=NULL WHERE id=?", (uid,))
            elif a == "extend":
                days = max(-365, min(365, int(f.get("days", "0"))))
                u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                cur = parse_dt(u["expires"]) if u["expires"] else None
                start = cur if cur and cur > now_ist() else now_ist()
                exp = (start + dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
                c.execute("UPDATE users SET expires=?, plan=CASE WHEN plan='none' THEN 'paid' ELSE plan END WHERE id=?",
                          (exp, uid))
            elif a == "note":
                c.execute("UPDATE users SET note=? WHERE id=?", (f.get("note", "")[:200], uid))
        c.commit()
    except Exception:
        c.rollback()
    c.close()
    return redirect(back)


FORM_EPS = {"login", "register", "verify", "resend_otp", "pay", "admin_act", "support_new", "admin_reply"}


def init_app(app):
    app.config.update(MAX_CONTENT_LENGTH=14 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax")
    R = app.add_url_rule
    R("/login", "login", route_login, methods=["GET", "POST"])
    R("/logout", "logout", route_logout)
    R("/register", "register", route_register, methods=["GET", "POST"])
    R("/verify", "verify", route_verify, methods=["GET", "POST"])
    R("/verify/resend", "resend_otp", route_resend, methods=["POST"])
    R("/profile", "profile", route_profile)
    R("/pay", "pay", route_pay, methods=["POST"])
    R("/support", "support", route_support)
    R("/support/new", "support_new", route_support_new, methods=["POST"])
    R("/admin/support", "admin_support", route_admin_support)
    R("/admin/reply", "admin_reply", route_admin_reply, methods=["POST"])
    R("/admin", "admin", route_admin)
    R("/admin/user/<int:uid>", "admin_user", route_admin_user)
    R("/admin/act", "admin_act", route_admin_act, methods=["POST"])
    app.context_processor(template_ctx)
