#!/usr/bin/env python3
"""BETPILOT universal PitchAPI xG quality policy.

A source-quality gate for any PitchAPI league; NOT a replacement GTS model.
Do not call PitchAPI own match-stats a league-official external source.
Requires operator-attested, independently sourced same-match league FT xG
for full official-comparator PASS. Never substitutes official xG for PitchAPI.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

MAX_ABS_XG_DELTA = Decimal('0.50')
POLICY_VERSION = 'BETPILOT-PITCHAPI-XG-QA-GLOBAL-20261031-v1'
MEASUREMENT = 'FT_XG_ALL_SHOTS_INCLUDING_PENALTIES'

class QualityError(ValueError):
    pass


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, float, int, Decimal)):
        raise QualityError('Invalid xG ' + name)
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise QualityError('Invalid xG ' + name) from None
    if not d.is_finite() or d < 0 or d > Decimal('100'):
        raise QualityError('Invalid xG ' + name)
    return d


def _url(url):
    if not isinstance(url,str):
        raise QualityError('Official reference URL missing')
    p = urlsplit(url)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment
            or p.hostname.lower().endswith('pitchapi.dev') or p.hostname.lower() == 'pitchapi.dev'):
        raise QualityError('Not an acceptable independent HTTPS official reference URL')
    return url


def validate_official_reference(reference, league):
    """Verify an operator-supplied *claim* and cross-provider mapping; not publisher authenticity.

    Non-PitchAPI source and operator validation must be documented separately.
    An arbitrary online xG estimate is not a 'league-official' reference.
    """
    if reference is None:
        return None
    if not isinstance(reference, dict):
        raise QualityError('Official reference must be a JSON object')
    if reference.get('source_type') != 'LEAGUE_OFFICIAL' or reference.get('verified_by_operator') is not True:
        raise QualityError('Official source and human verification required')
    for field in ('publisher','source_url','measurement_definition'):
        if not isinstance(reference.get(field),str) or not reference[field].strip():
            raise QualityError('Official reference missing '+field)
    _url(reference['source_url'])
    if reference['measurement_definition'] != MEASUREMENT:
        raise QualityError('Official league xG measurement definitions must match PitchAPI all-shot FT xG')
    c = reference.get('competition')
    if not isinstance(c,dict) or any(c.get(k) != league.get(k) for k in ('id','name','country')):
        raise QualityError('Official reference competition identity mismatch')
    raw = reference.get('matches')
    if not isinstance(raw,list):
        raise QualityError('Official reference matches[] required')
    indexes={}
    for item in raw:
        if not isinstance(item,dict) or not isinstance(item.get('pitchapi_match_id'),str):
            raise QualityError('Invalid official match reference')
        matchid=item['pitchapi_match_id']
        if matchid in indexes or item.get('mapping_verified_by_operator') is not True:
            raise QualityError('Duplicate or unverified official match identity')
        _url(item.get('source_match_url'))
        for key in ('date','home_team_id','away_team_id'):
            if not isinstance(item.get(key),str) or not item[key]:
                raise QualityError('Official match missing '+key)
        for key in ('home_xg','away_xg'):
            _number(item.get(key),key)
        indexes[matchid] = item
    return indexes


def _delta(a,b):
    return abs(_number(a,'PitchAPI')-_number(b,'reference'))


def assess_pitchapi_match(record, official_match=None):
    """Assess one match and BOTH teams with a 0.50 inclusive max xG difference.

    Internal: PitchAPI shots sum versus PitchAPI match team statistics.
    External: PitchAPI shots sum versus operator-attested LEAGUE official match FT xG.
    Missing external info is UNKNOWN, never PASS; failing either disqualifies match.
    """
    if not isinstance(record,dict) or not isinstance(record.get('match_id'),str):
        raise QualityError('Invalid PitchAPI match entry')
    homes = (record.get('home'),record.get('away'))
    if any(not isinstance(t,dict) or not isinstance(t.get('id'),str) for t in homes):
        raise QualityError('Invalid team identity')
    shots=tuple(_number(t.get('xg'), 'PitchAPI '+side) for t,side in zip(homes,('home','away')))
    for index,t in enumerate(homes):
        n=t.get('shots')
        if isinstance(n,bool) or not isinstance(n,int) or n<0:
            raise QualityError('Invalid shot count')
        if shots[index]>Decimal(n):
            raise QualityError('xG exceeds attempts')
    own=record.get('team_stats_xg')
    if own is None:
        internal_status='PITCHAPI_INTERNAL_REFERENCE_UNAVAILABLE'
        internal_deltas=None
    else:
        if not isinstance(own,(list,tuple)) or len(own)!=2:
            raise QualityError('Invalid PitchAPI team stats xG tuple')
        internal_deltas=[_delta(shots[i],own[i]) for i in range(2)]
        internal_status='PASS' if all(d<=MAX_ABS_XG_DELTA for d in internal_deltas) else 'REJECTED_INTERNAL_ABOVE_0_50'
    official_deltas=None
    if official_match is None:
        official_status='OFFICIAL_LEAGUE_XG_REFERENCE_MISSING'
    else:
        if (official_match.get('pitchapi_match_id')!=record['match_id'] or
            official_match.get('date')!=record.get('date') or
            official_match.get('home_team_id')!=homes[0]['id'] or
            official_match.get('away_team_id')!=homes[1]['id'] or
            official_match.get('mapping_verified_by_operator') is not True):
            raise QualityError('External official match/team/date identity mismatch')
        official_deltas=[_delta(shots[0],official_match.get('home_xg')),
                         _delta(shots[1],official_match.get('away_xg'))]
        official_status='PASS_OPERATOR_ATTESTED' if all(d<=MAX_ABS_XG_DELTA for d in official_deltas) else 'REJECTED_OFFICIAL_ABOVE_0_50'
    valid_internal=internal_status=='PASS'
    valid_official=official_status=='PASS_OPERATOR_ATTESTED'
    def represent(ds):
        return None if ds is None else [float(d) for d in ds]
    return {'match_id':record['match_id'],'date':record.get('date'),
            'policy':POLICY_VERSION,'threshold_xg':float(MAX_ABS_XG_DELTA),
            'pitchapi_shot_xg':list(map(float,shots)),
            'internal_pitchapi_status':internal_status,'internal_absolute_deltas':represent(internal_deltas),
            'league_official_status':official_status,'league_official_absolute_deltas':represent(official_deltas),
            'pitchapi_source_accepted':valid_internal and valid_official,
            'pitchapi_provisional_internal_pass':valid_internal and official_status != 'REJECTED_OFFICIAL_ABOVE_0_50',
            'official_source_verified':valid_official,
            'gts_quality_certified':valid_internal and valid_official,
            'uses_pitchapi_values_only':True}


def assess_batch(records, official_reference, league):
    refs=validate_official_reference(official_reference,league)
    result=[assess_pitchapi_match(r,refs.get(r['match_id']) if refs is not None else None) for r in records]
    internal_rejected=[r for r in result if r['internal_pitchapi_status']!='PASS']
    official_rejected=[r for r in result if r['league_official_status']=='REJECTED_OFFICIAL_ABOVE_0_50']
    pending=[r for r in result if r['league_official_status']=='OFFICIAL_LEAGUE_XG_REFERENCE_MISSING']
    return {'policy':POLICY_VERSION,'comparison_unit':'per team per match, both home and away',
            'max_abs_xg_delta_inclusive':float(MAX_ABS_XG_DELTA),'data_provider':'PitchAPI',
            'league':league,'official_reference_supplied':refs is not None,
            'official_reference_is_operator_attested_not_cryptographically_verified':refs is not None,
            'total_matches':len(result),'internal_rejected_count':len(internal_rejected),
            'official_rejected_count':len(official_rejected),'official_pending_count':len(pending),
            'internal_quality_passed':len(internal_rejected)==0,
            'hard_discrepancy':bool(internal_rejected or official_rejected),
            'all_matches_officially_crosschecked':len(result)>0 and len(internal_rejected)==0 and len(official_rejected)==0 and len(pending)==0,
            'matches':result}