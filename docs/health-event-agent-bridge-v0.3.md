# Health Event to RDK Agent Bridge V0.3

## Roles

| Component | Responsibility |
| --- | --- |
| Windows smart-neckband PC | Sensor owner, derived metrics, local rules, SQLite, signed Health Webhook sender, MCP server |
| RDK Agent Gateway | HMAC receiver, durable queue, notification/event idempotency, trusted Agent instruction template |
| RDK Agent | Calls the three Health MCP tools when capability mapping requests recent metrics |
| Robot MCP | Separate RDK-owned physical capability with its own safety and deduplication rules |

## Network direction

```text
Windows -> RDK: POST /v1/health-events
RDK -> Windows: Streamable HTTP MCP /mcp
RDK -> Windows: Authorization: Bearer <token>
```

ASR text remains a different Windows-to-RDK endpoint:

```text
POST /v1/instructions
```

ASR text is user input. A Health Webhook is a signed system event and must be
wrapped in a trusted event template rather than treated as raw user text.

## Gateway processing

1. Validate method, path, content type, length, timestamp, key ID, HMAC, JSON
   schema, wearer, and notification header/body equality.
2. Atomically persist the notification and event queue decision.
3. Return `202 accepted` or `202 duplicate`; return `409` for the same
   notification ID with different raw bytes.
4. Ignore/record an event revision less than or equal to the highest queued
   revision for that event ID.
5. Convert a queued event to a trusted Health instruction.
6. When `health.inspect_recent_metrics` is recommended, call exactly:

```text
health.get_heart_rate({"window_s": evidence.recommended_window_s})
health.get_hrv({"window_s": evidence.recommended_window_s})
health.get_imu_state({"window_s": evidence.recommended_window_s})
```

7. Generate a calm, non-diagnostic response. MCP failure must not be interpreted
   as a health abnormality.
8. Deliver the final text through the existing `agent.reply.completed` outbox.
   Retrying that callback must not rerun the Agent or MCP tools.

## RDK MCP client requirements

- MCP protocol revision compatible with the pinned Windows SDK baseline.
- Remote Streamable HTTP support.
- Ability to attach a fixed `Authorization: Bearer ...` header.
- Configurable connect/read timeout and bounded reconnect behavior.
- No automatic retry of robot tools when a Health MCP call or Agent run fails.
- Preserve unknown event types, evidence fields, and capabilities in the Agent
  context without guessing mappings.

## Safety gates

Replay, synthetic, or `test_mode=true` events may exercise the Agent/MCP path
but must never trigger a real robot action. A live event still does not itself
authorize movement; physical capability mapping and safety remain RDK-owned.

The Webhook, MCP values, and Agent response are engineering information, not a
medical diagnosis or emergency service.
