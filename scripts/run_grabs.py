"""Run one bounded maintained-source grab at a time and retain each exit status."""
import json,subprocess,sys,os,signal
from pathlib import Path
if __name__=='__main__':
 upstream=Path(sys.argv[1]).resolve();manifest=json.loads(Path(sys.argv[2]).read_text());out=Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True);results=[]
 for source in manifest:
  result=dict(source)
  if not source['selected_channels']:result.update(exit_code=None,error='No channels selected')
  else:
   try:
    command=['npm','run','grab','--','--channels='+source['channels_file'],'--days=2','--timeout=15000','--maxConnections=2','--output='+str(out/source['output'])]
    proc=subprocess.Popen(command,cwd=upstream,start_new_session=True)
    try:result['exit_code']=proc.wait(timeout=600)
    except subprocess.TimeoutExpired:
     os.killpg(proc.pid,signal.SIGTERM)
     try:proc.wait(timeout=5)
     except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
     result.update(exit_code=None,error='Source exceeded ten-minute budget; process group stopped')
   except OSError as e:result.update(exit_code=None,error=str(e))
  results.append(result);(out/'grab-results.json').write_text(json.dumps(results,indent=2)+'\n')
 # Merge validates actual rows and required regional coverage, not just exit codes.
