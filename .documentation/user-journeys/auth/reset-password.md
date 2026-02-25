# Journey: Reset Forgotten Password

## Tier
high

## Preconditions
- User has an existing account
- User has access to their registered email

## Steps

### 1. Navigate to Forgot Password
- **Sees**: Login form with "Forgot Password?" link
- **Does**: Clicks "Forgot Password?"
- **Sees**: Forgot password page with email input and "Send reset link" button

### 2. Request Reset Link
- **Does**: Enters registered email address, clicks "Send reset link"
- **Backend**: `POST /api/auth/forgot-password` — generates token, sends email
- **Sees**: Success message "If that email exists, a reset link has been sent..." (same message regardless of whether email exists, to prevent email enumeration)

### 3. Open Reset Link
- **Does**: Opens email, clicks reset link (points to `/reset-password?token=...`)
- **Sees**: "Set new password" page with password and confirm password fields

### 4. Set New Password
- **Does**: Enters new password (min 8 chars), confirms it, clicks "Set new password"
- **Backend**: `POST /api/auth/reset-password` with token and new password
- **Sees**: Success: "Password updated successfully!" with "Back to login" link

### 5. Login with New Password
- **Does**: Clicks "Back to login", enters email and new password
- **Backend**: `POST /api/auth/login`
- **Sees**: Logged in, redirected to /benchmark

## Success Criteria
- Reset email sent within seconds
- Token is single-use and time-limited
- New password works immediately after reset
- Old password no longer works
- Success/failure messages don't reveal whether email exists

## Error Scenarios

### Invalid/Missing Token
- **Trigger**: Direct navigation without token, or expired token
- **Sees**: "Invalid or missing reset link"
- **Recovery**: Request a new reset link

### Expired Token
- **Trigger**: Token used after expiration period
- **Sees**: "Reset failed. The link may have expired."
- **Recovery**: Request a new reset link

### Password Mismatch
- **Trigger**: Confirm password doesn't match
- **Sees**: "Passwords do not match."
- **Recovery**: Re-enter matching passwords

### Password Too Short
- **Trigger**: Password < 8 characters
- **Sees**: "Password must be at least 8 characters."
- **Recovery**: Enter longer password

## Maps to E2E Tests
(none yet — requires email infrastructure for E2E testing)
