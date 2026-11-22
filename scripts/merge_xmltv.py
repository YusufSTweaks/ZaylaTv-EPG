"""Validate and merge actual XMLTV programme data, preserving original timestamps."""
import copy,datetime as dt,gzip,json,sys,xml.etree.ElementTree as ET
from pathlib import Path
UTC=dt.timezone.utc

def ts(v):return dt.datetime.strptime(v.strip(),'%Y%m%d%H%M%S %z').astimezone(UTC)
def merge(input_dir,manifest,required,out_dir,now=None):
 now=now or dt.datetime.now(UTC);root=ET.Element('tv',{'generator-info-name':'Zayla TV maintained-site EPG merger'});channels={};rows=[];keys=set();owner={};covered=set();reports=[]
 for spec in manifest:
  info={'source':spec['id'],'region':spec['region'],'accepted':0,'rejected':0};reports.append(info)
  try:
   data=(Path(input_dir)/spec['output']).read_bytes()
   if len(data)>64*1024*1024:raise ValueError('Source exceeds 64 MiB')
   if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('DTD/entities unsupported')
   tree=ET.fromstring(data)
   if tree.tag!='tv':raise ValueError('Expected XMLTV root')
   definitions={ch.get('id'):ch for ch in tree.findall('channel') if ch.get('id') and ch.findtext('display-name')}
   valid=[];upcoming=set()
   for p in tree.findall('programme'):
    try:
     cid=p.get('channel');start=ts(p.get('start',''));stop=ts(p.get('stop',''))
     if cid not in definitions or not p.findtext('title') or stop<=start:raise ValueError('Invalid programme')
     valid.append((p,cid,start,stop))
     if stop>now:upcoming.add(cid)
    except (ValueError,TypeError):info['rejected']+=1
   for p,cid,start,stop in valid:
    if cid not in upcoming:continue
    if cid in owner and owner[cid]!=spec['id']:continue # one source owns a channel's schedule
    owner[cid]=spec['id'];channels[cid]=definitions[cid]
    key=(cid,start,stop)
    if key in keys:continue
    keys.add(key);rows.append(copy.deepcopy(p));info['accepted']+=1
   if info['accepted']:covered.add(spec['region'])
  except (OSError,ET.ParseError,ValueError) as e:info['error']=str(e)
 missing=set(required)-covered
 report={'generated_at_utc':now.isoformat(),'channels':len(channels),'programmes':len(rows),'covered_regions':sorted(covered),'missing_regions':sorted(missing),'sources':reports}
 if missing or not rows:
  raise ValueError('Publication stopped: missing usable future coverage '+','.join(sorted(missing))+'. '+json.dumps(report))
 for cid in sorted(channels):root.append(channels[cid])
 rows.sort(key=lambda p:(p.get('channel'),ts(p.get('start'))));root.extend(rows);ET.indent(root)
 payload=ET.tostring(root,encoding='utf-8',xml_declaration=True);ET.fromstring(payload)
 compressed=gzip.compress(payload,compresslevel=9,mtime=0)
 # Nothing is written until all validation and regional requirements pass.
 out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
 (out/'guide.xml').write_bytes(payload);(out/'guide.xml.gz').write_bytes(compressed)
 (out/'status.json').write_text(json.dumps(report,indent=2)+'\n');(out/'.nojekyll').write_text('')
 (out/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Zayla TV EPG</title><h1>Zayla TV EPG</h1><p><a href="guide.xml.gz">Compressed XMLTV</a> | <a href="guide.xml">XMLTV</a> | <a href="status.json">Coverage report</a></p></html>')
 return report
if __name__=='__main__':
 print(json.dumps(merge(sys.argv[1],json.loads(Path(sys.argv[2]).read_text()),json.loads(Path(sys.argv[3]).read_text())['required_regions'],sys.argv[4]),indent=2))
