"""Config flow (nothing to configure) and an options flow that imports an
app backup file uploaded through the UI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    QrCodeSelector,
    QrCodeSelectorConfig,
    QrErrorCorrectionLevel,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DOMAIN
from .pairing import app_link, async_pairing_url, async_reset_pairing
from .services import import_into

_LOGGER = logging.getLogger(__name__)

# "Use the baby named in the file (or the only known baby)".
BABY_AUTO = "auto"


class BabyLogConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Junior Baby Log", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BabyLogOptionsFlow()


class BabyLogOptionsFlow(OptionsFlow):
    """Settings → Devices & services → Junior Baby Log → Configure."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init", menu_options=["pair", "import_backup", "reset_pairing"]
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """QR code + link for the app. Scanning with the iPhone camera opens
        Junior and pairs it."""
        if user_input is not None:
            return self.async_abort(reason="paired")
        try:
            url = await async_pairing_url(self.hass, self.config_entry)
        except HomeAssistantError as err:
            return self.async_abort(reason="no_url", description_placeholders={"error": str(err)})
        link = app_link(url)
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema(
                {
                    vol.Optional("qr"): QrCodeSelector(
                        QrCodeSelectorConfig(
                            data=link, scale=6, error_correction_level=QrErrorCorrectionLevel.QUARTILE
                        )
                    )
                }
            ),
            description_placeholders={"url": url, "link": link},
        )

    async def async_step_reset_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await async_reset_pairing(self.hass, self.config_entry)
            return self.async_abort(reason="pairing_reset")
        return self.async_show_form(step_id="reset_pairing")

    async def async_step_import_backup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        store = self.config_entry.runtime_data

        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            baby = user_input["baby"]
            try:
                data = await self.hass.async_add_executor_job(
                    _read_upload, self.hass, user_input["file"]
                )
                result = import_into(
                    store, data, None if baby == BABY_AUTO else baby, user_input["mode"]
                )
            except (ValueError, ServiceValidationError) as err:
                # The upload is consumed either way; the user picks the file again.
                _LOGGER.warning("Backup import failed: %s", err)
                errors["file"] = "invalid_backup"
                placeholders["error"] = str(err)
            else:
                return self.async_abort(
                    reason="imported",
                    description_placeholders={
                        "baby": store.babies.get(result["baby_id"], result["baby_id"]),
                        **{k: str(v) for k, v in result.items() if k != "baby_id"},
                    },
                )

        babies = [
            SelectOptionDict(value=baby_id, label=f"{name} ({baby_id})")
            for baby_id, name in store.babies.items()
        ]
        schema = vol.Schema(
            {
                vol.Required("file"): FileSelector(
                    FileSelectorConfig(accept=".json,application/json")
                ),
                vol.Required("baby", default=BABY_AUTO): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=BABY_AUTO, label="From the file"), *babies],
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Required("mode", default="merge"): SelectSelector(
                    SelectSelectorConfig(
                        options=["merge", "replace"],
                        translation_key="mode",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="import_backup",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )


def _read_upload(hass, file_id: str) -> Any:
    """Runs in the executor; the uploaded file is deleted afterwards."""
    with process_uploaded_file(hass, file_id) as path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
