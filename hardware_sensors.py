"""Read-only AppleSMC temperature/fan sampling, run in a bounded child process.

SMC ABI, read protocol and sensor groups adapted from macmon v0.9.0 (MIT).
See third_party/macmon/PROVENANCE.json and LICENSE. This module contains only
key enumeration, key metadata and key reads; there is no SMC write operation.
"""
from __future__ import annotations

import ctypes as C
import datetime as dt
import json
import math
import re
import struct
import sys


class Version(C.Structure):
    _fields_ = [(k, C.c_uint8) for k in ('major', 'minor', 'build', 'reserved')] + [('release', C.c_uint16)]


class PowerLimits(C.Structure):
    _fields_ = [('version', C.c_uint16), ('length', C.c_uint16)] + [(k, C.c_uint32) for k in ('cpu', 'gpu', 'memory')]


class KeyInfo(C.Structure):
    _fields_ = [('size', C.c_uint32), ('type', C.c_uint32), ('attributes', C.c_uint8)]


class KeyData(C.Structure):
    _fields_ = [('key', C.c_uint32), ('version', Version), ('limits', PowerLimits),
                ('info', KeyInfo), ('result', C.c_uint8), ('status', C.c_uint8),
                ('command', C.c_uint8), ('index', C.c_uint32), ('data', C.c_uint8 * 32)]


def numeric(data, kind):
    """Decode only declared known scalar types, retaining real zero fan RPM."""
    if kind == 'flt ' and len(data) == 4:
        value = struct.unpack('<f', data)[0]
    elif kind == 'sp78' and len(data) == 2:
        value = int.from_bytes(data, 'big', signed=True) / 256
    elif kind == 'fpe2' and len(data) == 2:
        value = int.from_bytes(data, 'big') / 4
    elif kind in {'ui8 ', 'ui16', 'ui32'} and len(data) == {'ui8 ': 1, 'ui16': 2, 'ui32': 4}[kind]:
        value = int.from_bytes(data, 'big')
    else:
        return None
    return value if math.isfinite(value) else None


class SMC:
    def __init__(self):
        if sys.platform != 'darwin':
            raise OSError('AppleSMC requires macOS')
        if C.sizeof(KeyData) != 80 or KeyData.data.offset != 48:
            raise OSError('Unsupported AppleSMC ABI')
        self.io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.connection = C.c_uint32()
        self.cache = {}
        self.io.IOServiceMatching.argtypes = [C.c_char_p]
        self.io.IOServiceMatching.restype = C.c_void_p
        self.io.IOServiceGetMatchingServices.argtypes = [C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)]
        self.io.IOIteratorNext.argtypes = [C.c_uint32]
        self.io.IOIteratorNext.restype = C.c_uint32
        self.io.IORegistryEntryGetName.argtypes = [C.c_uint32, C.c_void_p]
        self.io.IOServiceOpen.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)]
        self.io.IOServiceClose.argtypes = [C.c_uint32]
        self.io.IOObjectRelease.argtypes = [C.c_uint32]
        self.io.IOConnectCallStructMethod.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p, C.POINTER(C.c_size_t)]
        iterator = C.c_uint32()
        matching = self.io.IOServiceMatching(b'AppleSMC')
        if not matching or self.io.IOServiceGetMatchingServices(0, matching, C.byref(iterator)):
            raise OSError('AppleSMC service unavailable')
        system = C.CDLL('/usr/lib/libSystem.B.dylib')
        task = C.c_uint32.in_dll(system, 'mach_task_self_').value
        try:
            for _ in range(64):
                device = self.io.IOIteratorNext(iterator)
                if not device:
                    break
                try:
                    name = C.create_string_buffer(128)
                    if self.io.IORegistryEntryGetName(device, name) == 0 and name.value == b'AppleSMCKeysEndpoint':
                        if self.io.IOServiceOpen(device, task, 0, C.byref(self.connection)):
                            raise OSError('AppleSMC read access unavailable')
                        break
                finally:
                    self.io.IOObjectRelease(device)
        finally:
            self.io.IOObjectRelease(iterator)
        if not self.connection.value:
            raise OSError('AppleSMC keys endpoint unavailable')

    def close(self):
        if self.connection.value:
            self.io.IOServiceClose(self.connection)
            self.connection.value = 0

    def call(self, command, *, key=0, index=0, info=None):
        if command not in {5, 8, 9}:
            raise ValueError('Only SMC read commands are supported')
        request = KeyData(key=key, command=command, index=index)
        if info is not None:
            request.info = info
        response = KeyData()
        size = C.c_size_t(C.sizeof(response))
        result = self.io.IOConnectCallStructMethod(self.connection, 2, C.byref(request), C.sizeof(request), C.byref(response), C.byref(size))
        if result or size.value != C.sizeof(response) or response.result:
            raise OSError('SMC reading unavailable')
        return response

    def read(self, key):
        if len(key) != 4 or not key.isascii():
            raise ValueError('SMC keys must have four ASCII bytes')
        code = int.from_bytes(key.encode('ascii'), 'big')
        if code not in self.cache:
            self.cache[code] = self.call(9, key=code).info
        info = self.cache[code]
        if not 0 < info.size <= 32:
            raise OSError('Invalid SMC payload size')
        result = self.call(5, key=code, info=info)
        return bytes(result.data[:info.size]), info.type.to_bytes(4, 'big').decode('ascii', 'replace')

    def keys(self):
        count = numeric(*self.read('#KEY'))
        if count is None or count != int(count) or not 0 <= count <= 8192:
            raise OSError('SMC key count unavailable or exceeds bound')
        keys = []
        for i in range(int(count)):
            try:
                key = self.call(8, index=i).key.to_bytes(4, 'big').decode('ascii')
                if key.isprintable():
                    keys.append(key)
            except (OSError, UnicodeError):
                continue
        return sorted(set(keys))


def collect(smc):
    keys = smc.keys()
    temperatures, fans = [], []
    skipped = 0
    for key in keys:
        group = 'cpu' if key.startswith(('Tp', 'Te', 'Ts')) else 'gpu' if key.startswith('Tg') else None
        is_fan = re.fullmatch(r'F[0-9A-F]Ac', key) is not None
        if not group and not is_fan:
            continue
        if group and len(temperatures) >= 128 or is_fan and len(fans) >= 16:
            continue
        try:
            data, kind = smc.read(key)
            value = numeric(data, kind) if not group or kind in {'flt ', 'sp78'} else None
        except (OSError, ValueError):
            value = None
        if group:
            if value is None or not 0 < value < 150:
                skipped += 1
                continue
            temperatures.append({'key': key, 'group': group, 'celsius': round(value, 2)})
        elif value is None or not 0 <= value <= 30000:
            fans.append({'key': key, 'name': 'Fan ' + str(int(key[1], 16) + 1), 'rpm': None, 'reason': 'Sensor reading unavailable'})
        else:
            fans.append({'key': key, 'name': 'Fan ' + str(int(key[1], 16) + 1), 'rpm': round(value)})
    averages = {}
    for group in ('cpu', 'gpu'):
        values = [t['celsius'] for t in temperatures if t['group'] == group]
        averages[group + '_celsius'] = round(sum(values) / len(values), 2) if values else None
    return {'available': bool(temperatures or any(f['rpm'] is not None for f in fans)),
            'sampled_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'source': 'AppleSMC',
            'temperature': {**averages, 'sensors': temperatures, 'excluded_readings': skipped,
                            'reason': None if temperatures else 'No supported temperature readings reported'},
            'fans': fans, 'fan_reason': None if fans else 'No fan sensors reported',
            'sensor_group_note': 'CPU/GPU groups follow macmon SMC key conventions; sensor locations are not independently identified.'}


def main():
    smc = None
    try:
        smc = SMC()
        value = collect(smc)
    except (OSError, ValueError) as exc:
        value = {'available': False, 'sampled_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'source': 'AppleSMC', 'reason': 'Native sensor access unavailable: ' + type(exc).__name__,
                 'temperature': {'cpu_celsius': None, 'gpu_celsius': None, 'sensors': []}, 'fans': []}
    finally:
        if smc is not None:
            smc.close()
    print(json.dumps(value, allow_nan=False))


if __name__ == '__main__':
    main()
