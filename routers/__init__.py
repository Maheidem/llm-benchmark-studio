"""Router package -- one APIRouter per domain, included by app.py."""

from routers.auth import router as auth_router
from routers.config import router as config_router
from routers.env import router as env_router
from routers.keys import router as keys_router
from routers.onboarding import router as onboarding_router
from routers.admin import router as admin_router
from routers.jobs import router as jobs_router
from routers.settings import router as settings_router
from routers.websocket import router as websocket_router
from routers.benchmark import router as benchmark_router
from routers.discovery import router as discovery_router
from routers.tool_eval import router as tool_eval_router
from routers.param_tune import router as param_tune_router
from routers.prompt_tune import router as prompt_tune_router
from routers.judge import router as judge_router
from routers.experiments import router as experiments_router
from routers.mcp import router as mcp_router
from routers.analytics import router as analytics_router
from routers.schedules import router as schedules_router
from routers.export_import import router as export_import_router
from routers.profiles import router as profiles_router
from routers.oauth import router as oauth_router
from routers.prompt_versions import router as prompt_versions_router
from routers.leaderboard import router as leaderboard_router
from routers.providers import router as providers_router

all_routers = [
    auth_router,
    config_router,
    env_router,
    keys_router,
    onboarding_router,
    admin_router,
    jobs_router,
    settings_router,
    websocket_router,
    benchmark_router,
    discovery_router,
    tool_eval_router,
    param_tune_router,
    prompt_tune_router,
    judge_router,
    experiments_router,
    mcp_router,
    analytics_router,
    schedules_router,
    export_import_router,
    profiles_router,
    oauth_router,
    prompt_versions_router,
    leaderboard_router,
    providers_router,
]
