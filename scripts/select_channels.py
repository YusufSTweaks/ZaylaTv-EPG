import copy,json,sys,xml.etree.ElementTree as ET
from pathlib import Path

def select(upstream,config,out,config_dir=None):
 upstream=Path(upstream);out=Path(out);out.mkdir(parents=True,exist_ok=True)
 base_dir=Path(config_dir).resolve() if config_dir else Path.cwd()
 manifest=[]
 for index,source in enumerate(config['sources']):
  name=f'{index:02d}-{source["id"]}'
  if source.get('adapter') in {'tsn','espn','pro_teams','tvguide','dstv_za','beinsports','dazn','fast'}:
   entry={'id':source['id'],'region':source['region'],'adapter':source['adapter'],'output':name+'.xml','selected_channels':None}
   if source['adapter']=='espn':
    entry['include_espn_plus']=bool(source.get('include_espn_plus',False))
    if source.get('espn_plus_map'):
     map_path=Path(source['espn_plus_map'])
     entry['espn_plus_map']=str((map_path if map_path.is_absolute() else base_dir/map_path).resolve())
   elif source['adapter']=='pro_teams':
    if source.get('leagues'):
     entry['leagues']=list(source['leagues'])
    if source.get('days') is not None:
     entry['days']=int(source['days'])
   elif source['adapter']=='tvguide':
    if source.get('lineups'):
     entry['lineups']=list(source['lineups'])
    if source.get('max_channels') is not None:
     entry['max_channels']=int(source['max_channels'])
   elif source['adapter']=='beinsports':
    if source.get('regions'):
     entry['regions']=list(source['regions'])
    if source.get('countries'):
     entry['countries']=list(source['countries'])
   elif source['adapter']=='dazn':
    if source.get('countries'):
     entry['countries']=list(source['countries'])
   elif source['adapter']=='fast':
    if source.get('feeds'):
     entry['feeds']=list(source['feeds'])
    if source.get('catalogs'):
     entry['catalogs']=list(source['catalogs'])
    if source.get('max_channels') is not None:
     entry['max_channels']=int(source['max_channels'])
   manifest.append(entry)
   continue
  seen=set();channels=[];requested=set(source.get('xmltv_ids',[]))
  for pattern in source['files']:
   for file in sorted((upstream/'sites').glob(pattern)):
    for ch in ET.parse(file).getroot().findall('channel'):
     cid=ch.get('xmltv_id','').strip();country=cid.split('@')[0].rsplit('.',1)[-1]
     if not cid or not ch.get('site') or not ch.get('site_id') or not ch.text:continue
     if source.get('countries') and country not in source['countries']:continue
     if source.get('languages') and ch.get('lang') not in source['languages']:continue
     if requested and cid not in requested:continue
     if cid in seen:continue
     seen.add(cid);channels.append(copy.deepcopy(ch))
  limit=int(source.get('limit',80))
  if not 1<=limit<=500:raise ValueError('Source limit must be between 1 and 500')
  # Explicit IDs preserve their requested order; otherwise use the upstream list.
  if requested:
   rank={cid:i for i,cid in enumerate(source['xmltv_ids'])};channels.sort(key=lambda ch:rank[ch.get('xmltv_id')])
  channels=channels[:limit];root=ET.Element('channels');root.extend(channels)
  path=out/(name+'.channels.xml');ET.indent(root)
  path.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
  entry={'id':source['id'],'region':source['region'],'channels_file':str(path.resolve()),'output':name+'.xml','selected_channels':len(channels)}
  if 'probe_url' in source:
   entry['probe_url']=source['probe_url']
  manifest.append(entry)
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest
if __name__=='__main__':
 cfg_path=Path(sys.argv[2]).resolve()
 print(json.dumps(select(sys.argv[1],json.loads(cfg_path.read_text()),sys.argv[3],config_dir=cfg_path.parent),indent=2))
