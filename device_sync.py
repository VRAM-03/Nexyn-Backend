from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from coiote_client import get_coiote_device_details, list_coiote_devices
from models import Device


def coiote_request_error(error: httpx.HTTPStatusError | httpx.RequestError) -> HTTPException:
    if isinstance(error, httpx.HTTPStatusError):
        detail = f"Coiote request failed (HTTP {error.response.status_code})"
    else:
        detail = "Coiote request could not be completed"
    return HTTPException(status_code=502, detail=detail)


class EndpointProperties(BaseModel):
    model_config = ConfigDict(strict=True)

    endpoint_name: str = Field(alias="endpointName", min_length=1)

    @field_validator("endpoint_name")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Coiote endpoint name must not be blank")
        return value


class CoioteRecord(BaseModel):
    id: str = Field(min_length=1, strict=True)
    properties: EndpointProperties
    friendly_name: str | None = Field(default=None, alias="friendlyName", strict=True)
    security_mode: str = Field(alias="securityMode", min_length=1, strict=True)
    connector_type: str = Field(alias="connectorType", min_length=1, strict=True)
    domain: str = Field(min_length=1, strict=True)
    first_registered_at: datetime | None = Field(alias="firstRegisterTime")
    last_registered_at: datetime | None = Field(alias="lastRegisterTime")
    last_seen_at: datetime | None = Field(alias="lastContactTime")

    @field_validator("first_registered_at", "last_registered_at", "last_seen_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("Coiote timestamps must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None


@dataclass
class RefreshResult:
    devices: list[Device]
    synced: list[str]
    updated: list[str]
    missing_from_coiote: list[str]


def refresh_devices(db: Session) -> RefreshResult:
    try:
        ids = list_coiote_devices()
        if (
            not isinstance(ids, list)
            or not all(isinstance(item, str) and item for item in ids)
            or len(ids) != len(set(ids))
        ):
            raise HTTPException(status_code=502, detail="Coiote returned an invalid device list")
        records = []
        endpoints = set()
        for device_id in ids:
            record = CoioteRecord.model_validate(get_coiote_device_details(device_id))
            if record.id != device_id or record.properties.endpoint_name in endpoints:
                raise HTTPException(
                    status_code=502, detail="Coiote returned inconsistent device identifiers"
                )
            records.append(record)
            endpoints.add(record.properties.endpoint_name)
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        raise coiote_request_error(error) from None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(
            status_code=502, detail="Coiote returned an invalid device response"
        ) from None

    devices = db.query(Device).order_by(Device.id).all()
    by_id = {device.coiote_device_id: device for device in devices if device.coiote_device_id}
    by_endpoint = {device.endpoint_name: device for device in devices}
    matched = set()
    synced = []
    updated = []
    checked_at = datetime.now(timezone.utc)
    try:
        for record in records:
            device = by_id.get(record.id)
            endpoint_device = by_endpoint.get(record.properties.endpoint_name)
            if device is not None and endpoint_device is not None and device is not endpoint_device:
                raise HTTPException(status_code=409, detail="Local device identifiers conflict")
            if device is None:
                device = endpoint_device
                if device is not None and device.coiote_device_id not in (None, "", record.id):
                    raise HTTPException(status_code=409, detail="Local device identifiers conflict")
            if device is None:
                device = Device(onboarding_status="synced_from_coiote")
                db.add(device)
                devices.append(device)
                synced.append(record.properties.endpoint_name)
            else:
                updated.append(record.properties.endpoint_name)
                if device.onboarding_status in ("created", "coiote_registration_failed"):
                    device.onboarding_status = "coiote_record_created"
            matched.add(device)
            device.endpoint_name = record.properties.endpoint_name
            device.coiote_device_id = record.id
            device.friendly_name = record.friendly_name
            device.security_mode = record.security_mode
            device.connector_type = record.connector_type
            device.domain = record.domain
            device.first_registered_at = record.first_registered_at
            device.last_registered_at = record.last_registered_at
            device.last_seen_at = record.last_seen_at
            device.refreshed_at = checked_at
            # Registration history alone cannot establish current reachability.
            device.connection_status = (
                "never_connected"
                if all(value is None for value in (
                    record.first_registered_at, record.last_registered_at, record.last_seen_at
                ))
                else "unknown"
            )
        missing = []
        for device in devices:
            if device not in matched:
                missing.append(device.endpoint_name)
                device.connection_status = "not_in_coiote"
                device.refreshed_at = checked_at
                device.first_registered_at = None
                device.last_registered_at = None
                device.last_seen_at = None
                device.connector_type = None
                device.domain = None
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Local device identifiers conflict") from None
    except HTTPException:
        db.rollback()
        raise
    return RefreshResult(devices, synced, updated, missing)
