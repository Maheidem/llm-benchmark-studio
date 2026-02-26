"""Authentication module for LLM Benchmark Studio.

JWT-based auth with:
- Access token: 24h, returned in JSON response body
- Refresh token: 7 days, stored in HttpOnly cookie
- bcrypt for password hashing
- python-jose[cryptography] for JWT
"""

import logging
import os
import hashlib
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

import bcrypt
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from jose import jwt, JWTError, ExpiredSignatureError

import db
import mailer


# --- Login Rate Limiter ---

class LoginRateLimiter:
    """IP-based rate limiter for login attempts."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lockouts: dict[str, float] = {}

    def check(self, ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). allowed=False means blocked."""
        now = time.time()

        # Check lockout
        if ip in self._lockouts:
            remaining = self._lockouts[ip] - now
            if remaining > 0:
                return False, int(remaining)
            del self._lockouts[ip]

        # Prune old attempts
        cutoff = now - self.window
        self._attempts[ip] = [t for t in self._attempts[ip] if t > cutoff]

        if len(self._attempts[ip]) >= self.max_attempts:
            self._lockouts[ip] = now + self.lockout
            return False, self.lockout

        return True, 0

    def record_attempt(self, ip: str):
        """Record a failed login attempt."""
        self._attempts[ip].append(time.time())


login_limiter = LoginRateLimiter()
forgot_password_limiter = LoginRateLimiter(max_attempts=5, window_seconds=300, lockout_seconds=900)

# --- Config ---
JWT_SECRET = os.environ.get("JWT_SECRET", "CHANGE-ME-IN-PRODUCTION-" + os.urandom(16).hex())
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
REFRESH_TOKEN_EXPIRE_DAYS = 7
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("true", "1", "yes")


# --- Password helpers ---

def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# --- Token helpers ---

def create_access_token(user_id: str, role: str) -> str:
    """Create a JWT access token (24h)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token (7 days). Returns raw token string."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def hash_token(token: str) -> str:
    """SHA-256 hash a token for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Raises JWTError/ExpiredSignatureError."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# --- FastAPI dependency: require auth ---

async def get_current_user(request: Request) -> dict:
    """FastAPI dependency that extracts and validates the user from the Authorization header.

    Returns dict with keys: id, email, role
    Raises HTTPException 401 if token is missing/invalid/expired.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]  # Strip "Bearer "
    try:
        payload = decode_token(token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("type") not in ("access", "cli"):
        raise HTTPException(status_code=401, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency: require admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# --- Auth route handlers (to be mounted in app.py) ---

async def register_handler(request: Request) -> JSONResponse:
    """POST /api/auth/register - Create new user account."""
    try:
        body = await request.json()
    except Exception:
        logger.debug("Register: invalid JSON body")
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    # Validation
    if not email or "@" not in email or len(email) > 254:
        return JSONResponse({"error": "Valid email required"}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)

    # Check duplicate
    existing = await db.get_user_by_email(email)
    if existing:
        return JSONResponse({"error": "Email already registered"}, status_code=409)

    # Determine role: first user is admin
    count = await db.count_users()
    role = "admin" if count == 0 else "user"

    # Create user
    hashed = hash_password(password)
    user = await db.create_user(email, hashed, role)
    logger.info("User registered: %s (role=%s)", email, role)

    # Seed providers/models from config.yaml for the new user
    try:
        await db.seed_providers_for_new_user(user["id"])
    except Exception as e:
        logger.warning("Failed to seed providers for new user %s: %s", email, e)

    # Audit: user registration
    ip = request.client.host if request.client else None
    await db.log_audit(
        user_id=user["id"],
        username=email,
        action="user_register",
        resource_type="user",
        detail={"username": email},
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    # Generate tokens
    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])

    # Store refresh token hash in DB
    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    await db.store_refresh_token(user["id"], hash_token(refresh), expires)

    # Build response with HttpOnly cookie for refresh token
    response = JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "access_token": access,
    })
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )
    return response


async def login_handler(request: Request) -> JSONResponse:
    """POST /api/auth/login - Authenticate and return tokens."""
    ip = request.client.host if request.client else "unknown"

    # Rate limit check
    allowed, retry_after = login_limiter.check(ip)
    if not allowed:
        logger.warning("Login rate limited: ip=%s retry_after=%ds", ip, retry_after)
        return JSONResponse(
            {"error": f"Too many login attempts. Try again in {retry_after} seconds."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        body = await request.json()
    except Exception:
        logger.debug("Login: invalid JSON body")
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    user = await db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        login_limiter.record_attempt(ip)
        logger.warning("Login failed for email=%s ip=%s", email, ip)
        # Audit: failed login
        await db.log_audit(
            user_id=user["id"] if user else None,
            username=email,
            action="user_login_failed",
            resource_type="user",
            detail={"reason": "bad_password"},
            ip_address=ip,
            user_agent=request.headers.get("user-agent", ""),
        )
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])

    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    await db.store_refresh_token(user["id"], hash_token(refresh), expires)
    logger.info("Login success: %s", email)

    # Audit: successful login
    await db.log_audit(
        user_id=user["id"],
        username=email,
        action="user_login",
        resource_type="user",
        detail={"method": "password"},
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    response = JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "access_token": access,
    })
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )
    return response


async def refresh_handler(request: Request) -> JSONResponse:
    """POST /api/auth/refresh - Exchange refresh token for new access token."""
    refresh = request.cookies.get("refresh_token")
    if not refresh:
        return JSONResponse({"error": "No refresh token"}, status_code=401)

    # Decode the refresh JWT
    try:
        payload = decode_token(refresh)
    except ExpiredSignatureError:
        return JSONResponse({"error": "Refresh token expired"}, status_code=401)
    except JWTError:
        return JSONResponse({"error": "Invalid refresh token"}, status_code=401)

    if payload.get("type") != "refresh":
        return JSONResponse({"error": "Invalid token type"}, status_code=401)

    # Verify token exists in DB (not revoked)
    stored = await db.get_refresh_token(hash_token(refresh))
    if not stored:
        return JSONResponse({"error": "Refresh token revoked"}, status_code=401)

    user = await db.get_user_by_id(payload["sub"])
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=401)

    # Issue new access token (refresh token stays the same until expiry)
    access = create_access_token(user["id"], user["role"])
    logger.debug("Token refreshed for user_id=%s", user["id"])

    # Audit: token refresh
    ip = request.client.host if request.client else None
    await db.log_audit(
        user_id=user["id"],
        username=user.get("email", ""),
        action="token_refresh",
        resource_type="user",
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    return JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "access_token": access,
    })


async def logout_handler(request: Request) -> JSONResponse:
    """POST /api/auth/logout - Revoke refresh token."""
    refresh = request.cookies.get("refresh_token")

    # Try to identify the user for audit logging
    user_id = None
    username = ""
    if refresh:
        try:
            payload = decode_token(refresh)
            user_id = payload.get("sub")
            user = await db.get_user_by_id(user_id) if user_id else None
            username = user.get("email", "") if user else ""
        except Exception:
            logger.debug("Could not decode refresh token during logout (expired or invalid)")
        await db.delete_refresh_token(hash_token(refresh))

    logger.info("User logged out: user_id=%s", user_id or "unknown")

    # Audit: logout
    ip = request.client.host if request.client else None
    await db.log_audit(
        user_id=user_id,
        username=username or "unknown",
        action="user_logout",
        resource_type="user",
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    response = JSONResponse({"status": "ok"})
    response.delete_cookie("refresh_token", path="/api/auth")
    return response


async def me_handler(request: Request) -> JSONResponse:
    """GET /api/auth/me - Return current user info (requires valid access token)."""
    user = await get_current_user(request)
    return JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
    })


async def forgot_password_handler(request: Request) -> JSONResponse:
    """POST /api/auth/forgot-password - Generate and email a password reset link.

    Always returns 200 to prevent email enumeration.
    Rate limited by IP (5 requests per 5 minutes).
    """
    ip = request.client.host if request.client else "unknown"

    # Rate limit
    allowed, retry_after = forgot_password_limiter.check(ip)
    if not allowed:
        logger.warning("Forgot-password rate limited: ip=%s", ip)
        return JSONResponse(
            {"error": f"Too many requests. Try again in {retry_after} seconds."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    forgot_password_limiter.record_attempt(ip)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "Valid email required"}, status_code=400)

    # Always return the same message (no email enumeration)
    _GENERIC_RESPONSE = {"message": "If that email exists, a reset link has been sent."}

    user = await db.get_user_by_email(email)
    if not user:
        logger.debug("Forgot-password: no user found for email=%s", email)
        return JSONResponse(_GENERIC_RESPONSE)

    # Delete any previous unused tokens for this user (keep DB clean)
    await db.delete_user_reset_tokens(user["id"])

    # Generate a short-lived reset token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    await db.store_password_reset_token(user["id"], token_hash, expires_at)

    # Send email (or log in dev mode)
    mailer.send_password_reset_email(email, raw_token)

    # Audit log
    await db.log_audit(
        user_id=user["id"],
        username=email,
        action="password_reset_request",
        resource_type="user",
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    logger.info("Password reset requested for email=%s", email)
    return JSONResponse(_GENERIC_RESPONSE)


async def reset_password_handler(request: Request) -> JSONResponse:
    """POST /api/auth/reset-password - Consume reset token and set a new password."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    raw_token = body.get("token", "").strip()
    new_password = body.get("password", "")

    if not raw_token:
        return JSONResponse({"error": "Token is required"}, status_code=400)
    if len(new_password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    record = await db.get_password_reset_token(token_hash)

    if not record:
        return JSONResponse({"error": "Invalid or expired reset token"}, status_code=400)

    # Mark token as used (single-use)
    await db.consume_password_reset_token(token_hash)

    # Update the password
    new_hash = hash_password(new_password)
    await db.update_user_password(record["user_id"], new_hash)

    # Delete all remaining reset tokens for this user
    await db.delete_user_reset_tokens(record["user_id"])

    # Audit log
    ip = request.client.host if request.client else None
    user = await db.get_user_by_id(record["user_id"])
    await db.log_audit(
        user_id=record["user_id"],
        username=user.get("email", "") if user else "",
        action="password_reset_complete",
        resource_type="user",
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    logger.info("Password reset completed for user_id=%s", record["user_id"])
    return JSONResponse({"message": "Password updated successfully."})
