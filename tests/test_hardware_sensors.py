import ctypes
import json
import math
from pathlib import Path
import struct
import subprocess
import unittest
from unittest.mock import patch

import hardware_sensors as H
import telemetry as T


class Sensors:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return list(self.values)

    def read(self, key):
        value = self.values[key]
        if isinstance(value, Exception):
            raise value
        return value


def floating(value):
    return struct.pack('<f', value), 'flt '


class SensorTests(unittest.TestCase):
    def test_declared_numeric_types_and_invalid_values(self):
        self.assertEqual(H.numeric(*floating(48.5)), 48.5)
        self.assertEqual(H.numeric(bytes.fromhex('1388'), 'fpe2'), 1250)
        self.assertEqual(H.numeric(bytes.fromhex('ff80'), 'sp78'), -.5)
        for kind, data, value in [('ui8 ', b'\0', 0), ('ui16', b'\x03\xe8', 1000), ('ui32', b'\0\0\x03\xe8', 1000)]:
            self.assertEqual(H.numeric(data, kind), value)
        for data, kind in [(b'', 'flt '), (b'\0'*8, 'flt '), (b'\0', 'fpe2'), (b'\0'*4, 'unknown'), (floating(math.nan)[0], 'flt ')]:
            self.assertIsNone(H.numeric(data, kind))

    def test_partial_readings_preserve_stopped_fan_without_inventing_temperature(self):
        value = H.collect(Sensors({'Tp01': floating(0), 'Te01': floating(50), 'Tp02': floating(60),
                                   'Tg01': OSError(), 'Ts01': (b'\x32', 'ui8 '), 'F0Ac': floating(0),
                                   'F1Ac': OSError(), 'private': floating(90)}))
        self.assertTrue(value['available'])
        self.assertEqual(value['temperature']['cpu_celsius'], 55)
        self.assertIsNone(value['temperature']['gpu_celsius'])
        self.assertEqual(value['temperature']['excluded_readings'], 3)
        self.assertEqual(value['fans'][0]['rpm'], 0)
        self.assertIsNone(value['fans'][1]['rpm'])
        self.assertNotIn('private', json.dumps(value))

    def test_absent_or_invalid_sensor_values_are_unavailable(self):
        value = H.collect(Sensors({'Tp01': floating(-127), 'Tg01': floating(math.inf), 'F0Ac': floating(-1)}))
        self.assertFalse(value['available'])
        self.assertIsNone(value['temperature']['cpu_celsius'])
        self.assertIsNone(value['fans'][0]['rpm'])
        self.assertIn('reason', value['fans'][0])
        empty = H.collect(Sensors({}))
        self.assertFalse(empty['available'])
        self.assertEqual(empty['fans'], [])
        self.assertEqual(empty['fan_reason'], 'No fan sensors reported')

    def test_abi_and_read_only_command_allowlist(self):
        self.assertEqual(ctypes.sizeof(H.KeyData), 80)
        self.assertEqual(H.KeyData.data.offset, 48)
        smc = object.__new__(H.SMC)
        for command in (0, 1, 2, 3, 4, 6, 7, 10):
            with self.assertRaises(ValueError):
                smc.call(command)

    def test_key_count_and_payload_size_are_bounded(self):
        smc = object.__new__(H.SMC)
        with patch.object(smc, 'read', return_value=(b'\0\0\x40\0', 'ui32')):
            with self.assertRaises(OSError):
                smc.keys()
        smc.cache = {int.from_bytes(b'Tp01', 'big'): H.KeyInfo(size=33, type=0)}
        with self.assertRaises(OSError):
            smc.read('Tp01')

    def test_native_helper_is_cached_and_failed_refresh_drops_old_readings(self):
        probe = object.__new__(T.MacProbe)
        probe.hardware_checked = None
        probe.hardware_cache = None
        document = {'available': True, 'sampled_at': 'fixture', 'temperature': {'cpu_celsius': 50}, 'fans': []}
        with patch.object(T.time, 'monotonic', side_effect=[0, 2, 10, 11]), patch.object(probe, 'run', side_effect=[json.dumps(document).encode(), subprocess.TimeoutExpired('native helper', 1.2)]) as run:
            first = probe.hardware()
            first['temperature']['cpu_celsius'] = 999
            self.assertEqual(probe.hardware()['temperature']['cpu_celsius'], 50)
            self.assertFalse(probe.hardware()['available'])
            self.assertFalse(probe.hardware()['available'])
            self.assertEqual(run.call_count, 2)
            command = run.call_args_list[0].args[0]
            self.assertEqual(command[1:3], ['-I', '-B'])
            self.assertEqual(Path(command[-1]).name, 'hardware_sensors.py')


if __name__ == '__main__':
    unittest.main()
