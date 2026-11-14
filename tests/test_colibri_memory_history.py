import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import colibri_provider as G


class MemoryHistory(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        directory=patch.object(G,'DIRECTORY',self.root);directory.start();self.addCleanup(directory.stop)
        self.native=G.NativeSession({'model':G.MODEL,'parameters_sha256':'a'*64})
        self.native.begin_request('b'*32,{'private':'not retained in memory diagnostics'})
        self.native.profile={'version':2,'charged_limit_bytes':26_000_000_000}
        (self.native.directory/'native.log').write_text('')

    def watch(self,values,callback=None):
        native=self.native;clock=[0];position=[-1];waits=[]
        class Ticks:
            stopped=False
            def wait(self,seconds):
                waits.append(seconds)
                if self.stopped:return True
                position[0]+=1;clock[0]=position[0]*5
                return position[0]>=len(values)
            def set(self):self.stopped=True
            def is_set(self):return self.stopped
        native.stop=Ticks()
        def capture():
            if callback:callback(position[0])
            value=values[position[0]]
            if isinstance(value,Exception):raise value
            return value
        with patch.object(G.time,'monotonic',side_effect=lambda:clock[0]),patch.object(G.time,'time',side_effect=lambda:100+clock[0]),\
             patch.object(native,'capture',side_effect=capture),patch.object(G.subprocess,'check_output',return_value='1'),\
             patch('sys.stdout',new_callable=io.StringIO):
            native.watch()
        self.assertEqual(waits[0],0)
        self.assertTrue(all(seconds==G.MEMORY_SAMPLE_SECONDS for seconds in waits[1:]))
        return self.history()

    def history(self):
        path=self.native.directory/'memory-history.json'
        self.assertTrue(path.is_file(),'Native samples and cleanup must retain bounded memory history')
        return json.loads(path.read_text())

    def test_ring_eviction_preserves_early_peak_first_last_and_exact_count(self):
        count=103
        values=[[{'footprint':20_000_000_000 if n==0 else n,'rss':15_000_000_000 if n==1 else n}] for n in range(count)]
        history=self.watch(values)
        self.assertEqual(history['sample_count'],count)
        self.assertEqual(len(history['samples']),96)
        self.assertEqual(history['samples'][0]['sequence'],8)
        self.assertEqual(history['first']['sequence'],1)
        self.assertEqual(history['last']['sequence'],count)
        self.assertEqual(history['peak_charged']['charged_bytes'],20_000_000_000)
        self.assertEqual(history['peak_charged']['sequence'],1)
        self.assertEqual(history['peak_rss']['sequence'],2)
        self.assertNotIn('private',json.dumps(history))
        self.assertLess((self.native.directory/'memory-history.json').stat().st_size,96_000)

    def test_actual_log_and_stream_activity_label_samples_without_resetting_request_peaks(self):
        def activity(index):
            if index==1:(self.native.directory/'native.log').write_text('[prefill] layer 1/78 · 1024 token · +0.00s\n')
            if index==3:
                self.native.emit(phase='generating',elapsed_seconds=15)
                # A late read of the earlier prefill log must not relabel decode.
                self.native.emit(phase='prefill',last_logged_layer=1,layers=78,chunk_tokens=1024)
        history=self.watch([[{'footprint':10+n,'rss':20+n}] for n in range(4)],activity)
        self.assertEqual([s['activity']['phase'] for s in history['samples']],['startup','startup','prefill','decode'])
        self.assertEqual(history['samples'][2]['activity']['chunk_tokens'],1024)
        self.assertEqual(history['samples'][2]['activity']['observed_at'],105)
        self.native.server=SimpleNamespace()
        self.native.begin_request('c'*32,{'next':'private request'})
        self.native.last_memory={'at':200,'charged_bytes':1,'processes':[{'rss':1}],'pressure':1}
        self.native.retain_memory();history=self.history()
        self.assertEqual(history['last']['request_id'],'c'*32)
        self.assertEqual(history['last']['activity']['phase'],'awaiting_response')
        self.assertEqual(history['peak_charged']['request_id'],'b'*32)
        self.assertEqual(history['sample_count'],5)

    def test_unknown_rss_does_not_become_false_zero(self):
        history=self.watch([[{'footprint':1}]])
        self.assertIsNone(history['last']['rss_bytes']);self.assertIsNone(history['peak_rss'])
        self.assertEqual(history['peak_charged']['charged_bytes'],1)

    def test_limit_crossing_is_retained_and_remains_terminal(self):
        history=self.watch([[{'footprint':27_000_000_000,'rss':4_000_000_000}]])
        self.assertEqual(history['peak_charged']['charged_bytes'],27_000_000_000)
        self.assertEqual(self.native.failure['kind'],'charged_limit')
        with self.assertRaises(RuntimeError):self.native.check_completion()

    def test_failed_sample_keeps_previous_evidence_and_cannot_complete(self):
        history=self.watch([[{'footprint':12,'rss':10}],OSError('synthetic unavailable observation')])
        self.assertEqual(history['sample_count'],1)
        self.assertEqual(history['last']['charged_bytes'],12)
        self.assertEqual(self.native.failure['kind'],'telemetry_unavailable')
        with self.assertRaises(RuntimeError):self.native.check_completion()

    def test_cleanup_preserves_last_observation_and_is_idempotent(self):
        history=self.watch([[{'footprint':12,'rss':10}]])
        self.native.server=SimpleNamespace(wait=lambda **kwargs:0)
        self.native.native_launch='created'
        with patch.object(G.P,'stop_tree') as stop:
            self.native.close();after=self.history();saved=(self.native.directory/'memory-history.json').read_bytes()
            self.native.close();stop.assert_called_once()
        self.assertEqual(after['last'],history['last'])
        self.assertEqual(after['peak_charged'],history['peak_charged'])
        self.assertEqual(after['sample_count'],1)
        self.assertTrue(after['cleanup']['owned_processes_drained'])
        self.assertEqual(saved,(self.native.directory/'memory-history.json').read_bytes())

    def test_unknown_launch_cleanup_does_not_claim_measurement_or_drain(self):
        self.native.native_launch='unknown'
        self.native.close();history=self.history()
        self.assertEqual(history['sample_count'],0)
        self.assertIsNone(history['first']);self.assertIsNone(history['peak_charged'])
        self.assertFalse(history['cleanup']['owned_processes_drained'])
        self.assertEqual(history['cleanup']['native_launch'],'unknown')
        self.assertFalse((self.native.directory/'unloaded.json').exists())


if __name__=='__main__':unittest.main()
