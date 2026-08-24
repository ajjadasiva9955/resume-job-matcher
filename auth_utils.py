import os
import re
import time
import secrets
import hashlib
import smtplib
import requests
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin
from flask import request, url_for
from dotenv import load_dotenv

# Absolute path to .env based on this file's directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Ensure .env is loaded reliably
load_dotenv(dotenv_path=ENV_PATH, override=True)

# --- Rate limiting state in memory ---
FAILED_ATTEMPTS = {}  # {key: [timestamps]}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes


def get_smtp_config():
    """
    Safely reads SMTP configuration from environment variables,
    re-reading .env to pick up any changes without server restart.
    """
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    
    server = (os.getenv("MAIL_SERVER") or "smtp.gmail.com").strip()
    port_val = (os.getenv("MAIL_PORT") or "587").strip()
    port = int(port_val) if port_val.isdigit() else 587
    use_tls = (os.getenv("MAIL_USE_TLS") or "True").strip().lower() in ("true", "1", "yes")
    username = (os.getenv("MAIL_USERNAME") or "skillbridge9955@gmail.com").strip()
    password = (os.getenv("MAIL_PASSWORD") or "").strip()
    mail_from = (os.getenv("MAIL_FROM") or username or "skillbridge9955@gmail.com").strip()

    is_configured = bool(server and username and password)

    return {
        "server": server,
        "port": port,
        "use_tls": use_tls,
        "username": username,
        "password": password,
        "from": mail_from,
        "is_configured": is_configured,
    }


def get_brevo_config():
    """
    Safely reads Brevo API configuration from environment variables.
    Reads BREVO_API_KEY only from the environment.
    Never hardcodes or logs the API key.
    """
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    api_key = (os.getenv("BREVO_API_KEY") or "").strip()
    provider = (os.getenv("EMAIL_PROVIDER") or "").strip().lower()
    sender_email = (
        os.getenv("BREVO_SENDER_EMAIL")
        or os.getenv("MAIL_FROM")
        or os.getenv("MAIL_USERNAME")
        or "skillbridge9955@gmail.com"
    ).strip()
    sender_name = (os.getenv("BREVO_SENDER_NAME") or "SkillBridge.AI").strip()
    api_url = (os.getenv("BREVO_API_URL") or "https://api.brevo.com/v3/smtp/email").strip()

    is_configured = bool(api_key)
    is_active = (provider == "brevo") or (is_configured and provider != "smtp")

    return {
        "api_key": api_key,
        "provider": provider,
        "sender_email": sender_email,
        "sender_name": sender_name,
        "api_url": api_url,
        "is_configured": is_configured,
        "is_active": is_active,
    }


def validate_password(password):
    """
    Validates password strength:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one number
    Returns (bool, str).
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number."
    if not re.search(r"[A-Z]", password):
        return False, "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number."
    if not re.search(r"[a-z]", password):
        return False, "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number."
    if not re.search(r"[0-9]", password):
        return False, "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number."
    return True, ""


def validate_username(username):
    """
    Validates username:
    - 3-30 characters
    - Alphanumeric characters and underscores only
    Returns (bool, str).
    """
    if not username:
        return False, "Username is required."
    trimmed = username.strip()
    if len(trimmed) < 3 or len(trimmed) > 30:
        return False, "Username must be between 3 and 30 characters."
    if not re.match(r"^[a-zA-Z0-9_]+$", trimmed):
        return False, "Username can only contain letters, numbers, and underscores."
    return True, ""


def validate_email(email):
    """
    Validates email format.
    Returns (bool, str).
    """
    if not email:
        return False, "Email address is required."
    trimmed = email.strip()
    email_regex = r"^[\w\.\+\-]+@[\w\-]+\.[a-zA-Z0-9\.\-]+$"
    if not re.match(email_regex, trimmed):
        return False, "Please enter a valid email address."
    return True, ""


def generate_secure_reset_token():
    """
    Generates a cryptographically secure URL-safe token and its SHA-256 hash.
    Returns (raw_token, token_hash, expires_at).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    return raw_token, token_hash, expires_at


def hash_reset_token(token):
    """
    Computes SHA-256 hash of a reset token string for database storage/comparison.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_remember_token(days=30):
    """
    Generates a cryptographically secure URL-safe remember token, its SHA-256 hash,
    and its expiration timestamp (default 30 days).
    Returns (raw_token, token_hash, expires_at).
    """
    raw_token = secrets.token_urlsafe(48)
    token_hash = hash_remember_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    return raw_token, token_hash, expires_at


def hash_remember_token(token):
    """
    Computes SHA-256 hash of a remember token string for database lookup/storage.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()



def check_rate_limit(identifier):
    """
    Checks if an IP or login identifier has exceeded failed attempt threshold.
    Returns (is_allowed, seconds_remaining).
    """
    now = time.time()
    attempts = FAILED_ATTEMPTS.get(identifier, [])
    valid_attempts = [ts for ts in attempts if now - ts < LOCKOUT_WINDOW_SECONDS]
    FAILED_ATTEMPTS[identifier] = valid_attempts

    if len(valid_attempts) >= MAX_FAILED_ATTEMPTS:
        oldest = valid_attempts[0]
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - oldest))
        return False, max(1, remaining)
    return True, 0


def record_failed_attempt(identifier):
    """Records a failed attempt timestamp for throttling."""
    now = time.time()
    attempts = FAILED_ATTEMPTS.get(identifier, [])
    attempts.append(now)
    FAILED_ATTEMPTS[identifier] = attempts


def reset_failed_attempts(identifier):
    """Clears failed attempts upon successful authentication."""
    FAILED_ATTEMPTS.pop(identifier, None)


def is_safe_url(target):
    """
    Ensures a redirect target URL is safe and relative to the current host.
    Prevents open redirect vulnerabilities.
    """
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def send_email_via_brevo(to_email, username, reset_url, text_content, html_content):
    """
    Sends transactional email via Brevo HTTPS API (POST https://api.brevo.com/v3/smtp/email).
    Does NOT use SMTP. Protects API keys and sensitive tokens from logs.
    """
    cfg = get_brevo_config()
    if not cfg["is_configured"]:
        print("[AUTH ERROR] Brevo API key is not configured in environment.")
        return {
            "success": False,
            "status": "NO_API_KEY",
            "reset_url": reset_url,
            "error": "Brevo API key not configured",
        }

    headers = {
        "accept": "application/json",
        "api-key": cfg["api_key"],
        "content-type": "application/json",
    }

    payload = {
        "sender": {
            "name": cfg["sender_name"],
            "email": cfg["sender_email"],
        },
        "to": [
            {
                "email": to_email,
                "name": username or to_email,
            }
        ],
        "subject": "Reset your SkillBridge.AI password",
        "htmlContent": html_content,
        "textContent": text_content,
    }

    try:
        response = requests.post(cfg["api_url"], json=payload, headers=headers, timeout=15)
        if response.status_code in (200, 201, 202):
            print(f"[AUTH SUCCESS] Password reset email successfully dispatched to {to_email} via Brevo HTTPS API.")
            return {
                "success": True,
                "status": "SENT",
                "reset_url": reset_url,
                "provider": "brevo",
            }
        else:
            print(f"[AUTH ERROR] Brevo HTTPS API returned HTTP status {response.status_code}")
            return {
                "success": False,
                "status": "BREVO_ERROR",
                "reset_url": reset_url,
                "error": f"Brevo API error status {response.status_code}",
            }
    except requests.RequestException as e:
        print(f"[AUTH ERROR] Brevo HTTPS API request failed: {type(e).__name__}")
        return {
            "success": False,
            "status": "BREVO_ERROR",
            "reset_url": reset_url,
            "error": str(e),
        }
    except Exception as e:
        print(f"[AUTH ERROR] Unexpected error during Brevo HTTPS email delivery: {type(e).__name__}")
        return {
            "success": False,
            "status": "BREVO_ERROR",
            "reset_url": reset_url,
            "error": str(e),
        }


def send_password_reset_email(to_email, username, reset_token):
    """
    Dispatches a password reset email using:
    1. Brevo HTTPS Transactional Email API if EMAIL_PROVIDER=brevo (or BREVO_API_KEY is configured)
    2. Local Gmail SMTP (smtplib) as a fallback for local development if configured

    Returns a dict:
    - success: bool
    - status: 'SENT' | 'NO_PASSWORD' | 'AUTH_ERROR' | 'CONNECT_ERROR' | 'NO_API_KEY' | 'BREVO_ERROR'
    - reset_url: str
    - provider: 'brevo' | 'smtp' (optional)
    - error: optional error message for developer diagnostic
    """
    reset_url = url_for("reset_password_route", token=reset_token, _external=True)

    # Plain text version
    text_content = f"""Hello {username},

Someone requested a password reset for your SkillBridge.AI account.
If this was you, use the link below to set a new password:

{reset_url}

This link expires in 30 minutes and can only be used once.

If you did not request this password reset, please ignore this email. Your account remains secure.

Best regards,
SkillBridge.AI Team
"""

    # HTML version with SkillBridge styling
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #030712; color: #f8fafc; margin: 0; padding: 24px; }}
    .container {{ max-width: 560px; margin: 0 auto; background: #0b1220; border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; padding: 32px; }}
    .logo-text {{ font-size: 22px; font-weight: 800; color: #22cdd6; margin-bottom: 20px; }}
    .btn {{ display: inline-block; padding: 14px 28px; background: linear-gradient(135deg, #22cdd6, #087d91); color: #ffffff !important; text-decoration: none; border-radius: 8px; font-weight: 700; margin: 24px 0; }}
    .note {{ font-size: 13px; color: #94a3b8; line-height: 1.5; margin-top: 24px; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 16px; }}
    .link-alt {{ font-size: 12px; color: #38bdf8; word-break: break-all; }}
</style>
</head>
<body>
<div class="container">
    <div class="logo-text">SkillBridge.AI</div>
    <h2>Reset Your Password</h2>
    <p>Hello <strong>{username}</strong>,</p>
    <p>Someone requested a password reset for your SkillBridge.AI account. Click the button below to set a new password:</p>
    <p><a href="{reset_url}" class="btn">Reset Password</a></p>
    <p>This password reset link expires in <strong>30 minutes</strong> and can only be used once.</p>
    <p class="link-alt">If the button does not work, copy and paste this URL into your browser:<br>{reset_url}</p>
    <div class="note">
        <p>If you did not request this password reset, you can safely ignore this email. Your password will remain unchanged.</p>
    </div>
</div>
</body>
</html>
"""

    provider = (os.getenv("EMAIL_PROVIDER") or "").strip().lower()
    brevo_cfg = get_brevo_config()

    # 1. Primary: Brevo HTTPS API
    if provider == "brevo" or (brevo_cfg["is_configured"] and provider != "smtp"):
        return send_email_via_brevo(to_email, username, reset_url, text_content, html_content)

    # 2. Local Fallback: Gmail SMTP
    cfg = get_smtp_config()
    if not cfg["is_configured"]:
        print(f"\n[AUTH DIAGNOSTIC] Password reset requested for {to_email}")
        print(f"[AUTH DIAGNOSTIC] SMTP status: MAIL_PASSWORD is not set in .env")
        print(f"[AUTH DIAGNOSTIC] Development Reset Link: {reset_url}")
        print(f"[AUTH DIAGNOSTIC] To enable real email sending from {cfg['username']}:")
        print(f"                  Set MAIL_PASSWORD=<16-char Gmail App Password> in {ENV_PATH}\n")
        return {
            "success": False,
            "status": "NO_PASSWORD",
            "reset_url": reset_url,
            "error": "Gmail App Password not set in .env",
        }

    msg = EmailMessage()
    msg["Subject"] = "Reset your SkillBridge.AI password"
    msg["From"] = cfg["from"]
    msg["To"] = to_email
    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")

    try:
        with smtplib.SMTP(cfg["server"], cfg["port"], timeout=12) as server:
            if cfg["use_tls"]:
                server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.send_message(msg)

        print(f"[AUTH SUCCESS] Password reset email successfully sent to {to_email} via {cfg['username']}")
        return {
            "success": True,
            "status": "SENT",
            "reset_url": reset_url,
            "provider": "smtp",
        }

    except smtplib.SMTPAuthenticationError as e:
        print(f"[AUTH ERROR] Gmail SMTP authentication failed for {cfg['username']}: {e}")
        print(f"[AUTH ERROR] Ensure you are using a 16-character App Password generated at https://myaccount.google.com/apppasswords")
        return {
            "success": False,
            "status": "AUTH_ERROR",
            "reset_url": reset_url,
            "error": f"SMTP Authentication failed: {e}",
        }
    except Exception as e:
        print(f"[AUTH ERROR] Failed to send password reset email via SMTP ({cfg['server']}:{cfg['port']}): {e}")
        return {
            "success": False,
            "status": "CONNECT_ERROR",
            "reset_url": reset_url,
            "error": str(e),
        }
