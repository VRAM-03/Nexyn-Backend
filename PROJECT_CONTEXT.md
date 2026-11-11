# NEXYN backend project context

Goal: Build an industry-oriented PoC backend for NEXYN's connector-based DMP.
AVSystem Coiote is the first device-management connector. Explain steps for a Python beginner and implement one feature at a time. Prioritize security, privacy, clear errors, and future extensibility.

Design direction (confirmed November 10):
- NEXYN's endpoints should leverage Coiote's available free-tier API capabilities through its connector, not drift into rebuilding provider functionality as an unrelated local-only system. Preserve the broader connector-oriented DMP/CMP platform goal.
- Use the saved capability/API lookup below before implementing a provider-backed feature; do not repeat broad free-tier research every session. Validate only missing contract details or account/device permissions for the selected feature. Recheck published plans if the account/plan changes, a request contradicts the lookup, or the user requests updated research. Do not assume every documented endpoint is free or introduce paid dependencies without approval.
- Keep NEXYN's public API independent of provider-specific details; translate through the Coiote connector. Local-only features must be explicitly identified and justified, not presented as synchronized Coiote features.
- Existing groups are local-only and are not visible to Coiote. Before extending groups, investigate Coiote group capabilities and free-tier access, then agree on ownership, mapping, and synchronization behavior. Preserve existing local groups while that decision is pending.

Coiote free-tier capability research (November 10):
- Official pricing lists Developer as limited to 10 devices and 1 user, with REST API access and remote device configuration/management operations included. "Group definition and management" is explicitly marked unavailable for Developer: https://avsystem.com/coiote-iot-device-management-platform/plans-and-pricing/
- Official introduction independently labels "Device groups*" as premium-only: https://iotdevzone.avsystem.com/Coiote_IoT_DM/introduction/
- These are published plan restrictions, not a live authorization test of this account. No authenticated group requests or remote writes were made during this research. Group endpoint paths/payloads and any account-specific exceptions have not been verified; do not infer them from search summaries.
- Pause remaining group operations and membership under the free-tier-first direction. Preserve existing local create/list behavior and rows, clearly identified as local-only; do not automatically migrate, synchronize, or delete them.
- Recommended next provider-backed feature: a single-device live LwM2M resource Read. Before implementation, verify the task/report API contract and account permissions, select a readable resource exposed by the connected Anjay device, and define asynchronous completion/error handling. Do not equate stored device metadata with a live resource read.
- Official Swagger access instructions: log in to Coiote, open the question-mark menu, then Specification > API v3: https://iotdevzone.avsystem.com/Coiote_IoT_DM/API_documentation/Overview/Using_the_API/

## Reusable Coiote device capability/API lookup

Snapshot: November 10, 2026. Public OpenAPI `info.version` was `3.21.0`, containing 126 paths. Official machine-readable reference: https://eu.iot.avsystem.cloud/apidoc/coiotedm/v3.json ; interactive reference: https://eu.iot.avsystem.cloud/apidoc/coiotedm/v3 .
All paths below are Coiote paths relative to `https://eu.iot.avsystem.cloud/api/coiotedm/v3`, NOT implemented NEXYN routes unless explicitly noted.

Evidence rules:
- "Included" below means the published Developer plan includes the capability category. The public specification documents contracts, NOT per-account entitlements. Only existing device CRUD/read successes have been verified live as described elsewhere in this file; new task, model, observation, monitoring, event and firmware operations remain account-untested.
- No authenticated requests or device commands were made for this lookup. Do not label new APIs implemented or account-verified. Check the selected feature once during implementation and record its result here for reuse.
- Preserve existing local groups and endpoints, but put ALL further NEXYN group work on hold per user direction. No automatic conversion, remote synchronization, membership extension, or deletion.

### Published Developer-plan scope

Source: https://avsystem.com/coiote-iot-device-management-platform/plans-and-pricing/
- Limits: 10 devices, 1 user; log retention 14 days; time-series retention 7 days; telemetry limit 100k requests/month. This is the published telemetry limit, NOT a verified global REST request allowance or rate limit.
- Included: single-device registration; device data model/twin; remote configuration/management; data monitoring; device communication logs; Operations Center (registration status/fleet statistics/alerts); REST API; virtual Anjay client; basic/multi-component firmware update; Fleet FOTA; Kafka and webhook event ingestion; nRF Cloud Location integration; DTLS/TLS, no-sec/PSK, DTLS session resumption/Connection ID.
- Not included: group definition/management; bulk device registration; custom LwM2M object definitions; OTA software update; detailed server logs; queue-mode enablement; time-series exporting; bootstrap server; certificate authentication; user management/multi-tenancy/SSO. Do not infer native group API access from Fleet FOTA being listed as included.
- UI features such as communication logs, alerts and registration state are not proof of a documented public API for those features. No dedicated communication-log/current-reachability API contract was established in this research.
- Generic API errors include 402 payment required, 403 forbidden, 429 too many requests, and 5xx. A generic 402 entry in OpenAPI does not mean that every endpoint requires payment. Preserve sanitized errors; do not silently substitute cached data or claim success.

### Device-centric API families and request/result mapping

| Capability | Documented Coiote request APIs | Result/read APIs and cautions |
| --- | --- | --- |
| Inventory and device records (already integrated) | `POST /devices`; `PUT /devices/{id}`; `DELETE /devices/{id}` | `GET /devices`, `GET /devices/{id}`, `GET /devices/find/details`. Records are stored metadata, not live LwM2M Read. The find/details endpoint is documented but not yet used by NEXYN. `POST /devices/batch` exists but bulk registration is excluded from Developer. |
| Resource/object definitions | None needed for retrieval | `GET /instantiatedResources/dataModelDefinition/{deviceId}` returns definitions without instance IDs; `GET /instantiatedResources/resourcesData/{deviceId}` returns definitions/values with full model paths. Optional `parameters` filters use names such as `Device,Location`. Do not represent returned values as newly read from hardware. |
| Cached model values | None needed for retrieval | `GET /cachedDataModels/{deviceId}`; optional `parameters` is a comma-separated array of dot-separated model keys (for example `Device.0.Manufacturer`); an empty list returns the entire model, a trailing dot selects sub-parameters. These are stored values, not proof of current connectivity. |
| Live Read, Write, Execute | `POST /tasks/configure/{deviceId}` with `taskDefinition.operations` | HTTP 201 returns a task ID; `GET /taskReports/{taskId}/{deviceId}` retrieves the report. Task acceptance is NOT device-operation success. Details below. |
| Task management and reports | `PUT /tasks/{id}` updates a task; `DELETE /tasks/{id}` deletes a task entity | `GET /tasks`, `GET /tasks/{id}`; `GET /taskReports`, `/taskReports/findReports`, `/taskReports/findSince`, `/taskReports/moreReports`, `/taskReports/summary`. Deleting a task is not proven to undo/cancel a device operation already executed. Pagination/filter contracts must be followed when used. |
| Resource observation | `POST /observations/device/{deviceId}/{path}` creates/updates; `DELETE` on same path cancels | `GET /observations/device/{deviceId}/{path}` and `GET /observations`. Paths are model keys, e.g. `Device.0.Current Time`. The ensureObservations task must be enabled for documented GET/DELETE behavior. POST returns an acknowledgement, NOT a stream of values. Retrieve values through model/monitoring APIs or event handlers. |
| Per-device monitoring setup | `POST /deviceMonitoring/configuration/device/{deviceId}/enable` or `/disable`; `POST /deviceMonitoring/configuration/device/{deviceId}/alias`; `DELETE /deviceMonitoring/configuration/device/{deviceId}/resourceUrl/{lwm2mUrl}` removes an alias | `GET /deviceMonitoring/configuration/device/{deviceId}/enabled` or `/aliases`. Enable/disable/alias POST success is HTTP 204. User permission is required; source guide names `monitoring.manageMonitoring`. |
| Monitoring history | None for retrieval; data must have been collected | `GET /deviceMonitoring/data/{deviceId}/alias/{alias}` or `/deviceMonitoring/data/{deviceId}/resourceUrl/{lwm2mUrl}`. Required `timeRangeStart`; optional `timeRangeEnd`, `limit` (default/max 2048). Start exclusive/end inclusive; omitted end means now. Numeric LwM2M URL example `/3303/0/5700`; verify path encoding from the spec. History retrieval is not the excluded bulk time-series export feature. |
| Push device/telemetry events | `POST /deviceEvents/handler/webhook` or `/kafka`; `PUT` on either `/{id}` updates; `DELETE /deviceEvents/handler/{id}` | `GET /deviceEvents/handler`, `GET /deviceEvents/handler/{id}`; test APIs `POST /deviceEvents/handler/test/webhook`, `/test/kafka`, `/test/existing/{id}`. Events arrive at a configured receiver. Requires a secure reachable receiver, event filtering and permission checks; do not expose credentials in payload logging. |
| Firmware file preparation | `POST /resources`, `PUT /resources/{id}/data`, `POST /resources/{id}/downloadUrl`; `DELETE /resources/{id}` | Here "resources" are server-hosted file artifacts, NOT LwM2M object resources. See firmware guide for file metadata/upload contract. |
| Single-device firmware update | `POST /tasks/upgrade/{deviceId}/{fileResourceId}` (guide flow); also documented `POST /firmwareUpdate/basic/{deviceId}`, `/firmwareUpdate/multicomponent/{deviceId}`, `/firmwareUpdate/cancel/{deviceId}` | Task-based flow uses `GET /taskReports/{taskId}/{deviceId}`. Newer firmware routes have separate contracts; do not mix their response models with task IDs without checking the selected route. Requires a supported device, compatible firmware and explicit user approval; defer actual updates. |
| Firmware configuration/campaigns (later) | `POST /firmwareUpdate/configurations`; `DELETE /firmwareUpdate/configurations/{id}`; `POST /firmwareUpdate/campaigns`; `POST /firmwareUpdate/campaigns/{id}/cancel` | `GET /firmwareUpdate/configurations`, `/firmwareUpdate/configurations/{id}`, `/firmwareUpdate/campaigns`, `/firmwareUpdate/campaigns/{id}`, `/firmwareUpdate/campaigns/{id}/devices`, `/firmwareUpdate/campaigns/{id}/statistics`. Fleet FOTA is listed as included, but account targeting rules without groups remain unverified. |

Other public API families (domains/users, groups, templates, legacy monitoring, software updates) are NOT a free-tier implementation backlog merely because they appear in OpenAPI. Group/template bulk examples and Azure integrations requiring groups must not be used as evidence for Developer access.

### Shared live operation contract (Read / Write / Execute)

Verified from the public OpenAPI specification, not from live task execution:
- Submit `POST /tasks/configure/{deviceId}?rerunIfExists=false` to request a new task rather than potentially rerunning a same-name task (default rerun behavior is true).
- Request shape: `{"taskDefinition":{"operations":[{"read":{"key":"Device.0.Manufacturer"}}],"name":"nexyn-read","batchRequests":false,"executeImmediately":true}}`.
- A Write operation uses `{"write":{"key":"<verified model key>","value":"<string value>"}}`; Execute uses `{"execute":{"key":"<verified executable model key>"}}` with optional `argumentList`. Do not use a numeric LwM2M URL as a dot-separated key without verifying mapping.
- `executeImmediately=true` executes immediately for a registered device; false waits until the device connects. Neither guarantees instant completion. Sequential operations stop after a failure unless batching changes execution behavior.
- `taskExecutionLog` options: `All`, `None`, `FailedOnly` (default). HTTP 201 returns a JSON task ID.
- Fetch `GET /taskReports/{taskId}/{deviceId}` with identifiers URL-encoded. A report contains `taskId`, `deviceId`, timestamps, `status`, `summary`, `blocking`, and `properties` name/value entries holding read values and diagnostic metadata.
- A report HTTP 404 with custom code `4041` means not executed yet OR transient reports; treat as pending, not automatically a missing device. Custom `4040` means task not found. Do not treat every 404 as pending.
- NEXYN should expose accepted/pending/succeeded/failed states explicitly, bounded polling or a separate result endpoint, sanitized errors and timeouts. Do not forward arbitrary report properties, credential-bearing resources, or upstream errors unfiltered. A completed read proves a response at that time, not perpetual online status.
- Supporting examples: https://iotdevzone.avsystem.com/Coiote_IoT_DM/API_documentation/Guides/Make_firmware_upgrade/ (includes Read, task-ID response and report example).

### Observation and telemetry notes

- Observation POST example: `{"attributes":[{"name":"pmax","value":"60"}],"createEnsureObserveIfNotExists":true}`. Allowed attributes: `pmin`, `pmax`, `gt`, `lt`, `st`; updates overwrite attributes. Observe config acceptance is not evidence that notifications have started.
- LwM2M numeric resource candidates: Device manufacturer `/3/0/0`, model `/3/0/1`, firmware version `/3/0/3`, current time `/3/0/13`; Temperature sensor value `/3303/0/5700` ONLY if the client exposes it. Verify actual instances, permissions and Coiote model-key mappings from the connected device; do not assume every Anjay demo exposes every object.
- Model/twin access can contain sensitive resources. Use explicit safe response fields and resource allowlists rather than returning the entire upstream model.
- Monitoring guide: https://iotdevzone.avsystem.com/Coiote_IoT_DM/API_documentation/Guides/Use_device_monitoring/
- Event handler guide: https://iotdevzone.avsystem.com/Coiote_IoT_DM/API_documentation/Guides/Send_data_outside_application/
- Onboarding/observation guide: https://iotdevzone.avsystem.com/Coiote_IoT_DM/API_documentation/Guides/Basic_onboarding/ . This guide includes bulk examples and an inconsistent `POST /devices/{id}` listing; use OpenAPI `PUT /devices/{id}` for device updates, matching the existing connector.

### Candidate priority order (recommendation, awaiting user selection)

1. Inspect safe device object/resource definitions and selected cached values to establish paths/operations supported by the actual client.
2. Live single-resource Read plus task/result tracking, initially manufacturer or model.
3. Observe/cancel a changing resource; retrieve latest values and monitoring history.
4. Write a safe, reversible demo resource and verify it through a subsequent Read; Execute a safe supported command only with approval.
5. Receive webhook device/telemetry events to reduce repetitive full inventory polling.
6. Single-device firmware operations only after compatibility, permissions and recovery planning; campaigns later.
Do not implement any of these until the user prioritizes. Groups remain on hold.

Environment: WSL Ubuntu, project folder ~/nexyn-backend, Python virtual environment, FastAPI/Uvicorn, SQLAlchemy, SQLite, HTTPX, python-dotenv.

Files: main.py, models.py, database.py, coiote_client.py, .env, .gitignore.
Never put real credentials in chat or source code. .env is gitignored.

Implemented:
- GET /health
- POST /groups creates a NEXYN-managed group with an ID and name, returning HTTP 201. GET /groups lists saved groups in ID order, returning an empty list when none exist. Groups are stored only in local SQLite; neither endpoint calls Coiote or modifies devices. Device membership and group update/delete are not implemented yet.
- Group names are required nonblank strings, trimmed before storage, and unique with case-sensitive matching: `Sensors` and `sensors` are different groups. Invalid names return HTTP 422; duplicate names return HTTP 409. Database initialization adds the groups table without deleting existing devices.
- November 10 group validation: all 23 unittest tests passed, including group persistence, invalid names, duplicate/case-sensitive names, no connector calls or device changes, and idempotent database initialization preserving devices and groups. Pylance syntax checks passed for main.py, models.py, and test_main.py. No live Coiote calls were made during these tests.
- On November 10 the user reported successful manual group creation/listing and error-case tests. This is user-reported verification, not an additional assistant-run test.
- POST /devices saves a local device and creates it in Coiote using Coiote POST /devices.
- POST /sync-devices refreshes existing Coiote records and imports new ones into the local database.
- GET /devices/{endpoint_name} fetches Coiote's stored device record (not a live LwM2M read) and returns it alongside NEXYN's local record.
- Device API responses use explicit response schemas that exclude PSK identity/key fields.
- Both GET /devices and GET /devices/{endpoint_name} fetch the Coiote inventory and every device's detail record on each request, then atomically refresh the database before responding. They use `Cache-Control: no-store`. GET /health is unchanged. There is no background polling; SQLite updates only when a device GET or explicit sync succeeds.
- GET /devices/{endpoint_name} keeps the `local` and `coiote_snapshot` envelope, includes `fetched_at`, and returns only allowlisted non-secret fields. The snapshot is fetched during the request, not a live LwM2M resource read. GET /devices uses the same credential-free device response schema.
- Failed HTTP/transport refreshes return sanitized HTTP 502 errors rather than cached device responses. Malformed upstream lists/details also return HTTP 502; identifier conflicts return HTTP 409. No partial refresh is committed. `coiote_error` is removed from successful detail responses.
- Sync matches by Coiote ID first and actual `properties.endpointName` second, never assuming the two identifiers are equal. Remote renames preserve local IDs; conflicts fail explicitly. Existing friendly names (including null), security mode, connector type, domain, and registration/contact timestamps refresh. Existing PSK credentials are preserved; imported records do not copy upstream credentials.
- Sync responses now contain `synced`, `updated`, and `missing_from_coiote`; `already_existed` is replaced by `updated`.
- Device responses across POST/GET/PATCH rename `status` to `onboarding_status` and add `connection_status`, `refreshed_at`, `first_registered_at`, `last_registered_at`, and `last_seen_at`. Onboarding values remain `created`, `coiote_record_created`, `coiote_registration_failed`, and `synced_from_coiote`. A locally failed/created record found in Coiote moves to `coiote_record_created`.
- `connection_status` is `never_connected` when Coiote's registration/contact timestamps are all null, `unknown` when history exists but current reachability is unavailable, or `not_in_coiote` when absent from a successfully fetched inventory. Unrefreshed POST responses have null connection state/timestamps. No `running`, `idle`, or `offline` state is guessed from contact age.
- Locally retained records absent from Coiote are not deleted automatically. Their provider-derived timestamps, connector type, and domain are cleared, and detail responses have `coiote_snapshot: null`; local identity, name, security settings, onboarding history, and credentials remain. This explicitly represents local-only records, not cached remote status.
- SQLite startup migration adds refresh columns without deleting rows. The SQLAlchemy attribute is now `onboarding_status`, but the physical legacy SQLite `status` column is retained to preserve existing data. SQLite timestamps are stored as UTC and responses include the UTC timezone.
- POST /devices rejects omitted, empty, and whitespace-only endpoint names with HTTP 422 before database writes or Coiote calls. Nonblank endpoint names are trimmed before use.
- POST /devices in `psk` mode requires a nonblank identity and a nonempty, even-length hexadecimal key (`0-9`, `a-f`, `A-F`; no spaces or `0x` prefix). Invalid requests return HTTP 422 before database writes or Coiote calls. Identity and key values are not silently trimmed or converted. Other security modes retain their existing behavior.
- Request validation errors return only `type`, `loc`, and `msg`, excluding submitted input and error context so credentials are not echoed. This intentionally changes the validation-error shape, not successful device responses.
- PSK validation checks format, not entropy or Coiote-specific byte-length limits; exact upstream limits have not been verified. Generate random 16-byte (32-hex-character) keys for tests rather than using published examples.
- November 9 validation: all five focused unittest tests passed (invalid/missing PSK fields rejected without writes/calls, valid hex preserved and response secrets filtered, blank endpoints rejected, non-PSK behavior preserved, and remote failures retained locally). Pylance syntax checks passed for `main.py` and `test_main.py`. No live Coiote requests were made.
- POST /devices passes a provided `friendly_name` through to Coiote as `friendlyName`; the field is omitted from the Coiote payload when no name is supplied.
- Coiote client helpers use `Authorization: Bearer <token>` for create, list, detail, update, and delete requests; device IDs are URL-encoded for detail/update/delete paths.
- PATCH /devices/{endpoint_name} updates `friendly_name` in Coiote first when a Coiote device ID exists, then updates the local record. Locally registered-only devices are updated locally.
- DELETE /devices/{endpoint_name} deletes from Coiote first when a Coiote device ID exists, then removes the local record. Coiote failures leave the local record unchanged.
- Validation completed: Python compilation and focused TestClient checks passed for list/detail response filtering, snapshot timestamp/allowlist, and sanitized Coiote HTTP errors. No production credentials were used.
- Earlier validation: compilation, mocked Bearer-header and URL-encoding checks for Coiote client operations, mocked PATCH/DELETE flow and remote-failure consistency checks, and focused POST validation/payload tests passed.
- November 9 refresh validation: all 20 unittest tests passed, covering repeated fresh reads, new imports, updates, null clearing, identity differences/renames/conflicts, failed-refresh atomicity, missing remote records, response filtering, UTC timestamps, PATCH/DELETE behavior, PSK validation, and idempotent legacy SQLite migration. Compilation passed; Pylance syntax requests timed out, so this round does not claim a Pylance diagnostic result.
- Live read-only Coiote inspection and live backend list/detail GETs succeeded. Four existing local IDs were retained and refresh data persisted. Local ID 2 has registration history and local ID 5 has null registration/contact timestamps. No remote device writes or commands were sent during this verification.
- On November 9 the user deleted both failed local records (empty endpoint and invalid PSK). A read-only check confirmed four local devices and zero `coiote_registration_failed` records.
- Coiote domain: /main/xbrybx34/
- A device created via the API appears in Coiote as "Never connected" until an actual LwM2M client connects.

Security/data-handling status:
- PSK secrets are still accepted by POST /devices and stored as plaintext in local SQLite; only use test credentials. Secure secret storage/encryption is required before real deployment.
- Response allowlists intentionally omit `psk_key`, `psk_identity`, and all unrecognized Coiote fields.
- Coiote GET /devices/{id} returns the stored Coiote device record, not a live LwM2M Read. A live resource read requires POST /tasks/configure/{deviceId} and checking GET /taskReports/{taskId}/{deviceId}.

Verified codebase notes:
- Runtime in the workspace virtual environment: Python 3.14.4, FastAPI 0.162.0, Pydantic 2.14.2.
- Source files are `main.py`, `models.py`, `database.py`, `coiote_client.py`, and `device_sync.py`. `test_main.py` contains unittest/TestClient regression tests using isolated in-memory databases and mocked Coiote calls. Run `venv/bin/python -m unittest test_main -v`. No dependency manifest was found at the workspace root.
- The workspace does not contain Git metadata. Searches of available session history did not find earlier Nexyn sessions, so this file is the continuity record.
- Live Coiote authentication and list/detail reads are verified. On November 10 the user reported successful live DELETE and PATCH friendly-name tests through NEXYN. These are user-reported successes, not additional assistant-run verification; live failure/recovery cases remain unverified. Do not copy credentials into chat or source.
- The verified device-detail response exposes `firstRegisterTime`, `lastRegisterTime`, `lastContactTime`, and `lastSessionTime`, but no explicit connection/status field. `last_seen_at` uses `lastContactTime`, not GET time. A separate documented Coiote interface is needed to establish current reachability. This refresh is latest Coiote metadata, not proof that a device is running.
- The PoC uses one inventory read plus one detail read per remote device for each GET/sync, including single-device GETs. The existing connector authenticates separately for its calls. Large inventories need verified pagination, batching/token reuse, and performance work before deployment.

Next:
1. The user connected local ID 2 (`nexyn-test-device-02`) with the installed Anjay demo to `coaps://eu.iot.avsystem.cloud:5684` and reported Registered in Coiote. Registration timestamps were confirmed through live reads. Verify a documented current connection-state interface before replacing `unknown` with more specific states.
2. Basic live update/delete success testing is complete based on the user's November 10 reports. Keep failure-path regression coverage; live failure/recovery cases remain unverified.
3. Group create/list and error cases passed user-reported manual testing. ALL further group work is on hold; preserve existing local groups. Use the saved device capability/API lookup above and await the user's priority selection. Task/report contracts have been researched; actual account/device access for new operations still needs a focused check when selected.
4. Address secure PSK storage, API access control, and registration recovery before real deployment.