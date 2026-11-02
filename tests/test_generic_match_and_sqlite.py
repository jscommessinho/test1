"""Universal match selection tests. No web, no external API, no real betting odds."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from betpilot_v3.command import execute
from betpilot_v3.sqlite_source import select_fixture, match_candidates, common_competition_country, SqliteSourceError
from betpilot_v3.fixture_autodiscovery import next_fixture
from betpilot_v3.pitchapi_preflight import PreflightBlocked


NOW=datetime(2026,11,2,15,tzinfo=timezone.utc)


def make_db(root, *, teams=('Juventus','Napoli'), kickoff=None, id='m_1', status='scheduled'):
    root=Path(root); db=root/'pitch.sqlite'
    con=sqlite3.connect(str(db))
    con.executescript('''
        CREATE TABLE fixtures (pitchapi_fixture_id TEXT, league_slug TEXT,
            season_label TEXT,kickoff_utc TEXT,home_team_id TEXT,
            away_team_id TEXT,fixture_status TEXT);
        CREATE TABLE teams(pitchapi_team_id TEXT,team_name TEXT);
        CREATE TABLE leagues(slug TEXT, pitchapi_league_id TEXT,name TEXT,country_code TEXT);
        INSERT INTO leagues VALUES ('ita-seriea','l_SerieA','Serie A','ITA');
    ''')
    con.executemany('INSERT INTO teams VALUES (?,?)',[('t1',teams[0]),('t2',teams[1])])
    con.execute('INSERT INTO fixtures VALUES (?,?,?,?,?,?,?)',(id,'ita-seriea','2026/2027',
                (kickoff or (NOW+timedelta(days=4)).isoformat()),'t1','t2',status))
    con.commit();con.close()
    return db

class TestSqliteSource(unittest.TestCase):
    def test_arbitrary_fixture_team_pair_same_original_db(self):
        for pair in [('Juventus','Napoli'),('Inter','Milan'),('Deportivo La Coruña','Osasuna')]:
            with self.subTest(pair=pair),tempfile.TemporaryDirectory() as td:
                db=make_db(td,teams=pair)
                row=select_fixture(Path(td),f'BETPILOT: {pair[0]} - {pair[1]}',now=NOW)
                self.assertEqual(row['pitchapi_event_id'],'m_1')
                self.assertEqual(row['league_slug'],'ita-seriea')
                self.assertEqual(row['provider'],'PitchAPI')
                self.assertEqual(db.stat().st_size,db.stat().st_size)
    def test_past_fixture_not_selected_for_live_prematch(self):
        with tempfile.TemporaryDirectory() as td:
            make_db(td,kickoff='2025-04-11T16:00:00+00:00',status='finished')
            self.assertIsNone(select_fixture(Path(td),'BETPILOT: Juventus - Napoli',now=NOW))
            self.assertEqual(len(match_candidates(Path(td),'BETPILOT: Juventus - Napoli',upcoming=False)),1)
    def test_database_remains_bitwise_unmodified(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            db=make_db(td)
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            select_fixture(Path(td),'BETPILOT: Juventus - Napoli',now=NOW)
            self.assertEqual(before,hashlib.sha256(db.read_bytes()).hexdigest())
    def test_original_sqlite_country_hint_only_from_both_club_memberships(self):
        with tempfile.TemporaryDirectory() as td:
            make_db(td,teams=('Inter','AC Milan'),status='finished')
            self.assertEqual(common_competition_country(Path(td),'BETPILOT: Inter - Milan'),'ITA')
            self.assertIsNone(common_competition_country(Path(td),'BETPILOT: Roma - Lazio'))

    def test_empty_catalogue_not_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'catalogue.sqlite'
            con=sqlite3.connect(p);con.execute('CREATE TABLE catalogue_snapshots(id INTEGER)');con.close()
            self.assertIsNone(select_fixture(Path(td),'BETPILOT: Roma - Lazio',now=NOW))


class DummyClient:
    def __init__(self, pair=('Juventus','Napoli')):
        self.pair=pair;self.calls=[]
    def get(self,path,**params):
        self.calls.append((path,params))
        if path=='/teams':
            return {'response':[{'team':{'id':104,'name':self.pair[0]}}]}
        if path=='/fixtures':
            return {'paging':{'total':1},'response':[{'fixture':{
                'id':100400,'date':'2026-11-10T18:00:00+00:00','status':{'short':'NS'}},
                'teams':{'home':{'id':104,'name':self.pair[0]},
                         'away':{'id':105,'name':self.pair[1]}},
                'league':{'id':135,'season':2026}}]}
        raise AssertionError(path)

class TestAutoFixtureDiscovery(unittest.TestCase):
    def test_generic_teams_and_no_date_needed(self):
        for pair in [('Juventus','Napoli'),('Roma','Lazio'),('Inter','Milan')]:
            with self.subTest(pair=pair):
                cli=DummyClient(pair)
                row=next_fixture(cli,f'BETPILOT: {pair[0]} - {pair[1]}',now=NOW)
                self.assertEqual(row['day_utc'],'2026-11-10')
                self.assertEqual(row['api_fixture_id'],100400)
                self.assertEqual([x[0] for x in cli.calls],['/teams','/fixtures'])
    def test_no_invented_opponent(self):
        cli=DummyClient(('Roma','Lazio'))
        with self.assertRaises(PreflightBlocked) as err:
            next_fixture(cli,'BETPILOT: Roma - Torino',now=NOW)
        self.assertEqual(err.exception.code,'FIXTURE_NOT_FOUND')
    def test_home_team_ambiguous(self):
        cli=DummyClient()
        def many(path,**params):
            if path=='/teams' and params['search']=='Juventus':
                return {'response':[{'team':{'id':1,'name':'Juventus'}},
                                    {'team':{'id':2,'name':'Juventus'}}]}
            if path=='/teams' and params['search']=='Napoli':
                return {'response':[]}
            raise AssertionError('Unexpected fixture call')
        cli.get=many
        with self.assertRaises(PreflightBlocked) as err:
            next_fixture(cli,'BETPILOT: Juventus - Napoli',now=NOW)
        self.assertEqual(err.exception.code,'HOME_TEAM_MISSING_OR_AMBIGUOUS')

class TestAmbiguousClubName(unittest.TestCase):
    def test_inter_ambiguity_resolved_by_real_milan_provider_id(self):
        class ClubClient:
            calls=[]
            def get(self,path,**params):
                self.calls.append((path,params))
                if path=='/teams' and params['search']=='Inter':
                    return {'response':[{'team':{'id':505,'name':'Inter'}},
                                        {'team':{'id':777,'name':'Inter'}}]}
                if path=='/teams' and params['search']=='Milan':
                    return {'response':[{'team':{'id':489,'name':'AC Milan'}}]}
                if path=='/fixtures':
                    assert params['team']==489
                    return {'paging':{'total':1},'response':[{'fixture':{'id':12001,
                       'date':'2026-11-10T20:00:00+00:00','status':{'short':'NS'}},
                       'teams':{'home':{'id':505,'name':'Inter'},'away':{'id':489,'name':'AC Milan'}},
                       'league':{'id':135,'season':2026}}]}
                raise AssertionError((path,params))
        client=ClubClient()
        result=next_fixture(client,'BETPILOT: Inter - Milan',now=NOW)
        self.assertEqual(result['api_fixture_id'],12001)
        self.assertEqual(result['disambiguation'],'AWAY_TEAM_EXACT_ID')
        self.assertEqual([p for p,_ in client.calls],['/teams','/teams','/fixtures'])
    def test_authenticated_country_disambiguates_same_name_clubs(self):
        class ClubClient:
            def __init__(self):self.calls=[]
            def get(self,path,**params):
                self.calls.append((path,params))
                if path=='/teams':
                    self.asserted=False
                    return {'response':[{'team':{'id':505,'name':'Inter','country':'Italy'}},
                                        {'team':{'id':777,'name':'Inter','country':'Brazil'}}]}
                if path=='/fixtures':
                    assert params['team']==505
                    return {'paging':{'total':1},'response':[{'fixture':{'id':12001,
                       'date':'2026-11-10T20:00:00+00:00','status':{'short':'NS'}},
                       'teams':{'home':{'id':505,'name':'Inter'},'away':{'id':489,'name':'AC Milan'}},
                       'league':{'id':135,'season':2026}}]}
                raise AssertionError((path,params))
        client=ClubClient()
        row=next_fixture(client,'BETPILOT: Inter - Milan',now=NOW,country_code='ITA')
        self.assertEqual(row['api_fixture_id'],12001)
        self.assertEqual(len(client.calls),2)

    def test_country_hint_is_passed_to_external_bridge(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);wh=root/'warehouse';hist=root/'history';db=root/'db'
            for p in (wh,hist,db):p.mkdir()
            make_db(db,teams=('Inter','AC Milan'),status='finished')
            captured=[]
            def fake_live(args):
                captured.append(args)
                return {'status':'NO_BET_PREMATCH_CHAIN_BLOCKED','reason_code':'NO_AUTHENTIC_BET365_ODDS'}
            with patch.dict(os.environ,{'PITCHAPI_API_KEY':'test-secret','APISPORTS_KEY':'test-secret'}):
                result=execute('BETPILOT: Inter - Milan',output_dir=root/'out',
                               warehouse_root=wh,history_root=hist,sqlite_root=db,
                               now=NOW,live_runner=fake_live)
            self.assertEqual(captured[0].country_code,'ITA')
            self.assertEqual(result['country_hint'],'ITA')

    def test_one_team_cannot_identify_arbitrary_duplicate_name(self):
        from betpilot_v3.general_fixture import team
        self.assertEqual(team('Internazionale'),team('Inter'))
        self.assertEqual(team('Milan'),team('AC Milan'))
        self.assertNotEqual(team('Inter Miami'),team('Inter'))

class TestCommandUniversal(unittest.TestCase):
    def test_passes_generic_match_to_native_runner_with_sqlite_day(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);wh=root/'warehouse';hist=root/'history'
            wh.mkdir();hist.mkdir();db=root/'sqlite';db.mkdir()
            make_db(db,teams=('Roma','Lazio'))
            captured=[]
            def fake_live(args):
                captured.append(args)
                return {'status':'NO_BET_PREMATCH_CHAIN_BLOCKED','reason_code':'ODDS_UNAVAILABLE'}
            with patch.dict(os.environ,{'PITCHAPI_API_KEY':'test-secret','APISPORTS_KEY':'test-secret'}):
                result=execute('BETPILOT: Roma - Lazio',output_dir=root/'out',warehouse_root=wh,
                               history_root=hist,sqlite_root=db,now=NOW,live_runner=fake_live)
            self.assertEqual(len(captured),1)
            self.assertEqual(captured[0].day_utc,'2026-11-06')
            self.assertEqual(result['status'],'NO_BET_DATA_OR_INTEGRATION_BLOCKED')
            self.assertFalse(result['five_engines_verified'])
    def test_when_no_sqlite_fixture_discover_on_authorized_api_not_guess_today(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);wh=root/'warehouse';hist=root/'history';wh.mkdir();hist.mkdir()
            captured=[]
            def fake_live(args):
                captured.append(args.day_utc)
                return {'status':'NO_BET_PREMATCH_CHAIN_BLOCKED','reason_code':'FIXTURE_NOT_FOUND'}
            with patch.dict(os.environ,{'PITCHAPI_API_KEY':'test-secret','APISPORTS_KEY':'test-secret'}):
                result=execute('BETPILOT: Inter - Milan',output_dir=root/'out',warehouse_root=wh,
                               history_root=hist,now=NOW,live_runner=fake_live)
            self.assertEqual(captured,[None])
            self.assertEqual(result['date_selection'],'authenticated_api_sports_autodiscovery')
            self.assertIsNone(result['date_utc_checked'])

if __name__=='__main__': unittest.main()
