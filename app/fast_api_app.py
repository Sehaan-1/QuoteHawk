# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import os
from collections.abc import AsyncGenerator

from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.api.routes import close_quotehawk_api_persistence, configure_quotehawk_api
from app.api.routes import router as quotehawk_router
from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes

load_dotenv()
configured_allow_origins = os.getenv("ALLOW_ORIGINS")
allow_origins = (
    configured_allow_origins.split(",")
    if configured_allow_origins is not None and configured_allow_origins != ""
    else None
)
# Local fixture/API paths must work without Application Default Credentials.
# Cloud telemetry remains an explicit deployment-time opt-in.
otel_to_cloud = os.getenv("QUOTEHAWK_OTEL_TO_CLOUD", "false").lower() == "true"

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    try:
        await attach_a2a_routes(
            app,
            agent=root_agent,
            runner=runner,
            task_store=InMemoryTaskStore(),
            rpc_path=f"/a2a/{adk_app.name}",
        )
        yield
    finally:
        close_quotehawk_api_persistence(app)


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "quotehawk"
app.description = "Draft-only RFQ workflow and approval API for QuoteHawk"
configure_quotehawk_api(app)
app.include_router(quotehawk_router)


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
