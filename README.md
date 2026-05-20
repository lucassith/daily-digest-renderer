# Daily Digest Renderer

Local Home Assistant add-on that renders daily power and 24-hour battery charts with Python and matplotlib, then publishes both digests to ntfy.

## Install

1. Copy this directory to Home Assistant:

   ```text
   /addons/daily-digest-renderer
   ```

2. Copy `addons/repository.yaml` to:

   ```text
   /addons/repository.yaml
   ```

3. In Home Assistant, go to Settings > Add-ons > Add-on Store.
4. Open the overflow menu and select **Check for updates**.
5. Install **Daily Digest Renderer** from **Local add-ons**.
6. Configure:

   ```yaml
   ha_url: http://supervisor/core
   ha_token: ""
   ntfy_url: https://ntfy.3lancers.dev
   ntfy_token: "<your ntfy token>"
   ntfy_topic: heartbeat
   listen_host: 0.0.0.0
   listen_port: 8099
   ```

`ha_token` can stay empty for the add-on path. The renderer falls back to `SUPERVISOR_TOKEN`, and the add-on has `homeassistant_api: true`.

## Home Assistant Shell Command

Add this to `configuration.yaml` or your shell command include:

```yaml
shell_command:
  send_daily_digest: >-
    curl -sS -X POST http://local-daily-digest-renderer:8099/send-digest
```

If add-on DNS does not resolve from Home Assistant Core, use the Home Assistant host address and mapped port:

```yaml
shell_command:
  send_daily_digest: >-
    curl -sS -X POST http://192.168.1.71:8099/send-digest
```

Reload shell commands after editing:

```yaml
action: shell_command.reload
```

## Home Assistant Script

Replace `script.ha_daily_power_digest` sequence with:

```yaml
sequence:
  - action: shell_command.send_daily_digest
```

Keep `automation.ha_heartbeat_power_status` as the 19:00 trigger that calls `script.ha_daily_power_digest`.

## Test

From the HA terminal:

```bash
curl -sS http://local-daily-digest-renderer:8099/health
curl -sS -X POST http://local-daily-digest-renderer:8099/send-digest
```

Expected result: ntfy receives two messages on `heartbeat`:

- `HA daily power digest` with the daily power PNG.
- `HA battery 24h` with the 24-hour battery SOC PNG.
