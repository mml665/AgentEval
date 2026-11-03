"""Exercise restart recovery on an explicitly dedicated Compose stack; preserves volumes."""
import os,sys,json,time,subprocess
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from examples.v05_demo import create_demo
import argparse
parser=argparse.ArgumentParser(description='Fault-recovery acceptance on a dedicated Compose stack only')
parser.add_argument('--project',required=True)
parser.add_argument('--base-url',default='http://127.0.0.1:8139')
args=parser.parse_args()
if not args.project.endswith('-acceptance'): parser.error('Use a dedicated project ending in -acceptance')
if not os.getenv('AGENTEVAL_API_TOKEN') or not os.getenv('POSTGRES_PASSWORD'): parser.error('Set ephemeral API token and PostgreSQL password in environment')
env=dict(os.environ)
def dc(*command_args):
 subprocess.run(['docker','compose','-p',args.project,*command_args],env=env,stdout=subprocess.DEVNULL,check=True,timeout=90)
def wait_api(client,ready=False):
 for _ in range(120):
  try:
   if client.get('/ready' if ready else '/health').status_code==200:return
  except httpx.RequestError:pass
  time.sleep(.5)
 raise RuntimeError('Compose API not ready')
with httpx.Client(base_url=args.base_url,headers={'Authorization':'Bearer '+env['AGENTEVAL_API_TOKEN']},trust_env=False,timeout=10) as client:
 wait_api(client,True)
 dc('stop','worker','dispatcher')
 demo=create_demo(client)
 assert all(client.get('/api/v1/evaluations/'+r['id']).json()['status']=='queued' for r in demo['runs'])
 dc('restart','db','redis')
 for _ in range(120):
  ping=subprocess.run(['docker','compose','-p',args.project,'exec','-T','db','pg_isready','-U','agenteval'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if ping.returncode==0:break
  time.sleep(.5)
 dc('restart','api');wait_api(client)
 assert all(client.get('/api/v1/evaluations/'+r['id']).json()['status']=='queued' for r in demo['runs'])
 dc('start','worker','dispatcher');wait_api(client,True)
 reports=[]
 for run in demo['runs']:
  for _ in range(240):
   reply=client.get('/api/v1/evaluations/'+run['id']+'/report');reply.raise_for_status();r=reply.json()
   if r['grading_status']=='completed':break
   time.sleep(.5)
  else:raise RuntimeError('Compose tasks did not complete')
  reports.append(r)
 assert [r['success_rate'] for r in reports]==[1,0,1]
 value={'compose_started':True,'postgres_redis_restarted':True,'api_restarted_with_queued_work':True,'worker_dispatcher_resumed':True,'state_rates':[r['success_rate'] for r in reports],'ready':client.get('/ready').status_code==200}
 Path('.build/compose-051-evidence.json').write_text(json.dumps(value,indent=2));print(json.dumps(value))
