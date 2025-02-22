"""Bluetooth support for esphome."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from functools import partial
import logging
from typing import Any

from aioesphomeapi import APIClient, BluetoothProxyFeature

from homeassistant.components.bluetooth import (
    HaBluetoothConnector,
    async_register_scanner,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback as hass_callback

from ..entry_data import RuntimeEntryData
from .cache import ESPHomeBluetoothCache
from .client import ESPHomeClient, ESPHomeClientData
from .device import ESPHomeBluetoothDevice
from .scanner import ESPHomeScanner

_LOGGER = logging.getLogger(__name__)


@hass_callback
def _async_can_connect(
    entry_data: RuntimeEntryData, bluetooth_device: ESPHomeBluetoothDevice, source: str
) -> bool:
    """Check if a given source can make another connection."""
    can_connect = bool(entry_data.available and bluetooth_device.ble_connections_free)
    _LOGGER.debug(
        (
            "%s [%s]: Checking can connect, available=%s, ble_connections_free=%s"
            " result=%s"
        ),
        entry_data.name,
        source,
        entry_data.available,
        bluetooth_device.ble_connections_free,
        can_connect,
    )
    return can_connect


@hass_callback
def _async_unload(unload_callbacks: list[CALLBACK_TYPE]) -> None:
    """Cancel all the callbacks on unload."""
    for callback in unload_callbacks:
        callback()


async def async_connect_scanner(
    hass: HomeAssistant,
    entry: ConfigEntry,
    cli: APIClient,
    entry_data: RuntimeEntryData,
    cache: ESPHomeBluetoothCache,
) -> CALLBACK_TYPE:
    """Connect scanner."""
    assert entry.unique_id is not None
    source = str(entry.unique_id)
    device_info = entry_data.device_info
    assert device_info is not None
    feature_flags = device_info.bluetooth_proxy_feature_flags_compat(
        entry_data.api_version
    )
    connectable = bool(feature_flags & BluetoothProxyFeature.ACTIVE_CONNECTIONS)
    bluetooth_device = ESPHomeBluetoothDevice(entry_data.name, device_info.mac_address)
    entry_data.bluetooth_device = bluetooth_device
    _LOGGER.debug(
        "%s [%s]: Connecting scanner feature_flags=%s, connectable=%s",
        entry.title,
        source,
        feature_flags,
        connectable,
    )
    client_data = ESPHomeClientData(
        bluetooth_device=bluetooth_device,
        cache=cache,
        client=cli,
        device_info=device_info,
        api_version=entry_data.api_version,
        title=entry.title,
        scanner=None,
        disconnect_callbacks=entry_data.disconnect_callbacks,
    )
    connector = HaBluetoothConnector(
        # MyPy doesn't like partials, but this is correct
        # https://github.com/python/mypy/issues/1484
        client=partial(ESPHomeClient, client_data=client_data),  # type: ignore[arg-type]
        source=source,
        can_connect=hass_callback(
            partial(_async_can_connect, entry_data, bluetooth_device, source)
        ),
    )
    scanner = ESPHomeScanner(source, entry.title, connector, connectable)
    client_data.scanner = scanner
    coros: list[Coroutine[Any, Any, CALLBACK_TYPE]] = []
    # These calls all return a callback that can be used to unsubscribe
    # but we never unsubscribe so we don't care about the return value

    if connectable:
        # If its connectable be sure not to register the scanner
        # until we know the connection is fully setup since otherwise
        # there is a race condition where the connection can fail
        coros.append(
            cli.subscribe_bluetooth_connections_free(
                bluetooth_device.async_update_ble_connection_limits
            )
        )

    if feature_flags & BluetoothProxyFeature.RAW_ADVERTISEMENTS:
        coros.append(
            cli.subscribe_bluetooth_le_raw_advertisements(
                scanner.async_on_raw_advertisements
            )
        )
    else:
        coros.append(
            cli.subscribe_bluetooth_le_advertisements(scanner.async_on_advertisement)
        )

    await asyncio.gather(*coros)
    return partial(
        _async_unload,
        [
            async_register_scanner(hass, scanner, connectable),
            scanner.async_setup(),
        ],
    )
