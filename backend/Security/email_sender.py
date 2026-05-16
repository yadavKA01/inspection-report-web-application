"""
Email delivery for the authentication system.

Delivery strategy (in priority order):
  1. Resend    – if EMAIL.RESEND_API_KEY is set (or PROVIDER == "resend").
  2. SendGrid  – if EMAIL.SENDGRID_API_KEY is set.
  3. SMTP      – if EMAIL.SMTP_HOST is set.
  4. Console   – development fallback; prints the message to stdout.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config


def _cfg() -> dict:
    return config.GetConfiguration("EMAIL") or {}


def _print_console_temp_password(to_email: str, subject: str, temp_password: str) -> None:
    """Print credentials to stdout when email APIs fail (e.g. Resend sandbox / unverified domain)."""
    print("\n" + "=" * 60)
    print("[EMAIL — CONSOLE FALLBACK]  Email API did not deliver. Use this to log in:")
    print(f"[EMAIL — CONSOLE FALLBACK]  To: {to_email}")
    print(f"[EMAIL — CONSOLE FALLBACK]  Subject: {subject}")
    print(f"[EMAIL — CONSOLE FALLBACK]  Temporary password: {temp_password}")
    print("=" * 60 + "\n")


# ── public API ────────────────────────────────────────────────────────────────

def send_temp_password_email(
    to_email:      str,
    firstname:     str,
    temp_password: str,
    app_url:       str = "",
) -> bool:
    """
    Send the temporary-password email to a newly-registered user.

    Returns True after the user can obtain the password (email sent, or printed to server console).
    """
    cfg       = _cfg()
    from_email = cfg.get("FROM_EMAIL", "noreply@example.com")
    app_name  = cfg.get("APP_NAME", "Auto Ballooning")
    login_url = f"{app_url.rstrip('/')}/login" if app_url else "/login"

    subject   = f"Your {app_name} Account — Temporary Password"
    html_body = _build_html(firstname, temp_password, login_url, app_name)
    text_body = _build_text(firstname, temp_password, login_url, app_name)

    resend_key = cfg.get("RESEND_API_KEY", "").strip()
    sg_key     = cfg.get("SENDGRID_API_KEY", "").strip()
    smtp_host  = cfg.get("SMTP_HOST", "").strip()

    if resend_key:
        if _send_resend(resend_key, from_email, to_email, subject, html_body, text_body):
            return True
        _print_console_temp_password(to_email, subject, temp_password)
        print(
            "[email_sender] Resend rejected this recipient (e.g. sandbox). "
            "Verify a domain at resend.com/domains or use the password printed above. "
            "See: https://resend.com/docs"
        )
        return True

    if sg_key:
        if _send_sendgrid(sg_key, from_email, to_email, subject, html_body):
            return True
        _print_console_temp_password(to_email, subject, temp_password)
        return True

    if smtp_host:
        if _send_smtp(cfg, from_email, to_email, subject, html_body, text_body):
            return True
        _print_console_temp_password(to_email, subject, temp_password)
        return True

    # ── console fallback (no email provider configured) ───────────────────────
    _print_console_temp_password(to_email, subject, temp_password)
    print("[email_sender] No Resend/SendGrid/SMTP in config — password is only shown above.")
    return True


# ── delivery backends ─────────────────────────────────────────────────────────

def _send_resend(api_key: str, from_email: str, to_email: str, subject: str, html: str, text: str) -> bool:
    try:
        import resend
        resend.api_key = api_key
        resp = resend.Emails.send({
            "from":    from_email,
            "to":      [to_email],
            "subject": subject,
            "html":    html,
            "text":    text,
        })
        # Resend returns a dict with an "id" key on success
        if not resp.get("id"):
            print(f"[email_sender] Resend unexpected response: {resp}")
            return False
        return True
    except Exception as exc:
        print(f"[email_sender] Resend error: {exc}")
        return False


def _send_sendgrid(api_key: str, from_email: str, to_email: str, subject: str, html: str) -> bool:
    try:
        import sendgrid
        from sendgrid.helpers.mail import Content, Email, Mail, To

        sg      = sendgrid.SendGridAPIClient(api_key=api_key)
        message = Mail(
            from_email=Email(from_email),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html),
        )
        resp = sg.send(message)
        if resp.status_code not in (200, 202):
            print(f"[email_sender] SendGrid returned HTTP {resp.status_code}")
            return False
        return True
    except Exception as exc:
        print(f"[email_sender] SendGrid error: {exc}")
        return False


def _send_smtp(
    cfg: dict,
    from_email: str,
    to_email:   str,
    subject:    str,
    html:       str,
    text:       str,
) -> bool:
    try:
        msg             = MIMEMultipart("alternative")
        msg["Subject"]  = subject
        msg["From"]     = from_email
        msg["To"]       = to_email
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        host     = cfg.get("SMTP_HOST", "localhost")
        port     = int(cfg.get("SMTP_PORT", 587))
        user     = cfg.get("SMTP_USER", "")
        password = cfg.get("SMTP_PASSWORD", "")

        with smtplib.SMTP(host, port, timeout=25) as smtp:
            smtp.ehlo()
            smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as exc:
        print(f"[email_sender] SMTP error: {exc}")
        return False


# ── email templates ───────────────────────────────────────────────────────────

def _build_html(firstname: str, temp_password: str, login_url: str, app_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:2rem 1rem">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:12px;overflow:hidden;
                    border:1px solid #e2e8f0;max-width:560px">

        <!-- Header -->
        <tr>
          <td style="background:#1a2332;padding:1.5rem 2rem">
            <h1 style="margin:0;color:#fff;font-size:1.4rem">{app_name}</h1>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:2rem">
            <p style="margin:0 0 1rem;color:#1a2332;font-size:1rem">
              Hi <strong>{firstname}</strong>,
            </p>
            <p style="margin:0 0 1rem;color:#4a5568">
              Your account has been created. Use the temporary password below to log in:
            </p>

            <!-- Password box -->
            <div style="background:#f4f6f9;border:1px solid #cbd5e0;border-radius:8px;
                        padding:1rem 1.5rem;margin:1.5rem 0;text-align:center">
              <span style="font-size:1.5rem;font-weight:700;letter-spacing:0.15em;
                           color:#1a2332;font-family:monospace">
                {temp_password}
              </span>
            </div>

            <p style="margin:0 0 0.5rem;color:#4a5568">
              After logging in you will be asked to set a new permanent password.
            </p>
            <p style="margin:0 0 1.5rem;color:#e53e3e;font-size:0.9rem">
              <strong>This temporary password expires in 24 hours.</strong>
            </p>

            <a href="{login_url}"
               style="display:inline-block;background:#3b82f6;color:#fff;
                      padding:0.7rem 1.75rem;border-radius:8px;
                      text-decoration:none;font-weight:600;font-size:0.95rem">
              Log in now
            </a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8fafc;padding:1rem 2rem;
                     border-top:1px solid #e2e8f0">
            <p style="margin:0;color:#a0aec0;font-size:0.8rem">
              If you did not request this account, you can safely ignore this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _build_text(firstname: str, temp_password: str, login_url: str, app_name: str) -> str:
    return (
        f"{app_name}\n"
        f"{'─' * 40}\n\n"
        f"Hi {firstname},\n\n"
        f"Your account has been created.\n\n"
        f"Temporary password: {temp_password}\n\n"
        f"Log in at: {login_url}\n\n"
        f"This password expires in 24 hours.\n"
        f"After login you will be prompted to set a permanent password.\n\n"
        f"If you did not request this account, ignore this email."
    )
