from datetime import timedelta

DOMAIN = "emerald"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)

# AWS IoT MQTT — same endpoint heat pumps use; LiveLink (IHD) shares the broker.
MQTT_HOST = "a13v32g67itvz9-ats.iot.ap-southeast-2.amazonaws.com"
COGNITO_IDENTITY_POOL_ID = "ap-southeast-2:f5bbb02c-c00e-4f10-acb3-e7d1b05268e8"

# How often to ask the LiveLink for current power. Doubles as a keep-alive
# — the gateway stops pushing 10-min bins if it isn't being talked to.
IHD_POLL_INTERVAL = timedelta(seconds=30)

# How long we tolerate silence on the MQTT subscription before assuming the
# AWS IoT session has gone zombie and forcing a teardown + reconnect. Each
# poll round-trip should yield one inbound message; six missed polls in a row
# is well past anything explainable by transient network jitter.
IHD_STALE_RECONNECT_AFTER = timedelta(seconds=180)

# Pause between teardown and rebuild when reconnecting. AWS IoT throttles
# subscribes per-account at roughly 10/sec and silently *drops* SUBACKs when
# throttled (rather than returning a reason code) — back-to-back rebuilds
# produce a "subscribe succeeded but no messages arrive" zombie that looks
# exactly like the credential-renewal zombie we're already guarding against.
IHD_RECONNECT_BACKOFF = timedelta(seconds=5)
