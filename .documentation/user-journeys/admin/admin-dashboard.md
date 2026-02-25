# Journey: Admin Dashboard

## Tier
medium

## Preconditions
- User is logged in with admin role (email matches ADMIN_EMAIL env var, or promoted by another admin)

## Steps

### 1. Access Admin Page
- **Sees**: "Admin Dashboard" header, system health card, tab navigation (Active Jobs, Users, Audit Log)
- **Does**: Clicks "Admin" in navigation
- **Backend**: `GET /api/admin/system` — loads system health (DB size, uptime, active jobs, WS clients). `GET /api/admin/stats` — loads usage stats (benchmarks 24h/7d/30d, top users, total users)

### 2. Monitor System Health
- **Sees**: System health card with: DB Size (MB), Results Files count, Uptime, Active Jobs count, WebSocket Clients count
- **Does**: Reviews metrics

### 3. Manage Active Jobs
- **Sees**: Jobs table with columns: Job ID, Type, User, Status, Progress, Created
- **Does**: Can cancel any job by clicking × button, confirming in dialog
- **Backend**: `GET /api/admin/jobs` — lists all active jobs (auto-refreshes every 15s). `POST /api/admin/jobs/{jobId}/cancel` — cancels job
- **Sees**: Toast "Process cancelled"

### 4. Manage Users
- **Sees**: Users table with: Email, Role, Created, Last Login, Benchmark Count, Key Count
- **Does**: Can change user role (admin/user), can delete user (cascade deletes all user data)
- **Backend**: `GET /api/admin/users` — lists all users. `PUT /api/admin/users/{userId}/role` — changes role. `DELETE /api/admin/users/{userId}` — deletes user
- **Sees**: Role badge updates, or user removed from list

### 5. Review Audit Log
- **Sees**: Audit entries table: Timestamp, User, Action, Resource, Details
- **Does**: Can filter by user, action, time range. Can paginate through entries
- **Backend**: `GET /api/admin/audit?user=&action=&since=&limit=&offset=` — loads filtered audit entries

## Success Criteria
- Only admin-role users can access the page
- System health metrics are accurate and current
- Active jobs list refreshes automatically every 15 seconds
- Job cancellation works immediately
- User role changes take effect immediately
- User deletion cascade-removes all associated data
- Audit log is searchable and filterable

## Error Scenarios

### Access Denied
- **Trigger**: Non-admin user navigates to /admin
- **Sees**: "Access Denied" message, no admin content visible
- **Recovery**: Only admins can access this page

### Cannot Change Own Role
- **Trigger**: Admin tries to demote themselves
- **Sees**: API validation error
- **Recovery**: Another admin must change your role

### Cannot Delete Self
- **Trigger**: Admin tries to delete own account
- **Sees**: API validation error
- **Recovery**: Another admin must delete your account

### Cancel Job Fails
- **Trigger**: Job already completed or doesn't exist
- **Sees**: "Failed to cancel" toast
- **Recovery**: Refresh jobs list

## Maps to E2E Tests
- `e2e/tests/admin/admin-page.spec.js` — System health, jobs, users, audit log
