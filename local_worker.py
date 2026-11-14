#!/usr/bin/env -S /opt/homebrew/bin/python3 -I -B
"""Midnight-only typed local worker. Interactive local-code stays unchanged."""
import sys
import os
sys.path.insert(0,os.path.dirname(os.path.realpath(__file__)))
import json
import signal
import model_manager as M
import structured_worker as S

def main():
    item=json.loads(os.environ['MIDNIGHT_WORKER_DESCRIPTOR'])
    pinned={k:v for k,v in item.items() if k!='promotion_generation'}
    if M.local_descriptor(item['model'],item['context'])!=pinned:
        raise ValueError('Pinned task model changed')
    return S.main()

if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(InterruptedError('Worker stopped')))
    try:sys.exit(main())
    except Exception as exc:sys.exit('Midnight local model unavailable: '+type(exc).__name__+': '+str(exc))
