"""File-shaped export/import at `/api/babylog/backup` (Bearer token auth).

GET  ?baby_id=…[&include_deleted=1] -> app backup file as a download
POST ?baby_id=…&mode=merge|replace, body = app backup file
"""

from __future__ import annotations

from http import HTTPStatus
import json

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .services import export_for, get_store, import_into


class BabyLogBackupView(HomeAssistantView):
    url = "/api/babylog/backup"
    name = "api:babylog:backup"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        try:
            backup = export_for(
                get_store(hass),
                request.query.get("baby_id"),
                request.query.get("include_deleted") in ("1", "true"),
            )
        except ServiceValidationError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        filename = f"junior-backup-{dt_util.now().strftime('%Y-%m-%d')}.json"
        return web.Response(
            text=json.dumps(backup, indent=2, sort_keys=True),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        if not request["hass_user"].is_admin:
            return self.json_message("Admin required", HTTPStatus.FORBIDDEN)
        mode = request.query.get("mode", "merge")
        if mode not in ("merge", "replace"):
            return self.json_message("mode must be merge or replace", HTTPStatus.BAD_REQUEST)
        try:
            data = await request.json()
        except ValueError:
            return self.json_message("Body must be JSON", HTTPStatus.BAD_REQUEST)
        try:
            result = import_into(
                get_store(hass), data, request.query.get("baby_id"), mode
            )
        except ServiceValidationError as err:
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        return self.json(result)
