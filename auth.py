"""Authentication module for LLM Benchmark Studio.

JWT-based auth with:
- Access token: 15min, returned in JSON response body
- Refresh token: 7 days, stored in HttpOnly cookie
- bcrypt for password hashing
- python-jose[cryptography] for JWT
"""

import os
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from jose import jwt, JWTError, ExpiredSignatureError

import db

# --- Config ---
JWT_SECRET = os.environ.get("JWT_SECRET", "CHANGE-ME-IN-PRODUCTION-" + os.urandom(16).hex())
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7


# --- Password helpers ---

def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# --- Token helpers ---

def create_access_token(user_id: str, role: str) -> str:
    """Create a short-lived JWT access token (15 min)."""
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

    if payload.get("type") != "access":
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
        secure=False,  # Set True in production with HTTPS
        samesite="strict",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/auth",
    )
    return response


async def login_handler(request: Request) -> JSONResponse:
    """POST /api/auth/login - Authenticate and return tokens."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    user = await db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    access = create_access_token(user["id"], user["role"])
    refresh = create_refresh_token(user["id"])

    expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)).isoformat()
    await db.store_refresh_token(user["id"], hash_token(refresh), expires)

    response = JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "access_token": access,
    })
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=False,
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
    return JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "access_token": access,
    })


async def logout_handler(request: Request) -> JSONResponse:
    """POST /api/auth/logout - Revoke refresh token."""
    refresh = request.cookies.get("refresh_token")
    if refresh:
        await db.delete_refresh_token(hash_token(refresh))

    response = JSONResponse({"status": "ok"})
    response.delete_cookie("refresh_token", path="/api/auth")
    return response


async def me_handler(request: Request) -> JSONResponse:
    """GET /api/auth/me - Return current user info (requires valid access token)."""
    user = await get_current_user(request)
    return JSONResponse({
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
    })
