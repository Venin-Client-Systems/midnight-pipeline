"""Operator entry for private agent conversations; read commands never schedule."""
import argparse,json,uuid
from pathlib import Path
from agent_workspace import Workspace


def run(arguments,runtime,projects):
 parser=argparse.ArgumentParser(prog='midnight chat');sub=parser.add_subparsers(dest='command')
 for name in ('rooms','agents','overview'):sub.add_parser(name).add_argument('--json',action='store_true')
 p=sub.add_parser('read');p.add_argument('room');p.add_argument('--before',type=int);p.add_argument('--limit',type=int,default=50);p.add_argument('--query');p.add_argument('--json',action='store_true')
 p=sub.add_parser('inbox');p.add_argument('--actor',default='codex');p.add_argument('--json',action='store_true')
 p=sub.add_parser('post');p.add_argument('target');p.add_argument('--body-file',required=True);p.add_argument('--kind',choices=['message','question','reply'],default='message');p.add_argument('--reply-to');p.add_argument('--mention',action='append',default=[]);p.add_argument('--json',action='store_true')
 p=sub.add_parser('group');p.add_argument('--name',required=True);p.add_argument('--member',action='append',required=True);p.add_argument('--json',action='store_true')
 p=sub.add_parser('register');p.add_argument('actor');p.add_argument('--name',required=True);p.add_argument('--project');p.add_argument('--json',action='store_true')
 p=sub.add_parser('decide');p.add_argument('--scope',default='all');p.add_argument('--effect',choices=['guidance','hold_delivery','release'],required=True);p.add_argument('--releases');p.add_argument('--supersedes',action='append',default=[]);p.add_argument('--body-file',required=True);p.add_argument('--json',action='store_true')
 args=parser.parse_args(arguments);w=Workspace(runtime)
 if args.command in {'post','group','register','decide'}:w.bootstrap(projects)
 if args.command=='rooms':value={'rooms':w.rooms()}
 elif args.command=='agents':value={'agents':w.actors()}
 elif args.command=='read':value=w.messages(args.room,before=args.before,limit=args.limit,query=args.query)
 elif args.command=='inbox':value={'actor':args.actor,'questions':w.inbox(args.actor)}
 elif args.command=='register':w.register_agent(args.actor,args.name,args.project);value={'registered':args.actor,'execution_admitted':False}
 elif args.command=='group':value={'conversation':w.conversation('codex',args.member,name=args.name)}
 elif args.command=='post':
  target=w.conversation('codex',[args.target[1:]]) if args.target.startswith('@') else args.target
  value=w.operator_post('codex',target,Path(args.body_file).read_text(),args.kind,parent=args.reply_to,mentions=args.mention,
      context={'authorship':'Midnight automation','mode':'operator'})
 elif args.command=='decide':value=w.decide(args.scope,Path(args.body_file).read_text(),args.effect,releases=args.releases,supersedes=args.supersedes)
 else:value=w.overview()
 print(json.dumps(value,indent=2));return 0
