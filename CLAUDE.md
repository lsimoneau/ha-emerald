# CLAUDE.md

Custom Home Assistant integration for two Emerald Energy products under one
cloud account: Heat Pump Hot Water (HWS) and Electricity Advisor (EA).

## Layout

```
custom_components/emerald/
  api.py             Async REST client (login, refresh, discovery, EA flashes)
  hws.py             Thin wrapper around the emerald_hws PyPI library
  coordinator.py     HwsCoordinator (push), ElectricityAdvisorCoordinator (poll),
                     EaSnapshot dataclass, EmeraldRuntimeData
  device.py          DeviceInfo helpers shared by entity platforms
  config_flow.py     Username/password flow, validates via REST sign-in only
  __init__.py        Setup: REST discovery → conditional coordinators
  water_heater.py    HWS entity
  sensor.py          HWS + EA sensors
  manifest.json      requirements: ["emerald-hws==X"]
  strings.json + translations/en.json
tests/               pytest-homeassistant-custom-component
docker-compose.yml   Dev HA instance on :8123, mounts ./custom_components
```

## Commands

```sh
uv sync                              # install deps
uv run pytest                        # tests
uv run ruff check .                  # lint
uv run ruff check --fix .            # auto-fix
docker compose up -d                 # start HA at http://localhost:8123
docker compose restart               # reload after editing the integration
docker compose logs -f               # tail HA logs
```

## Design notes worth remembering

**Two transports, one account.** HWS state is pushed over AWS IoT MQTT (region
`ap-southeast-2`, Cognito identity pool auth) using the upstream `emerald-hws`
library — vendored as a runtime dep rather than reimplemented. EA has only a
REST API and 10-minute resolution; we poll once a minute. `EmeraldRuntimeData`
holds both coordinators, each `Optional` so single-product accounts work.

**Sync→async boundary.** `emerald_hws` is synchronous and uses background
threads for MQTT callbacks. Everything goes through `hass.async_add_executor_job`,
and the MQTT thread callback bounces into the loop via
`hass.loop.call_soon_threadsafe(...)` (see `hws.HwsBridge`). Don't call vendor
methods directly from the event loop.

**Mode mapping.** Emerald uses 0=boost, 1=normal, 2=quiet. The `water_heater`
entity maps these to HA states as: `STATE_PERFORMANCE` ↔ boost,
`STATE_HEAT_PUMP` ↔ normal (the natural HP mode), `STATE_ECO` ↔ quiet (lower
power / silent). `STATE_OFF` is a separate switch=0 path.

**Discovery payload shape.** `GET /customer/property/list` returns properties
each containing both a `heat_pump[]` array and a `devices[]` array (filter
`device_category == "Electricity Advisor"`). Both `property` and
`shared_property` arrays must be merged.

**Cloud-only by design.** The EA itself is BLE; the user reaches it via
Emerald's LiveLink WiFi bridge, which is the cloud-API path. Do **not** add a
direct BLE path — out of scope.

## Working from reference repos

The two upstream repos
([`ross-w/emerald_hws_py`](https://github.com/ross-w/emerald_hws_py),
[`WeekendWarrior1/emerald_electricity_advisor`](https://github.com/WeekendWarrior1/emerald_electricity_advisor))
are the source of API knowledge — endpoints, auth flow, payload shapes, MQTT
topic structure. Do not copy their code style or class structure; mirror
standard HA patterns instead.

## Tooling

Use `uv` for everything Python. Don't run `pip` directly.
