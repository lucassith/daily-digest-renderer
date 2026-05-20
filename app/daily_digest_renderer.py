from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import requests


POWER_STAT_IDS = [
    "sensor.inverter_pv_power",
    "sensor.inverter_load_power",
    "sensor.inverter_grid_power",
    "sensor.inverter_battery_power",
    "sensor.weather_station_weather_station_temperature",
]

BATTERY_24H_STAT_IDS = [
    "sensor.inverter_battery",
    "sensor.inverter_battery_power",
]

ENERGY_ENTITY_IDS = [
    "sensor.inverter_today_production",
    "sensor.energy_production_today",
    "sensor.energy_production_today_remaining",
    "sensor.energy_production_tomorrow",
    "sensor.inverter_today_load_consumption",
    "sensor.inverter_today_energy_import",
    "sensor.inverter_today_energy_export",
    "sensor.inverter_today_battery_charge",
    "sensor.inverter_today_battery_discharge",
    "sensor.inverter_battery",
    "sensor.inverter_battery_state",
    "sensor.backup_last_successful_automatic_backup",
    "sensor.ups_status_data",
    "sensor.ups_battery_charge",
    "sensor.weather_station_weather_station_temperature",
]


@dataclass(frozen=True)
class Settings:
    ha_url: str
    ha_token: str
    ntfy_url: str
    ntfy_token: str
    ntfy_topic: str
    listen_host: str
    listen_port: int


@dataclass(frozen=True)
class DigestResult:
    message: str
    chart_png: bytes
    filename: str


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.session.headers.update({"Content-Type": "application/json"})

    def get_config(self) -> dict[str, Any]:
        return self._get("/api/config")

    def get_states(self) -> list[dict[str, Any]]:
        return self._get("/api/states")

    def get_statistics(
        self,
        *,
        statistic_ids: list[str],
        start_time: datetime,
        end_time: datetime,
        period: str,
        types: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        response = self._post(
            "/api/services/recorder/get_statistics?return_response",
            {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "statistic_ids": statistic_ids,
                "period": period,
                "types": types,
            },
        )
        service_response = response.get("service_response", response)
        return service_response.get("statistics", {})

    def _get(self, path: str) -> Any:
        response = self.session.get(self._url(path), timeout=30)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        response = self.session.post(self._url(path), json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path.lstrip("/"))


class NtfyClient:
    def __init__(self, base_url: str, token: str, topic: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.topic = topic.strip("/")

    def publish_png(self, *, title: str, message: str, filename: str, png: bytes) -> None:
        headers = {"Content-Type": "image/png"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        response = requests.put(
            f"{self.base_url}/{self.topic}",
            params={"title": title, "filename": filename, "message": message},
            headers=headers,
            data=png,
            timeout=60,
        )
        response.raise_for_status()


def load_settings() -> Settings:
    options = _load_options_file()
    ha_token = _option(options, "ha_token", "HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")

    return Settings(
        ha_url=_option(options, "ha_url", "HA_URL") or "http://supervisor/core",
        ha_token=ha_token,
        ntfy_url=_option(options, "ntfy_url", "NTFY_URL") or "https://ntfy.3lancers.dev",
        ntfy_token=_option(options, "ntfy_token", "NTFY_TOKEN") or "",
        ntfy_topic=_option(options, "ntfy_topic", "NTFY_TOPIC") or "heartbeat",
        listen_host=_option(options, "listen_host", "LISTEN_HOST") or "0.0.0.0",
        listen_port=int(_option(options, "listen_port", "LISTEN_PORT") or 8099),
    )


def _load_options_file() -> dict[str, Any]:
    options_path = Path(os.environ.get("ADDON_OPTIONS_PATH", "/data/options.json"))
    if not options_path.exists():
        return {}
    return json.loads(options_path.read_text(encoding="utf-8"))


def _option(options: dict[str, Any], option_name: str, env_name: str) -> str:
    value = options.get(option_name)
    if value not in (None, ""):
        return str(value)
    return os.environ.get(env_name, "")


def build_digest(settings: Settings) -> DigestResult:
    ha = HomeAssistantClient(settings.ha_url, settings.ha_token)
    ha_config = ha.get_config()
    tz = ZoneInfo(ha_config.get("time_zone") or "UTC")
    now = datetime.now(tz)
    start = datetime.combine(now.date(), time.min, tzinfo=tz)

    all_states = ha.get_states()
    state_by_id = {state["entity_id"]: state for state in all_states}
    power_stats = ha.get_statistics(
        statistic_ids=POWER_STAT_IDS,
        start_time=start,
        end_time=now,
        period="hour",
        types=["mean", "min", "max"],
    )

    message = build_message(now, power_stats, state_by_id, all_states)
    chart_png = render_chart(now, power_stats, state_by_id)
    filename = f"daily_power_digest_{now:%Y%m%d}.png"
    return DigestResult(message=message, chart_png=chart_png, filename=filename)


def build_battery_digest(settings: Settings) -> DigestResult:
    ha = HomeAssistantClient(settings.ha_url, settings.ha_token)
    ha_config = ha.get_config()
    tz = ZoneInfo(ha_config.get("time_zone") or "UTC")
    now = datetime.now(tz)
    start = now - timedelta(hours=24)

    all_states = ha.get_states()
    state_by_id = {state["entity_id"]: state for state in all_states}
    battery_stats = ha.get_statistics(
        statistic_ids=BATTERY_24H_STAT_IDS,
        start_time=start,
        end_time=now,
        period="hour",
        types=["mean", "min", "max"],
    )

    message = build_battery_message(now, battery_stats, state_by_id)
    chart_png = render_battery_chart(now, battery_stats, state_by_id)
    filename = f"battery_24h_{now:%Y%m%d_%H%M}.png"
    return DigestResult(message=message, chart_png=chart_png, filename=filename)


def build_message(
    now: datetime,
    power_stats: dict[str, list[dict[str, Any]]],
    state_by_id: dict[str, dict[str, Any]],
    all_states: list[dict[str, Any]],
) -> str:
    pv_rows = power_stats.get("sensor.inverter_pv_power", [])
    load_rows = power_stats.get("sensor.inverter_load_power", [])
    grid_rows = power_stats.get("sensor.inverter_grid_power", [])
    temp_rows = power_stats.get("sensor.weather_station_weather_station_temperature", [])

    pv_peak = max_value(pv_rows, "max")
    pv_peak_time = row_start_label(max_row(pv_rows, "max"))
    pv_avg = average_value(pv_rows, "mean")
    load_avg = average_value(load_rows, "mean")
    load_peak = max_value(load_rows, "max")
    max_import = max(0.0, max_value(grid_rows, "max"))
    max_export = abs(min(0.0, min_value(grid_rows, "min")))
    temp_current = state_value(state_by_id, "sensor.weather_station_weather_station_temperature")
    temp_min = min_value(temp_rows, "min")
    temp_max = max_value(temp_rows, "max")
    temp_avg = average_value(temp_rows, "mean")

    updates = sum(1 for state in all_states if state["entity_id"].startswith("update.") and state["state"] == "on")
    unavailable = sum(1 for state in all_states if state["state"] == "unavailable")
    problems = sum(
        1
        for state in all_states
        if state["entity_id"].startswith("binary_sensor.")
        and state["state"] == "on"
        and state.get("attributes", {}).get("device_class") == "problem"
    )

    lines = [
        f"Daily power digest {now:%Y-%m-%d %H:%M}",
        "Solar: "
        f"today={state_value(state_by_id, 'sensor.inverter_today_production')} kWh, "
        f"forecast={state_value(state_by_id, 'sensor.energy_production_today')} kWh, "
        f"remaining={state_value(state_by_id, 'sensor.energy_production_today_remaining')} kWh, "
        f"tomorrow={state_value(state_by_id, 'sensor.energy_production_tomorrow')} kWh",
        f"Solar peak: {kw(pv_peak)} kW at {pv_peak_time}, avg={kw(pv_avg)} kW",
        "Home: "
        f"today={state_value(state_by_id, 'sensor.inverter_today_load_consumption')} kWh, "
        f"avg={whole(load_avg)} W, peak={whole(load_peak)} W",
        "Grid: "
        f"bought={state_value(state_by_id, 'sensor.inverter_today_energy_import')} kWh, "
        f"sold={state_value(state_by_id, 'sensor.inverter_today_energy_export')} kWh, "
        f"max import={whole(max_import)} W, max export={whole(max_export)} W",
        "Battery: "
        f"{state_value(state_by_id, 'sensor.inverter_battery')}%, "
        f"{state_value(state_by_id, 'sensor.inverter_battery_state')}, "
        f"charge={state_value(state_by_id, 'sensor.inverter_today_battery_charge')} kWh, "
        f"discharge={state_value(state_by_id, 'sensor.inverter_today_battery_discharge')} kWh",
        "Temp: "
        f"current={temp_current} C, min={one_decimal(temp_min)} C, "
        f"max={one_decimal(temp_max)} C, avg={one_decimal(temp_avg)} C",
        "System: "
        f"unavailable={unavailable}, updates={updates}, problem_sensors={problems}, "
        f"backup={state_value(state_by_id, 'sensor.backup_last_successful_automatic_backup')}, "
        f"UPS={state_value(state_by_id, 'sensor.ups_status_data')} "
        f"{state_value(state_by_id, 'sensor.ups_battery_charge')}%",
    ]
    return "\n".join(lines)


def build_battery_message(
    now: datetime,
    battery_stats: dict[str, list[dict[str, Any]]],
    state_by_id: dict[str, dict[str, Any]],
) -> str:
    soc_rows = battery_stats.get("sensor.inverter_battery", [])
    power_rows = battery_stats.get("sensor.inverter_battery_power", [])
    max_charge_power = abs(min(0.0, min_value(power_rows, "min")))
    max_discharge_power = max(0.0, max_value(power_rows, "max"))
    min_soc = min_value(soc_rows, "min")
    max_soc = max_value(soc_rows, "max")
    avg_soc = average_value(soc_rows, "mean")
    min_soc_time = row_start_label(min_row(soc_rows, "min"))
    max_soc_time = row_start_label(max_row(soc_rows, "max"))

    lines = [
        f"Battery 24h digest {now:%Y-%m-%d %H:%M}",
        "Battery: "
        f"current={state_value(state_by_id, 'sensor.inverter_battery')}%, "
        f"state={state_value(state_by_id, 'sensor.inverter_battery_state')}",
        f"SOC 24h: min={one_decimal(min_soc)}% at {min_soc_time}, "
        f"avg={one_decimal(avg_soc)}%, max={one_decimal(max_soc)}% at {max_soc_time}",
        f"Power 24h: max charge={whole(max_charge_power)} W, max discharge={whole(max_discharge_power)} W",
        "Energy today: "
        f"charge={state_value(state_by_id, 'sensor.inverter_today_battery_charge')} kWh, "
        f"discharge={state_value(state_by_id, 'sensor.inverter_today_battery_discharge')} kWh",
    ]
    return "\n".join(lines)


def render_chart(
    now: datetime,
    power_stats: dict[str, list[dict[str, Any]]],
    state_by_id: dict[str, dict[str, Any]],
) -> bytes:
    pv = series(power_stats, "sensor.inverter_pv_power", scale=1000.0)
    load = series(power_stats, "sensor.inverter_load_power", scale=1000.0)
    grid = series(power_stats, "sensor.inverter_grid_power", scale=1000.0)
    battery = series(power_stats, "sensor.inverter_battery_power", scale=1000.0)
    temp = series(power_stats, "sensor.weather_station_weather_station_temperature", scale=1.0)
    labels = [row_start_label(row) for row in power_stats.get("sensor.inverter_pv_power", [])]

    fig, ax_power = plt.subplots(figsize=(12, 7), dpi=130)
    ax_power.plot(labels, pv, label="PV kW", color="#22c55e", linewidth=2.2)
    ax_power.plot(labels, load, label="Load kW", color="#f97316", linewidth=2.0)
    ax_power.plot(labels, grid, label="Grid kW", color="#3b82f6", linewidth=2.0)
    ax_power.plot(labels, battery, label="Battery kW", color="#a855f7", linewidth=2.0)
    ax_power.axhline(0, color="#444444", linewidth=0.8)
    ax_power.set_ylabel("Power (kW)")
    ax_power.grid(True, axis="y", alpha=0.25)
    ax_power.tick_params(axis="x", labelrotation=45)

    ax_temp = ax_power.twinx()
    ax_temp.plot(labels, temp, label="Temp C", color="#ef4444", linewidth=1.8, linestyle="--")
    ax_temp.set_ylabel("Temperature (C)")

    pv_rows = power_stats.get("sensor.inverter_pv_power", [])
    peak_row = max_row(pv_rows, "max")
    if peak_row and labels:
        peak_index = pv_rows.index(peak_row)
        peak_value = float(peak_row.get("max") or 0.0) / 1000.0
        ax_power.scatter(labels[peak_index], peak_value, color="#166534", zorder=5)
        ax_power.annotate(
            f"PV peak {peak_value:.2f} kW",
            xy=(labels[peak_index], peak_value),
            xytext=(10, 15),
            textcoords="offset points",
            fontsize=9,
            arrowprops={"arrowstyle": "->", "color": "#166534"},
        )

    totals = (
        f"PV {state_value(state_by_id, 'sensor.inverter_today_production')} kWh | "
        f"Home {state_value(state_by_id, 'sensor.inverter_today_load_consumption')} kWh | "
        f"Grid import {state_value(state_by_id, 'sensor.inverter_today_energy_import')} kWh | "
        f"Grid export {state_value(state_by_id, 'sensor.inverter_today_energy_export')} kWh | "
        f"Battery {state_value(state_by_id, 'sensor.inverter_battery')}%"
    )
    fig.suptitle(f"Daily power digest {now:%Y-%m-%d}", fontsize=15, fontweight="bold")
    ax_power.set_title(totals, fontsize=10)

    handles_power, labels_power = ax_power.get_legend_handles_labels()
    handles_temp, labels_temp = ax_temp.get_legend_handles_labels()
    ax_power.legend(handles_power + handles_temp, labels_power + labels_temp, loc="upper left", ncol=3)

    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def render_battery_chart(
    now: datetime,
    battery_stats: dict[str, list[dict[str, Any]]],
    state_by_id: dict[str, dict[str, Any]],
) -> bytes:
    soc_rows = battery_stats.get("sensor.inverter_battery", [])
    power_rows = battery_stats.get("sensor.inverter_battery_power", [])
    labels = [row_start_label(row) for row in soc_rows]
    soc = [float(row.get("mean") or 0.0) for row in soc_rows]
    power = [float(row.get("mean") or 0.0) / 1000.0 for row in power_rows]

    fig, ax_soc = plt.subplots(figsize=(12, 6.5), dpi=130)
    ax_soc.plot(labels, soc, label="Battery SOC %", color="#16a34a", linewidth=2.4)
    ax_soc.set_ylabel("Battery charge (%)")
    ax_soc.set_ylim(max(0, min(soc) - 5) if soc else 0, min(105, max(soc) + 5) if soc else 105)
    ax_soc.grid(True, axis="y", alpha=0.25)
    ax_soc.tick_params(axis="x", labelrotation=45)

    ax_power = ax_soc.twinx()
    ax_power.plot(labels, power, label="Battery power kW", color="#a855f7", linewidth=2.0)
    ax_power.axhline(0, color="#444444", linewidth=0.8)
    ax_power.set_ylabel("Battery power (kW)")

    min_soc_row = min_row(soc_rows, "min")
    max_soc_row = max_row(soc_rows, "max")
    for row, label, color in (
        (min_soc_row, "min", "#dc2626"),
        (max_soc_row, "max", "#166534"),
    ):
        if row and labels:
            row_index = soc_rows.index(row)
            row_value = float(row.get("mean") or row.get("min") or row.get("max") or 0.0)
            ax_soc.scatter(labels[row_index], row_value, color=color, zorder=5)
            ax_soc.annotate(
                f"{label} {row_value:.1f}%",
                xy=(labels[row_index], row_value),
                xytext=(8, 12 if label == "max" else -18),
                textcoords="offset points",
                fontsize=9,
                arrowprops={"arrowstyle": "->", "color": color},
            )

    title = (
        f"Current {state_value(state_by_id, 'sensor.inverter_battery')}% | "
        f"State {state_value(state_by_id, 'sensor.inverter_battery_state')} | "
        f"Charge today {state_value(state_by_id, 'sensor.inverter_today_battery_charge')} kWh | "
        f"Discharge today {state_value(state_by_id, 'sensor.inverter_today_battery_discharge')} kWh"
    )
    fig.suptitle(f"Battery charge last 24h {now:%Y-%m-%d %H:%M}", fontsize=15, fontweight="bold")
    ax_soc.set_title(title, fontsize=10)

    handles_soc, labels_soc = ax_soc.get_legend_handles_labels()
    handles_power, labels_power = ax_power.get_legend_handles_labels()
    ax_soc.legend(handles_soc + handles_power, labels_soc + labels_power, loc="upper left")

    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def series(stats: dict[str, list[dict[str, Any]]], entity_id: str, *, scale: float) -> list[float]:
    return [float(row.get("mean") or 0.0) / scale for row in stats.get(entity_id, [])]


def min_row(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return min(rows, key=lambda row: float(row.get(key) or 0.0))


def max_row(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: float(row.get(key) or 0.0))


def max_value(rows: list[dict[str, Any]], key: str) -> float:
    row = max_row(rows, key)
    return float(row.get(key) or 0.0) if row else 0.0


def min_value(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return min(float(row.get(key) or 0.0) for row in rows)


def average_value(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row.get(key) or 0.0) for row in rows]
    return sum(values) / len(values) if values else 0.0


def row_start_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "n/a"
    return datetime.fromisoformat(row["start"]).astimezone().strftime("%H:%M")


def state_value(state_by_id: dict[str, dict[str, Any]], entity_id: str) -> str:
    state = state_by_id.get(entity_id)
    if not state:
        return "unknown"
    return str(state.get("state", "unknown"))


def kw(value: float) -> str:
    return f"{value / 1000.0:.2f}"


def whole(value: float) -> str:
    return f"{value:.0f}"


def one_decimal(value: float) -> str:
    return f"{value:.1f}"


class DigestRequestHandler(BaseHTTPRequestHandler):
    settings: Settings

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/send-digest":
            self.send_error(404)
            return

        try:
            result = build_digest(self.settings)
            battery_result = build_battery_digest(self.settings)
            ntfy = NtfyClient(self.settings.ntfy_url, self.settings.ntfy_token, self.settings.ntfy_topic)
            ntfy.publish_png(
                title="HA daily power digest",
                message=result.message,
                filename=result.filename,
                png=result.chart_png,
            )
            ntfy.publish_png(
                title="HA battery 24h",
                message=battery_result.message,
                filename=battery_result.filename,
                png=battery_result.chart_png,
            )
            self._write_json(
                200,
                {
                    "success": True,
                    "messages": [
                        {
                            "title": "HA daily power digest",
                            "filename": result.filename,
                            "chart_bytes": len(result.chart_png),
                            "message": result.message,
                        },
                        {
                            "title": "HA battery 24h",
                            "filename": battery_result.filename,
                            "chart_bytes": len(battery_result.chart_png),
                            "message": battery_result.message,
                        },
                    ],
                },
            )
        except Exception as exc:  # noqa: BLE001 - return operational errors to HA trace.
            self._write_json(500, {"success": False, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    settings = load_settings()
    DigestRequestHandler.settings = settings
    server = ThreadingHTTPServer((settings.listen_host, settings.listen_port), DigestRequestHandler)
    print(f"Daily digest renderer listening on {settings.listen_host}:{settings.listen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
