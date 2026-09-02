#!/usr/bin/python3
# sudo hciconfig hci1 inqdata 0e0972617370626572727970693031020a00091002006b1d460237051107FFCACADEAFDECADEDEFACADE000000000d0950455547454f2d3633383400110600000000DECAFADEDECADEAFDECACAFF1107D31FBF505D572797A24041CD484388EC
import asyncio
import socket
import threading
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BUS_NAME = 'org.bluez'
PROFILE_INTERFACE = 'org.bluez.Profile1'
AGENT_INTERFACE = 'org.bluez.Agent1'


def ask(prompt):
    try:
        return raw_input(prompt)
    except:
        return input(prompt)


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


class Agent(dbus.service.Object):
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Release(self):
        print("Release")

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        print("AuthorizeService (%s, %s)" % (device, uuid))

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        print("RequestPinCode (%s)" % (device))
        return ask("Enter PIN Code: ")

    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        print("RequestPasskey (%s)" % (device))
        passkey = ask("Enter passkey: ")
        return dbus.UInt32(passkey)

    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print("DisplayPasskey (%s, %06u entered %u)" %
              (device, passkey, entered))

    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print("DisplayPinCode (%s, %s)" % (device, pincode))

    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print("RequestConfirmation (%s, %06d)" % (device, passkey))
        time.sleep(2)

    @dbus.service.method(dbus_interface=AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        print("RequestAuthorization (%s)" % device)

    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("Cancel")


class IAPProfile(dbus.service.Object):
    def __init__(self, bus, path, on_connection, loop):
        dbus.service.Object.__init__(self, bus, path)
        self.__path = path
        self.on_connection = on_connection
        self._loop = loop

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE, in_signature='')
    def Release(self):
        print("Release")

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE,
                         in_signature='oha{sv}')
    def NewConnection(self, device, fd, opts):
        print("new bluetooth connection", device, self.__path)
        raw_fd = fd.take()
        s = socket.socket(fileno=raw_fd)
        s.setblocking(False)

        async def on_connection():
            try:
                reader, writer = await asyncio.open_connection(sock=s)
                self.on_connection(reader, writer)
            except Exception as exc:
                s.close()
                print(f"[bluez] Failed to adopt RFCOMM connection from {device}: {exc}")

        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(on_connection()))

    @dbus.service.method(dbus_interface=PROFILE_INTERFACE, in_signature='o')
    def RequestDisconnection(self, device):
        print("Disconnect")


IAP_SERVER_UUID = "00000000-deca-fade-deca-deafdecacaff"
IAP_CLIENT_UUID = "00000000-deca-fade-deca-deafdecacafe"
CHANNEL = 3
IAP_RECORD = f"""
<?xml version="1.0" encoding="UTF-8" ?>
<record>
    <attribute id="0x0001"><sequence><uuid value="{IAP_SERVER_UUID}" /></sequence></attribute>
    <attribute id="0x0002"><uint32 value="0x00000000" /></attribute>
    <attribute id="0x0004">
        <sequence>
            <sequence><uuid value="0x0100" /></sequence>
            <sequence><uuid value="0x0003" /><uint8 value="0x{CHANNEL:02x}" /></sequence>
        </sequence>
    </attribute>
    <attribute id="0x0005"><sequence><uuid value="0x1002" /></sequence></attribute>
    <attribute id="0x0006">
        <sequence>
            <uint16 value="0x656e" /><uint16 value="0x006a" /><uint16 value="0x0100" />
            <uint16 value="0x6672" /><uint16 value="0x006a" /><uint16 value="0x0110" />
            <uint16 value="0x6465" /><uint16 value="0x006a" /><uint16 value="0x0120" />
            <uint16 value="0x6a61" /><uint16 value="0x006a" /><uint16 value="0x0130" />
        </sequence>
    </attribute>
    <attribute id="0x0008"><uint8 value="0xff" /></attribute>
    <attribute id="0x0009"><sequence><sequence><uuid value="0x1101" /><uint16 value="0x0100" /></sequence></sequence></attribute>
    <attribute id="0x0100"><text value="Wireless iAP" /></attribute>
</record>
"""


class BluetoothTransport:
    def __init__(self, on_connection, loop, adapter: str = "hci0", advertise_bonjour: bool = True):
        self._glib_loop = GLib.MainLoop()
        self._loop = loop
        self.on_connection = on_connection
        self._adapter = adapter
        self._advertise_bonjour = advertise_bonjour
        self._adapter_path = f"/org/bluez/{adapter}"
        self._adapter_tag = adapter.replace("/", "_").replace(":", "").replace(".", "_")
        threading.Thread(target=self._bluetooth_thread, daemon=True).start()

    def close(self):
        if self._glib_loop:
            self._glib_loop.quit()
        self._glib_loop = None

    def _bluetooth_thread(self):
        DBusGMainLoop(set_as_default=True)
        try:
            bus = dbus.SystemBus()
            bluez = bus.get_object(BUS_NAME, '/org/bluez')
        except dbus.DBusException as exc:
            print(f"[bluez] BlueZ D-Bus service unavailable: {exc}. Bluetooth transport not started.")
            return

        try:
            adapter_obj = bus.get_object(BUS_NAME, self._adapter_path)
        except dbus.DBusException as exc:
            print(f"[bluez] Adapter {self._adapter} not found: {exc}.")
            return

        adapter = dbus.Interface(adapter_obj, dbus.PROPERTIES_IFACE)
        adapter.Set("org.bluez.Adapter1", 'Powered', True)
        adapter.Set("org.bluez.Adapter1", 'Discoverable', True)
        adapter.Set("org.bluez.Adapter1", 'Pairable', True)
        if self._advertise_bonjour:
            # Avahi is only required for the full wireless-CarPlay path. Keep it
            # out of the Bluetooth-only probe's import graph entirely.
            try:
                import iap2.carplay_bonjour as carplay_bonjour

                adapter_addr = str(adapter.Get("org.bluez.Adapter1", "Address"))
                carplay_bonjour.start_service(adapter_addr)
            except (ImportError, dbus.DBusException) as exc:
                print(f"[bluez] Bonjour unavailable, skipping advertisement: {exc}.")
        else:
            print("[bluez] CarPlay Bonjour advertisement disabled (Bluetooth-only probe mode).")

        server_path = f"/org/bluez/{self._adapter_tag}/iap_server"
        iapServerProfile = IAPProfile(bus, server_path, self.on_connection, self._loop)
        profileManager = dbus.Interface(bluez, 'org.bluez.ProfileManager1')
        profileManager.RegisterProfile(iapServerProfile, IAP_SERVER_UUID, {
            'Role': 'server',
            'Channel': dbus.types.UInt16(CHANNEL),
            'ServiceRecord': IAP_RECORD,
            'RequireAuthentication': False,
            'RequireAuthorization': False,
            'Adapter': dbus.ObjectPath(self._adapter_path),
        })

        agent = Agent(bus, f"/org/bluez/{self._adapter_tag}/iap_agent")
        agent_manager = dbus.Interface(bluez, "org.bluez.AgentManager1")
        agent_manager.RegisterAgent(agent, "KeyboardDisplay")
        agent_manager.RequestDefaultAgent(agent)
        if self._glib_loop:
            self._glib_loop.run()
        profileManager.UnregisterProfile(iapServerProfile)
        agent_manager.UnregisterAgent(agent)
