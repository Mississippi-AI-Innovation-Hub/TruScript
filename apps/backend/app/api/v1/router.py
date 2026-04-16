"""
API v1 router — aggregates all v1 endpoint routers into a single prefix.
Add new feature routers here as the system grows.
"""
from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.transcripts import router as transcripts_router
from app.api.v1.endpoints.upload import router as upload_router
from app.api.v1.endpoints.workflow import router as workflow_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(auth_router)
# upload router MUST be included before transcripts to avoid path conflicts
api_v1_router.include_router(upload_router)
api_v1_router.include_router(transcripts_router)
api_v1_router.include_router(workflow_router)
