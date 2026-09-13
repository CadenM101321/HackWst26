"""Who's logged in, for the FastAPI video app.

Login happens once, in Flask, through Auth0 (see auth.py). The video app never
logs anyone in itself: it reads the same signed session cookie Flask sets, and
gets the same user - including their Auth0 `sub`, which is what TigerData keys
everything by.

The cookie is signed with AUTH0_SECRET, so it can't be forged or edited. This
uses Flask's own serializer rather than a copy of its logic, so the two can't
drift apart. Both apps must share AUTH0_SECRET, and the browser only sends the
cookie to the same hostname it came from (localhost vs 127.0.0.1 differ).

    from websitefiles.session_bridge import user_from_cookies
    user = user_from_cookies(request.cookies)   # {"sub", "name", "email", ...} or None
"""

import os

from flask import Flask
from flask.sessions import SecureCookieSessionInterface

_flask = Flask(__name__)
_flask.secret_key = os.getenv("AUTH0_SECRET", "").strip() or None
_serializer = SecureCookieSessionInterface().get_signing_serializer(_flask)
_COOKIE_NAME = _flask.config["SESSION_COOKIE_NAME"]
# Same expiry Flask applies when it reads its own session.
_MAX_AGE = int(_flask.permanent_session_lifetime.total_seconds())


def user_from_cookies(cookies) -> dict | None:
    """The logged-in Auth0 user, or None if nobody is (or the cookie is invalid)."""
    raw = cookies.get(_COOKIE_NAME)
    if not raw or _serializer is None:
        return None
    try:
        return _serializer.loads(raw, max_age=_MAX_AGE).get("user")
    except Exception:  # tampered, expired, or signed with a different secret
        return None
