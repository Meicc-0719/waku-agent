"""Google Calendar — read your real schedule, with a sign-in you can actually do.

Two OAuth-client sources, with the same read-only access:

  DEFAULT   waku's own OAuth client, shipped below. Click, approve in the
            browser, done. No files to download.

  OVERRIDE  your own `.waku/credentials.json`, when present. This replaces the
            OAuth client configuration only; it does not expand permissions.

Why the read client can live in a public repo: for Google's "Desktop app" OAuth
client type the secret is not confidential, and Google says so. It identifies
the app, it does not authorise anything. Nobody can read your calendar with it;
every user still approves in their own browser and their token is stored only on
their machine. This is what gcloud and rclone do. The blast radius of a leak is
brand impersonation and quota, not data — which is exactly why READ is shipped
and WRITE is not.

Everything here is best-effort and honest: a missing dependency, a refused
consent or an expired token returns a sentence explaining what to do, never an
exception, because a calendar hiccup must not take down a chat turn.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
DEFAULT_SCOPES = [READONLY_SCOPE]
TIMEOUT = 30

# waku 自己的 OAuth 客户端（桌面应用程序）。
# TODO：之前填写项目拥有的 Google OAuth 客户端 ID 和密钥
# 发布。桌面应用程序客户端机密是标识符，而不是机密
# 证书;每个用户仍然必须在自己的浏览器中授予访问权限。
BUNDLED_CLIENT_CONFIG = {
    "installed": {
        "client_id": "",
        "client_secret": "",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

_INSTALL_HINT = (
    "Google Calendar support is not installed — run: pip install -e '.[gcal]'"
)
_SETUP_HINT = (
    "Google Calendar is not connected yet. Run `waku connect google` (or click "
    "Connect in the dashboard's Connections tab) to sign in — it opens your "
    "browser and takes about ten seconds."
)


def _token_path(home: Path) -> Path:
    return home / "google-token.json"


def _load_credentials(home: Path, scopes: list[str]):
    """Cached token → refresh if stale → None. Never launches a browser: that
    only happens in connect(), so a chat turn can never block on a consent
    screen the user cannot see."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None

    path = _token_path(home)
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path), scopes=scopes)
    except Exception:
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds if (creds and creds.valid) else None


def connect(home: Path) -> str:
    """Open the browser, get consent, cache the token. The ONE place a browser
    window is allowed to appear — called from the CLI/dashboard, never mid-turn.

    Uses waku's bundled client by default. If you dropped your own
    `.waku/credentials.json` in place, that OAuth client configuration wins.
    Both paths request the same read-only scope."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return _INSTALL_HINT

    own = home / "credentials.json"
    try:
        if own.exists():
            flow = InstalledAppFlow.from_client_secrets_file(
                str(own), scopes=DEFAULT_SCOPES
            )
            client_source = "credentials.json override"
        else:
            bundled = BUNDLED_CLIENT_CONFIG.get("installed", {})
            if not bundled.get("client_id") or not bundled.get("client_secret"):
                return (
                    "Google Calendar's bundled OAuth client is not configured "
                    "yet. Add .waku/credentials.json to use your own Desktop "
                    "OAuth client."
                )
            flow = InstalledAppFlow.from_client_config(
                BUNDLED_CLIENT_CONFIG,
                scopes=DEFAULT_SCOPES,
            )
            client_source = "bundled OAuth client"
        # port=0 = 任何空闲的本地端口。重定向返回到这台机器上，
        # 所以任何地方都不涉及服务器和公共 URL。
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        return f"Google sign-in failed or was cancelled ({type(exc).__name__}). Nothing was saved."

    path = _token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    return (
        f"Google Calendar connected (read-only via {client_source}). "
        f"Token cached at {path}."
    )


def list_google_events(home: Path, start: str = "", end: str = "", limit: int = 20) -> str:
    """Events from the user's PRIMARY Google calendar between two dates.

    `start`/`end` are YYYY-MM-DD; empty means today. Returns one event per line
    as `title | start | attendees`, or a sentence saying why it could not.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return _INSTALL_HINT

    creds = _load_credentials(home, DEFAULT_SCOPES)
    if creds is None:
        return _SETUP_HINT

    today = _dt.date.today().isoformat()
    s = (start or today)[:10]
    e = (end or s)[:10]
    # 谷歌希望 RFC3339 带有偏移量；当地午夜至当地午夜+1d
    # 所以“今天”是指用户的今天，而不是 UTC 的今天。
    tz = _dt.datetime.now().astimezone().tzinfo
    lo = _dt.datetime.fromisoformat(s).replace(tzinfo=tz)
    hi = _dt.datetime.fromisoformat(e).replace(tzinfo=tz) + _dt.timedelta(days=1)

    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        resp = service.events().list(
            calendarId="primary",
            timeMin=lo.isoformat(),
            timeMax=hi.isoformat(),
            singleEvents=True,          # 将重复序列扩展为实例
            orderBy="startTime",
            maxResults=max(1, min(int(limit or 20), 100)),
        ).execute()
    except Exception as exc:
        return f"Google Calendar unavailable ({type(exc).__name__}: {str(exc)[:120]})"

    items = resp.get("items", [])
    if not items:
        return f"No Google Calendar events between {s} and {e}."
    lines = []
    for ev in items:
        when = ev.get("start", {})
        at = when.get("dateTime") or when.get("date") or "?"
        who = ", ".join(a.get("email", "") for a in ev.get("attendees", []) if a.get("email"))
        lines.append(f"{ev.get('summary', '(no title)')} | {at}" + (f" | {who}" if who else ""))
    return "\n".join(lines)


def is_connected(home: Path) -> bool:
    """True when a usable token exists. Cheap and side-effect free: callers use
    this to decide whether to even mention Google, so it must never trigger a
    browser or a network call."""
    return _token_path(home).exists()
