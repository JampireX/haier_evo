from __future__ import annotations
import requests
import voluptuous as vol
from typing import Any
from urllib.parse import urljoin
from homeassistant import config_entries, exceptions
from homeassistant.core import HomeAssistant
from homeassistant.core import callback
from . import api
from . import const as C
from .const import DOMAIN
from .logger import _LOGGER


REGIONS = ["ru", "kz", "by"]
DATA_SCHEMA = vol.Schema({
    vol.Required("email"): str,
    vol.Required("password"): str,
    vol.Required("region", default="ru"): vol.In(REGIONS),
})


def entry_unique_id(email: str, region: str) -> str:
    # One config entry per Evo account and region.
    return f"{email.strip().lower()}_{region}"


def _entry_account(entry: config_entries.ConfigEntry) -> str:
    # Entries created before the flow set a unique_id have none: derive it from their data.
    if entry.unique_id is not None:
        return entry.unique_id
    return entry_unique_id(str(entry.data.get("email") or ""), entry.data.get("region") or "ru")


def _account_in_use(
    hass: HomeAssistant,
    unique_id: str,
    exclude_entry_id: str | None = None,
    legacy_only: bool = False,
) -> bool:
    # legacy_only: only entries without a unique_id (the others are found by unique_id).
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry_id or (legacy_only and entry.unique_id is not None):
            continue
        if _entry_account(entry) == unique_id:
            return True
    return False


def _title(data: dict) -> str:
    return f'{data["email"]} ({data["region"]})'


def sign_in(email: str, password: str, region: str) -> None:
    """One sign-in attempt against the Evo cloud (blocking: run it in the executor).

    Sends the request api.Haier.auth_login sends through make_request. The tokens it returns
    are discarded: the integration signs in by itself when the entry is (re)loaded.
    Raises InvalidAuth when the cloud rejects the email/password, CannotConnect otherwise.
    """
    url = urljoin(C.API_PATH, C.API_LOGIN.format(region=region))
    try:
        response = requests.post(
            url,
            data={"email": email, "password": password},
            headers={"User-Agent": "evo-mobile", "Platform": "android", "Accept": "*/*"},
            timeout=C.API_TIMEOUT,
        )
        if response.status_code == 429:
            raise CannotConnect("429 Too Many Requests")
        response.raise_for_status()
        auth = api.AuthResponse(response)
        auth.raise_for_error()
        if not auth.token.get("accessToken"):
            raise CannotConnect("no access token in the sign-in response")
    except api.AuthUserError as e:  # "Введен неверный email или пароль"
        raise InvalidAuth(str(e)) from e
    except CannotConnect:
        raise
    except (requests.RequestException, ValueError, TypeError, AttributeError) as e:
        # Network errors, timeouts, HTTP errors, an error answer from the cloud (AuthError and
        # its subclasses are HTTPErrors) or a response that is not the expected JSON.
        raise CannotConnect(f"{type(e).__name__}: {e}") from e


async def validate_input(hass: HomeAssistant, data: dict) -> dict[str, Any]:
    if len(data["email"]) < 3:
        raise InvalidEmail
    if len(data["password"]) < 3:
        raise InvalidPassword
    if data["region"] not in REGIONS:
        raise InvalidRegion
    await hass.async_add_executor_job(sign_in, data["email"], data["password"], data["region"])
    return {"title": _title(data)}


async def _async_validate(hass: HomeAssistant, data: dict) -> dict[str, str]:
    """Check the form and sign in once. Returns the form errors (empty when valid)."""
    try:
        await validate_input(hass, data)
    except InvalidEmail:
        return {"email": "invalid_email"}
    except InvalidPassword:
        return {"password": "invalid_password"}
    except InvalidRegion:
        return {"region": "invalid_region"}
    except InvalidAuth:
        return {"base": "invalid_auth"}
    except CannotConnect as e:
        _LOGGER.warning(f"Haier Evo sign-in check failed: {e}")
        return {"base": "cannot_connect"}
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception")
        return {"base": "unknown"}
    return {}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        schema = DATA_SCHEMA
        if user_input is not None:
            user_input = {**user_input, "email": str(user_input.get("email") or "").strip()}
            unique_id = entry_unique_id(user_input["email"], user_input["region"])
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            if _account_in_use(self.hass, unique_id, legacy_only=True):
                return self.async_abort(reason="already_configured")
            errors = await _async_validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=_title(user_input), data=user_input)
            # Keep what was typed (except the password) when the form is shown again.
            schema = self.add_suggested_values_to_schema(
                DATA_SCHEMA, {k: v for k, v in user_input.items() if k != "password"}
            )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors
        )

    async def async_step_reauth(self, entry_data):
        # Started by HA when setup raised ConfigEntryAuthFailed, or by the integration when
        # the server rejected the stored password at runtime.
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await _async_validate(self.hass, {**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"email": entry.data.get("email", "")},
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry
        current = entry.data
        if user_input is not None:
            data = {
                **current,
                "email": str(user_input.get("email") or "").strip(),
                # The stored password is never sent to the form: an empty field keeps it.
                "password": user_input.get("password") or current.get("password") or "",
                "region": user_input.get("region") or current.get("region") or "ru",
            }
            unique_id = entry_unique_id(data["email"], data["region"])
            if _account_in_use(self.hass, unique_id, exclude_entry_id=entry.entry_id):
                errors["base"] = "already_configured"
            else:
                errors = await _async_validate(self.hass, data)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry,
                    title=_title(data),
                    data=data,
                    unique_id=unique_id,
                )
                # Apply the new credentials right away (there is no update listener).
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})
        shown = {**current, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("email", default=shown.get("email", "")): str,
                vol.Optional("password"): str,
                vol.Required("region", default=shown.get("region", "ru")): vol.In(REGIONS),
            }),
            errors=errors
        )


class InvalidEmail(exceptions.HomeAssistantError):
    """Error to indicate the email is invalid."""

class InvalidPassword(exceptions.HomeAssistantError):
    """Error to indicate the password is invalid."""

class InvalidRegion(exceptions.HomeAssistantError):
    """Error to indicate there is an invalid region."""

class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate the Evo cloud rejected the email/password."""

class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate the Evo cloud could not be reached or answered with an error."""
