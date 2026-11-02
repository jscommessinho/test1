"""Bounded authenticated API-Sports next-fixture discovery, for any two clubs.

No web search or guessed IDs. Exact provider team IDs disambiguate common names
such as Inter; at most 3 discovery requests + 1 same-day verification + 1 odds.
"""
from __future__ import annotations
from datetime import datetime, timezone
from betpilot_v3.full_analysis import parse_request
from betpilot_v3.general_fixture import team
from betpilot_v3.pitchapi_preflight import PreflightBlocked, instant, require


def _teams(response, requested, country_code=None):
    entries=response.get('response')
    require(isinstance(entries,list),'TEAM_SEARCH_INVALID','Invalid authorized team list')
    result=set()
    for item in entries:
        obj=item.get('team') if isinstance(item,dict) else None
        if not isinstance(obj,dict) or team(obj.get('name'))!=team(requested):
            continue
        if country_code is not None:
            from betpilot_v3.general_fixture import countries_equal
            if not countries_equal(country_code, obj.get('country')):
                continue
        ident=obj.get('id')
        if isinstance(ident,int) and not isinstance(ident,bool) and ident>0:
            result.add(ident)
    require(len(result)<=20,'TEAM_SEARCH_TOO_AMBIGUOUS','Too many exact-label provider team IDs')
    return result


def next_fixture(client, request, *, now=None, next_count=50, country_code=None):
    """Find a real exact home/away next fixture with provider-ID cross-checks.

    Unambiguous home: two API calls. Ambiguous home: search the away club and
    ask for its fixtures if and only if away ID is unambiguous (three calls).
    Both paths are within the five-call limit including later fixture+odds.
    """
    home, away = parse_request(request)
    now = now or datetime.now(timezone.utc)
    require(1<=next_count<=50,'FIXTURE_QUERY_INVALID','Invalid search horizon')
    home_ids=_teams(client.get('/teams',search=home),home,country_code)
    require(bool(home_ids),'HOME_TEAM_MISSING_OR_AMBIGUOUS',
            'Home team not found with a verified provider name')
    if len(home_ids)==1:
        queried_id=next(iter(home_ids))
        away_ids=None
        side='home'
    else:
        away_ids=_teams(client.get('/teams',search=away),away,country_code)
        require(len(away_ids)==1,'HOME_TEAM_MISSING_OR_AMBIGUOUS',
                'Home label ambiguous; away side cannot uniquely disambiguate it')
        queried_id=next(iter(away_ids))
        side='away'
    data=client.get('/fixtures',team=queried_id,next=next_count,timezone='UTC')
    require(isinstance(data.get('response'),list),'FIXTURES_FEED_INVALID','Invalid future fixture list')
    require(data.get('paging',{}).get('total',1)==1,'API_FIXTURES_PAGINATED','Paginated list incomplete')
    candidates=[]
    for item in data['response']:
        try:
            clubs=item['teams'];fixture=item['fixture'];league=item['league']
            if team(clubs['home']['name'])!=team(home) or team(clubs['away']['name'])!=team(away):
                continue
            if clubs['home'].get('id') not in home_ids:
                continue
            if away_ids is not None and clubs['away'].get('id') not in away_ids:
                continue
            if clubs[side].get('id')!=queried_id:
                continue
            when=instant(fixture['date'])
            if when<=now or str(fixture['status']['short']).upper() not in ('NS','TBD'):
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
            'provenance':'API_SPORTS_AUTHENTICATED_EXACT_ID_NEXT_FIXTURE',
            'disambiguation':'AWAY_TEAM_EXACT_ID' if away_ids is not None else 'HOME_TEAM_EXACT_ID'}
