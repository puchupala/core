"""Tests for the Nature Remo climate platform."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

from aionatureremo import (
    Appliance,
    Device,
    NatureRemoApiError,
    NatureRemoAuthError,
    NatureRemoConnectionError,
    NatureRemoError,
    NatureRemoRateLimitError,
)
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
    ClimateService,
    ClimateServiceArgument,
    HVACMode,
)
from homeassistant.const import STATE_UNAVAILABLE, Platform, TargetKey
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import async_poll

ENTITY_ID = "climate.living_ac"
APPLIANCE_ID = "appliance-ac-1"
HUB_ID = "device-remo3-1"

NO_CHANGE: dict[str, str | None] = {
    "operation_mode": None,
    "temperature": None,
    "air_volume": None,
    "air_direction": None,
    "air_direction_h": None,
    "button": None,
}
POWER_OFF = {"button": "power-off"}


@pytest.fixture
def platforms() -> list[Platform]:
    """Platforms to set up."""
    return [Platform.CLIMATE]


def _with_settings(appliances: list[Appliance], **changes: str) -> list[Appliance]:
    """Return the appliances with the living room AC settings changed."""
    return [
        replace(appliance, settings=replace(appliance.settings, **changes))
        if appliance.id == APPLIANCE_ID
        else appliance
        for appliance in appliances
    ]


async def test_all_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    entity_registry: er.EntityRegistry,
    init_integration: MockConfigEntry,
) -> None:
    """Snapshot every climate entity and registry entry."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(
            {"mode": "warm", "temperature": "20", "direction_h": ""}, id="heat"
        ),
        pytest.param(
            {"mode": "dry", "temperature": "0", "direction": ""}, id="dry_offset"
        ),
        pytest.param(
            {"mode": "blow", "temperature": "", "volume": "1", "direction": ""},
            id="fan_only_no_temperature",
        ),
        pytest.param(
            {"mode": "auto", "temperature": "-1", "direction": ""}, id="auto_offset"
        ),
        pytest.param({"button": "power-off"}, id="powered_off"),
        pytest.param({"mode": "turbo"}, id="unknown_mode"),
        pytest.param({"temperature": ""}, id="no_target_temperature"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_state_follows_settings(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    mock_client: AsyncMock,
    appliances: list[Appliance],
    freezer: FrozenDateTimeFactory,
    settings: dict[str, str],
) -> None:
    """The state and the supported settings follow the mode the AC is in."""
    mock_client.get_appliances.return_value = _with_settings(appliances, **settings)
    await async_poll(hass, freezer)

    assert hass.states.get(ENTITY_ID) == snapshot


@pytest.mark.parametrize(
    ("settings", "service", "service_data", "expected"),
    [
        pytest.param(
            {},
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.HEAT},
            {"operation_mode": "warm"},
            id="heat",
        ),
        pytest.param(
            {},
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.DRY},
            {"operation_mode": "dry"},
            id="dry",
        ),
        pytest.param(
            {},
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.FAN_ONLY},
            {"operation_mode": "blow"},
            id="fan_only",
        ),
        pytest.param(
            {},
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.AUTO},
            {"operation_mode": "auto"},
            id="auto",
        ),
        pytest.param(
            {},
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.OFF},
            POWER_OFF,
            id="off",
        ),
        pytest.param({}, ClimateService.TURN_OFF, {}, POWER_OFF, id="turn_off"),
        pytest.param(
            POWER_OFF,
            ClimateService.TURN_ON,
            {},
            {"operation_mode": "cool"},
            id="turn_on",
        ),
        pytest.param(
            POWER_OFF,
            ClimateService.SET_HVAC_MODE,
            {ClimateServiceArgument.HVAC_MODE: HVACMode.HEAT},
            {"operation_mode": "warm"},
            id="heat_while_off",
        ),
        pytest.param(
            {},
            ClimateService.SET_TEMPERATURE,
            {ClimateServiceArgument.TEMPERATURE: 27},
            {"temperature": "27"},
            id="temperature",
        ),
        pytest.param(
            {},
            ClimateService.SET_TEMPERATURE,
            {ClimateServiceArgument.TEMPERATURE: 26.4},
            {"temperature": "26"},
            id="temperature_between_steps",
        ),
        pytest.param(
            {},
            ClimateService.SET_TEMPERATURE,
            {
                ClimateServiceArgument.TEMPERATURE: 24,
                ClimateServiceArgument.HVAC_MODE: HVACMode.HEAT,
            },
            {"operation_mode": "warm", "temperature": "22"},
            id="temperature_outside_the_new_mode_range",
        ),
        pytest.param(
            {},
            ClimateService.SET_TEMPERATURE,
            {
                ClimateServiceArgument.TEMPERATURE: 25,
                ClimateServiceArgument.HVAC_MODE: HVACMode.DRY,
            },
            {"operation_mode": "dry"},
            id="temperature_with_a_mode_without_setpoints",
        ),
        pytest.param(
            {},
            ClimateService.SET_TEMPERATURE,
            {
                ClimateServiceArgument.TEMPERATURE: 25,
                ClimateServiceArgument.HVAC_MODE: HVACMode.OFF,
            },
            {"temperature": "25"} | POWER_OFF,
            id="temperature_with_off",
        ),
        pytest.param(
            POWER_OFF,
            ClimateService.SET_TEMPERATURE,
            {ClimateServiceArgument.TEMPERATURE: 27},
            {"temperature": "27"} | POWER_OFF,
            id="temperature_while_off",
        ),
        pytest.param(
            POWER_OFF,
            ClimateService.SET_TEMPERATURE,
            {
                ClimateServiceArgument.TEMPERATURE: 25,
                ClimateServiceArgument.HVAC_MODE: HVACMode.COOL,
            },
            {"operation_mode": "cool", "temperature": "25"},
            id="temperature_with_a_mode_while_off",
        ),
        pytest.param(
            {},
            ClimateService.SET_FAN_MODE,
            {ClimateServiceArgument.FAN_MODE: "2"},
            {"air_volume": "2"},
            id="fan_mode",
        ),
        pytest.param(
            POWER_OFF,
            ClimateService.SET_FAN_MODE,
            {ClimateServiceArgument.FAN_MODE: "2"},
            {"air_volume": "2"} | POWER_OFF,
            id="fan_mode_while_off",
        ),
        pytest.param(
            {},
            ClimateService.SET_SWING_MODE,
            {ClimateServiceArgument.SWING_MODE: "1"},
            {"air_direction": "1"},
            id="swing_mode",
        ),
        pytest.param(
            POWER_OFF,
            ClimateService.SET_SWING_MODE,
            {ClimateServiceArgument.SWING_MODE: "1"},
            {"air_direction": "1"} | POWER_OFF,
            id="swing_mode_while_off",
        ),
        pytest.param(
            {},
            ClimateService.SET_SWING_HORIZONTAL_MODE,
            {ClimateServiceArgument.SWING_HORIZONTAL_MODE: "3"},
            {"air_direction_h": "3"},
            id="swing_horizontal_mode",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_service_sends_settings(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    appliances: list[Appliance],
    freezer: FrozenDateTimeFactory,
    settings: dict[str, str],
    service: ClimateService,
    service_data: dict[str, Any],
    expected: dict[str, str],
) -> None:
    """Each action sends only what it changes, plus the extras to keep.

    While the power is off, a change that does not pick a mode carries the
    power-off button so that it does not turn the AC on.
    """
    mock_client.get_appliances.return_value = _with_settings(appliances, **settings)
    await async_poll(hass, freezer)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        service,
        {TargetKey.ENTITY_ID: ENTITY_ID, **service_data},
        blocking=True,
    )

    mock_client.set_aircon_settings.assert_called_once_with(
        APPLIANCE_ID, **(NO_CHANGE | expected), extra={"autoclean": "on"}
    )


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        pytest.param(21, "21", id="whole"),
        pytest.param(21.5, "21.5", id="half"),
        pytest.param(21.3, "21.5", id="between_steps"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_set_temperature_in_half_steps(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    temperature: float,
    expected: str,
) -> None:
    """The temperature is sent the way the AC lists it."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        ClimateService.SET_TEMPERATURE,
        {
            TargetKey.ENTITY_ID: "climate.bedroom_ac",
            ClimateServiceArgument.TEMPERATURE: temperature,
        },
        blocking=True,
    )

    mock_client.set_aircon_settings.assert_called_once_with(
        "appliance-ac-2",
        **(NO_CHANGE | {"temperature": expected}),
        extra={"powerful": "off"},
    )


@pytest.mark.usefixtures("init_integration")
async def test_state_updates_from_the_response(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    mock_client: AsyncMock,
) -> None:
    """The settings the API answers with are shown without another poll."""
    polls = mock_client.get_appliances.call_count

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        ClimateService.SET_HVAC_MODE,
        {
            TargetKey.ENTITY_ID: ENTITY_ID,
            ClimateServiceArgument.HVAC_MODE: HVACMode.HEAT,
        },
        blocking=True,
    )

    assert hass.states.get(ENTITY_ID) == snapshot
    assert mock_client.get_appliances.call_count == polls


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        pytest.param(
            NatureRemoConnectionError("refused"), "command_failed", id="connection"
        ),
        pytest.param(NatureRemoApiError(500, "oops"), "command_failed", id="api"),
        pytest.param(NatureRemoAuthError(401, "bad token"), "auth_failed", id="auth"),
        pytest.param(
            NatureRemoRateLimitError(429, "limited", reset=None),
            "command_rate_limited",
            id="rate_limit",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_service_error(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    error: NatureRemoError,
    translation_key: str,
) -> None:
    """A failed command raises a translated error and keeps the state."""
    mock_client.set_aircon_settings.side_effect = error

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            ClimateService.SET_HVAC_MODE,
            {
                TargetKey.ENTITY_ID: ENTITY_ID,
                ClimateServiceArgument.HVAC_MODE: HVACMode.HEAT,
            },
            blocking=True,
        )

    assert exc_info.value.translation_key == translation_key
    assert hass.states.get(ENTITY_ID).state == HVACMode.COOL


@pytest.mark.parametrize(
    "change_hub",
    [
        pytest.param(lambda hub: [replace(hub, online=False)], id="offline"),
        pytest.param(lambda hub: [], id="removed"),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_unavailable_without_its_hub(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    devices: list[Device],
    freezer: FrozenDateTimeFactory,
    change_hub: Callable[[Device], list[Device]],
) -> None:
    """An AC cannot be reached while its Remo is offline or gone."""
    mock_client.get_devices.return_value = [
        changed
        for device in devices
        for changed in (change_hub(device) if device.id == HUB_ID else [device])
    ]
    await async_poll(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_unavailable_when_the_appliance_disappears(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    appliances: list[Appliance],
    freezer: FrozenDateTimeFactory,
) -> None:
    """An AC the API stops reporting becomes unavailable."""
    mock_client.get_appliances.return_value = [
        appliance for appliance in appliances if appliance.id != APPLIANCE_ID
    ]
    await async_poll(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("init_integration")
async def test_no_current_temperature_without_a_reading(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    devices: list[Device],
    freezer: FrozenDateTimeFactory,
) -> None:
    """A Remo without a temperature sensor leaves the current temperature unset."""
    mock_client.get_devices.return_value = [
        replace(device, events={}) if device.id == HUB_ID else device
        for device in devices
    ]
    await async_poll(hass, freezer)

    assert hass.states.get(ENTITY_ID).attributes[ATTR_CURRENT_TEMPERATURE] is None


async def test_fahrenheit_air_conditioner(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    appliances: list[Appliance],
) -> None:
    """The Remo reading stays in Celsius when the AC is set in Fahrenheit."""
    mock_client.get_appliances.return_value = [
        replace(appliance, aircon=replace(appliance.aircon, temp_unit="f"))
        if appliance.id == APPLIANCE_ID
        else appliance
        for appliance in appliances
    ]

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).attributes[ATTR_CURRENT_TEMPERATURE] == 26.4
