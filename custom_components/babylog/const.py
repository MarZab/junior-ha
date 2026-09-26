"""Constants for the Junior baby log integration."""

from __future__ import annotations

DOMAIN = "babylog"

STORAGE_VERSION = 1

KIND_FEEDING = "feeding"
KIND_SLEEP = "sleep"
KIND_BOTTLE = "bottle"
KIND_DIAPER = "diaper"
KIND_COMMENT = "comment"

KINDS = [KIND_FEEDING, KIND_SLEEP, KIND_BOTTLE, KIND_DIAPER, KIND_COMMENT]
SESSION_KINDS = [KIND_FEEDING, KIND_SLEEP, KIND_BOTTLE]

DEFAULT_BABY_NAME = "Baby"

# Entity keys per platform. Anything else registered for the entry (entities
# from earlier versions) is removed on setup.
SENSOR_KEYS = {"feeding", "diaper"}
BINARY_SENSOR_KEYS = {"sleeping"}

SERVICE_UPSERT = "upsert"
SERVICE_DELETE = "delete"
SERVICE_LIST = "list"
SERVICE_EXPORT = "export"
SERVICE_IMPORT = "import"
SERVICE_STATS = "stats"

PANEL_URL_PATH = "junior"
STATIC_URL = "/babylog_static"
