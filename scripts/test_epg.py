import datetime as dt,gzip,json,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from grab_espn import (
 ESTIMATE_DURATION_NOTE,
 ESTIMATE_NEXT_NOTE,
 build_guide_data,
 write_guide as write_espn_guide,
)
from merge_xmltv import merge
from select_channels import select
NOW=dt.datetime(2029,1,1,tzinfo=dt.timezone.utc)
GOOD='<tv><channel id="a"><display-name>A</display-name></channel><programme channel="a" start="20300101000000 +0000" stop="20300101010000 +0000"><title>News</title></programme></tv>'
class MergeTests(unittest.TestCase):
 def run_merge(self,data,required=('US',),region='US',now=NOW):
  d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);p=Path(d.name);(p/'a.xml').write_text(data)
  return p,merge(p,[{'id':'one','region':region,'output':'a.xml'}],required,p/'public',now=now)
 def test_valid_and_gzip(self):
  p,r=self.run_merge(GOOD);self.assertEqual(r['programmes'],1)
  self.assertEqual((p/'public/guide.xml').read_bytes(),gzip.decompress((p/'public/guide.xml.gz').read_bytes()))
  self.assertEqual(ET.parse(p/'public/guide.xml').find('programme').get('start'),'20300101000000 +0000')
 def test_empty_invalid_and_stale_do_not_publish(self):
  for data in ['<tv/>','<html/>',GOOD.replace('20300101000000 +0000','bad'),GOOD.replace('channel="a"','channel="missing"'),GOOD.replace('20300101010000','20290101010000')]:
   with self.subTest(data=data),self.assertRaises(ValueError):self.run_merge(data)
  with self.assertRaises(ValueError):self.run_merge(GOOD,now=dt.datetime(2031,1,1,tzinfo=dt.timezone.utc))
 def test_missing_region_blocks(self):
  with self.assertRaisesRegex(ValueError,'CA'):self.run_merge(GOOD,required=['US','CA'])
 def test_existing_file_is_preserved_on_failure(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'a.xml').write_text('<tv/>');out=p/'public';out.mkdir();(out/'guide.xml').write_text('previous')
   with self.assertRaises(ValueError):merge(p,[{'id':'one','region':'US','output':'a.xml'}],['US'],out,now=NOW)
   self.assertEqual((out/'guide.xml').read_text(),'previous')
 def test_duplicate_source_keeps_first_schedule(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'a.xml').write_text(GOOD);(p/'b.xml').write_text(GOOD.replace('News','Alternate'))
   r=merge(p,[{'id':'one','region':'US','output':'a.xml'},{'id':'two','region':'US','output':'b.xml'}],['US'],p/'public',now=NOW)
   self.assertEqual(r['programmes'],1);self.assertEqual(ET.parse(p/'public/guide.xml').findtext('programme/title'),'News')
 def test_channel_aliases_follow_regional_membership(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   (p/'a.xml').write_text(GOOD.replace('<display-name>A</display-name>','<display-name>Original A</display-name><icon src="https://example.com/icon.png"/>'))
   aliases={'CA':{'a':['station.ca','STATION.CA'],'missing':['missing.ca']},'US':{'a':['station.us']}}
   manifest=[{'id':'ca','region':'CA','output':'a.xml'},{'id':'us','region':'US','output':'a.xml'}]
   report=merge(p,manifest,['CA'],p/'public',now=NOW,aliases_by_region=aliases)
   for path,expected in [('regions/ca.xml',['station.ca']),('regions/us.xml',['station.us']),('guide.xml',['station.ca','station.us'])]:
    root=ET.parse(p/'public'/path).getroot()
    self.assertEqual([x.text for x in root.findall('channel/display-name')],['Original A',*expected])
    self.assertEqual([x.tag for x in root.find('channel')],['display-name']*(1+len(expected))+['icon'])
    self.assertEqual(root.find('programme').get('channel'),'a')
   self.assertEqual(report['channel_aliases_added'],2)
   self.assertEqual(report['regions']['CA']['channel_aliases_added'],1)
 def test_channel_alias_does_not_shadow_another_station(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   (p/'a.xml').write_text(GOOD.replace('</tv>', GOOD.removeprefix('<tv>').replace('id="a"','id="b"').replace('channel="a"','channel="b"').replace('>A<','>Original B<')))
   aliases={'US':{'a':['Original B','shared.us'],'b':['shared.US']}}
   report=merge(p,[{'id':'us','region':'US','output':'a.xml'}],['US'],p/'public',now=NOW,aliases_by_region=aliases)
   self.assertEqual(report['channel_aliases_added'],0)
 def test_selector_scopes_deduplicates_and_caps(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);site=p/'sites/demo';site.mkdir(parents=True)
   (site/'a.channels.xml').write_text('<channels>'+''.join(f'<channel site="demo" site_id="{i}" lang="en" xmltv_id="{id}">Name</channel>' for i,id in enumerate(['a.us','a.us','b.ca','c.us','d.us']))+'</channels>')
   cfg={'sources':[{'id':'us','region':'US','files':['demo/*.channels.xml'],'countries':['us'],'limit':2}]}
   manifest=select(p,cfg,p/'selected');root=ET.parse(manifest[0]['channels_file']).getroot()
   self.assertEqual([n.get('xmltv_id') for n in root],['a.us','c.us'])
 def test_per_region_files_and_status_metadata(self):
  us_xml='<tv><channel id="a.us"><display-name>US A</display-name></channel><channel id="shared.de"><display-name>Shared DE</display-name></channel><programme channel="a.us" start="20300101000000 +0000" stop="20300101010000 +0000"><title>US News</title></programme><programme channel="shared.de" start="20300101000000 +0000" stop="20300101010000 +0000"><title>US Copy</title></programme></tv>'
  de_xml='<tv><channel id="shared.de"><display-name>Shared DE</display-name></channel><channel id="b.de"><display-name>DE B</display-name></channel><programme channel="shared.de" start="20300101000000 +0000" stop="20300101010000 +0000"><title>DE Native</title></programme><programme channel="b.de" start="20300101000000 +0000" stop="20300101010000 +0000"><title>Tagesschau</title></programme></tv>'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   (p/'us.xml').write_text(us_xml)
   (p/'de.xml').write_text(de_xml)
   report=merge(p,[{'id':'us-src','region':'US','output':'us.xml'},{'id':'de-src','region':'DE','output':'de.xml'}],['US'],p/'public',now=NOW)
   self.assertIn('regions',report)
   self.assertEqual(report['regions']['US']['file'],'regions/us.xml.gz')
   self.assertEqual(report['regions']['DE']['file'],'regions/de.xml.gz')
   self.assertEqual(report['regions']['DE']['channels'],2)
   # Verify gzip decompression matches XML and DE file keeps its own native shared.de schedule
   de_xml_bytes=(p/'public/regions/de.xml').read_bytes()
   self.assertEqual(de_xml_bytes,gzip.decompress((p/'public/regions/de.xml.gz').read_bytes()))
   de_titles={el.get('channel'):el.findtext('title') for el in ET.fromstring(de_xml_bytes).findall('programme')}
   self.assertEqual(de_titles,{'shared.de':'DE Native','b.de':'Tagesschau'})
   # Index page lists regional files
   index_html=(p/'public/index.html').read_text()
   self.assertIn('regions/us.xml.gz',index_html)
   self.assertIn('regions/de.xml.gz',index_html)
class EspnTests(unittest.TestCase):
 SAMPLE_NOW=dt.datetime(2030,1,1,12,0,tzinfo=dt.timezone.utc)
 def test_selector_handles_espn_adapter_and_mapping_path(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   cfg={'sources':[
    {'id':'us-espn-default','region':'US','adapter':'espn'},
    {'id':'us-espn-plus','region':'US','adapter':'espn','include_espn_plus':True,'espn_plus_map':'lineups/espn-plus-map.json'},
   ]}
   manifest=select(p,cfg,p/'selected',config_dir=p)
   self.assertEqual(manifest[0]['adapter'],'espn')
   self.assertFalse(manifest[0]['include_espn_plus'])
   self.assertNotIn('espn_plus_map',manifest[0])
   self.assertTrue(manifest[1]['include_espn_plus'])
   self.assertEqual(manifest[1]['espn_plus_map'],str((p/'lineups/espn-plus-map.json').resolve()))
 def test_linear_end_times_explicit_estimated_and_omit_final(self):
  records=[
   {'id':'lin-1','name':'First Take','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T17:00:00Z','network':{'id':'espn1','type':'linear'}},
   {'id':'lin-1','name':'First Take','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T17:00:00Z','network':{'id':'espn1','type':'linear'}},
   {'id':'lin-2','name':'SportsCenter','startDateTime':'2030-01-01T17:00:00Z','endDateTime':None,'network':{'id':'espn1','type':'linear'}},
   {'id':'lin-3','name':'NBA Today','startDateTime':'2030-01-01T18:00:00Z','endDateTime':None,'network':{'id':'espn1','type':'linear'}},
  ]
  channels,programmes,stats=build_guide_data(records,self.SAMPLE_NOW)
  self.assertEqual(channels,{'ESPN.us@SD':'ESPN'})
  self.assertEqual(len(programmes),2)
  self.assertEqual(stats['linear_channels'],1)
  self.assertEqual(stats['linear_programmes'],2)
  ordered=sorted(programmes.items())
  (c1,s1,e1),(t1,_,d1)=ordered[0]
  self.assertEqual((c1,t1),('ESPN.us@SD','First Take'))
  self.assertEqual(e1,dt.datetime(2030,1,1,17,0,tzinfo=dt.timezone.utc))
  self.assertIsNone(d1)
  (c2,s2,e2),(t2,_,d2)=ordered[1]
  self.assertEqual((c2,t2),('ESPN.us@SD','SportsCenter'))
  self.assertEqual(e2,dt.datetime(2030,1,1,18,0,tzinfo=dt.timezone.utc))
  self.assertIn(ESTIMATE_NEXT_NOTE,d2)
 def test_espn_plus_deduplicates_airings_and_preserves_feeds_languages_and_ends(self):
  records=[
   # Duplicate airing by same id (second has explicit endDateTime and should win)
   {'id':'air-1','eventId':1001,'name':'Penguins vs. Stars','startDateTime':'2030-01-01T15:00:00Z','endDateTime':None,'duration':10800,'feedName':'Stars Broadcast','language':'en','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   {'id':'air-1','eventId':1001,'name':'Penguins vs. Stars','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T18:30:00Z','duration':10800,'feedName':'Stars Broadcast','language':'en','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Duplicate airing with different id but same eventId + feedName + language
   {'id':'air-1-dup','eventId':1001,'name':'Penguins vs. Stars','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T18:30:00Z','feedName':'Stars Broadcast','language':'en','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Distinct broadcast feed for the same eventId (must be preserved)
   {'id':'air-2','eventId':1001,'name':'Penguins vs. Stars','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T18:30:00Z','feedName':'Penguins Broadcast','language':'en','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Distinct language for the same eventId (must be preserved)
   {'id':'air-3','eventId':1001,'name':'En Español - Penguins vs. Stars','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T18:30:00Z','feedName':'Stars Broadcast','language':'es','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Simultaneous event with duration-only end (preserved with duration estimate note)
   {'id':'air-4','eventId':1002,'name':'Game Duration Only','startDateTime':'2030-01-01T15:00:00Z','endDateTime':None,'duration':7200,'language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Simultaneous event with NO endDateTime and NO duration: MUST be omitted (never inferred from next start)
   {'id':'air-5','eventId':1003,'name':'Game Missing End','startDateTime':'2030-01-01T15:00:00Z','endDateTime':None,'duration':None,'language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   {'id':'air-6','eventId':1004,'name':'Later Simultaneous Game','startDateTime':'2030-01-01T16:00:00Z','endDateTime':'2030-01-01T18:00:00Z','language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
  ]
  channels,programmes,stats=build_guide_data(records,self.SAMPLE_NOW,include_espn_plus=True)
  self.assertEqual(stats['espn_plus_collected'],7)
  self.assertEqual(stats['espn_plus_deduplicated'],2)
  self.assertEqual(stats['espn_plus_unique'],5)
  self.assertEqual(stats['espn_plus_mapped'],0)
  self.assertEqual(stats['espn_plus_unmapped'],5)
  # Never generate guessed ESPNPlus.us@01 / ESPN+ 01 channel IDs or display names
  self.assertFalse(any(cid.startswith('ESPNPlus.us@') for cid in channels))
  self.assertTrue(all(cid.startswith('ESPNPlus.unmapped.') for cid in channels))
  self.assertTrue(all(name.startswith('[Unmapped ESPN+]') for name in channels.values()))
  titles={title for (title,_,_) in programmes.values()}
  self.assertNotIn('Game Missing End',titles)
  self.assertIn('Penguins vs. Stars (Stars Broadcast)',titles)
  self.assertIn('Penguins vs. Stars (Penguins Broadcast)',titles)
  self.assertIn('En Español - Penguins vs. Stars (Stars Broadcast)',titles)
  # Verify explicit endDateTime won over duration for Stars Broadcast, and duration note on Game Duration Only
  by_title={title:(stop,lang,desc) for (_,_,stop),(title,lang,desc) in programmes.items()}
  self.assertEqual(by_title['Penguins vs. Stars (Stars Broadcast)'][0],dt.datetime(2030,1,1,18,30,tzinfo=dt.timezone.utc))
  self.assertNotIn(ESTIMATE_DURATION_NOTE,by_title['Penguins vs. Stars (Stars Broadcast)'][2])
  self.assertEqual(by_title['En Español - Penguins vs. Stars (Stars Broadcast)'][1],'es')
  self.assertIn(ESTIMATE_DURATION_NOTE,by_title['Game Duration Only'][2])
 def test_espn_plus_explicit_mappings_conflicts_and_refresh_stability(self):
  mapping={
   'version':1,
   'mappings':[
    {
     'channel_id':'MyProvider.ESPNPlus.Live1',
     'display_name':'Provider ESPN+ Live 1',
     'match':{'event_id':'2001','feed':'Home Feed','language':'en'},
    },
    {
     'channel_id':'MyProvider.ESPNPlus.Live1',
     'display_name':'Provider ESPN+ Live 1',
     'match':{'event_id':'2002','language':'en'},
    },
   ],
  }
  base_events=[
   {'id':'e-home','eventId':2001,'name':'Team A vs. Team B','startDateTime':'2030-01-01T16:00:00Z','endDateTime':'2030-01-01T18:00:00Z','feedName':'Home Feed','language':'en','network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Overlaps with event 2001 on MyProvider.ESPNPlus.Live1 -> must NOT overlap playable channel, kept unmapped
   {'id':'e-conflict','eventId':2002,'name':'Conflicting Game','startDateTime':'2030-01-01T17:00:00Z','endDateTime':'2030-01-01T19:00:00Z','language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   # Unmapped simultaneous event
   {'id':'e-unmapped','eventId':2003,'name':'Unmapped Match','startDateTime':'2030-01-01T16:00:00Z','endDateTime':'2030-01-01T18:00:00Z','language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
  ]
  ch1,prog1,stats1=build_guide_data(base_events,self.SAMPLE_NOW,include_espn_plus=True,espn_plus_map=mapping)
  self.assertEqual(stats1['espn_plus_mapped'],1)
  self.assertEqual(stats1['espn_plus_unmapped'],2)
  cid_by_title_1={title:cid for (cid,_,_),(title,_,_) in prog1.items()}
  self.assertEqual(cid_by_title_1['Team A vs. Team B (Home Feed)'],'MyProvider.ESPNPlus.Live1')
  self.assertTrue(cid_by_title_1['Conflicting Game'].startswith('ESPNPlus.unmapped.'))
  self.assertTrue(cid_by_title_1['Unmapped Match'].startswith('ESPNPlus.unmapped.'))
  self.assertEqual(ch1['MyProvider.ESPNPlus.Live1'],'Provider ESPN+ Live 1')
  # Refresh stability: add earlier/simultaneous events and reorder list; existing mapped & unmapped IDs must not shift
  refreshed_events=[
   {'id':'e-new-early','eventId':1999,'name':'Earlier Event','startDateTime':'2030-01-01T14:00:00Z','endDateTime':'2030-01-01T15:30:00Z','language':'en','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   base_events[2],
   base_events[0],
   base_events[1],
  ]
  _,prog2,_=build_guide_data(refreshed_events,self.SAMPLE_NOW,include_espn_plus=True,espn_plus_map=mapping)
  cid_by_title_2={title:cid for (cid,_,_),(title,_,_) in prog2.items()}
  self.assertEqual(cid_by_title_2['Team A vs. Team B (Home Feed)'],cid_by_title_1['Team A vs. Team B (Home Feed)'])
  self.assertEqual(cid_by_title_2['Unmapped Match'],cid_by_title_1['Unmapped Match'])
  self.assertEqual(cid_by_title_2['Conflicting Game'],cid_by_title_1['Conflicting Game'])
 def test_espn_takes_priority_over_tvguide_with_per_channel_and_full_fallback(self):
  records=[
   {'id':'lin-espn1','name':'Official ESPN Show','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T16:00:00Z','network':{'id':'espn1','type':'linear'}},
  ]
  tvguide_xml=(
   '<tv>'
   '<channel id="ESPN.us@SD"><display-name>ESPN</display-name></channel>'
   '<channel id="ESPN2.us@SD"><display-name>ESPN2</display-name></channel>'
   '<channel id="CNN.us@SD"><display-name>CNN</display-name></channel>'
   '<programme channel="ESPN.us@SD" start="20300101150000 +0000" stop="20300101160000 +0000"><title>TV Guide ESPN</title></programme>'
   '<programme channel="ESPN2.us@SD" start="20300101150000 +0000" stop="20300101160000 +0000"><title>TV Guide ESPN2</title></programme>'
   '<programme channel="CNN.us@SD" start="20300101150000 +0000" stop="20300101160000 +0000"><title>CNN News</title></programme>'
   '</tv>'
  )
  manifest=[
   {'id':'us-espn-official','region':'US','output':'00-us-espn-official.xml'},
   {'id':'us-tvguide','region':'US','output':'01-us-tvguide.xml'},
  ]
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   (p/'01-us-tvguide.xml').write_text(tvguide_xml)
   with patch('grab_espn.fetch_schedule',side_effect=[records,[]]):
    write_espn_guide(p/'00-us-espn-official.xml',now=self.SAMPLE_NOW)
   report=merge(p,manifest,['US'],p/'public',now=self.SAMPLE_NOW)
   self.assertEqual(report['channels'],3)
   titles={el.get('channel'):el.findtext('title') for el in ET.parse(p/'public/guide.xml').findall('programme')}
   # ESPN owns ESPN.us@SD; TV Guide automatically fills ESPN2.us@SD (missing from ESPN) and CNN.us@SD
   self.assertEqual(titles,{'ESPN.us@SD':'Official ESPN Show','ESPN2.us@SD':'TV Guide ESPN2','CNN.us@SD':'CNN News'})
   # Full fallback to TV Guide when ESPN has no usable output
   (p/'00-us-espn-official.xml').write_text('<tv/>')
   merge(p,manifest,['US'],p/'public-fallback',now=self.SAMPLE_NOW)
   fallback_titles={el.get('channel'):el.findtext('title') for el in ET.parse(p/'public-fallback/guide.xml').findall('programme')}
   self.assertEqual(fallback_titles,{'ESPN.us@SD':'TV Guide ESPN','ESPN2.us@SD':'TV Guide ESPN2','CNN.us@SD':'CNN News'})
class OfficialAdapterTests(unittest.TestCase):
 SAMPLE_NOW=dt.datetime(2030,1,1,12,0,tzinfo=dt.timezone.utc)
 def test_selector_propagates_new_adapters(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   cfg={'sources':[
    {'id':'us-tvguide','region':'US','adapter':'tvguide','lineups':['9100001138'],'max_channels':500},
    {'id':'za-dstv','region':'ZA','adapter':'dstv_za'},
    {'id':'mena-beinsports','region':'MENA','adapter':'beinsports','regions':['ar-mena']},
   ]}
   manifest=select(p,cfg,p/'selected',config_dir=p)
   self.assertEqual([m['adapter'] for m in manifest],['tvguide','dstv_za','beinsports'])
   self.assertEqual(manifest[0]['lineups'],['9100001138'])
   self.assertEqual(manifest[0]['max_channels'],500)
   self.assertEqual(manifest[2]['regions'],['ar-mena'])
 def test_tvguide_canonical_dedup_junk_and_local_aliases(self):
  from grab_tvguide import build_guide_data as build_tvguide
  st=int(dt.datetime(2030,1,1,15,0,tzinfo=dt.timezone.utc).timestamp())
  en=int(dt.datetime(2030,1,1,16,0,tzinfo=dt.timezone.utc).timestamp())
  canonical={'100':('CNN.us@SD','CNN'),'200':('ESPN.us@SD','ESPN')}
  batch=[
   {'channel':{'sourceId':100,'fullName':'CNN','name':'CNN'},'programSchedules':[{'title':'CNN News','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':101,'fullName':'CNN HD','name':'CNNHD'},'programSchedules':[{'title':'CNN News HD','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':201,'fullName':'ESPN HD','name':'ESPNHD'},'programSchedules':[{'title':'TVG ESPN','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':300,'fullName':'KTRK-DT Houston, Texas (ABC)','name':'KTRK-DT'},'programSchedules':[{'title':'ABC13 News','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':400,'fullName':'Music Choice Hit List','name':'MC01'},'programSchedules':[{'title':'Music','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':401,'fullName':'On Demand Television','name':'OnDmd'},'programSchedules':[{'title':'VOD','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':500,'fullName':'MS NOW HD','name':'MSNOWHD'},'programSchedules':[{'title':'Morning Joe','startTime':st,'endTime':en}]},
   {'channel':{'sourceId':501,'fullName':'Discovery Channel West','name':'DSCW'},'programSchedules':[{'title':'Gold Rush','startTime':st,'endTime':en}]},
  ]
  name_map={'msnow':('MSNBC.us@SD','MS NOW HD'),'discoverychannel':('DiscoveryChannel.us@East','Discovery Channel')}
  channels,programmes,stats=build_tvguide([batch],self.SAMPLE_NOW,canonical_map=canonical,canonical_name_map=name_map)
  self.assertIn('CNN.us@SD',channels)
  self.assertIn('ESPN.us@SD',channels)
  self.assertIn('MSNBC.us@SD',channels)
  self.assertNotIn('TVGuide.500.us',channels)
  # West feeds must stay separate and never collapse onto an East canonical ID
  self.assertIn('TVGuide.501.us',channels)
  self.assertIn('TVGuide.300.us',channels)
  self.assertEqual(channels['TVGuide.300.us'],['KTRK-DT','KTRK-DT Houston, Texas (ABC)'])
  self.assertNotIn('TVGuide.101.us',channels)
  self.assertNotIn('TVGuide.201.us',channels)
  self.assertNotIn('TVGuide.400.us',channels)
  self.assertNotIn('TVGuide.401.us',channels)
  self.assertEqual(stats['skipped_junk'],2)
  self.assertGreaterEqual(stats['deduplicated_simulcasts'],1)
 def test_dstv_za_canonical_and_unmapped_sast_times(self):
  from grab_dstv_za import build_guide_data as build_dstv_za, write_guide as write_dstv_za
  canonical={'191':('SABC1.za@SD','SABC 1')}
  payload=[
   {'Number':'191','Name':'SABC 1','Programmes':[{'Title':'Uzalo','StartTime':'2030-01-01T18:00:00','EndTime':'2030-01-01T18:30:00'}]},
   {'Number':'404','Name':'SABC News','Programmes':[{'Title':'Full View','StartTime':'2030-01-01T18:00:00','EndTime':'2030-01-01T19:00:00'}]},
  ]
  channels,programmes=build_dstv_za([payload],self.SAMPLE_NOW,canonical_map=canonical)
  self.assertEqual(channels['SABC1.za@SD'],['SABC 1'])
  self.assertEqual(channels['DStvZA.404.za'],['SABC News'])
  self.assertIn(('SABC1.za@SD',dt.datetime(2030,1,1,16,0,tzinfo=dt.timezone.utc),dt.datetime(2030,1,1,16,30,tzinfo=dt.timezone.utc)),programmes)
  # Verify automatic fallback when direct DStv API fails
  fallback_xml=b'<tv><channel id="191.dstv_com"><display-name>SABC 1</display-name></channel><programme channel="191.dstv_com" start="20300101150000 +0000" stop="20300101160000 +0000"><title>Fallback Show</title></programme></tv>'
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'za.xml'
   with patch('grab_dstv_za.fetch_day',side_effect=OSError('HTTP 404')), patch('grab_dstv_za.fetch_fallback_xml',return_value=fallback_xml):
    c,pr=write_dstv_za(out,now=self.SAMPLE_NOW)
    self.assertEqual((c,pr),(1,1))
    self.assertEqual(ET.parse(out).find('channel').get('id'),'DStvZA.191.za')
 def test_beinsports_canonical_and_region_ids(self):
  from grab_beinsports import build_guide_data as build_beinsports
  canonical={'uuid-1':('beINSports1.qa@MENA','beIN SPORTS 1','ar')}
  pairs=[
   ('ar-mena',{'id':'uuid-1','externalId':'101','name':'beIN SPORTS 1'},[{'title':'Match 1','startDate':'2030-01-01T15:00:00Z','endDate':'2030-01-01T17:00:00Z'}]),
   ('ar-mena',{'id':'uuid-2','externalId':'202','name':'beIN SPORTS MAX 5'},[{'title':'Match 2','startDate':'2030-01-01T15:00:00Z','endDate':'2030-01-01T17:00:00Z'}]),
  ]
  channels,programmes=build_beinsports(pairs,self.SAMPLE_NOW,canonical_map=canonical)
  self.assertIn('beINSports1.qa@MENA',channels)
  self.assertIn('beINSports.mena.202.qa',channels)
  self.assertEqual(len(programmes),2)
 def test_fast_adapter_extract_and_selector(self):
  from grab_fast import extract_feed_programmes,load_canonical_fast_map
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   sdir=p/'sites/i.mjh.nz';sdir.mkdir(parents=True)
   (sdir/'i.mjh.nz_pluto.channels.xml').write_text('<channels><channel site="i.mjh.nz" site_id="PlutoTV/us#abc" lang="en" xmltv_id="PlutoDemo.us@SD">Pluto Demo</channel></channels>')
   cfg={'sources':[{'id':'fast-streaming','region':'FAST','adapter':'fast','feeds':['PlutoTV/us'],'max_channels':100}]}
   manifest=select(p,cfg,p/'selected',config_dir=p)
   self.assertEqual(manifest[0]['adapter'],'fast')
   self.assertEqual(manifest[0]['feeds'],['PlutoTV/us'])
   fmap=load_canonical_fast_map(p,catalogs=('i.mjh.nz_pluto.channels.xml',),feeds=('PlutoTV/us',))
   xml=b'<tv><channel id="abc"><display-name>Pluto Demo Raw</display-name></channel><programme channel="abc" start="20300101150000 +0000" stop="20300101160000 +0000"><title>Fast Show</title></programme></tv>'
   channels,programmes=extract_feed_programmes(xml,fmap['PlutoTV/us'],self.SAMPLE_NOW)
   self.assertEqual(channels['PlutoDemo.us@SD'],['Pluto Demo','Pluto Demo Raw'])
   self.assertEqual(len(programmes),1)
 def test_checked_in_lineups_and_domain_concurrency(self):
  from run_grabs import _source_domain_key,run_all_grabs
  repo=Path(__file__).resolve().parent.parent
  for lineup in sorted((repo/'lineups').glob('*.channels.xml')):
   root=ET.parse(lineup).getroot()
   self.assertEqual(root.tag,'channels')
   cids=[ch.get('xmltv_id','').strip() for ch in root.findall('channel')]
   self.assertTrue(len(cids)>0,f'{lineup.name} should not be empty')
   self.assertEqual(len(cids),len(set(c.lower() for c in cids)),f'Duplicate xmltv_id in {lineup.name}')
   for ch in root.findall('channel'):
    self.assertTrue(ch.get('site') and ch.get('site_id') and (ch.text or '').strip())
  cfg=json.loads((repo/'sources.json').read_text())
  self.assertEqual(cfg['required_regions'],['US','CA','FR','UK','MENA','ZA'])
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   c1=p/'c1.xml';c1.write_text('<channels><channel site="tvpassport.com" site_id="1" lang="en" xmltv_id="A.ca@SD">A</channel></channels>')
   c2=p/'c2.xml';c2.write_text('<channels><channel site="tvpassport.com" site_id="2" lang="en" xmltv_id="B.ca@SD">B</channel></channels>')
   manifest=[
    {'id':'first','region':'CA','channels_file':str(c1),'output':'00-first.xml','selected_channels':1},
    {'id':'second','region':'CA','channels_file':str(c2),'output':'01-second.xml','selected_channels':1},
   ]
   self.assertEqual(_source_domain_key(manifest[0]),'site:tvpassport.com')
   self.assertEqual(_source_domain_key(manifest[1]),'site:tvpassport.com')
   with patch('run_grabs.run_grab',side_effect=lambda src,u,o:{'exit_code':0,'ran':src['id']}):
    res=run_all_grabs(manifest,p,p/'out',max_workers=2)
   self.assertEqual([r['id'] for r in res],['first','second'])
   saved=json.loads((p/'out/grab-results.json').read_text())
   self.assertEqual([r['id'] for r in saved],['first','second'])
 def test_western_hemisphere_grab_start_date(self):
  from run_grabs import grab_start_date,run_grab
  evening_utc=dt.datetime(2026,11,23,3,30,tzinfo=dt.timezone.utc)
  self.assertEqual(grab_start_date({'region':'CA'},now=evening_utc),'2026-11-22')
  self.assertEqual(grab_start_date({'region':'US'},now=evening_utc),'2026-11-22')
  self.assertEqual(grab_start_date({'region':'BR'},now=evening_utc),'2026-11-22')
  self.assertEqual(grab_start_date({'region':'MX'},now=evening_utc),'2026-11-22')
  self.assertEqual(grab_start_date({'region':'LATAM'},now=evening_utc),'2026-11-22')
  self.assertEqual(grab_start_date({'region':'FR'},now=evening_utc),'2026-11-22')
  after_ny_midnight=dt.datetime(2026,11,23,5,30,tzinfo=dt.timezone.utc)
  self.assertEqual(grab_start_date({'region':'FR'},now=after_ny_midnight),'2026-11-23')
  self.assertEqual(grab_start_date({'region':'CA'},now=after_ny_midnight),'2026-11-22')
  daytime_utc=dt.datetime(2026,11,23,14,0,tzinfo=dt.timezone.utc)
  self.assertEqual(grab_start_date({'region':'CA'},now=daytime_utc),'2026-11-23')
  captured={}
  class FakeProc:
   pid=12345
   def wait(self,timeout=None):return 0
  def fake_popen(cmd,cwd=None,env=None,start_new_session=False):
   captured['env']=env
   return FakeProc()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   src={'id':'ca-tvpassport','region':'CA','channels_file':'ca.xml','output':'ca.xml','selected_channels':5}
   with patch('run_grabs.grab_start_date',return_value='2026-11-22'),patch('run_grabs.subprocess.Popen',side_effect=fake_popen):
    res=run_grab(src,p,p)
   self.assertEqual(res['exit_code'],0)
   self.assertEqual(captured['env']['CURR_DATE'],'2026-11-22')
if __name__=='__main__':unittest.main()
