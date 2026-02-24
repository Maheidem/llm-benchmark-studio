import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/benchmark',
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/LandingPage.vue'),
    meta: { public: true },
  },
  {
    path: '/leaderboard',
    name: 'PublicLeaderboard',
    component: () => import('../views/PublicLeaderboardPage.vue'),
    meta: { public: true },
  },
  {
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: () => import('../views/ForgotPassword.vue'),
    meta: { public: true },
  },
  {
    path: '/reset-password',
    name: 'ResetPassword',
    component: () => import('../views/ResetPassword.vue'),
    meta: { public: true },
  },
  {
    path: '/oauth-callback',
    name: 'OAuthCallback',
    component: () => import('../views/OAuthCallback.vue'),
    meta: { public: true },
  },
  {
    path: '/benchmark',
    name: 'Benchmark',
    component: () => import('../views/BenchmarkPage.vue'),
  },
  {
    path: '/tool-eval',
    name: 'ToolEval',
    component: () => import('../views/ToolEvalPage.vue'),
    children: [
      { path: '', redirect: { name: 'ToolEvalSuites' } },
      { path: 'suites', name: 'ToolEvalSuites', component: () => import('../views/tool-eval/SuitesView.vue') },
      { path: 'suites/:id', name: 'ToolEvalEditor', component: () => import('../views/tool-eval/EditorView.vue') },
      { path: 'evaluate', name: 'ToolEvalEvaluate', component: () => import('../views/tool-eval/EvaluateView.vue') },
      { path: 'param-tuner', name: 'ParamTunerConfig', component: () => import('../views/tool-eval/ParamTunerConfig.vue') },
      { path: 'param-tuner/run', name: 'ParamTunerRun', component: () => import('../views/tool-eval/ParamTunerRun.vue') },
      { path: 'param-tuner/history', name: 'ParamTunerHistory', component: () => import('../views/tool-eval/ParamTunerHistory.vue') },
      { path: 'prompt-tuner', name: 'PromptTunerConfig', component: () => import('../views/tool-eval/PromptTunerConfig.vue') },
      { path: 'prompt-tuner/run', name: 'PromptTunerRun', component: () => import('../views/tool-eval/PromptTunerRun.vue') },
      { path: 'prompt-tuner/history', name: 'PromptTunerHistory', component: () => import('../views/tool-eval/PromptTunerHistory.vue') },
      { path: 'judge', name: 'JudgeHistory', component: () => import('../views/tool-eval/JudgeHistory.vue') },
      { path: 'judge/compare', name: 'JudgeCompare', component: () => import('../views/tool-eval/JudgeCompare.vue') },
      { path: 'timeline', name: 'Timeline', component: () => import('../views/tool-eval/TimelineView.vue') },
      { path: 'history', name: 'ToolEvalHistory', component: () => import('../views/tool-eval/HistoryView.vue') },
      { path: 'prompt-library', name: 'PromptLibrary', component: () => import('../views/tool-eval/PromptLibrary.vue') },
      { path: 'auto-optimize', name: 'AutoOptimize', component: () => import('../views/tool-eval/AutoOptimizeView.vue') },
    ],
  },
  {
    path: '/history',
    name: 'History',
    component: () => import('../views/HistoryPage.vue'),
  },
  {
    path: '/analytics',
    name: 'Analytics',
    component: () => import('../views/AnalyticsPage.vue'),
    children: [
      { path: '', redirect: { name: 'Leaderboard' } },
      { path: 'leaderboard', name: 'Leaderboard', component: () => import('../views/analytics/LeaderboardView.vue') },
      { path: 'compare', name: 'Compare', component: () => import('../views/analytics/CompareView.vue') },
      { path: 'trends', name: 'Trends', component: () => import('../views/analytics/TrendsView.vue') },
    ],
  },
  {
    path: '/schedules',
    name: 'Schedules',
    component: () => import('../views/SchedulesPage.vue'),
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsPage.vue'),
    children: [
      { path: '', redirect: { name: 'ApiKeys' } },
      { path: 'keys', name: 'ApiKeys', component: () => import('../views/settings/ApiKeysPanel.vue') },
      { path: 'providers', name: 'Providers', component: () => import('../views/settings/ProvidersPanel.vue') },
      { path: 'judge', name: 'JudgeSettings', component: () => import('../views/settings/JudgePanel.vue') },
      { path: 'tuning', name: 'TuningSettings', component: () => import('../views/settings/TuningPanel.vue') },
      { path: 'profiles', name: 'ProfilesSettings', component: () => import('../views/settings/ProfilesPanel.vue') },
      { path: 'leaderboard', name: 'LeaderboardSettings', component: () => import('../views/settings/LeaderboardPanel.vue') },
    ],
  },
  {
    path: '/admin',
    name: 'Admin',
    component: () => import('../views/AdminPage.vue'),
    meta: { requiresAdmin: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guard: redirect unauthenticated users to login
router.beforeEach(async (to, from, next) => {
  const token = localStorage.getItem('auth_token')

  if (to.meta?.public) {
    // OAuth callback must always be accessible (it sets the token)
    if (to.name === 'OAuthCallback') return next()
    // Public leaderboard: accessible regardless of auth status
    if (to.name === 'PublicLeaderboard') return next()
    // Other public routes: redirect to /benchmark if already logged in
    if (token) return next('/benchmark')
    return next()
  }

  // Protected routes: redirect to /login if no token
  if (!token) return next('/login')

  // Admin guard
  if (to.meta?.requiresAdmin) {
    const userStr = localStorage.getItem('user')
    let user = null
    try { user = userStr ? JSON.parse(userStr) : null } catch { /* ignore */ }
    if (!user || user.role !== 'admin') return next('/benchmark')
  }

  next()
})

export default router
