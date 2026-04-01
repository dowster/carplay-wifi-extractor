"""
Minimal BlueZ profile registration for the iPhone-side CarPlay UUID.

CarPlay accessories expect the phone to advertise an RFCOMM profile under
``00000000-deca-fade-deca-deafdecacafe``. The accessory code already does
this when running as a server, but the client emulator needs to register
the same UUID so tooling such as ``sdptool`` can discover it while tests
are running.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BUS_NAME = "org.bluez"
PROFILE_INTERFACE = "org.bluez.Profile1"
IAP_CLIENT_UUID = "00000000-deca-fade-deca-deafdecacafe"
DEFAULT_CHANNEL = 3


class _ClientProfile(dbus.service.Object):
    def __init__(self, bus, path: str):
        super().__init__(bus, path)
        self._path = path

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        print("Client profile released")

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE, in_signature="oha{sv}")
    def NewConnection(self, device, fd, _opts):
        # Accessories are not expected to initiate RFCOMM connections toward
        # the iPhone profile during Wi-Fi provisioning, but BlueZ still calls
        # this method if it happens. We just close the file descriptor to avoid
        # leaking resources.
        print(f"Unexpected inbound connection for CarPlay client profile: {device} on {self._path}")
        os.close(fd.take())

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE, in_signature="o")
    def RequestDisconnection(self, device):
        print(f"RequestDisconnection for CarPlay client profile: {device}")


class ClientBluetoothService:
    """
    Registers the CarPlay client UUID with BlueZ so that sdptool/peers can see it.

    The class mirrors the accessory's Bluetooth transport but only exposes the
    phone/iPhone side profile. It runs the GLib main loop in a background thread
    and keeps it alive until :meth:`close` is called.
    """

    def __init__(self, adapter: str = "hci0", channel: int = DEFAULT_CHANNEL):
        self._adapter_path = f"/org/bluez/{adapter}"
        self._channel = channel
        self._glib_loop: Optional[GLib.MainLoop] = GLib.MainLoop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        if self._glib_loop:
            self._glib_loop.quit()
            self._glib_loop = None
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def _run(self):
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        bluez = bus.get_object(BUS_NAME, "/org/bluez")
        profile_manager = dbus.Interface(bluez, "org.bluez.ProfileManager1")
        profile = _ClientProfile(bus, "/org/bluez/iap2_client_emulator")
        opts = {
            "Role": "client",
            "Channel": dbus.types.UInt16(self._channel),
            "AutoConnect": True,
            "Adapter": dbus.ObjectPath(self._adapter_path),
        }
        profile_manager.RegisterProfile(profile, IAP_CLIENT_UUID, opts)

        adapter = dbus.Interface(bus.get_object(BUS_NAME, self._adapter_path), dbus.PROPERTIES_IFACE)
        adapter.Set("org.bluez.Adapter1", "Powered", True)
        adapter.Set("org.bluez.Adapter1", "Discoverable", True)
        adapter.Set("org.bluez.Adapter1", "Pairable", True)
        try:
            if self._glib_loop:
                self._glib_loop.run()
        finally:
            profile_manager.UnregisterProfile(profile)
