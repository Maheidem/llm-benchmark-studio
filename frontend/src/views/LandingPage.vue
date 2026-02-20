<template>
  <div>
    <!-- Top bar -->
    <div class="landing-topbar">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-sm flex items-center justify-center font-display font-bold text-sm" style="background: var(--lime); color: #09090B;">
          B<span style="font-size:10px; opacity:0.6;">s</span>
        </div>
        <div class="font-display font-bold text-sm text-zinc-100 tracking-wide">
          BENCHMARK <span style="color: var(--lime)">STUDIO</span>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button @click="openAuth('login')" class="landing-btn-secondary" style="padding: 8px 20px; font-size: 12px;">Login</button>
        <button @click="openAuth('register')" class="landing-btn-primary" style="padding: 8px 20px; font-size: 12px; box-shadow: none;">Sign Up</button>
      </div>
    </div>

    <!-- Hero -->
    <div class="landing-hero">
      <h1 class="landing-animate">BENCHMARK <span>STUDIO</span></h1>
      <div class="landing-tagline landing-animate landing-animate-d1">Measure &middot; Compare &middot; Optimize</div>
      <p class="landing-description landing-animate landing-animate-d2">
        The all-in-one platform for benchmarking LLM providers.
        Compare throughput, latency, and cost across models in real-time.
      </p>
      <div class="landing-cta-group landing-animate landing-animate-d3">
        <button @click="openAuth('register')" class="landing-btn-primary">Get Started Free</button>
        <button @click="openAuth('login')" class="landing-btn-secondary">Login</button>
      </div>
    </div>

    <!-- Features grid -->
    <div class="landing-features">
      <div class="landing-feature-card landing-animate landing-animate-d4">
        <div class="feature-icon">&#9889;</div>
        <h3>Real-Time Benchmarks</h3>
        <p>Stream token-by-token metrics across multiple providers simultaneously. Measure TTFT and throughput live.</p>
      </div>
      <div class="landing-feature-card landing-animate landing-animate-d5">
        <div class="feature-icon">&#9881;</div>
        <h3>Tool Calling Eval</h3>
        <p>Test and score function calling accuracy with custom MCP tool suites. Grade responses automatically.</p>
      </div>
      <div class="landing-feature-card landing-animate landing-animate-d6">
        <div class="feature-icon">&#9776;</div>
        <h3>Analytics Dashboard</h3>
        <p>Leaderboards, trend analysis, and run comparisons over time. Find the best model for your workload.</p>
      </div>
      <div class="landing-feature-card landing-animate landing-animate-d7">
        <div class="feature-icon">&#8635;</div>
        <h3>Scheduled Runs</h3>
        <p>Set up recurring benchmarks to track performance changes. Get notified when metrics shift.</p>
      </div>
      <div class="landing-feature-card landing-animate landing-animate-d8">
        <div class="feature-icon">&#9729;</div>
        <h3>Multi-Provider</h3>
        <p>OpenAI, Anthropic, Google Gemini, and custom endpoints. Compare any provider side-by-side.</p>
      </div>
      <div class="landing-feature-card landing-animate landing-animate-d9">
        <div class="feature-icon">&#8681;</div>
        <h3>Export Everything</h3>
        <p>CSV exports, JSON eval data, and settings backup/restore. Full data portability.</p>
      </div>
    </div>

    <!-- How it works -->
    <div class="landing-how">
      <div class="section-title landing-animate landing-animate-d7">How It Works</div>
      <div class="landing-steps">
        <div class="landing-step landing-animate landing-animate-d8">
          <div class="step-num">01</div>
          <h4>Add Your API Keys</h4>
          <p>Configure providers and models through the settings panel</p>
        </div>
        <div class="landing-step landing-animate landing-animate-d9">
          <div class="step-num">02</div>
          <h4>Run Benchmarks</h4>
          <p>Stream results in real-time with live progress tracking</p>
        </div>
        <div class="landing-step landing-animate landing-animate-d10">
          <div class="step-num">03</div>
          <h4>Analyze &amp; Compare</h4>
          <p>Leaderboard, trends, and side-by-side model comparisons</p>
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div class="landing-footer">
      <p>Built for LLM engineers</p>
      <p v-if="appVersion" style="margin-top:8px; font-size:11px; color:#27272a;">{{ appVersion }}</p>
    </div>

    <!-- Auth Modal -->
    <AuthModal
      :visible="authModalVisible"
      :initial-mode="authMode"
      @close="authModalVisible = false"
      @authenticated="onAuthenticated"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import { useNotificationsStore } from '../stores/notifications.js'
import AuthModal from '../components/auth/AuthModal.vue'

const router = useRouter()
const authStore = useAuthStore()
const notifStore = useNotificationsStore()

const authModalVisible = ref(false)
const authMode = ref('login')
const appVersion = ref('')

function openAuth(mode) {
  authMode.value = mode
  authModalVisible.value = true
}

async function onAuthenticated() {
  authModalVisible.value = false
  notifStore.connect()
  // Check onboarding status
  try {
    const token = localStorage.getItem('auth_token')
    const res = await fetch('/api/onboarding/status', {
      headers: { 'Authorization': 'Bearer ' + token },
    })
    if (res.ok) {
      const data = await res.json()
      if (!data.completed) {
        // Navigate to benchmark first, onboarding will show as overlay
        router.push('/benchmark')
        return
      }
    }
  } catch { /* ignore */ }
  router.push('/benchmark')
}

onMounted(async () => {
  // Fetch app version (unauthenticated endpoint)
  try {
    const res = await fetch('/healthz')
    const data = await res.json()
    if (data.version) {
      appVersion.value = 'v' + data.version
    }
  } catch { /* ignore */ }
})
</script>
