"""
API v1 router — aggregates all v1 endpoint routers into a single prefix.
Add new feature routers here as the system grows.
"""
from fastapi import APIRouter

from app.api.v1.endpoints.transcripts import router as transcripts_router
from app.api.v1.endpoints.workflow import router as workflow_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(transcripts_router)
api_v1_router.include_router(workflow_router)
