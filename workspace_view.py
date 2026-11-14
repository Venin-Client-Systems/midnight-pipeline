"""Fixed read-only dashboard routes for the private agent workspace."""
from urllib.parse import urlsplit,parse_qs
from agent_workspace import Workspace


def inspect(path,workspace=None):
 w=workspace or Workspace();url=urlsplit(path);query=parse_qs(url.query,keep_blank_values=True)
 if any(len(v)!=1 for v in query.values()):raise ValueError('Duplicate workspace query field')
 values={k:v[0] for k,v in query.items()}
 if url.path=='/api/chat/overview' and not values:return w.overview()
 if url.path=='/api/chat/rooms' and not values:return {'available':w.overview()['available'],'rooms':w.rooms(),'agents':w.actors()}
 if url.path=='/api/chat/inbox' and set(values)<= {'actor'}:return {'questions':w.inbox(values.get('actor','codex'))}
 if url.path=='/api/chat/thread' and set(values)=={'room','message'}:return w.thread(values['room'],values['message'])
 if url.path=='/api/chat/messages' and 'room' in values and set(values)<= {'room','before','limit','q'}:
  return w.messages(values['room'],before=int(values['before']) if values.get('before') else None,limit=int(values.get('limit','40')),query=values.get('q') or None)
 raise ValueError('Unknown workspace read request')
