# PC Agent Webhook

The Windows PC application has a dedicated **Webhook** tab that acts as both:

1. an input client for `POST /v1/instructions`; and
2. a reply receiver for `agent.reply.completed`.

This is PC-side HTTP integration. The ESP32-C3 continues to send the unchanged V0 ECG, IMU, and device-status byte stream over BLE. No Webhook data is placed in the firmware stream.

## Data flow

```text
ESP32-C3 -- BLE V0 packets --> PC connection state
                                  |
                                  +-- RECEIVING gate --> Webhook instruction input

PC Webhook tab -- POST /v1/instructions --> Agent Webhook Gateway
PC reply server <-- agent.reply.completed -- Agent Webhook Gateway
```

The optional connection gate only enables ordinary instruction submission after the PC application is actually receiving valid packets. The callback listener remains independent of the device connection so delayed Agent replies are not lost.

## Same-PC quick start

The safest MVP arrangement runs the PC application and Agent Webhook Gateway on the same trusted Windows computer.

Configure the Gateway before starting it:

```powershell
$env:AGENT_WEBHOOK_REPLY_URL = "http://127.0.0.1:9080/agent-replies"
```

Start the PC application:

```powershell
.\tools\project.ps1 pc-gui
```

Then open the **Webhook** tab:

1. Keep `Agent Gateway` as `http://127.0.0.1:8080/v1/instructions`, unless the deployment uses another host or port.
2. Keep the callback bind address and public URL on `127.0.0.1:9080`.
3. Select **启动回调监听**.
4. Connect the ESP32-C3 from the **实时** tab and wait for the state to reach `RECEIVING`.
5. Enter one complete user request and select **发送指令**.
6. Treat HTTP `202` only as durable acceptance. Wait for the final reply row to appear.

The Gateway reads `AGENT_WEBHOOK_REPLY_URL` only at process startup. Changing the public callback URL in the PC tab does not reconfigure a running Gateway; copy the generated PowerShell environment command and restart the Gateway.

## Webhook tab settings

| Setting | Meaning |
| --- | --- |
| Agent Gateway | Exact absolute URL whose path is `/v1/instructions`. No trailing slash, query, or fragment is accepted. |
| Listener host | Local interface used by the PC callback server. Default `127.0.0.1`. |
| Port | Local callback server port. Default `9080`. |
| Path | Exact callback path. Default `/agent-replies`. |
| Gateway-accessible callback URL | URL written to `AGENT_WEBHOOK_REPLY_URL`; it must point back to the listener from the Gateway host. |
| Request timeout | Timeout for one instruction POST. |
| Require device receiving | When enabled, ordinary instructions require current `RECEIVING` state. |
| Auto-start receiver | Starts the saved callback listener when the PC GUI opens. |

The explicit **发送“停”（非物理急停）** button submits the exact text `停` and bypasses the device-receiving convenience gate. It is not a physical emergency stop, and the resulting text does not prove that the machine dog is physically stationary.

## Durable client behavior

The PC application stores Webhook state in ignored local data:

```text
data/webhook_client.sqlite3
```

For every instruction it:

1. generates a stable UUID;
2. stores the UUID and exact original text before any network request;
3. sends JSON containing only `instruction_id` and `text`;
4. marks HTTP `202` as accepted;
5. retries network errors, timeouts, and HTTP `503` with the same ID and text;
6. treats HTTP `400`, `404`, `409`, and other contract errors as terminal until the operator explicitly retries.

An interrupted `submitting` row is recovered after PC restart and retried with the same ID and text. This is safe because the Gateway contract de-duplicates an identical instruction ID and text.

## Reply receiver behavior

The local callback server:

- accepts only `POST` on the configured exact path;
- requires `Content-Type: application/json`;
- validates `agent.reply.completed`, `reply_id`, `instruction_id`, `text`, and `completed_at`;
- persists a valid reply before returning HTTP `204`;
- returns HTTP `204` for an identical duplicate `reply_id` without duplicate UI delivery;
- returns HTTP `409` if an existing ID is reused with conflicting content;
- limits callback request bodies to 64 KiB.

The instruction and reply tables are diagnostic records. Callback order is not assumed to match instruction order; association always uses `instruction_id`.

## Remote Gateway

If the Gateway runs on another computer:

1. bind the callback listener to an appropriate trusted LAN interface, such as `0.0.0.0`;
2. set the public callback URL to the PC's reachable LAN address, not `0.0.0.0`;
3. allow the selected TCP port through Windows Firewall only for the trusted network and Gateway host;
4. restart the Gateway with that public callback URL.

The MVP contract has no authentication, request signatures, timestamp validation, or replay protection. Do not expose either direction directly to the public internet.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Ordinary send button is disabled | Connect the collar and wait for `RECEIVING`, or explicitly turn off the gate. |
| Callback listener cannot start | Check whether the port is already occupied and whether the bind address exists locally. |
| HTTP 404 | The Gateway path must be exactly `/v1/instructions`, with no trailing slash or query. |
| HTTP 409 | The same instruction ID was associated with different text. Do not change text during a retry. |
| HTTP 503 or network error | The PC queues an exponential retry using the same ID and text. |
| HTTP 202 but no final reply | Verify the Gateway outbox, its configured reply URL, PC listener status, routing, and firewall. There is no Gateway status-query API. |
| Duplicate callback in logs | Identical `reply_id` events are acknowledged and de-duplicated by SQLite. |

## Validation

PC-only validation:

```powershell
.\tools\project.ps1 pc-test
```

This feature does not require a firmware build, COM21 flash, serial monitor, protocol update, or body-connected acquisition.
