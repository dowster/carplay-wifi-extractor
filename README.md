# iAP2 CarPlay WiFi Credential Extraction

## Overview

Research project implementing the **iAP2 (iPod Accessory Protocol v2)** — Apple's proprietary
protocol for communication between iPhones and accessories (headsets, car head units, etc.).
In the context of **Wireless CarPlay**, the protocol runs over Bluetooth RFCOMM and is used
to establish a WiFi connection between the iPhone and an IVI (In-Vehicle Infotainment) system.

### Research Finding

When an iPhone connects to a CarPlay head unit over Bluetooth, it goes through an
**Identification → Authentication → WiFi Config exchange** flow. In the final step, the head
unit sends the car's WiFi hotspot SSID and passphrase in plaintext inside an
`AccessoryWiFiConfigurationInformation` message (CSM ID `0x5703`).

---

## System Architecture

```
┌─────────────────────┐        Bluetooth RFCOMM        ┌─────────────────────────┐
│   iPhone / Client   │◄──────────────────────────────►│  CarPlay Accessory      │
│  (iDevice / emulated│        iAP2 Protocol            │  (IVI Head Unit / fake) │
│   via hciX adapter) │                                 │  (hciX + Python stack)  │
└─────────────────────┘                                 └─────────────────────────┘
```

**Two operating modes:**

| Mode | File | Role | Purpose |
|------|------|------|---------|
| Server (Accessory) | `__main__.py` | Emulates a car head unit | Real iPhone connects in, receives fake WiFi config |
| Client (iPhone) | `client/__main__.py` | Emulates an iPhone | Connects to a real car, extracts WiFi credentials |

---

## iAP2 Protocol Flow — Step by Step

### Phase 0: Link Layer Handshake (byte-level)

```
IVI     ──► Adapter :  ff 55 02 00 ee 10   (SYN)
Adapter ──► IVI     :  ff 55 02 00 ee 10   (SYN-ACK)
```

Then parameters are negotiated (max packet size, retransmit timeout, session config):

```
IVI     ──► Adapter :  ff 5a 00 1a 80 e8 00 00 25 01 40 20 ...  (NEGO req)
Adapter ──► IVI     :  ff 5a 00 1a c0 1d e8 00 c8 01 7f ff ...  (NEGO resp)
```

Frame format:

```
┌──────┬──────┬───────────┬──────────┬──────────┬────────────┬──────────┐
│ 0xFF │ 0x5A │ Length(2B)│ SeqNum   │ AckNum   │  Payload   │ Checksum │
└──────┴──────┴───────────┴──────────┴──────────┴────────────┴──────────┘
```

---

### Phase 1: Identification

```
Adapter ──► IVI     :  StartIdentification          (CSM 0x1D00)
IVI     ──► Adapter :  IdentificationInformation   (CSM 0x1D01)
              │
              ├── name:                "Sonata"
              ├── model_identifier:   "DN8"
              ├── manufacturer:       "HYUNDAI MOBIS"
              ├── serial_number:      "28cf08ab633b"
              ├── firmware_version:   "DN8.USA.D2V.001.003"
              ├── bluetooth_transport_component  (MAC, supports_iap2_connection)
              ├── vehicle_information_component  (engine_type)
              ├── vehicle_status_component
              └── wireless_car_play_transport_component

Adapter ──► IVI     :  IdentificationAccepted       (CSM 0x1D02)
```

In server mode (fake accessory), the code responds with:

```python
IdentificationInformation(
    name="raspberrypi",
    manufacturer="wiomoc",
    wireless_car_play_transport_component=WirelessCarPlayTransportComponent(
        supports_car_play=True
    )
)
```

---

### Phase 2: MFi Authentication

This is the most critical step — Apple requires accessories to have an **MFi co-processor**
(hardware chip) to sign the challenge. This project replaces it with a software certificate/key.

```
Adapter ──► IVI     :  RequestAuthenticationCertificate       (0xAA00)
IVI     ──► Adapter :  AuthenticationCertificate              (0xAA01)
              └── certificate: [DER-encoded X.509, ~920 bytes]
                  ├── 30 82 03 88 06 09 2a 86 48 86 f7 0d 01 07 02 ...
                  └── Apple-signed MFi leaf certificate

Adapter ──► IVI     :  RequestAuthenticationChallengeResponse (0xAA02)
              └── challenge: [20 bytes random]
                  └── 79 70 4e d5 f2 dc 9c 7d 43 f3 54 ec 44 a4 c7 5b ...

IVI     ──► Adapter :  AuthenticationResponse                 (0xAA03)
              └── response: [128 bytes ECDSA/RSA signature]
                  └── 3b 24 7a 19 40 6e 1c 52 f5 5d 3c 98 6b ed d8 30 ...

Adapter ──► IVI     :  AuthenticationSucceeded                (0xAA05)
```

---

### Phase 3: WiFi Credential Exchange (target)

```
Adapter ──► IVI     :  RequestAccessoryWiFiConfigurationInformation  (0x5702)

IVI     ──► Adapter :  AccessoryWiFiConfigurationInformation         (0x5703)
              ├── ssid:          "teslamodelx"        ← plaintext!
              ├── passphrase:    "testtest12"          ← plaintext!
              ├── security_type: WPA_WPA2 (2)
              └── channel:       10
```

Message encoding (TLV — Type-Length-Value):

```
Tag(2B) | Len(2B) | Value
0x5703  | total   | [TLV fields]
  0x01  | len     | SSID bytes
  0x02  | len     | Passphrase bytes
  0x03  | 01      | SecurityType byte
  0x04  | 01      | Channel byte
```

---

## Sequence Diagram — Client Mode (extract credentials from a real car)

```
  Python Client                          CarPlay IVI (real car)
       │                                          │
       │──── BT RFCOMM connect ─────────────────►│
       │◄─── Link SYN / SYN-ACK ─────────────────│
       │──── Params NEGO ───────────────────────►│
       │◄─── Params NEGO ────────────────────────│
       │                                          │
       │◄─── StartIdentification ────────────────│  ← IVI initiates
       │──── IdentificationInformation ─────────►│  ← fake iPhone info
       │◄─── IdentificationAccepted ─────────────│
       │                                          │
       │──── RequestAuthCertificate ────────────►│
       │◄─── AuthCertificate (MFi cert) ──────────│  ← capture cert
       │──── RequestChallengeResponse ──────────►│  ← send challenge
       │◄─── AuthResponse (signature) ────────────│  ← capture sig
       │──── AuthenticationSucceeded ───────────►│  ← skip verify!
       │                                          │
       │──── RequestAccessoryWiFiConfig ────────►│
       │◄─── AccessoryWiFiConfigInfo ─────────────│  ← SSID + passphrase
       │                                          │
  [print summary]
  SSID:       ...
  Passphrase: ...
```

---

## CSM Message ID Reference

| Message | ID (hex) | Direction |
|---------|----------|-----------|
| `RequestAuthenticationCertificate` | `0xAA00` | Client → Accessory |
| `AuthenticationCertificate` | `0xAA01` | Accessory → Client |
| `RequestAuthenticationChallengeResponse` | `0xAA02` | Client → Accessory |
| `AuthenticationResponse` | `0xAA03` | Accessory → Client |
| `AuthenticationFailed` | `0xAA04` | Client → Accessory |
| `AuthenticationSucceeded` | `0xAA05` | Client → Accessory |
| `StartIdentification` | `0x1D00` | Client → Accessory |
| `IdentificationInformation` | `0x1D01` | Accessory → Client |
| `IdentificationAccepted` | `0x1D02` | Client → Accessory |
| `IdentificationRejected` | `0x1D03` | Client → Accessory |
| `RequestAccessoryWiFiConfigurationInformation` | `0x5702` | Client → Accessory |
| `AccessoryWiFiConfigurationInformation` | `0x5703` | Accessory → Client |
| `WirelessCarPlayUpdate` | CarPlay ns | Bidirectional |
| `DeviceTransportIdentifierNotification` | CarPlay ns | Accessory → Client |

---

## Tech Stack

- **Transport:** Bluetooth RFCOMM via BlueZ (`hciX` adapter)
- **Link Layer:** iAP2 custom framing (`ff 55` / `ff 5a` markers, sequence numbers, ACK)
- **Session Layer:** Control Session Messages (TLV-encoded)
- **Auth:** MFi X.509 certificate + ECDSA challenge-response
- **Runtime:** Python 3.11+ / asyncio

---

## Key Files

| File | Role |
|------|------|
| `example/iap2/__main__.py` | Accessory server — fake head unit |
| `example/iap2/client/__main__.py` | Client — fake iPhone, extract WiFi creds |
| `example/iap2/link_layer.py` | iAP2 link-layer framing (SYN, NEGO, ACK) |
| `example/iap2/control_session_message/wifi.py` | WiFi CSM messages (0x5702, 0x5703) |
| `example/iap2/control_session_message/authentication.py` | Auth CSM (0xAA00–0xAA05) |
| `example/iap2/mfi_auth_coprocessor.py` | Read cert + sign challenge |
| `example/iap2/transport/bluetooth.py` | BlueZ RFCOMM server |
| `example/iap2/client/bluetooth.py` | BlueZ RFCOMM client |

## Run

```
cd d:\projectPT\iap2\example
python -m iap2.client --mac AA:BB:CC:DD:EE:FF --channel 3
```

## References

https://wiomoc.de/misc/posts/mfi_iap.html
https://www.oligo.security/blog/pwn-my-ride-exploring-the-carplay-attack-surface
