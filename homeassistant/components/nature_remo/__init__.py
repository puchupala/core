"""The Nature Remo integration."""

from aionatureremo import (
    APPLIANCE_TYPE_AC,
    APPLIANCE_TYPE_SMART_METER,
    Appliance,
    NatureRemoClient,
)

from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .coordinator import NatureRemoConfigEntry, NatureRemoCoordinator
from .entity import build_appliance_device_info, build_remo_device_info

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR]


def _has_entities(appliance: Appliance) -> bool:
    """Return if a platform creates entities for the appliance."""
    if appliance.type == APPLIANCE_TYPE_AC:
        return appliance.aircon is not None and appliance.settings is not None
    return (
        appliance.type == APPLIANCE_TYPE_SMART_METER
        and appliance.smart_meter is not None
    )


@callback
def _async_register_devices(hass: HomeAssistant, entry: NatureRemoConfigEntry) -> None:
    """Register the Remo hubs and the appliances the platforms expose.

    Registration is eager because an energy-only hub (Remo E / E lite)
    has no entities to create its device.
    """
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    hub_ids: dict[str, str] = {}
    for device in coordinator.data.devices.values():
        device_entry = device_registry.async_get_or_create_from_info(
            config_entry_id=entry.entry_id,
            device_info=build_remo_device_info(device),
        )
        hub_ids[device.id] = device_entry.id
    for appliance in coordinator.data.appliances.values():
        if not _has_entities(appliance):
            continue
        device_registry.async_get_or_create_from_info(
            config_entry_id=entry.entry_id,
            device_info=build_appliance_device_info(
                appliance, hub_ids.get(appliance.device_id or "")
            ),
        )


async def async_setup_entry(hass: HomeAssistant, entry: NatureRemoConfigEntry) -> None:
    """Set up Nature Remo from a config entry."""
    client = NatureRemoClient(entry.data[CONF_API_TOKEN], async_get_clientsession(hass))
    coordinator = NatureRemoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_register_devices(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: NatureRemoConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
