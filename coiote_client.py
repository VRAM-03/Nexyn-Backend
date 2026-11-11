import os
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv() # to get the required variable values from .env file

COIOTE_BASE_URL = os.getenv("COIOTE_BASE_URL")
COIOTE_USERNAME = os.getenv("COIOTE_USERNAME")
COIOTE_PASSWORD = os.getenv("COIOTE_PASSWORD")


def _authorization_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_coiote_token() -> str:
    """Logs into Coiote and returns a short-lived access token."""
    response = httpx.post(
        f"{COIOTE_BASE_URL}/api/auth/oauth_password",
        data={
            "grant_type": "password",
            "username": COIOTE_USERNAME,
            "password": COIOTE_PASSWORD,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def create_coiote_device(
    endpoint_name: str,
    security_mode: str,
    psk_identity: str,
    psk_key: str,
    friendly_name: str | None = None,
) -> dict[str, Any]:
    """Registers a device on Coiote and returns the created device's JSON."""
    token = get_coiote_token()
    payload = {
        "connectorType": "management",
        "properties": {"endpointName": endpoint_name},
        "domain": "/main/xbrybx34/",
        "securityMode": security_mode,
        "dtlsIdentity": psk_identity,
        "dtlsPsk": {"HexadecimalPsk": psk_key},
    }
    if friendly_name is not None:
        payload["friendlyName"] = friendly_name

    response = httpx.post(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices",
        headers=_authorization_headers(token),
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def list_coiote_devices() -> list[str]:
    """Returns the list of device IDs registered on Coiote."""
    token = get_coiote_token()
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices",
        headers=_authorization_headers(token),
    )
    response.raise_for_status()
    return response.json()


def get_coiote_device_details(device_id: str) -> dict[str, Any]:
    """Returns full details for one Coiote device."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.get(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
    )
    response.raise_for_status()
    return response.json()


def update_coiote_device(
    device_id: str,
    friendly_name: str,
) -> None:
    """Updates a device's friendly name in Coiote."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.put(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
        json={"friendlyName": friendly_name},
        timeout=15.0,
    )
    response.raise_for_status()


def delete_coiote_device(device_id: str) -> None:
    """Deletes one device from Coiote."""
    token = get_coiote_token()
    encoded_id = quote(device_id, safe="")
    response = httpx.delete(
        f"{COIOTE_BASE_URL}/api/coiotedm/v3/devices/{encoded_id}",
        headers=_authorization_headers(token),
        timeout=15.0,
    )
    response.raise_for_status()
