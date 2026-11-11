from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from typing import Optional
from datetime import datetime, timezone
import httpx
import re

from database import engine, SessionLocal, Base, initialize_database
from device_sync import coiote_request_error, refresh_devices
from models import Device, Group
from coiote_client import (
    create_coiote_device,
    delete_coiote_device,
    update_coiote_device,
)

initialize_database(engine)

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: item[key] for key in ("type", "loc", "msg")}
                for item in error.errors()
            ]
        },
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DeviceCreate(BaseModel):
    endpoint_name: str
    friendly_name: Optional[str] = None
    security_mode: str = "psk"
    psk_identity: Optional[str] = Field(default=None, validate_default=True, repr=False)
    psk_key: Optional[str] = Field(default=None, validate_default=True, repr=False)

    @field_validator("endpoint_name")
    @classmethod
    def normalize_endpoint_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("endpoint_name must not be blank")
        return normalized

    @field_validator("psk_identity")
    @classmethod
    def validate_psk_identity(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if info.data.get("security_mode") == "psk":
            if value is None or not value.strip():
                raise ValueError("psk_identity must not be blank in PSK mode")
        return value

    @field_validator("psk_key")
    @classmethod
    def validate_psk_key(
        cls, value: Optional[str], info: ValidationInfo
    ) -> Optional[str]:
        if info.data.get("security_mode") == "psk":
            if value is None or not value:
                raise ValueError("psk_key is required in PSK mode")
            if re.fullmatch(r"[0-9a-fA-F]+", value) is None or len(value) % 2:
                raise ValueError(
                    "psk_key must contain an even number of hexadecimal characters "
                    "(0-9, a-f, A-F), with no spaces or 0x prefix"
                )
        return value


class DeviceUpdate(BaseModel):
    friendly_name: str = Field(min_length=1)


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    endpoint_name: str
    friendly_name: Optional[str] = None
    security_mode: Optional[str] = None
    coiote_device_id: Optional[str] = None
    onboarding_status: Optional[str] = None
    connection_status: Optional[str] = None
    refreshed_at: Optional[datetime] = None
    first_registered_at: Optional[datetime] = None
    last_registered_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @field_validator(
        "refreshed_at", "first_registered_at", "last_registered_at",
        "last_seen_at", "created_at"
    )
    @classmethod
    def restore_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class CoioteDeviceSnapshot(BaseModel):
    fetched_at: datetime
    device_id: Optional[str] = None
    endpoint_name: Optional[str] = None
    friendly_name: Optional[str] = None
    connector_type: Optional[str] = None
    domain: Optional[str] = None
    security_mode: Optional[str] = None


class DeviceDetailsResponse(BaseModel):
    local: DeviceResponse
    coiote_snapshot: Optional[CoioteDeviceSnapshot] = None


class GroupCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/groups", response_model=GroupResponse, status_code=201)
def create_group(group: GroupCreate, db: Session = Depends(get_db)):
    new_group = Group(name=group.name)
    db.add(new_group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(Group).filter(Group.name == group.name).first() is not None:
            raise HTTPException(status_code=409, detail="Group name already exists")
        raise
    db.refresh(new_group)
    return new_group


@app.get("/groups", response_model=list[GroupResponse])
def list_groups(db: Session = Depends(get_db)):
    return db.query(Group).order_by(Group.id).all()


@app.post("/devices", response_model=DeviceResponse)
def create_device(device: DeviceCreate, db: Session = Depends(get_db)):
    new_device = Device(
        endpoint_name=device.endpoint_name,
        friendly_name=device.friendly_name,
        security_mode=device.security_mode,
        psk_identity=device.psk_identity,
        psk_key=device.psk_key,
        onboarding_status="created",
    )
    db.add(new_device)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="endpoint_name already exists")
    db.refresh(new_device)

    try:
        coiote_response = create_coiote_device(
            endpoint_name=device.endpoint_name,
            friendly_name=device.friendly_name,
            security_mode=device.security_mode,
            psk_identity=device.psk_identity,
            psk_key=device.psk_key,
        )
        new_device.coiote_device_id = coiote_response.get("id")
        new_device.onboarding_status = "coiote_record_created"
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        new_device.onboarding_status = "coiote_registration_failed"
        db.commit()
        error = coiote_request_error(e)
        error.detail = f"Saved locally, but {error.detail}"
        raise error

    db.commit()
    db.refresh(new_device)
    return new_device

@app.post("/sync-devices")
def sync_devices(db: Session = Depends(get_db)):
    result = refresh_devices(db)
    return {
        "synced": result.synced,
        "updated": result.updated,
        "missing_from_coiote": result.missing_from_coiote,
    }

@app.get("/devices/{endpoint_name}", response_model=DeviceDetailsResponse)
def get_device(endpoint_name: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = refresh_devices(db)
    device = next(
        (item for item in result.devices if item.endpoint_name == endpoint_name), None
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "local": device,
        "coiote_snapshot": None if device.connection_status == "not_in_coiote" else {
            "fetched_at": device.refreshed_at.replace(tzinfo=timezone.utc),
            "device_id": device.coiote_device_id,
            "endpoint_name": device.endpoint_name,
            "friendly_name": device.friendly_name,
            "connector_type": device.connector_type,
            "domain": device.domain,
            "security_mode": device.security_mode,
        },
    }


@app.get("/devices", response_model=list[DeviceResponse])
def list_devices(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return refresh_devices(db).devices


@app.patch("/devices/{endpoint_name}", response_model=DeviceResponse)
def update_device(
    endpoint_name: str,
    update: DeviceUpdate,
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.coiote_device_id:
        try:
            update_coiote_device(device.coiote_device_id, update.friendly_name)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise coiote_request_error(e)

    device.friendly_name = update.friendly_name
    db.commit()
    db.refresh(device)
    return device


@app.delete("/devices/{endpoint_name}", status_code=204)
def delete_device(endpoint_name: str, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.endpoint_name == endpoint_name).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.coiote_device_id:
        try:
            delete_coiote_device(device.coiote_device_id)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            raise coiote_request_error(e)

    db.delete(device)
    db.commit()
    return Response(status_code=204)