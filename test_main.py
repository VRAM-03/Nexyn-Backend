import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import initialize_database
from models import Device, Group


class DeviceAPITestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        main.Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

        def get_test_db():
            with self.session_factory() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_test_db
        self.client = TestClient(main.app)
        self.create_patch = patch("main.create_coiote_device")
        self.create = self.create_patch.start()
        self.create.return_value = {"id": "test-endpoint"}
        self.payload = {
            "endpoint_name": "test-endpoint",
            "friendly_name": "Test device",
            "psk_identity": "test-identity",
            "psk_key": "00112233445566778899aabbccddeeff",
        }

    def tearDown(self):
        self.create_patch.stop()
        self.client.close()
        main.app.dependency_overrides.pop(main.get_db)
        self.engine.dispose()


class GroupAPITests(DeviceAPITestCase):
    def test_create_and_list_groups_are_local_and_persistent(self):
        with self.session_factory() as db:
            db.add(Device(endpoint_name="existing-device"))
            db.commit()
        with (
            patch("device_sync.list_coiote_devices") as remote_list,
            patch("device_sync.get_coiote_device_details") as remote_detail,
            patch("main.update_coiote_device") as remote_update,
            patch("main.delete_coiote_device") as remote_delete,
        ):
            self.assertEqual(self.client.get("/groups").json(), [])
            first = self.client.post("/groups", json={"name": "  Building A Sensors  "})
            self.assertEqual(first.status_code, 201)
            self.assertEqual(set(first.json()), {"id", "name"})
            self.assertIsInstance(first.json()["id"], int)
            self.assertEqual(first.json()["name"], "Building A Sensors")
            second = self.client.post("/groups", json={"name": "Building B Sensors"})
            self.assertEqual(second.status_code, 201)
            listed = self.client.get("/groups")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json(), [first.json(), second.json()])
            for remote in (self.create, remote_list, remote_detail,
                           remote_update, remote_delete):
                remote.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Group).count(), 2)
            self.assertEqual(db.get(Group, first.json()["id"]).name, "Building A Sensors")
            self.assertEqual(db.query(Device).one().endpoint_name, "existing-device")

    def test_invalid_group_names_are_rejected_without_writes(self):
        for payload in ({}, {"name": None}, {"name": ""}, {"name": " \t\n"},
                        {"name": 123}, {"name": []}):
            with self.subTest(payload=payload):
                response = self.client.post("/groups", json=payload)
                self.assertEqual(response.status_code, 422)
                for error in response.json()["detail"]:
                    self.assertEqual(set(error), {"type", "loc", "msg"})
        with self.session_factory() as db:
            self.assertEqual(db.query(Group).count(), 0)
        self.create.assert_not_called()

    def test_duplicate_names_conflict_but_capitalization_matters(self):
        self.assertEqual(
            self.client.post("/groups", json={"name": "Sensors"}).status_code, 201
        )
        for name in ("Sensors", "  Sensors  "):
            response = self.client.post("/groups", json={"name": name})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"detail": "Group name already exists"})
        self.assertEqual(
            self.client.post("/groups", json={"name": "sensors"}).status_code, 201
        )
        self.assertEqual(
            [group["name"] for group in self.client.get("/groups").json()],
            ["Sensors", "sensors"],
        )
        self.create.assert_not_called()


class DeviceCreateValidationTests(DeviceAPITestCase):
    def test_invalid_psk_is_rejected_before_writes_or_coiote_calls(self):
        invalid = {
            "psk_key": [None, "", "abc", "0x1234", "12 34", "1234\n",
                        "zz", "\uff11\uff12", "\u79d8\u5bc6", 1234],
            "psk_identity": [None, "", " \t\n", 1234],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = self.client.post(
                        "/devices", json={**self.payload, field: value}
                    )
                    self.assertEqual(response.status_code, 422)
                    errors = response.json()["detail"]
                    self.assertTrue(
                        any(item["loc"] == ["body", field] for item in errors)
                    )
                    for item in errors:
                        self.assertEqual(set(item), {"type", "loc", "msg"})
        for field in invalid:
            with self.subTest(omitted=field):
                payload = self.payload.copy()
                del payload[field]
                self.assertEqual(
                    self.client.post("/devices", json=payload).status_code, 422
                )
        self.create.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 0)

    def test_hex_keys_and_identity_are_preserved(self):
        for index, key in enumerate(("ab", "AABB", self.payload["psk_key"])):
            with self.subTest(key=key):
                endpoint = f"test-endpoint-{index}"
                self.create.return_value = {"id": endpoint}
                response = self.client.post(
                    "/devices",
                    json={**self.payload, "endpoint_name": f" {endpoint} ",
                          "psk_key": key},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["endpoint_name"], endpoint)
                self.assertEqual(response.json()["onboarding_status"], "coiote_record_created")
                self.assertNotIn("psk_key", response.json())
                self.assertNotIn("psk_identity", response.json())
                self.create.assert_called_with(
                    endpoint_name=endpoint,
                    friendly_name="Test device",
                    security_mode="psk",
                    psk_identity="test-identity",
                    psk_key=key,
                )
                with self.session_factory() as db:
                    device = db.query(Device).filter_by(endpoint_name=endpoint).one()
                    self.assertEqual(device.psk_key, key)

    def test_blank_endpoint_still_rejected(self):
        for endpoint in ("", " \t\n"):
            response = self.client.post(
                "/devices", json={**self.payload, "endpoint_name": endpoint}
            )
            self.assertEqual(response.status_code, 422)
        self.create.assert_not_called()
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 0)

    def test_non_psk_mode_keeps_existing_behavior(self):
        response = self.client.post(
            "/devices", json={"endpoint_name": "test-endpoint", "security_mode": "nosec"}
        )
        self.assertEqual(response.status_code, 200)
        self.create.assert_called_once_with(
            endpoint_name="test-endpoint", friendly_name=None,
            security_mode="nosec", psk_identity=None, psk_key=None,
        )

    def test_remote_failure_still_keeps_failed_local_record(self):
        request = httpx.Request("POST", "https://example.invalid/devices")
        self.create.side_effect = httpx.HTTPStatusError(
            "upstream secret", request=request,
            response=httpx.Response(400, request=request, text="upstream secret"),
        )
        response = self.client.post("/devices", json=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("upstream secret", response.text)
        with self.session_factory() as db:
            self.assertEqual(
                db.query(Device).one().onboarding_status, "coiote_registration_failed"
            )


class DeviceRefreshTests(DeviceAPITestCase):
    def setUp(self):
        super().setUp()
        self.list_patch = patch("device_sync.list_coiote_devices")
        self.details_patch = patch("device_sync.get_coiote_device_details")
        self.list_devices = self.list_patch.start()
        self.details = self.details_patch.start()
        self.list_devices.return_value = ["remote-id"]
        self.record = {
            "id": "remote-id",
            "properties": {"endpointName": "test-endpoint"},
            "friendlyName": "Current remote name",
            "securityMode": "psk",
            "connectorType": "management",
            "domain": "/test/",
            "firstRegisterTime": "2026-11-09T10:00:00Z",
            "lastRegisterTime": "2026-11-09T10:01:00Z",
            "lastContactTime": "2026-11-09T10:02:00Z",
            "dtlsIdentity": "upstream identity",
            "dtlsPsk": {"HexadecimalPsk": "upstream secret"},
            "unexpected": "must not appear",
        }
        self.details.return_value = self.record

    def tearDown(self):
        self.details_patch.stop()
        self.list_patch.stop()
        super().tearDown()

    def seed(self, **changes):
        values = {
            "endpoint_name": "test-endpoint",
            "coiote_device_id": "remote-id",
            "friendly_name": "Old local name",
            "security_mode": "psk",
            "onboarding_status": "coiote_record_created",
            "psk_identity": "local identity",
            "psk_key": "001122",
        }
        with self.session_factory() as db:
            device = Device(**{**values, **changes})
            db.add(device)
            db.commit()
            return device.id

    def test_list_refreshes_on_every_get_and_preserves_local_identity_and_secrets(self):
        local_id = self.seed()
        first = self.client.get("/devices")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["cache-control"], "no-store")
        device = first.json()[0]
        self.assertEqual(device["id"], local_id)
        self.assertEqual(device["friendly_name"], "Current remote name")
        self.assertEqual(device["onboarding_status"], "coiote_record_created")
        self.assertEqual(device["connection_status"], "unknown")
        self.assertEqual(device["last_seen_at"], "2026-11-09T10:02:00Z")
        self.assertIsNotNone(device["refreshed_at"])
        self.assertNotIn("status", device)
        self.assertNotIn("psk_key", device)
        self.assertNotIn("psk_identity", device)
        self.record["friendlyName"] = "Changed again"
        self.record["lastContactTime"] = "2026-11-09T10:03:00Z"
        second = self.client.get("/devices")
        self.assertEqual(second.json()[0]["friendly_name"], "Changed again")
        self.assertEqual(second.json()[0]["last_seen_at"], "2026-11-09T10:03:00Z")
        self.assertEqual(self.list_devices.call_count, 2)
        self.assertEqual(self.details.call_count, 2)
        self.details.assert_called_with("remote-id")
        with self.session_factory() as db:
            device = db.query(Device).one()
            self.assertEqual(device.id, local_id)
            self.assertEqual(device.friendly_name, "Changed again")
            self.assertEqual(device.psk_identity, "local identity")
            self.assertEqual(device.psk_key, "001122")

    def test_detail_imports_new_devices_and_only_returns_allowlisted_fields(self):
        response = self.client.get("/devices/test-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(
            body["local"]["onboarding_status"], "synced_from_coiote"
        )
        self.assertEqual(set(body), {"local", "coiote_snapshot"})
        snapshot = body["coiote_snapshot"]
        self.assertEqual(set(snapshot), {
            "fetched_at", "device_id", "endpoint_name", "friendly_name",
            "connector_type", "domain", "security_mode",
        })
        self.assertEqual(snapshot["device_id"], "remote-id")
        self.assertEqual(snapshot["fetched_at"], body["local"]["refreshed_at"])
        self.assertNotIn("upstream secret", response.text)
        self.assertNotIn("upstream identity", response.text)
        self.assertNotIn("unexpected", response.text)
        with self.session_factory() as db:
            self.assertIsNone(db.query(Device).one().psk_key)
            self.assertIsNone(db.query(Device).one().psk_identity)

    def test_sync_updates_existing_and_imports_without_using_id_as_endpoint(self):
        local_id = self.seed()
        self.list_devices.return_value = ["remote-id", "second-id"]
        second = {
            **self.record,
            "id": "second-id",
            "properties": {"endpointName": "second-endpoint"},
        }
        self.details.side_effect = [self.record, second]
        response = self.client.post("/sync-devices")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "synced": ["second-endpoint"], "updated": ["test-endpoint"],
            "missing_from_coiote": [],
        })
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 2)
            self.assertEqual(db.get(Device, local_id).friendly_name, "Current remote name")
            imported = db.query(Device).filter_by(endpoint_name="second-endpoint").one()
            self.assertEqual(imported.coiote_device_id, "second-id")

    def test_failed_refresh_returns_errors_and_does_not_commit_partial_updates(self):
        self.seed()
        self.list_devices.return_value = ["remote-id", "second-id"]
        request = httpx.Request("GET", "https://example.invalid/devices/second-id")
        failure = httpx.HTTPStatusError(
            "secret error", request=request,
            response=httpx.Response(503, request=request, text="secret error"),
        )
        for method, route in (
            ("get", "/devices"), ("get", "/devices/test-endpoint"),
            ("post", "/sync-devices"),
        ):
            with self.subTest(route=route):
                self.details.side_effect = [self.record, failure]
                response = getattr(self.client, method)(route)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.json()["detail"], "Coiote request failed (HTTP 503)"
                )
                self.assertNotIn("secret error", response.text)
                self.assertNotIn("Old local name", response.text)
                with self.session_factory() as db:
                    device = db.query(Device).one()
                    self.assertEqual(device.friendly_name, "Old local name")
                    self.assertIsNone(device.refreshed_at)

    def test_transport_failure_is_explicit(self):
        self.seed()
        self.list_devices.side_effect = httpx.ConnectError("secret connection details")
        response = self.client.get("/devices")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Coiote request could not be completed")
        self.assertNotIn("secret connection details", response.text)

    def test_invalid_payloads_fail_without_writes(self):
        invalid_records = [
            {**self.record, "id": "wrong-id"},
            {**self.record, "properties": {}},
            {**self.record, "lastContactTime": "invalid"},
            {**self.record, "lastContactTime": "2026-11-09T10:00:00"},
            {**self.record, "securityMode": None},
        ]
        missing_time = self.record.copy()
        del missing_time["firstRegisterTime"]
        invalid_records.append(missing_time)
        for record in invalid_records:
            with self.subTest(record=record):
                self.details.return_value = record
                self.assertEqual(self.client.get("/devices").status_code, 502)
                with self.session_factory() as db:
                    self.assertEqual(db.query(Device).count(), 0)

    def test_invalid_list_is_rejected(self):
        for ids in ({}, ["remote-id", "remote-id"], [1], [""]):
            with self.subTest(ids=ids):
                self.list_devices.return_value = ids
                self.assertEqual(self.client.get("/devices").status_code, 502)
        self.details.assert_not_called()

    def test_never_connected_is_not_inferred_as_offline_or_running(self):
        self.record.update(
            firstRegisterTime=None, lastRegisterTime=None, lastContactTime=None
        )
        response = self.client.get("/devices")
        self.assertEqual(response.json()[0]["connection_status"], "never_connected")
        self.assertIsNone(response.json()[0]["last_seen_at"])
        self.record["lastContactTime"] = "2026-11-09T10:00:00Z"
        response = self.client.get("/devices")
        self.assertEqual(response.json()[0]["connection_status"], "unknown")

    def test_missing_remote_record_is_explicit_and_local_record_is_preserved(self):
        local_id = self.seed(connection_status="unknown", domain="/old/")
        self.list_devices.return_value = []
        response = self.client.get("/devices/test-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["coiote_snapshot"])
        device = response.json()["local"]
        self.assertEqual(device["connection_status"], "not_in_coiote")
        self.assertEqual(device["onboarding_status"], "coiote_record_created")
        self.assertIsNotNone(device["refreshed_at"])
        with self.session_factory() as db:
            device = db.get(Device, local_id)
            self.assertIsNotNone(device)
            self.assertIsNone(device.domain)
            self.assertEqual(device.psk_key, "001122")
        self.details.assert_not_called()

    def test_unknown_endpoint_returns_404_after_refresh(self):
        response = self.client.get("/devices/missing")
        self.assertEqual(response.status_code, 404)
        self.list_devices.assert_called_once()

    def test_remote_rename_preserves_local_id_and_does_not_duplicate(self):
        local_id = self.seed()
        self.record["properties"]["endpointName"] = "renamed-endpoint"
        response = self.client.get("/devices/renamed-endpoint")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["local"]["id"], local_id)
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 1)

    def test_conflicting_local_identifiers_fail_without_changes(self):
        self.seed()
        self.seed(endpoint_name="other-endpoint", coiote_device_id="other-id")
        self.record["properties"]["endpointName"] = "other-endpoint"
        response = self.client.get("/devices")
        self.assertEqual(response.status_code, 409)
        with self.session_factory() as db:
            self.assertEqual(db.query(Device).count(), 2)
            self.assertTrue(all(d.refreshed_at is None for d in db.query(Device).all()))

    def test_remote_null_name_clears_old_name_and_offsets_are_normalized(self):
        self.seed()
        self.record["friendlyName"] = None
        self.record["lastContactTime"] = "2026-11-09T12:02:00+02:00"
        response = self.client.get("/devices")
        self.assertIsNone(response.json()[0]["friendly_name"])
        self.assertEqual(response.json()[0]["last_seen_at"], "2026-11-09T10:02:00Z")

    def test_patch_and_delete_keep_remote_first_behavior_and_renamed_response(self):
        self.seed()
        with patch("main.update_coiote_device") as update:
            response = self.client.patch(
                "/devices/test-endpoint", json={"friendly_name": "Renamed"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["onboarding_status"], "coiote_record_created")
            self.assertNotIn("status", response.json())
            update.assert_called_once_with("remote-id", "Renamed")
        with patch("main.delete_coiote_device") as delete:
            delete.side_effect = httpx.ConnectError("secret")
            self.assertEqual(
                self.client.delete("/devices/test-endpoint").status_code, 502
            )
            with self.session_factory() as db:
                self.assertEqual(db.query(Device).count(), 1)
            delete.side_effect = None
            self.assertEqual(
                self.client.delete("/devices/test-endpoint").status_code, 204
            )
            with self.session_factory() as db:
                self.assertEqual(db.query(Device).count(), 0)


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_sqlite_rows_survive_idempotent_migration(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE devices (
                        id INTEGER PRIMARY KEY, endpoint_name VARCHAR UNIQUE,
                        friendly_name VARCHAR, security_mode VARCHAR,
                        psk_identity VARCHAR, psk_key VARCHAR,
                        coiote_device_id VARCHAR, status VARCHAR, created_at DATETIME
                    )
                """))
                connection.execute(text("""
                    INSERT INTO devices (id, endpoint_name, status, psk_key)
                    VALUES (2, 'legacy-device', 'coiote_record_created', '001122')
                """))
            initialize_database(engine)
            initialize_database(engine)
            self.assertIn("groups", inspect(engine).get_table_names())
            with sessionmaker(bind=engine)() as db:
                db.add(Group(name="Migration test group"))
                db.commit()
            initialize_database(engine)
            self.assertIn(
                "connection_status",
                {column["name"] for column in inspect(engine).get_columns("devices")},
            )
            with sessionmaker(bind=engine)() as db:
                device = db.get(Device, 2)
                self.assertEqual(device.endpoint_name, "legacy-device")
                self.assertEqual(device.onboarding_status, "coiote_record_created")
                self.assertEqual(device.psk_key, "001122")
                self.assertIsNone(device.connection_status)
                self.assertEqual(db.query(Group).one().name, "Migration test group")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
