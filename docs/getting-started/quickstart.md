# Quick Start

This guide walks you through running your first benchmark after [installation](installation.md).

## 1. Register and Log In

Open `http://localhost:8501` in your browser. You will see the login screen.

1. Click **Register** to create a new account
2. Enter your email and a password (minimum 8 characters)
3. The first user is automatically promoted to admin

<!-- Screenshot: Login/registration screen -->

## 2. Add API Keys

After registering, the onboarding wizard guides you through adding API keys.

You can add keys for any provider in your configuration:

1. Navigate to the API Keys section
2. Select a provider (e.g., OpenAI, Anthropic, Google Gemini)
3. Paste your API key
4. Click Save

Keys are encrypted with Fernet symmetric encryption and stored per-user in the database. Each user manages their own keys independently.

!!! note "Key Priority"
    Per-user keys take priority over global environment variables. Admin-set `.env` keys serve as fallbacks.

## 3. Run a Benchmark

1. Navigate to the **Benchmark** screen
2. Select one or more models from the provider list
3. Configure parameters:
    - **Prompt**: The text to send to each model (or select a template)
    - **Runs**: Number of iterations per model (1-20, default 3)
    - **Max Tokens**: Maximum output tokens (1-16384, default 512)
    - **Temperature**: Sampling temperature (0.0-2.0, default 0.7)
    - **Context Tiers**: Token counts for context window testing (default: [0])
    - **Warmup**: Enable/disable warmup run (discarded, reduces cold-start variance)
4. Click **Run Benchmark**

Results stream in real-time via WebSocket:

- **Progress**: Shows current run number, model, and context tier
- **Results**: Tokens/sec, TTFT, total time, cost per individual run
- **Summary**: Aggregated statistics across all runs
- **Process Tracker**: Active jobs appear in the notification area, so you can navigate away and return without losing progress

<!-- Screenshot: Benchmark results streaming in -->

## 4. View Results

After the benchmark completes:

- **Results Table**: Shows per-model averages for tokens/sec, TTFT, cost
- **Charts**: Visual comparison across models (bar charts, scatter plots)
- **History**: All runs are saved and accessible from the History page

!!! tip "SPA Navigation"
    LLM Benchmark Studio is a Vue 3 single-page application. Navigation between pages (Benchmark, History, Analytics, etc.) is instant -- there are no full page reloads.

## 5. Run a Tool Eval

Tool calling evaluation tests whether models correctly use function calling.

1. Navigate to the **Tool Eval** screen
2. Create a tool suite or import one from JSON
3. Define tools using OpenAI function calling schema
4. Add test cases (prompt + expected tool + expected parameters)
5. Select models and click **Run Eval**

Each test case scores:

- **Tool Selection**: Did the model call the correct tool? (0% or 100%)
- **Parameter Accuracy**: Did the model pass the correct parameters? (0-100%)
- **Overall Score**: Weighted combination (60% tool + 40% params)

See [Tool Calling Evaluation](../guide/tool-eval.md) for the full guide.

## 6. Explore Analytics

The Analytics page has three tabs:

- **Leaderboard**: Models ranked by tokens/sec (benchmark) or overall score (tool eval), filterable by time period (7d, 30d, 90d, all)
- **Compare**: Select 2-4 benchmark runs for side-by-side comparison with charts
- **Trends**: Track model performance (tokens/sec and TTFT) over time with multi-model selection

## Next Steps

- [Configuration](configuration.md) -- Customize providers, models, and defaults
- [Tool Calling Evaluation](../guide/tool-eval.md) -- Deep dive into tool eval
- [Judge System](../guide/judge.md) -- Use LLMs to evaluate other LLMs
- [API Reference](../api/rest.md) -- Integrate via REST API
