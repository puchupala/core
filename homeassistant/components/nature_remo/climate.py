"""Climate platform for the Nature Remo integration."""

from typing import Any, override

from aionatureremo import (
    APPLIANCE_TYPE_AC,
    EVENT_HUMIDITY,
    EVENT_TEMPERATURE,
    Aircon,
    AirconModeRange,
)

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    ClimateServiceArgument,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import BUTTON_POWER_OFF
from .coordinator import NatureRemoConfigEntry, NatureRemoCoordinator
from .entity import NatureRemoApplianceEntity

PARALLEL_UPDATES = 1

API_TO_HVAC_MODE: dict[str, HVACMode] = {
    "auto": HVACMode.AUTO,
    "blow": HVACMode.FAN_ONLY,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "warm": HVACMode.HEAT,
}
HVAC_MODE_TO_API = {hvac_mode: mode for mode, hvac_mode in API_TO_HVAC_MODE.items()}

_NO_RANGE = AirconModeRange(temperatures=[], volumes=[], directions=[], directions_h=[])


def _setpoints(mode_range: AirconModeRange) -> list[float]:
    """Return the temperatures a mode can be set to.

    Some modes, typically dry and auto, take an offset such as -2 to 2
    instead of a temperature; those have no setpoints.
    """
    setpoints = sorted(float(value) for value in mode_range.temperatures)
    return setpoints if setpoints and setpoints[0] > 0 else []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NatureRemoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a climate entity for each air conditioner present at setup."""
    coordinator = entry.runtime_data
    async_add_entities(
        NatureRemoClimate(coordinator, appliance_id, appliance.aircon)
        for appliance_id, appliance in coordinator.data.appliances.items()
        if appliance.type == APPLIANCE_TYPE_AC
        and appliance.aircon is not None
        and appliance.settings is not None
    )


class NatureRemoClimate(NatureRemoApplianceEntity, ClimateEntity):
    """An air conditioner controlled through a Remo."""

    _attr_name = None
    _attr_translation_key = "air_conditioner"
    _mode: str

    def __init__(
        self, coordinator: NatureRemoCoordinator, appliance_id: str, aircon: Aircon
    ) -> None:
        """Initialize the air conditioner."""
        super().__init__(coordinator, appliance_id)
        self._modes = aircon.modes
        self._attr_unique_id = appliance_id
        self._attr_native_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT
            if aircon.temp_unit == "f"
            else UnitOfTemperature.CELSIUS
        )
        self._attr_hvac_modes = [
            HVACMode.OFF,
            *(
                API_TO_HVAC_MODE[mode]
                for mode in aircon.modes
                if mode in API_TO_HVAC_MODE
            ),
        ]
        self._async_update_attrs()

    @callback
    def _async_update_attrs(self) -> None:
        """Update the attributes from the coordinator data."""
        data = self.coordinator.data
        appliance = data.appliances.get(self._appliance_id)
        if appliance is None or (settings := appliance.settings) is None:
            return

        # The API keeps reporting the last mode while the power is off.
        self._mode = settings.mode
        self._attr_hvac_mode = (
            HVACMode.OFF
            if settings.button == BUTTON_POWER_OFF
            else API_TO_HVAC_MODE.get(settings.mode)
        )

        # The settings an air conditioner accepts depend on its mode.
        mode_range = self._modes.get(settings.mode, _NO_RANGE)
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        setpoints = _setpoints(mode_range)
        if setpoints:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_min_temp = setpoints[0]
            self._attr_max_temp = setpoints[-1]
        self._attr_target_temperature_step = (
            setpoints[1] - setpoints[0] if len(setpoints) > 1 else None
        )
        self._attr_native_target_temperature = (
            float(settings.temperature)
            if setpoints and settings.temperature in mode_range.temperatures
            else None
        )
        if mode_range.volumes:
            features |= ClimateEntityFeature.FAN_MODE
        self._attr_fan_modes = mode_range.volumes
        self._attr_fan_mode = settings.volume or None
        if mode_range.directions:
            features |= ClimateEntityFeature.SWING_MODE
        self._attr_swing_modes = mode_range.directions
        self._attr_swing_mode = settings.direction or None
        if mode_range.directions_h:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
        self._attr_swing_horizontal_modes = mode_range.directions_h
        self._attr_swing_horizontal_mode = settings.direction_h or None
        self._attr_supported_features = features

        # An air conditioner reports no readings itself; the Remo next to it does.
        hub = data.devices.get(appliance.device_id or "")
        events = hub.events if hub is not None else {}
        temperature = events.get(EVENT_TEMPERATURE)
        self._attr_native_current_temperature = (
            TemperatureConverter.convert(
                temperature.value,
                UnitOfTemperature.CELSIUS,
                self._attr_native_temperature_unit,
            )
            if temperature is not None
            else None
        )
        humidity = events.get(EVENT_HUMIDITY)
        self._attr_current_humidity = humidity.value if humidity is not None else None

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._async_update_attrs()
        super()._handle_coordinator_update()

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the mode, or turn the power off."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_aircon_settings(
                self._appliance_id, button=BUTTON_POWER_OFF
            )
            return
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id, operation_mode=HVAC_MODE_TO_API[hvac_mode]
        )

    @override
    async def async_turn_on(self) -> None:
        """Turn the power on in the mode it was last used in."""
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id, operation_mode=self._mode
        )

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature, switching the mode when one is given."""
        hvac_mode: HVACMode | None = kwargs.get(ClimateServiceArgument.HVAC_MODE)
        operation_mode: str | None = None
        if hvac_mode is not None:
            self._valid_mode_or_raise("hvac", hvac_mode, self.hvac_modes)
            operation_mode = HVAC_MODE_TO_API.get(hvac_mode)

        mode_range = self._modes.get(operation_mode or self._mode, _NO_RANGE)
        temperature: str | None = None
        if _setpoints(mode_range):
            # The API only accepts the temperatures as it lists them.
            temperature = min(
                mode_range.temperatures,
                key=lambda value: abs(
                    float(value) - kwargs[ClimateServiceArgument.TEMPERATURE]
                ),
            )
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id,
            operation_mode=operation_mode,
            temperature=temperature,
            button=BUTTON_POWER_OFF if hvac_mode == HVACMode.OFF else None,
        )

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id, air_volume=fan_mode
        )

    @override
    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set the vertical swing mode."""
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id, air_direction=swing_mode
        )

    @override
    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        """Set the horizontal swing mode."""
        await self.coordinator.async_set_aircon_settings(
            self._appliance_id, air_direction_h=swing_horizontal_mode
        )
