import datetime as dt,gzip,json,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
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
if __name__=='__main__':unittest.main()
