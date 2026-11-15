from fastapi import APIRouter
from src.routers.thinktogether import course_generation

from src.routers.thinktogether import billing, learning, rooms, workspace, notifications, facilitator, support, community, usage, content_generation

router = APIRouter(prefix='/tt', tags=['thinktogether'])
router.include_router(workspace.router)
router.include_router(learning.router)
router.include_router(rooms.router)
router.include_router(billing.router)
router.include_router(notifications.router)
router.include_router(facilitator.router)
router.include_router(support.router)
router.include_router(community.router)
router.include_router(usage.router)
router.include_router(content_generation.router)
router.include_router(course_generation.router)
