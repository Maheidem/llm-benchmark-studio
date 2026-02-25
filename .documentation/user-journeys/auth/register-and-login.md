# Journey: Register and Login

## Tier
critical

## Preconditions
- App is running and accessible
- User has a valid email address

## Steps

### 1. View Landing Page
- **Sees**: Hero section with "Benchmark Studio" title, "Measure · Compare · Optimize" tagline, feature cards, "Get Started Free" and "Login" buttons
- **Does**: Clicks "Get Started Free" (to register) or "Login" (to sign in)
- **Backend**: `GET /healthz` — loads app version

### 2a. Register (new user)
- **Sees**: Auth modal in "Register" mode with email, password, confirm password fields
- **Does**: Enters email, password (min 8 chars), confirms password. Clicks "Create Account"
- **Backend**: `POST /api/auth/register` — creates user, returns access_token + refresh_token (HttpOnly cookie)
- **Sees**: Modal closes, redirected to /benchmark

### 2b. Login (existing user)
- **Sees**: Auth modal in "Login" mode with email and password fields
- **Does**: Enters email and password. Clicks "Login"
- **Backend**: `POST /api/auth/login` — validates credentials, returns access_token + refresh_token
- **Sees**: Modal closes, redirected to /benchmark

### 3. Onboarding Check
- **Backend**: `GET /api/onboarding/status` — checks if first-time user
- **Sees**: If not completed: onboarding overlay guides through API key setup. If completed: normal benchmark page
- **WebSocket**: WebSocket connection established for real-time notifications

## Success Criteria
- New user can register with valid email/password
- Existing user can log in with correct credentials
- JWT access token stored in localStorage
- Refresh token set as HttpOnly cookie
- WebSocket connection established after auth
- First-time users see onboarding flow
- Returning users go straight to benchmark page

## Error Scenarios

### Invalid Email
- **Trigger**: Malformed email address
- **Sees**: HTML5 validation error on email field
- **Recovery**: Enter valid email format

### Password Too Short
- **Trigger**: Password < 8 characters
- **Sees**: Validation error "Password must be at least 8 characters"
- **Recovery**: Enter longer password

### Passwords Don't Match (Register)
- **Trigger**: Confirm password doesn't match password
- **Sees**: "Passwords do not match" error
- **Recovery**: Re-enter matching passwords

### Email Already Exists (Register)
- **Trigger**: Email already registered
- **Sees**: Error "Email already registered"
- **Recovery**: Use different email or login instead

### Wrong Credentials (Login)
- **Trigger**: Incorrect email/password
- **Sees**: Error "Invalid credentials"
- **Recovery**: Check email/password, or use "Forgot Password"

### Rate Limited
- **Trigger**: 5+ failed login attempts in 5 minutes
- **Sees**: HTTP 429 "Too many attempts. Try again in 15 minutes."
- **Recovery**: Wait 15 minutes before retrying

## Maps to E2E Tests
- `e2e/tests/auth/registration.spec.js` — Registration flow
- `e2e/tests/auth/login-logout.spec.js` — Login + logout + header state
- `e2e/tests/auth/onboarding.spec.js` — Landing page + auth modal + onboarding
- `e2e/tests/auth/error-states.spec.js` — Wrong password, duplicate, validation
