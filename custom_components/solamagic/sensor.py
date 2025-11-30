"""Solamagic Sensors - Power Level, RSSI, Connection Status."""
from __future__ import annotations
import asyncio
import logging
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, get_device_info

_LOGGER = logging.getLogger(__name__)

# Polling interval for status updates
POLL_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solamagic sensors from config entry."""
    client = hass.data[DOMAIN][entry.entry_id]
    name = entry.title or entry.data.get("address") or "Solamagic"

    sensors = [
        SolamagicPowerSensor(client, name, entry.entry_id),
        SolamagicRSSISensor(client, name, entry.entry_id),
        SolamagicConnectionSensor(client, name, entry.entry_id),
    ]

    async_add_entities(sensors, True)


class SolamagicPowerSensor(SensorEntity):
    """
    Sensor that reads power level from the heater.

    This sensor polls the heater periodically and also receives real-time
    updates via Bluetooth notifications.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:radiator"
    _attr_suggested_display_precision = 0

    def __init__(self, client, name: str, unique_id: str) -> None:
        """
        Initialize the power sensor.

        Args:
            client: SolamagicClient instance
            name: Entity name (from entry.title)
            unique_id: Unique identifier
        """
        self._client = client
        self._attr_name = "Power Level"  # Suffix added to device name
        self._attr_unique_id = f"{unique_id}-power"
        self._address = getattr(client._ble, "address", None)
        self._entry_title = name  # Save for get_device_info

        # Current state
        self._attr_native_value = 0
        self._polling = False
        self._cancel_poll = None

        _LOGGER.debug("[%s] Initialized power sensor: %s (poll interval=%s)", self._address, name, POLL_INTERVAL)

    @property
    def device_info(self):
        """Return device information for device registry."""
        return get_device_info(self._address, self._entry_title)

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        return {
            "poll_interval_minutes": POLL_INTERVAL.total_seconds() / 60,
            "last_poll": getattr(self, "_last_poll", "Never"),
        }

    async def async_added_to_hass(self) -> None:
        """Start polling when added to hass."""
        await super().async_added_to_hass()

        # Register callback for real-time status updates
        self._client._ble.set_status_callback(self._handle_status_update)

        # Start periodic polling
        self._cancel_poll = async_track_time_interval(
            self.hass,
            self._async_poll_status,
            POLL_INTERVAL,
        )

        # Delayed first poll to avoid startup congestion
        async def delayed_first_poll():
            """Wait before first poll."""
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
        """
        Handle real-time status update from heater.

        Args:
            level: Power level in percent (0, 33, 66, 100)
        """
        _LOGGER.debug("[%s] Real-time status update: %d%%", self._address, level)
        self._attr_native_value = level
        self.async_write_ha_state()

    async def _async_poll_status(self, now=None) -> None:
        """
        Periodic polling of status.

        This connects to the heater, reads status, and disconnects.
        Allows the mobile app to connect when we're not actively using HA.

        Args:
            now: Current time (from async_track_time_interval)
        """
        if self._polling:
            _LOGGER.debug("[%s] Poll already in progress, skipping", self._address)
            return

        self._polling = True

        try:
            _LOGGER.debug("[%s] Polling status from heater...", self._address)

            received_status = None

            def poll_callback(level: int):
                """Temporary callback to capture polled status."""
                nonlocal received_status
                received_status = level
                _LOGGER.debug("[%s] Polled status: %d%%", self._address, level)

            # Temporarily replace callback
            old_callback = self._client._ble._status_callback
            self._client._ble.set_status_callback(poll_callback)

            try:
                await self._client._ensure_initialized()

                # Wait up to 3 seconds for status
                for _ in range(30):
                    if received_status is not None:
                        break
                    await asyncio.sleep(0.1)

                if received_status is not None:
                    self._attr_native_value = received_status
                    self._last_poll = self.hass.loop.time()
                    _LOGGER.info("[%s] Polled status: %d%%", self._address, received_status)
                else:
                    _LOGGER.warning("[%s] No status received during poll (timeout)", self._address)

            finally:
                # Restore original callback
                self._client._ble.set_status_callback(old_callback)

            # Short delay before disconnect
            await asyncio.sleep(0.5)
            await self._client.disconnect()
            _LOGGER.debug("[%s] Disconnected after poll (allows app access)", self._address)

            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("[%s] Status polling failed: %s", self._address, e)

        finally:
            self._polling = False


class SolamagicRSSISensor(SensorEntity):
    """
    Sensor that shows RSSI (signal strength).

    Uses Home Assistant's Bluetooth integration to get signal strength
    without needing an active connection.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client, name: str, unique_id: str) -> None:
        """
        Initialize the RSSI sensor.

        Args:
            client: SolamagicClient instance
            name: Entity name (from entry.title)
            unique_id: Unique identifier
        """
        self._client = client
        self._attr_name = "Signal Strength"  # Suffix added to device name
        self._attr_unique_id = f"{unique_id}-rssi"
        self._address = getattr(client._ble, "address", None)
        self._attr_native_value = None
        self._entry_title = name  # Save for get_device_info

    @property
    def device_info(self):
        """Return device information for device registry."""
        return get_device_info(self._address, self._entry_title)

    async def async_update(self) -> None:
        """
        Update RSSI from BLE device via Home Assistant bluetooth.

        This reads signal strength from the last Bluetooth advertisement,
        so it works even when the device is not connected.
        """
        try:
            from homeassistant.components import bluetooth

            # Get latest service info from bluetooth integration
            service_info = bluetooth.async_last_service_info(
                self.hass, self._address, connectable=False
            )

            if service_info and service_info.rssi is not None:
                self._attr_native_value = service_info.rssi
                _LOGGER.debug("[%s] RSSI updated: %d dBm from %s", self._address, service_info.rssi, service_info.name)
            else:
                _LOGGER.debug("[%s] No RSSI service info available", self._address)
                # Try alternative method
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self._address, connectable=False
                )
                if device:
                    _LOGGER.debug("[%s] Found device via address lookup: %s, RSSI: %s", self._address, device.name, getattr(device, "rssi", "N/A"))
                    if hasattr(device, "rssi") and device.rssi is not None:
                        self._attr_native_value = device.rssi
                        _LOGGER.debug("[%s] RSSI updated from device: %d dBm", self._address, device.rssi)
                else:
                    _LOGGER.debug("No device found for %s", self._address)

        except Exception as e:
            _LOGGER.warning("[%s] Could not get RSSI: %s", self._address, e, exc_info=True)


class SolamagicConnectionSensor(SensorEntity):
    """
    Sensor that shows connection status.

    Monitors whether the Bluetooth connection is active.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False  # We update via callbacks instead

    def __init__(self, client, name: str, unique_id: str) -> None:
        """
        Initialize the connection sensor.

        Args:
            client: SolamagicClient instance
            name: Entity name (from entry.title)
            unique_id: Unique identifier
        """
        self._client = client
        self._attr_name = "Connection Status"  # Suffix added to device name
        self._attr_unique_id = f"{unique_id}-connection"
        self._address = getattr(client._ble, "address", None)
        self._attr_native_value = "disconnected"
        self._remove_listener = None
        self._entry_title = name  # Save for get_device_info

    @property
    def device_info(self):
        """Return device information for device registry."""
        return get_device_info(self._address, self._entry_title)

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attrs = {"address": self._address}

        # Check if we have an active Bleak client
        try:
            if (
                hasattr(self._client._ble, "_client")
                and self._client._ble._client
            ):
                attrs["has_client"] = True
                attrs["is_connected"] = self._client._ble._client.is_connected
            else:
                attrs["has_client"] = False
                attrs["is_connected"] = False
        except Exception as e:
            _LOGGER.debug("[%s] Could not get client info: %s", self._address, e)
            attrs["error"] = str(e)

        return attrs

    async def async_added_to_hass(self) -> None:
        """Set up connection monitoring when added to hass."""
        await super().async_added_to_hass()

        # Start a polling loop that checks every second
        async def check_connection(now=None):
            """Check connection status frequently."""
            try:
                old_value = self._attr_native_value

                if (
                    hasattr(self._client._ble, "_client")
                    and self._client._ble._client
                ):
                    if self._client._ble._client.is_connected:
                        self._attr_native_value = "connected"
                    else:
                        self._attr_native_value = "disconnected"
                else:
                    self._attr_native_value = "disconnected"

                # Only update if status changed
                if old_value != self._attr_native_value:
                    _LOGGER.info("[%s] Connection status changed: %s → %s", self._address, old_value, self._attr_native_value)
                    self.async_write_ha_state()

            except Exception as e:
                _LOGGER.debug("[%s] Could not check connection status: %s", self._address, e)

        # Run check every second
        self._remove_listener = async_track_time_interval(
            self.hass,
            check_connection,
            timedelta(seconds=1),
        )

        # Run first check immediately
        await check_connection()

    async def async_will_remove_from_hass(self) -> None:
        """Stop monitoring when removed."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()