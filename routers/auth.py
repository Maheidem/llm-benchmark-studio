"""Auth routes: register, login, refresh, logout, me, cli-token."""

from fastapi import APIRouter, Depends
import auth

router = APIRouter(tags=["auth"])

# These endpoints are delegated directly to auth module handlers
router.post("/api/auth/register")(auth.register_handler)
router.post("/api/auth/login")(auth.login_handler)
router.post("/api/auth/refresh")(auth.refresh_handler)
router.post("/api/auth/logout")(auth.logout_handler)
router.get("/api/auth/me")(auth.me_handler)


@router.post("/api/auth/cli-token")
async def generate_cli_token(user: dict = Depends(auth.get_current_user)):
    """Generate a long-lived JWT for CLI usage (30 days)."""
    from datetime import timedelta, datetime, timezone
    from jose import jwt as jose_jwt

    expire = datetime.now(timezone.utc) + timedelta(days=30)
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "exp": expire,
        "type": "cli",
    }
    token = jose_jwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    return {"token": token, "expires_in_days": 30}
