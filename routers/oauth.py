"""Google OAuth 2.0 routes.

Flow:
  1. GET /api/auth/google/authorize  — returns the Google OAuth URL + sets state cookie
  2. GET /api/auth/google/callback   — exchanges code, finds/creates user, issues JWT

Requires env vars:
  GOOGLE_CLIENT_ID     — OAuth app client ID
  GOOGLE_CLIENT_SECRET — OAuth app client secret
  APP_BASE_URL         — App base URL (for redirect_uri, default: http://localhost:8501)
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

import auth
import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8501").rstrip("/")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

STATE_COOKIE_NAME = "oauth_state"
STATE_COOKIE_MAX_AGE = 600  # 10 minutes


def _redirect_uri() -> str:
    return f"{APP_BASE_URL}/api/auth/google/callback"


@router.get("/api/auth/google/authorize")
async def google_authorize(request: Request):
    """Return the Google OAuth authorization URL and set a CSRF state cookie."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return JSONResponse({"error": "Google OAuth not configured"}, status_code=400)

    state = secrets.token_urlsafe(16)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    response = JSONResponse({"url": url})
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=state,
        httponly=True,
        secure=auth.COOKIE_SECURE,
        samesite="lax",   # lax so cookie survives the Google redirect
        max_age=STATE_COOKIE_MAX_AGE,
        path="/api/auth/google",
    )
    return response


@router.get("/api/auth/google/callback")
async def google_callback(request: Request):
    """Handle Google's redirect after user consent.

    Exchanges the authorization code for tokens, fetches user info,
    finds or creates the local user, then redirects the browser to
    /oauth-callback?token=<jwt> where the frontend extracts the token.
    """
    error = request.query_params.get("error")
    if error:
        logger.warning("Google OAuth error: %s", error)
        return RedirectResponse(f"{APP_BASE_URL}/oauth-callback?error={error}", status_code=302)

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    # CSRF: verify state cookie
    cookie_state = request.cookies.get(STATE_COOKIE_NAME, "")
    if not state or not cookie_state or state != cookie_state:
        logger.warning("Google OAuth CSRF state mismatch")
        return RedirectResponse(
            f"{APP_BASE_URL}/oauth-callback?error=state_mismatch", status_code=302
        )

    if not code:
        return RedirectResponse(
            f"{APP_BASE_URL}/oauth-callback?error=missing_code", status_code=302
        )

    # Exchange code for access token
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            })
            token_resp.raise_for_status()
            token_data = token_resp.json()

            google_access_token = token_data.get("access_token")
            if not google_access_token:
                raise ValueError("No access_token in Google token response")

            # Fetch user info from Google
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {google_access_token}"},
            )
            userinfo_resp.raise_for_status()
            google_user = userinfo_resp.json()
    except Exception:
        logger.exception("Google OAuth token exchange failed")
        return RedirectResponse(
            f"{APP_BASE_URL}/oauth-callback?error=token_exchange_failed", status_code=302
        )

    email = (google_user.get("email") or "").strip().lower()
    google_id = str(google_user.get("id", ""))
    avatar_url = google_user.get("picture")

    if not email or not google_id:
        logger.warning("Google OAuth: missing email or id in userinfo response")
        return RedirectResponse(
            f"{APP_BASE_URL}/oauth-callback?error=missing_user_info", status_code=302
        )

    # Find or create local user
    user = await db.get_user_by_google_id(google_id)
    if not user:
        # Check if an account already exists with this email (password-based)
        user = await db.get_user_by_email(email)
        if user:
            # Link Google account to existing email-based user
            if not user.get("google_id"):
                await db.link_google_id(user["id"], google_id, avatar_url)
                logger.info("Linked Google account to existing user: %s", email)
        else:
            # Create brand new user
            count = await db.count_users()
            role = "admin" if count == 0 else "user"
            user = await db.create_google_user(email, google_id, avatar_url, role)
            logger.info("Created new user via Google OAuth: %s (role=%s)", email, role)

    if not user:
        logger.error("Google OAuth: failed to find or create user for email=%s", email)
        return RedirectResponse(
            f"{APP_BASE_URL}/oauth-callback?error=user_creation_failed", status_code=302
        )

    # Issue our JWT + refresh token
    access_token = auth.create_access_token(user["id"], user["role"])
    refresh_token = auth.create_refresh_token(user["id"])

    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=auth.REFRESH_TOKEN_EXPIRE_DAYS)
    ).isoformat()
    await db.store_refresh_token(user["id"], auth.hash_token(refresh_token), expires_at)

    # Audit log
    ip = request.client.host if request.client else None
    await db.log_audit(
        user_id=user["id"],
        username=email,
        action="user_login",
        resource_type="user",
        detail={"method": "google_oauth"},
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    logger.info("Google OAuth login success: %s", email)

    # Redirect to frontend with access token in URL fragment (not query string)
    # Frontend /oauth-callback extracts token and stores it
    redirect_url = f"{APP_BASE_URL}/oauth-callback?token={access_token}"
    response = RedirectResponse(redirect_url, status_code=302)

    # Set refresh token HttpOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=auth.COOKIE_SECURE,
        samesite="strict",
        max_age=auth.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )

    # Clear the state cookie
    response.delete_cookie(STATE_COOKIE_NAME, path="/api/auth/google")

    return response
