"""Bounded authenticated API-Sports discovery for generic BETPILOT team pairs.

Only authorized API-Sports /teams and /fixtures calls; no web browsing,
no guessed IDs, no third-party sources and no synthetic fixture fallback.
"""
from __future__ import annotations
from datetime import datetime, timezone
from betpilot_v3.full_analysis import parse_request
from betpilot_v3.general_fixture import team
from betpilot_v3.pitchapi_preflight import PreflightBlocked, instant, require


def next_fixture(client, request, *, now=None, next_count=30):
    """At most two billable GETs; consumed from same quote-capture quota client.

    Every successful row matches home/away names AND authentic provider IDs.
    """
    home, away = parse_request(request)
    now = now or datetime.now(timezone.utc)
    require(1 <= next_count <= 50,'FIXTURE_QUERY_INVALID','Invalid search horizon')
    result=client.get('/teams', search=home)
    entries=result.get('response')
    require(isinstance(entries,list),'TEAM_SEARCH_INVALID','Invalid authorized team list')
    exact=[]
    for item in entries:
        obj=item.get('team') if isinstance(item,dict) else None
        if not isinstance(obj,dict) or team(obj.get('name'))!=team(home):
            continue
        ident=obj.get('id')
        if isinstance(ident,int) and not isinstance(ident,bool) and ident>0:
            exact.append(ident)
    require(len(set(exact))==1,'HOME_TEAM_MISSING_OR_AMBIGUOUS',
            'Exact home team not uniquely identifiable via API-Sports')
    data=client.get('/fixtures',team=exact[0],next=next_count,timezone='UTC')
    require(isinstance(data.get('response'),list),'FIXTURES_FEED_INVALID','Invalid future fixture list')
    require(data.get('paging',{}).get('total',1)==1,'API_FIXTURES_PAGINATED','Paginated list incomplete')
    candidates=[]
    for item in data['response']:
        try:
            clubs=item['teams'];fixture=item['fixture'];league=item['league']
            if team(clubs['home']['name'])!=team(home) or team(clubs['away']['name'])!=team(away):
                continue
            if clubs['home']['id']!=exact[0]:
                continue
            when=instant(fixture['date'])
            if when<=now:
                continue
            if str(fixture['status']['short']).upper() not in ('NS','TBD'):
                continue
            if any(not isinstance(league.get(k),int) or isinstance(league.get(k),bool)
                    for k in ('id','season')):
                continue
            if not isinstance(fixture.get('id'),int) or isinstance(fixture['id'],bool):
                continue
            candidates.append((when,item))
        except (KeyError,TypeError,PreflightBlocked):
            continue
    candidates.sort(key=lambda x:(x[0],x[1]['fixture']['id']))
    require(bool(candidates),'FIXTURE_NOT_FOUND',
            'No exact future match in authorized API-Sports fixture horizon')
    require(len(candidates)==1 or candidates[0][0]!=candidates[1][0],
            'FIXTURE_AMBIGUOUS','Multiple same-time fixtures; unable to identify')
    when,chosen=candidates[0]
    return {'day_utc':when.date().isoformat(),
            'api_fixture_id':chosen['fixture']['id'],
            'api_league_id':chosen['league']['id'],
            'api_season':chosen['league']['season'],
            'kickoff_utc':when.isoformat(),
            'home':chosen['teams']['home']['name'],
            'away':chosen['teams']['away']['name'],
            'provenance':'API_SPORTS_AUTHENTICATED_NEXT_FIXTURE'}
