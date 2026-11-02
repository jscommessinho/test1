"""Native source conversion tests: no odds, no invented xG, no SQLite writes."""
from __future__ import annotations
import gzip,hashlib,json,os,sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from betpilot.pitchapi_history_archive import load_archive
from betpilot_v3.sqlite_native_export import convert,SourceError
from betpilot_v3.command import execute


def fixture_db(root,*, corrupt=False, has_stats=True):
    path=Path(root)/'eng-premier'/'2026-2027.sqlite'
    path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path)
    c.executescript('''
       CREATE TABLE leagues(slug text,name text,country_code text,pitchapi_league_id text);
       CREATE TABLE teams(pitchapi_team_id text,team_name text);
       CREATE TABLE fixtures(pitchapi_fixture_id text,league_slug text,season_label text,
         kickoff_utc text,home_team_id text,away_team_id text,fixture_status text,
         home_goals integer,away_goals integer,raw_json text);
       CREATE TABLE source_responses(response_id integer primary key,dataset text,match_id text,
         body_gzip blob,sha256_hex text,original_bytes integer,fetched_at_utc text);
    ''')
    c.execute('INSERT INTO leagues VALUES (?,?,?,?)',('eng-premier','Premier League','ENG','l_4WFCIZ'))
    c.executemany('INSERT INTO teams VALUES (?,?)',[('t_000101','Arsenal'),('t_000102','Liverpool')])
    raw_fixture={'id':'m_000101','home_team':{'id':'t_000101'},'away_team':{'id':'t_000102'}}
    c.execute('INSERT INTO fixtures VALUES (?,?,?,?,?,?,?,?,?,?)',
       ('m_000101','eng-premier','2026/2027','2026-10-01T18:00:00+00:00',
        't_000101','t_000102','finished',2,1,json.dumps(raw_fixture)))
    if has_stats:
        payload={'data':{'match_id':'m_000101','home_team':{'id':'t_000101'},
          'away_team':{'id':'t_000102'},'periods':[{'period':'All','groups':[
          {'items':[{'key':'expected_goals','home':'1.42','away':'0.87'},
                    {'key':'total_shots','home':'12','away':'9'}]}]}]}}
        raw=json.dumps(payload).encode();sha=hashlib.sha256(raw).hexdigest()
        if corrupt:sha='0'*64
        c.execute('INSERT INTO source_responses(dataset,match_id,body_gzip,sha256_hex,original_bytes,fetched_at_utc) VALUES (?,?,?,?,?,?)',
          ('stats','m_000101',gzip.compress(raw),sha,len(raw),'2026-10-02T12:00:00+00:00'))
    c.commit();c.close();return path

class SqliteNativeExportTests(unittest.TestCase):
    def test_real_original_gts_validator_source_and_sha_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'sqlite';original=fixture_db(src)
            before=hashlib.sha256(original.read_bytes()).digest()
            result=convert(src,root/'history',root/'stats',cutoff='2026-11-02T12:00:00+00:00')
            self.assertEqual(result['counts']['accepted'],1)
            self.assertTrue(result['xg_from_team_stats_not_shot_reconstruction'])
            self.assertFalse(result['model_formulas_changed'])
            self.assertEqual(hashlib.sha256(original.read_bytes()).digest(),before)
            path=root/'history'/'eng-premier'/'archive.json'
            doc=load_archive(path,{'id':'l_4WFCIZ','name':'Premier League','country':'ENG'},'2026-11-02')
            rec=doc['records']['m_000101'];self.assertEqual(rec['home']['xg'],1.42)
            self.assertEqual(rec['away']['shots'],9)
            self.assertEqual(rec['qa_internal'],'PASS')
            self.assertIn('team',rec['source'].lower())
            p=json.loads((root/'stats'/'eng-premier'/'archive.json').read_text())
            self.assertEqual(p['records']['m_000101']['statistics']['match_id'],'m_000101')
            self.assertEqual(p['records']['m_000101']['statistics']['periods'][0]['period'],'All')

    def test_source_checksum_conflict_blocks_export(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture_db(root/'sqlite',corrupt=True)
            with self.assertRaises(SourceError) as error:
                convert(root/'sqlite',root/'history',root/'stats',cutoff='2026-11-02T12:00:00+00:00')
            self.assertEqual(error.exception.code,'ORIGINAL_PROVIDER_SHA256_MISMATCH')

    def test_no_stats_not_invented(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture_db(root/'sqlite',has_stats=False)
            result=convert(root/'sqlite',root/'history',root/'stats',cutoff='2026-11-02T12:00:00+00:00')
            self.assertEqual(result['history_records']['eng-premier'],0)
            self.assertEqual(result['counts']['missing_stats'],1)

    def test_generic_launcher_materializes_sqlite_before_authenticated_native_bridge(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture_db(root/'sqlite')
            seen=[]
            def blocked_native(args):
                h=Path(args.history_root)/'eng-premier'/'archive.json'
                s=Path(args.team_stats_root)/'eng-premier'/'archive.json'
                self.assertTrue(h.is_file());self.assertTrue(s.is_file())
                self.assertTrue(Path(args.warehouse_root).is_dir())
                seen.append((h,s))
                return {'status':'NO_BET','reason_code':'BOOKMAKER_ODDS_UNAVAILABLE',
                        'reason':'No true Bet365 odds; no bet'}
            with patch.dict(os.environ,{'PITCHAPI_API_KEY':'test_auth_123456',
                                       'APISPORTS_KEY':'test_auth_654321'}):
                out=execute('BETPILOT: Arsenal - Liverpool',output_dir=root/'out',
                  sqlite_root=root/'sqlite',now=datetime(2026,11,2,12,tzinfo=timezone.utc),
                  live_runner=blocked_native)
            self.assertEqual(out['status'],'NO_BET_DATA_OR_INTEGRATION_BLOCKED')
            self.assertEqual(out['reason_code'],'BOOKMAKER_ODDS_UNAVAILABLE')
            self.assertFalse(out['real_fixture_analysis_confirmed'])
            self.assertEqual(len(seen),1)
            self.assertFalse(seen[0][0].exists(),'Ephemeral archive must be deleted')

if __name__=='__main__': unittest.main()
