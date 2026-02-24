"""Tests for forgot password and Google OAuth auth features.

Covers:
- POST /api/auth/forgot-password
- POST /api/auth/reset-password
- GET /api/auth/google/authorize
- GET /api/auth/google/callback

External calls (SMTP, Google APIs) are mocked. All tests use the shared
async TestClient and session-scoped SQLite DB from conftest.py.

Run: uv run pytest tests/test_forgot_password_oauth.py -v
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

def _clear_forgot_limiter():
    """Reset the forgot-password rate limiter so tests don't interfere with each other."""
    import auth as auth_module
    auth_module.forgot_password_limiter._attempts.clear()
    auth_module.forgot_password_limiter._lockouts.clear()


# =========================================================================
# Helpers
# =========================================================================


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# =========================================================================
# Forgot Password — POST /api/auth/forgot-password
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
class TestForgotPassword:
    """Tests for the forgot-password endpoint."""

    async def test_returns_200_for_existing_email(self, app_client, test_user):
        """Always returns 200 even when email exists (anti-enumeration)."""
        _clear_forgot_limiter()
        user, _ = test_user
        with patch("mailer.send_password_reset_email", return_value=True):
            resp = await app_client.post(
                "/api/auth/forgot-password", json={"email": user["email"]}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data

    async def test_returns_200_for_nonexistent_email(self, app_client):
        """Returns 200 even for unknown emails (prevents email enumeration)."""
        _clear_forgot_limiter()
        resp = await app_client.post(
            "/api/auth/forgot-password",
            json={"email": "doesnotexist@nowhere.example"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data

    async def test_same_message_for_known_and_unknown_email(self, app_client, test_user):
        """The response body is identical for existing vs. nonexistent emails."""
        _clear_forgot_limiter()
        user, _ = test_user
        with patch("mailer.send_password_reset_email", return_value=True):
            resp_known = await app_client.post(
                "/api/auth/forgot-password", json={"email": user["email"]}
            )
        _clear_forgot_limiter()
        resp_unknown = await app_client.post(
            "/api/auth/forgot-password",
            json={"email": "ghost@nobody.example"},
        )
        assert resp_known.json()["message"] == resp_unknown.json()["message"]

    async def test_invalid_email_returns_400(self, app_client):
        """Missing @ in email returns 400."""
        _clear_forgot_limiter()
        resp = await app_client.post(
            "/api/auth/forgot-password", json={"email": "notanemail"}
        )
        assert resp.status_code == 400

    async def test_empty_email_returns_400(self, app_client):
        """Empty string email returns 400."""
        _clear_forgot_limiter()
        resp = await app_client.post(
            "/api/auth/forgot-password", json={"email": ""}
        )
        assert resp.status_code == 400

    async def test_missing_email_field_returns_400(self, app_client):
        """Missing email field returns 400."""
        _clear_forgot_limiter()
        resp = await app_client.post("/api/auth/forgot-password", json={})
        assert resp.status_code == 400

    async def test_invalid_json_returns_400(self, app_client):
        """Non-JSON body returns 400."""
        _clear_forgot_limiter()
        resp = await app_client.post(
            "/api/auth/forgot-password",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_mailer_is_called_for_existing_user(self, app_client, test_user):
        """Mailer is invoked when email belongs to a real user."""
        _clear_forgot_limiter()
        user, _ = test_user
        with patch("mailer.send_password_reset_email", return_value=True) as mock_mail:
            await app_client.post(
                "/api/auth/forgot-password", json={"email": user["email"]}
            )
        mock_mail.assert_called_once()
        call_args = mock_mail.call_args[0]
        assert call_args[0] == user["email"]   # first arg: to_email
        assert isinstance(call_args[1], str)   # second arg: raw_token string

    async def test_mailer_not_called_for_unknown_user(self, app_client):
        """Mailer is NOT called for email addresses that have no account."""
        _clear_forgot_limiter()
        with patch("mailer.send_password_reset_email", return_value=True) as mock_mail:
            await app_client.post(
                "/api/auth/forgot-password",
                json={"email": "ghost2@nobody.example"},
            )
        mock_mail.assert_not_called()

    async def test_rate_limiting_triggers_after_many_requests(self, app_client, test_user):
        """Endpoint rate-limits after 5 rapid requests from the same IP."""
        user, _ = test_user
        import auth as auth_module

        # Reset the limiter state for a fresh IP to avoid cross-test pollution
        test_ip = "10.99.99.1"
        auth_module.forgot_password_limiter._attempts.pop(test_ip, None)
        auth_module.forgot_password_limiter._lockouts.pop(test_ip, None)

        with patch("mailer.send_password_reset_email", return_value=True):
            responses = []
            for _ in range(7):
                r = await app_client.post(
                    "/api/auth/forgot-password",
                    json={"email": user["email"]},
                    headers={"X-Forwarded-For": test_ip},
                )
                responses.append(r.status_code)

        # At least one of the later responses must be 429
        assert 429 in responses


# =========================================================================
# Reset Password — POST /api/auth/reset-password
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
class TestResetPassword:
    """Tests for the reset-password endpoint."""

    @pytest_asyncio.fixture(scope="class")
    async def reset_token(self, app_client, test_user):
        """Obtain a valid raw reset token for the test user."""
        _clear_forgot_limiter()
        user, _ = test_user
        captured = {}
        with patch("mailer.send_password_reset_email") as mock_mail:
            def capture(to_email, raw_token):
                captured["raw_token"] = raw_token
                return True

            mock_mail.side_effect = capture
            await app_client.post(
                "/api/auth/forgot-password", json={"email": user["email"]}
            )
        return captured.get("raw_token")

    async def test_valid_token_resets_password(self, app_client, test_user, reset_token):
        """A valid token allows password to be updated; endpoint returns 200."""
        assert reset_token, "No reset token captured — check forgot-password flow"
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": reset_token, "password": "NewPassword99!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data

    async def test_token_is_single_use(self, app_client, reset_token):
        """After a successful reset, the same token is rejected."""
        if not reset_token:
            pytest.skip("No reset token available")
        # Token was consumed in test_valid_token_resets_password; use it again
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": reset_token, "password": "AnotherPass99!"},
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json().get("error", "").lower() or \
               "expired" in resp.json().get("error", "").lower()

    async def test_invalid_token_rejected(self, app_client):
        """A random/fake token returns 400."""
        fake_token = secrets.token_urlsafe(32)
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": fake_token, "password": "ValidPass99!"},
        )
        assert resp.status_code == 400

    async def test_missing_token_returns_400(self, app_client):
        """Missing token field returns 400."""
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"password": "ValidPass99!"},
        )
        assert resp.status_code == 400

    async def test_short_password_rejected(self, app_client):
        """Password shorter than 8 characters returns 400."""
        fake_token = secrets.token_urlsafe(32)
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": fake_token, "password": "short"},
        )
        assert resp.status_code == 400
        assert "8" in resp.json().get("error", "")

    async def test_missing_password_returns_400(self, app_client):
        """Missing password field returns 400 (empty string = too short)."""
        fake_token = secrets.token_urlsafe(32)
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": fake_token, "password": ""},
        )
        assert resp.status_code == 400

    async def test_invalid_json_returns_400(self, app_client):
        """Non-JSON body returns 400."""
        resp = await app_client.post(
            "/api/auth/reset-password",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_expired_token_rejected(self, app_client, test_user, _patch_db_path):
        """A token with past expires_at is rejected."""
        import aiosqlite

        user, _ = test_user
        raw_token = secrets.token_urlsafe(32)
        token_hash = _sha256(raw_token)
        expired_at = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, used) "
                "VALUES (?, ?, ?, 0)",
                (user["id"], token_hash, expired_at),
            )
            await conn.commit()

        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "password": "NewValidPass99!"},
        )
        assert resp.status_code == 400

    async def test_used_token_rejected(self, app_client, test_user, _patch_db_path):
        """A token already marked used=1 is rejected."""
        import aiosqlite

        user, _ = test_user
        raw_token = secrets.token_urlsafe(32)
        token_hash = _sha256(raw_token)
        valid_until = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        async with aiosqlite.connect(str(_patch_db_path)) as conn:
            await conn.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, used) "
                "VALUES (?, ?, ?, 1)",
                (user["id"], token_hash, valid_until),
            )
            await conn.commit()

        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "password": "NewValidPass99!"},
        )
        assert resp.status_code == 400

    async def test_new_password_works_for_login(self, app_client, test_user):
        """After resetting password, user can log in with new credentials."""
        _clear_forgot_limiter()
        user, _ = test_user
        new_password = "ResetAndLogin99!"

        # Request a fresh reset token
        captured = {}
        with patch("mailer.send_password_reset_email") as mock_mail:
            def capture(to_email, raw_token):
                captured["raw_token"] = raw_token
                return True

            mock_mail.side_effect = capture
            await app_client.post(
                "/api/auth/forgot-password", json={"email": user["email"]}
            )

        raw_token = captured.get("raw_token")
        assert raw_token, "No reset token captured"

        # Reset to new password
        resp = await app_client.post(
            "/api/auth/reset-password",
            json={"token": raw_token, "password": new_password},
        )
        assert resp.status_code == 200

        # Log in with new password
        login_resp = await app_client.post(
            "/api/auth/login",
            json={"email": user["email"], "password": new_password},
        )
        assert login_resp.status_code == 200
        assert "access_token" in login_resp.json()


# =========================================================================
# Google OAuth — GET /api/auth/google/authorize
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
class TestGoogleAuthorize:
    """Tests for the Google OAuth authorize endpoint."""

    async def test_returns_400_when_oauth_not_configured(self, app_client):
        """When GOOGLE_CLIENT_ID/SECRET are unset, returns 400."""
        with patch.dict(
            "routers.oauth.__dict__",
            {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""},
        ):
            import routers.oauth as oauth_module
            original_id = oauth_module.GOOGLE_CLIENT_ID
            original_secret = oauth_module.GOOGLE_CLIENT_SECRET
            oauth_module.GOOGLE_CLIENT_ID = ""
            oauth_module.GOOGLE_CLIENT_SECRET = ""
            try:
                resp = await app_client.get("/api/auth/google/authorize")
            finally:
                oauth_module.GOOGLE_CLIENT_ID = original_id
                oauth_module.GOOGLE_CLIENT_SECRET = original_secret

        assert resp.status_code == 400
        assert "not configured" in resp.json().get("error", "").lower()

    async def test_returns_url_when_configured(self, app_client):
        """When OAuth credentials exist, returns a Google auth URL."""
        import routers.oauth as oauth_module

        original_id = oauth_module.GOOGLE_CLIENT_ID
        original_secret = oauth_module.GOOGLE_CLIENT_SECRET
        oauth_module.GOOGLE_CLIENT_ID = "test-client-id"
        oauth_module.GOOGLE_CLIENT_SECRET = "test-client-secret"
        try:
            resp = await app_client.get("/api/auth/google/authorize")
        finally:
            oauth_module.GOOGLE_CLIENT_ID = original_id
            oauth_module.GOOGLE_CLIENT_SECRET = original_secret

        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert "accounts.google.com" in data["url"]

    async def test_state_cookie_is_set(self, app_client):
        """A CSRF state cookie is set when OAuth is configured."""
        import routers.oauth as oauth_module

        original_id = oauth_module.GOOGLE_CLIENT_ID
        original_secret = oauth_module.GOOGLE_CLIENT_SECRET
        oauth_module.GOOGLE_CLIENT_ID = "test-client-id"
        oauth_module.GOOGLE_CLIENT_SECRET = "test-client-secret"
        try:
            resp = await app_client.get("/api/auth/google/authorize")
        finally:
            oauth_module.GOOGLE_CLIENT_ID = original_id
            oauth_module.GOOGLE_CLIENT_SECRET = original_secret

        assert resp.status_code == 200
        assert "oauth_state" in resp.cookies

    async def test_url_contains_required_params(self, app_client):
        """Auth URL contains client_id, redirect_uri, response_type, scope."""
        import routers.oauth as oauth_module

        original_id = oauth_module.GOOGLE_CLIENT_ID
        original_secret = oauth_module.GOOGLE_CLIENT_SECRET
        oauth_module.GOOGLE_CLIENT_ID = "my-client-id"
        oauth_module.GOOGLE_CLIENT_SECRET = "my-secret"
        try:
            resp = await app_client.get("/api/auth/google/authorize")
        finally:
            oauth_module.GOOGLE_CLIENT_ID = original_id
            oauth_module.GOOGLE_CLIENT_SECRET = original_secret

        url = resp.json()["url"]
        assert "client_id=my-client-id" in url
        assert "response_type=code" in url
        assert "scope=" in url
        assert "state=" in url


# =========================================================================
# Google OAuth — GET /api/auth/google/callback
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
class TestGoogleCallback:
    """Tests for the Google OAuth callback endpoint."""

    def _make_google_userinfo(
        self,
        email: str = "oauthuser@example.com",
        google_id: str = "999888777",
        picture: str = "https://lh3.googleusercontent.com/photo.jpg",
    ) -> dict:
        return {"email": email, "id": google_id, "picture": picture}

    def _mock_google_http(self, userinfo: dict):
        """Return a context-manager mock for httpx.AsyncClient that fakes Google APIs."""

        async def mock_post(*args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"access_token": "fake-google-token"})
            return resp

        async def mock_get(*args, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=userinfo)
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = mock_post
        mock_client.get = mock_get
        return mock_client

    async def test_error_param_redirects_to_frontend(self, app_client):
        """Google-returned ?error= redirects to frontend oauth-callback with error."""
        resp = await app_client.get(
            "/api/auth/google/callback?error=access_denied",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "oauth-callback" in resp.headers["location"]
        assert "access_denied" in resp.headers["location"]

    async def test_state_mismatch_redirects_with_error(self, app_client):
        """When state cookie is missing/mismatched, redirect includes state_mismatch."""
        resp = await app_client.get(
            "/api/auth/google/callback?code=abc&state=wrongstate",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "state_mismatch" in resp.headers["location"]

    async def test_missing_code_redirects_with_error(self, app_client):
        """When code is absent but state matches, redirect includes missing_code."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)
        # Send the state cookie along
        resp = await app_client.get(
            f"/api/auth/google/callback?state={state}",
            follow_redirects=False,
            cookies={oauth_module.STATE_COOKIE_NAME: state},
        )
        assert resp.status_code == 302
        assert "missing_code" in resp.headers["location"]

    async def test_token_exchange_failure_redirects(self, app_client):
        """When Google token exchange raises an exception, redirect with token_exchange_failed."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)

        async def bad_post(*args, **kwargs):
            raise Exception("Network error")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = bad_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=bad&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        assert "token_exchange_failed" in resp.headers["location"]

    async def test_new_user_created_on_first_oauth_login(self, app_client):
        """A brand-new Google user is created in the DB and gets a JWT."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)
        new_email = "brand-new-google-user@example.com"
        userinfo = self._make_google_userinfo(email=new_email, google_id="111222333")

        with patch("httpx.AsyncClient", return_value=self._mock_google_http(userinfo)):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=goodcode&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "oauth-callback" in location
        assert "token=" in location
        assert "error" not in location

    async def test_existing_email_user_gets_linked(self, app_client):
        """If email already exists (password-based), Google account is linked."""
        import routers.oauth as oauth_module

        # Register a password-based account first
        email = "link-target@example.com"
        await app_client.post(
            "/api/auth/register",
            json={"email": email, "password": "ExistingPass99!"},
        )

        state = secrets.token_urlsafe(16)
        userinfo = self._make_google_userinfo(email=email, google_id="444555666")

        with patch("httpx.AsyncClient", return_value=self._mock_google_http(userinfo)):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=goodcode&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "token=" in location
        assert "error" not in location

    async def test_returning_google_user_gets_jwt(self, app_client):
        """A previously linked Google user gets a JWT on subsequent logins."""
        import routers.oauth as oauth_module

        google_id = "777888999000"
        email = "returning-oauth@example.com"
        userinfo = self._make_google_userinfo(email=email, google_id=google_id)

        # First login — creates user
        state = secrets.token_urlsafe(16)
        with patch("httpx.AsyncClient", return_value=self._mock_google_http(userinfo)):
            await app_client.get(
                f"/api/auth/google/callback?code=code1&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        # Second login — recognizes existing google_id
        state2 = secrets.token_urlsafe(16)
        with patch("httpx.AsyncClient", return_value=self._mock_google_http(userinfo)):
            resp2 = await app_client.get(
                f"/api/auth/google/callback?code=code2&state={state2}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state2},
            )

        assert resp2.status_code == 302
        assert "token=" in resp2.headers["location"]
        assert "error" not in resp2.headers["location"]

    async def test_missing_email_in_userinfo_redirects_with_error(self, app_client):
        """If Google returns no email, redirect with missing_user_info."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)
        bad_userinfo = {"id": "123", "picture": "https://example.com/pic.jpg"}  # no email

        with patch("httpx.AsyncClient", return_value=self._mock_google_http(bad_userinfo)):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=code&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        assert "missing_user_info" in resp.headers["location"]

    async def test_missing_google_id_in_userinfo_redirects_with_error(self, app_client):
        """If Google returns no user id, redirect with missing_user_info."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)
        bad_userinfo = {"email": "noid@example.com", "picture": None}  # no id

        with patch("httpx.AsyncClient", return_value=self._mock_google_http(bad_userinfo)):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=code&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        assert "missing_user_info" in resp.headers["location"]

    async def test_refresh_token_cookie_is_set_on_success(self, app_client):
        """On successful OAuth login, a refresh_token cookie is set."""
        import routers.oauth as oauth_module

        state = secrets.token_urlsafe(16)
        userinfo = self._make_google_userinfo(
            email="refresh-cookie@example.com", google_id="201020102010"
        )

        with patch("httpx.AsyncClient", return_value=self._mock_google_http(userinfo)):
            resp = await app_client.get(
                f"/api/auth/google/callback?code=code&state={state}",
                follow_redirects=False,
                cookies={oauth_module.STATE_COOKIE_NAME: state},
            )

        assert resp.status_code == 302
        assert "refresh_token" in resp.cookies


# =========================================================================
# Mailer unit tests — mailer.py
# =========================================================================


class TestMailer:
    """Tests for mailer.py pure-function behaviour."""

    def test_dev_mode_returns_true_without_smtp(self):
        """In dev mode (no SMTP_HOST), send returns True without connecting."""
        import mailer

        original = mailer.SMTP_HOST
        mailer.SMTP_HOST = ""
        try:
            result = mailer.send_password_reset_email("user@test.com", "raw-token-xyz")
        finally:
            mailer.SMTP_HOST = original

        assert result is True

    def test_smtp_error_returns_false(self):
        """When SMTP connection raises, send returns False."""
        import smtplib
        import mailer

        original = mailer.SMTP_HOST
        mailer.SMTP_HOST = "smtp.example.com"  # non-empty → tries real send
        try:
            with patch("smtplib.SMTP", side_effect=smtplib.SMTPConnectError(421, "fail")):
                result = mailer.send_password_reset_email("user@test.com", "tok")
        finally:
            mailer.SMTP_HOST = original

        assert result is False

    def test_reset_url_contains_token(self):
        """Dev-mode log includes the raw token in the reset URL."""
        import mailer

        original_host = mailer.SMTP_HOST
        original_base = mailer.APP_BASE_URL
        mailer.SMTP_HOST = ""
        mailer.APP_BASE_URL = "http://localhost:8501"

        raw_token = "test-token-abc123"
        with patch.object(mailer.logger, "info") as mock_log:
            mailer.send_password_reset_email("user@test.com", raw_token)

        finally_restore = lambda: None  # noqa
        mailer.SMTP_HOST = original_host
        mailer.APP_BASE_URL = original_base

        logged_msg = " ".join(str(a) for a in mock_log.call_args[0])
        assert raw_token in logged_msg

    def test_is_smtp_configured_false_for_empty_host(self):
        """_is_smtp_configured returns False when SMTP_HOST is empty."""
        import mailer

        original = mailer.SMTP_HOST
        mailer.SMTP_HOST = ""
        try:
            assert mailer._is_smtp_configured() is False
        finally:
            mailer.SMTP_HOST = original

    def test_is_smtp_configured_false_for_localhost(self):
        """_is_smtp_configured returns False when SMTP_HOST is 'localhost'."""
        import mailer

        original = mailer.SMTP_HOST
        mailer.SMTP_HOST = "localhost"
        try:
            assert mailer._is_smtp_configured() is False
        finally:
            mailer.SMTP_HOST = original

    def test_is_smtp_configured_true_for_real_host(self):
        """_is_smtp_configured returns True for a real hostname."""
        import mailer

        original = mailer.SMTP_HOST
        mailer.SMTP_HOST = "smtp.sendgrid.net"
        try:
            assert mailer._is_smtp_configured() is True
        finally:
            mailer.SMTP_HOST = original
