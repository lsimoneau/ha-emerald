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
