import re
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import RedirectResponse
from typings.log import inject_log
from util.user import get_user_name
from util.object_id import (
    get_current_user,
    validate_object_id,
)
from datetime import datetime
from database import db
from pydantic import BaseModel
from util.validation import validate_activity_name

router = APIRouter()


async def create_activity():
    pass


class PutDescription(BaseModel):
    description: str


async def change_activity_description(
    activity_oid: str,
    payload: PutDescription,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    pass


class PutActivityName(BaseModel):
    name: str


async def change_activity_title(
    activity_oid: str,
    payload: PutActivityName,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    pass


class PutActivityStatus(BaseModel):
    status: str


@router.put("/{activity_oid}/status")
async def change_activity_status(
    activity_oid: str,
    payload: PutActivityStatus,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    pass


async def read_activities(
    type: str | None,
    mode: str | None,
    page: int = -1,
    perpage: int = 10,
    query: str = "",
    user=Depends(get_current_user),
):
    pass


async def read_activity(activity_oid: str, user=Depends(get_current_user)):
    pass


class PutActivityDuration(BaseModel):
    duration: float


async def update_activity_member_duration(
    activity_oid: str,
    uid: str,
    payload: PutActivityDuration,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    pass


async def user_activity_signup():
    pass


async def read_activity_user(
    activity_oid: str, uid: str, user=Depends(get_current_user)
):
    pass


async def user_activity_signoff():
    pass


async def delete_activity():
    pass


