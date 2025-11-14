"""Solamagic Sensors - Power Level, RSSI, Connection Status."""
from __future__ import annotations
import asyncio, logging
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
    SensorDeviceClass,
)
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Polling-intervall
POLL_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solamagic sensors."""
    client = hass.data[DOMAIN][entry.entry_id]
    name = entry.title or entry.data.get("address") or "Solamagic"

    sensors = [
        SolamagicPowerSensor(client, name, entry.entry_id),
        SolamagicRSSISensor(client, name, entry.entry_id),
        SolamagicConnectionSensor(client, name, entry.entry_id),
    ]

    async_add_entities(sensors, True)


class SolamagicPowerSensor(SensorEntity):
    """Sensor som läser effektnivå från värmaren."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:radiator"
    _attr_suggested_display_precision = 0

    def __init__(self, client, name: str, unique_id: str) -> None:
        """Initialize the power sensor."""
        self._client = client
        self._attr_name = "Power Level"
        self._attr_unique_id = f"{unique_id}-power"
        self._address = getattr(client._ble, "address", None)

        # Current state
        self._attr_native_value = 0
        self._polling = False
        self._cancel_poll = None

        # Spara referens till climate callback
        self._climate_callback = None

        _LOGGER.debug(
            "Initialized power sensor: %s (poll interval=%s)",
            name, POLL_INTERVAL
        )

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._address or self.unique_id)},
            "manufacturer": "Solamagic",
            "name": self._attr_name.replace(" Power Level", ""),
            "model": "BT2000",
        }

    @property
    def extra_state_attributes(self):
        """Return extra state attributes."""
        return {
            "poll_interval_minutes": POLL_INTERVAL.total_seconds() / 60,
            "last_poll": getattr(self, "_last_poll", "Never"),
        }

    async def async_added_to_hass(self) -> None:
        """Start polling when added to hass."""
        await super().async_added_to_hass()

        # Spara climate callback och kedja vår egen
        old_callback = self._client._ble._status_callback
        self._climate_callback = old_callback

        def combined_callback(level: int):
            self._handle_status_update(level)
            if old_callback:
                try:
                    old_callback(level)
                except Exception as e:
                    _LOGGER.error(f"Old callback error: {e}")

        self._client._ble.set_status_callback(combined_callback)

        # Starta periodisk polling
        self._cancel_poll = async_track_time_interval(
            self.hass,
            self._async_poll_status,
            POLL_INTERVAL
        )

        # Fördröjd första polling
        async def delayed_first_poll():
            await asyncio.sleep(10)
            await self._async_poll_status(None)

        self.hass.async_create_task(delayed_first_poll())

    async def async_will_remove_from_hass(self) -> None:
        """Stop polling when removed."""
        if self._cancel_poll:
            self._cancel_poll()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_status_update(self, level: int) -> None:
        """Hantera realtids-uppdatering från värmaren."""
        _LOGGER.debug("Real-time status update: %d%%", level)
        self._attr_native_value = level
        self.async_write_ha_state()

    async def _async_poll_status(self, now=None) -> None:
        """Periodisk polling av status."""
        if self._polling:
            _LOGGER.debug("Poll already in progress, skipping")
            return

        # Skippa polling om anslutningen redan är aktiv
        if hasattr(self._client._ble, '_client') and self._client._ble._client:
            if self._client._ble._client.is_connected:
                _LOGGER.debug("Already connected, skipping poll (will get real-time updates)")
                return

        self._polling = True

        try:
            _LOGGER.debug("Polling status from heater...")

            received_status = None

            def poll_callback(level: int):
                nonlocal received_status
                received_status = level
                _LOGGER.debug(f"Polled status: {level}%")

            old_callback = self._client._ble._status_callback
            self._client._ble.set_status_callback(poll_callback)

            try:
                await self._client._ensure_initialized()

                for _ in range(30):
                    if received_status is not None:
                        break
                    await asyncio.sleep(0.1)

                if received_status is not None:
                    self._attr_native_value = received_status
                    self._last_poll = self.hass.loop.time()
                    _LOGGER.info(f"✓ Polled status: {received_status}%")

                    # Notifiera climate
                    if hasattr(self, '_climate_callback') and self._climate_callback:
                        try:
                            _LOGGER.debug(f"Calling climate callback with: {received_status}%")
                            self._climate_callback(received_status)
                            _LOGGER.debug(f"✓ Notified climate entity: {received_status}%")
                        except Exception as e:
                            _LOGGER.error(f"Climate callback failed: {e}", exc_info=True)
                else:
                    _LOGGER.warning("No status received during poll (timeout)")

            finally:
                self._client._ble.set_status_callback(old_callback)

            await asyncio.sleep(0.5)
            await self._client.disconnect()
            _LOGGER.debug("Disconnected after poll (allows app access)")

            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error(f"Status polling failed: {e}")

        finally:
            self._polling = False


class SolamagicRSSISensor(SensorEntity):
    """Sensor som visar RSSI (signalstyrka)."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client, name: str, unique_id: str) -> None:
        """Initialize the RSSI sensor."""
        self._client = client
        self._attr_name = "Signal Strength"
        self._attr_unique_id = f"{unique_id}-rssi"
        self._address = getattr(client._ble, "address", None)
        self._attr_native_value = None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._address or self.unique_id)},
        }

    async def async_update(self) -> None:
        """Update RSSI from BLE device via Home Assistant bluetooth."""
        try:
            from homeassistant.components import bluetooth

            # Hämta senaste service info från bluetooth integration
            service_info = bluetooth.async_last_service_info(
                self.hass, self._address, connectable=False
            )

            if service_info and service_info.rssi is not None:
                self._attr_native_value = service_info.rssi
                _LOGGER.debug(f"RSSI updated: {service_info.rssi} dBm from {service_info.name}")
            else:
                _LOGGER.debug(f"No RSSI service info available for {self._address}")
                # Försök alternativ metod
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self._address, connectable=False
                )
                if device:
                    _LOGGER.debug(f"Found device via address lookup: {device.name}, RSSI: {getattr(device, 'rssi', 'N/A')}")
                    if hasattr(device, 'rssi') and device.rssi is not None:
                        self._attr_native_value = device.rssi
                        _LOGGER.debug(f"RSSI updated from device: {device.rssi} dBm")
                else:
                    _LOGGER.debug(f"No device found for {self._address}")

        except Exception as e:
            _LOGGER.warning(f"Could not get RSSI: {e}", exc_info=True)


class SolamagicConnectionSensor(SensorEntity):
    """Sensor som visar anslutningsstatus."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False  # Vi uppdaterar via callbacks istället

    def __init__(self, client, name: str, unique_id: str) -> None:
        """Initialize the connection sensor."""
        self._client = client
        self._attr_name = "Connection Status"
        self._attr_unique_id = f"{unique_id}-connection"
        self._address = getattr(client._ble, "address", None)
        self._attr_native_value = "disconnected"
        self._remove_listener = None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._address or self.unique_id)},
        }

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        attrs = {"address": self._address}

        # Kontrollera om vi har en aktiv Bleak client
        try:
            if hasattr(self._client._ble, '_client') and self._client._ble._client:
                attrs["has_client"] = True
                attrs["is_connected"] = self._client._ble._client.is_connected
            else:
                attrs["has_client"] = False
                attrs["is_connected"] = False
        except Exception as e:
            _LOGGER.debug(f"Could not get client info: {e}")
            attrs["error"] = str(e)

        return attrs

    async def async_added_to_hass(self) -> None:
        """Set up connection monitoring when added to hass."""
        await super().async_added_to_hass()

        # Starta en polling-loop som kollar varje sekund
        async def check_connection(now=None):
            """Check connection status frequently."""
            try:
                old_value = self._attr_native_value

                if hasattr(self._client._ble, '_client') and self._client._ble._client:
                    if self._client._ble._client.is_connected:
                        self._attr_native_value = "connected"
                    else:
                        self._attr_native_value = "disconnected"
                else:
                    self._attr_native_value = "disconnected"

                # Uppdatera bara om status ändrats
                if old_value != self._attr_native_value:
                    _LOGGER.info(f"Connection status changed: {old_value} → {self._attr_native_value}")
                    self.async_write_ha_state()

            except Exception as e:
                _LOGGER.debug(f"Could not check connection status: {e}")

        # Kör check varje sekund
        self._remove_listener = async_track_time_interval(
            self.hass,
            check_connection,
            timedelta(seconds=1)
        )

        # Kör en första check direkt
        await check_connection()

    async def async_will_remove_from_hass(self) -> None:
        """Stop monitoring when removed."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()