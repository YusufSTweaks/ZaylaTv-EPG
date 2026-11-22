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
 def test_selector_scopes_deduplicates_and_caps(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);site=p/'sites/demo';site.mkdir(parents=True)
   (site/'a.channels.xml').write_text('<channels>'+''.join(f'<channel site="demo" site_id="{i}" lang="en" xmltv_id="{id}">Name</channel>' for i,id in enumerate(['a.us','a.us','b.ca','c.us','d.us']))+'</channels>')
   cfg={'sources':[{'id':'us','region':'US','files':['demo/*.channels.xml'],'countries':['us'],'limit':2}]}
   manifest=select(p,cfg,p/'selected');root=ET.parse(manifest[0]['channels_file']).getroot()
   self.assertEqual([n.get('xmltv_id') for n in root],['a.us','c.us'])
class EspnTests(unittest.TestCase):
 SAMPLE_NOW=dt.datetime(2030,1,1,12,0,tzinfo=dt.timezone.utc)
 def test_selector_handles_espn_adapter_and_espn_plus_flag(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   cfg={'sources':[
    {'id':'us-espn-default','region':'US','adapter':'espn'},
    {'id':'us-espn-plus','region':'US','adapter':'espn','include_espn_plus':True},
   ]}
   manifest=select(p,cfg,p/'selected')
   self.assertEqual(manifest[0]['adapter'],'espn')
   self.assertFalse(manifest[0]['include_espn_plus'])
   self.assertTrue(manifest[1]['include_espn_plus'])
 def test_linear_end_times_explicit_estimated_and_omit_final(self):
  records=[
   {'name':'First Take','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T17:00:00Z','network':{'id':'espn1','type':'linear'}},
   {'name':'SportsCenter','startDateTime':'2030-01-01T17:00:00Z','endDateTime':None,'network':{'id':'espn1','type':'linear'}},
   {'name':'NBA Today','startDateTime':'2030-01-01T18:00:00Z','endDateTime':None,'network':{'id':'espn1','type':'linear'}},
  ]
  channels,programmes=build_guide_data(records,self.SAMPLE_NOW)
  self.assertEqual(channels,{'ESPN.us@SD':'ESPN'})
  self.assertEqual(len(programmes),2)
  ordered=sorted(programmes.items())
  # First keeps explicit endDateTime without estimate note
  (c1,s1,e1),(t1,_,d1)=ordered[0]
  self.assertEqual((c1,t1),('ESPN.us@SD','First Take'))
  self.assertEqual(e1,dt.datetime(2030,1,1,17,0,tzinfo=dt.timezone.utc))
  self.assertIsNone(d1)
  # Second uses next programme start and is marked as estimated; final item without end is omitted
  (c2,s2,e2),(t2,_,d2)=ordered[1]
  self.assertEqual((c2,t2),('ESPN.us@SD','SportsCenter'))
  self.assertEqual(e2,dt.datetime(2030,1,1,18,0,tzinfo=dt.timezone.utc))
  self.assertIn(ESTIMATE_NEXT_NOTE,d2)
 def test_espn_plus_disabled_by_default_and_packs_slots_when_enabled(self):
  records=[
   {'name':'SportsCenter','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T16:00:00Z','network':{'id':'espn1','type':'linear'}},
   {'name':'Game A','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T17:00:00Z','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   {'name':'Game B','startDateTime':'2030-01-01T16:00:00Z','endDateTime':None,'duration':7200,'network':{'id':'bam_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   {'name':'Game C','startDateTime':'2030-01-01T17:00:00Z','endDateTime':'2030-01-01T19:00:00Z','network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
   {'name':'Game No End','startDateTime':'2030-01-01T18:00:00Z','endDateTime':None,'duration':None,'network':{'id':'espn_dtc','type':'online'},'packages':[{'name':'ESPN_PLUS'}]},
  ]
  ch_default,prog_default=build_guide_data(records,self.SAMPLE_NOW)
  self.assertEqual(set(ch_default),{'ESPN.us@SD'})
  self.assertEqual(len(prog_default),1)
  ch_plus,prog_plus=build_guide_data(records,self.SAMPLE_NOW,include_espn_plus=True)
  self.assertEqual(set(ch_plus),{'ESPN.us@SD','ESPNPlus.us@01','ESPNPlus.us@02'})
  self.assertEqual(len(prog_plus),4)
  slot_events={}
  for (cid,start,stop),(title,_,desc) in prog_plus.items():
   if cid.startswith('ESPNPlus.us@'):
    slot_events.setdefault(cid,[]).append((start,stop,title,desc))
  # Verify zero overlaps on each ESPN+ slot and duration estimate note on Game B
  found_duration_estimate=False
  for cid,events in slot_events.items():
   events.sort()
   for i in range(len(events)-1):
    self.assertLessEqual(events[i][1],events[i+1][0])
   for _,_,title,desc in events:
    if title=='Game B':
     self.assertIn(ESTIMATE_DURATION_NOTE,desc)
     found_duration_estimate=True
  self.assertTrue(found_duration_estimate)
 def test_espn_takes_priority_over_tvguide_with_fallback(self):
  records=[
   {'name':'Official ESPN Show','startDateTime':'2030-01-01T15:00:00Z','endDateTime':'2030-01-01T16:00:00Z','network':{'id':'espn1','type':'linear'}},
  ]
  tvguide_xml=(
   '<tv>'
   '<channel id="ESPN.us@SD"><display-name>ESPN</display-name></channel>'
   '<channel id="CNN.us@SD"><display-name>CNN</display-name></channel>'
   '<programme channel="ESPN.us@SD" start="20300101150000 +0000" stop="20300101160000 +0000"><title>TV Guide ESPN</title></programme>'
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
   self.assertEqual(report['channels'],2)
   titles={el.get('channel'):el.findtext('title') for el in ET.parse(p/'public/guide.xml').findall('programme')}
   self.assertEqual(titles,{'ESPN.us@SD':'Official ESPN Show','CNN.us@SD':'CNN News'})
   # Fallback to TV Guide when ESPN has no usable output
   (p/'00-us-espn-official.xml').write_text('<tv/>')
   merge(p,manifest,['US'],p/'public-fallback',now=self.SAMPLE_NOW)
   fallback_titles={el.get('channel'):el.findtext('title') for el in ET.parse(p/'public-fallback/guide.xml').findall('programme')}
   self.assertEqual(fallback_titles,{'ESPN.us@SD':'TV Guide ESPN','CNN.us@SD':'CNN News'})
if __name__=='__main__':unittest.main()
