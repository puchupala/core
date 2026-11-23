"""Tests for Nature Remo integration setup."""

from dataclasses import replace
from unittest.mock import AsyncMock

from aionatureremo import Appliance, NatureRemoAuthError, NatureRemoConnectionError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.nature_remo.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_energy_only_hub_is_registered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A Remo E lite with no sensor events is still registered.

    It reports no temperature/humidity/illuminance/motion events, so no
    device-scoped entity is ever created for it; without eager registration
    its hub device would be missing.
    """
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, "device-remoe-1"), init_integration.entry_id
    )
    assert hub is not None
    assert hub.manufacturer == "Nature"
    assert hub.model == "Remo-E-lite"
    assert hub.sw_version == "1.7.2"
    assert hub.serial_number == "4W123456789012"
    assert hub.configuration_url == "https://home.nature.global/"
    assert (dr.CONNECTION_NETWORK_MAC, "ab:cd:ef:12:34:59") in hub.connections


@pytest.mark.parametrize(
    ("appliance_id", "hub_id"),
    [
        pytest.param("appliance-meter-1", "device-remoe-1", id="smart_meter"),
        pytest.param("appliance-ac-1", "device-remo3-1", id="air_conditioner"),
    ],
)
async def test_appliance_links_to_its_hub(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    appliance_id: str,
    hub_id: str,
) -> None:
    """An appliance points at its parent hub via via_device.

    It must be linked to its Remo rather than orphaned at the top level,
    regardless of platform setup ordering.
    """
    hub = device_registry.async_get_device_by_identifier(
        (DOMAIN, hub_id), init_integration.entry_id
    )
    appliance = device_registry.async_get_device_by_identifier(
        (DOMAIN, appliance_id), init_integration.entry_id
    )
    assert hub is not None
    assert appliance is not None
    assert appliance.via_device_id == hub.id


@pytest.mark.parametrize("appliance_id", ["appliance-tv-1", "appliance-floorheater-1"])
async def test_appliance_without_entities_is_not_registered(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    appliance_id: str,
) -> None:
    """An appliance no platform exposes leaves no empty device behind."""
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, appliance_id), init_integration.entry_id
        )
        is None
    )


async def test_setup_retries_on_connection_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """A connection failure during first refresh puts the entry in retry."""
    mock_client.get_devices.side_effect = NatureRemoConnectionError("refused")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_error_is_setup_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """An auth failure during first refresh marks the entry as errored."""
    mock_client.get_devices.side_effect = NatureRemoAuthError(401, "bad token")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_appliance_rename_reaches_the_device_registry_on_reload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    appliances: list[Appliance],
    device_registry: dr.DeviceRegistry,
) -> None:
    """A nickname edited in the Nature app is picked up on reload."""
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "appliance-meter-1"), init_integration.entry_id
    )
    assert device is not None
    assert device.name == "Smart meter"

    mock_client.get_appliances.return_value = [
        replace(appliance, nickname="Grid meter")
        if appliance.id == "appliance-meter-1"
        else appliance
        for appliance in appliances
    ]
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "appliance-meter-1"), init_integration.entry_id
    )
    assert device is not None
    assert device.name == "Grid meter"
