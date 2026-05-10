# ha-emerald

A Home Assistant custom integration for Emerald Energy products: the **Heat
Pump Hot Water** system and the **Electricity Advisor** (via the LiveLink WiFi
bridge).

Both products are exposed under a single integration backed by your Emerald
cloud account.

## Status

Early development. Tested against the maintainer's own devices. Expect rough
edges; please file an issue if you hit one.

## What it gives you

**Heat Pump Hot Water** — `water_heater` entity:

- On / off and operation mode (`Heat pump` = Emerald *normal*, `Eco` =
  *quiet*, `Performance` = *boost*)
- Current tank temperature, set-point, current-hour energy use

**Electricity Advisor** — `sensor` entities:

- Power (W, derived from the latest 10-minute bin)
- Energy today (kWh, `total_increasing` — drops straight into the HA
  Energy dashboard)
- Cost today
- Last-synced timestamp

The integration auto-discovers whichever products are on the account; if you
only have one, only that one is set up.

## Installation

### Manual (custom_components)

1. Copy `custom_components/emerald/` into your HA `config/custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → "Emerald".
4. Enter the email and password you use for the Emerald mobile app.

### HACS

Not yet listed. You can add this repo as a custom HACS repository in the
meantime.

## How it works

- **Heat Pump:** the device's state and controls live on AWS IoT MQTT
  (Cognito-authed WebSockets). The integration uses the upstream
  [`emerald-hws`](https://pypi.org/project/emerald-hws/) Python package as the
  transport layer and bridges its threaded callbacks into HA's event loop.
- **Electricity Advisor:** state arrives over the same AWS IoT MQTT
  endpoint as the heat pump. The integration polls the LiveLink for
  `cur_consump` every 30 s (live wattage) and consumes the LiveLink's
  auto-pushed `ihd_10min` energy bins. The 30 s poll doubles as a
  keep-alive — the LiveLink only uploads while something is talking to
  it. Today's running total is seeded once on startup from the cloud
  REST API so the daily-energy sensor reflects the morning rather than
  only the time since Home Assistant was last restarted.

This means everything works without any local network access to the devices
themselves. For sub-second power data you'd need direct BLE to the EA,
which is out of scope here.

## Development

Requires [`uv`](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                       # install dev dependencies
uv run pytest                 # unit tests
uv run ruff check .           # lint

docker compose up -d          # start a HA dev instance on http://localhost:8123
docker compose restart        # pick up code changes
docker compose logs -f        # tail logs
```

The `docker-compose.yml` mounts `./custom_components` and `./config` into the
container so edits to the integration are visible without rebuilding.

## Credits

API reverse-engineering courtesy of:

- [`ross-w/emerald_hws_py`](https://github.com/ross-w/emerald_hws_py) — the
  upstream HWS transport library, used here directly.
- [`WeekendWarrior1/emerald_electricity_advisor`](https://github.com/WeekendWarrior1/emerald_electricity_advisor)
  — the EA cloud API documentation and Postman collection.

## License

MIT
