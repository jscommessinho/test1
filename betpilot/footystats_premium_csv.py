#!/usr/bin/env python3
"""FootyStats Premium manual Match CSV -> frozen Step5-compatible history.

NEVER authenticates, automates, scrapes, or fetches FootyStats website.
Original FootyStats Match CSV does not guarantee native match/team IDs: the
numeric IDs created here are documented, deterministic LOCAL SURROGATES.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = ('timestamp', 'status', 'home_team_goal_count', 'away_team_goal_count')
NAME_PAIRS = (('team_a_name', 'team_b_name'), ('home_team_name', 'away_team_name'))
EXCLUDED = re.compile(r'(esoccer|efootball|futsal|indoor|beach soccer|\bmin(?:utes)?\s+play\b|kings league|queens league|socca)', re.I)


class ImportErrorFS(ValueError):
    pass


def sig(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def key_name(s):
    if not isinstance(s, str):
        raise ImportErrorFS('Invalid team name')
    s = ' '.join(unicodedata.normalize('NFKC', s).split())
    if not s or len(s) > 120:
        raise ImportErrorFS('Missing or excessive team name')
    return s


def to_int(value, label):
    if isinstance(value, bool) or not re.fullmatch(r'\d{1,12}', str(value).strip()):
        raise ImportErrorFS('Invalid ' + label)
    return int(str(value).strip())


def stable_id(kind, identifier):
    # 52 bits: safe in JSON/JavaScript and deterministic across machines.
    return int(sig(kind + '\x1f' + identifier)[:13], 16) + 1


def parse_manifest(path):
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text('utf-8'))
    except (ValueError, OSError) as e:
        raise ImportErrorFS('Manifest is missing or not valid JSON') from e
    if not isinstance(data, dict):
        raise ImportErrorFS('Manifest must be an object')
    league = key_name(data.get('competition_key'))
    name = key_name(data.get('api_football_competition'))
    if EXCLUDED.search(league) or EXCLUDED.search(name):
        raise ImportErrorFS('Nonstandard competition excluded')
    if data.get('match_format') != 'standard_90min':
        raise ImportErrorFS('Explicit standard_90min format required')
    seasons = data.get('seasons')
    if not isinstance(seasons, list) or not seasons:
        raise ImportErrorFS('seasons[] required; one Match CSV per season')
    aliases = data.get('team_aliases', {})
    if not isinstance(aliases, dict):
        raise ImportErrorFS('team_aliases must be a JSON object')
    aliases = {key_name(k): key_name(v) for k, v in aliases.items()}
    sources = []
    seen = set()
    for spec in seasons:
        if not isinstance(spec, dict):
            raise ImportErrorFS('Invalid season spec')
        season = key_name(spec.get('season'))
        filename = spec.get('filename')
        if not isinstance(filename, str) or not re.fullmatch(r'[\w.() -]+\.csv', filename, re.I):
            raise ImportErrorFS('Season CSV filename must be a simple relative basename')
        if filename in seen:
            raise ImportErrorFS('Duplicate CSV filename in manifest')
        seen.add(filename)
        source = path.parent / filename
        if not source.is_file():
            raise ImportErrorFS('CSV file missing: ' + filename)
        sources.append((season, source))
    return {'league': league, 'api_name': name, 'aliases': aliases, 'sources': sources,
            'manifest_path': str(path), 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read_csv(path):
    # Only reads local, manually exported CSV. Strict, no web traffic.
    with Path(path).open('r', encoding='utf-8-sig', newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        if len(set(headers)) != len(headers) or not set(REQUIRED).issubset(headers):
            raise ImportErrorFS('Not a FootyStats Match CSV: missing required columns ' +
                                ', '.join(sorted(set(REQUIRED) - set(headers))))
        has_legacy = all(k in headers for k in NAME_PAIRS[0])
        has_premium = all(k in headers for k in NAME_PAIRS[1])
        if not (has_legacy or has_premium):
            raise ImportErrorFS('Not a FootyStats Match CSV: missing complete home/away team name pair')
        for n, row in enumerate(reader, 2):
            if None in row:
                raise ImportErrorFS(f'Malformed columns in {path.name}:{n}')
            row = {key.strip(): value for key, value in row.items()}
            if has_legacy and has_premium:
                if (key_name(row['team_a_name']) != key_name(row['home_team_name']) or
                        key_name(row['team_b_name']) != key_name(row['away_team_name'])):
                    raise ImportErrorFS(f'Conflicting home/away name pairs in {path.name}:{n}')
            elif has_premium:
                row['team_a_name'] = row['home_team_name']
                row['team_b_name'] = row['away_team_name']
            yield n, row


def convert(manifest_path, cutoff=None):
    config = parse_manifest(manifest_path)
    matches = []
    teams = {}
    team_raw_labels = {}
    match_ids = set()
    skipped = Counter()
    inputs = []
    cutoff_unix = None
    if cutoff:
        try:
            dt = datetime.fromisoformat(cutoff.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('Missing timezone')
            cutoff_unix = int(dt.timestamp())
        except ValueError as error:
            raise ImportErrorFS('Cutoff requires timezone-aware ISO8601 timestamp') from error
    # Every match belongs to one specific competition and an operator-declared season.
    for season, path in config['sources']:
        counts = Counter()
        for line, r in read_csv(path):
            counts['total'] += 1
            status = (r.get('status') or '').strip().lower()
            if status != 'complete':
                skipped['non_complete'] += 1
                continue
            home_source = key_name(r['team_a_name'])
            away_source = key_name(r['team_b_name'])
            home = config['aliases'].get(home_source, home_source)
            away = config['aliases'].get(away_source, away_source)
            if home.casefold() == away.casefold():
                raise ImportErrorFS(f'Identical home/away in {path.name}:{line}')
            # Do not silently interpret a score like 2.0 or a blank as numeric result.
            hg = to_int(r['home_team_goal_count'], 'home goals')
            ag = to_int(r['away_team_goal_count'], 'away goals')
            ts = to_int(r['timestamp'], 'timestamp')
            if not (946684800 <= ts <= 4102444800) or hg > 30 or ag > 30:
                raise ImportErrorFS(f'Invalid match values {path.name}:{line}')
            # Avoid using upcoming match as a past result when cutoff is supplied.
            if cutoff_unix is not None and ts >= cutoff_unix:
                skipped['at_or_after_cutoff'] += 1
                continue
            hid = stable_id(config['league'], home.casefold())
            aid = stable_id(config['league'], away.casefold())
            for n, ident in [(home, hid), (away, aid)]:
                if ident in teams and teams[ident] != n:
                    raise ImportErrorFS('Surrogate ID collision or conflicting team spelling: ' + n)
                teams[ident] = n
            team_raw_labels.setdefault(home, set()).add(home_source)
            team_raw_labels.setdefault(away, set()).add(away_source)
            identity = '|'.join([config['league'], season, str(ts), str(hid), str(aid)])
            mid = stable_id('match', identity)
            if mid in match_ids:
                raise ImportErrorFS('Duplicate fixture (same league, season, teams and timestamp)')
            match_ids.add(mid)
            matches.append({'id': mid, 'homeID': hid, 'awayID': aid,
                            'homeGoalCount': hg, 'awayGoalCount': ag,
                            'date_unix': ts, 'status': 'complete',
                            'season': season,
                            'competition_id': stable_id('season', config['league']+'|'+season),
                            'no_home_away': 0})
            counts['complete_accepted'] += 1
        inputs.append({'season': season, 'file': path.name, 'rows_total': counts['total'],
                       'complete_accepted': counts['complete_accepted'],
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    matches.sort(key=lambda x: (x['date_unix'], x['id']))
    if not matches:
        raise ImportErrorFS('No complete historical matches in supplied CSV sources')
    ids = {str(id_): name for id_, name in sorted(teams.items())}
    candidate = {'mapping_verified_by_operator': False,
                 'competition': config['api_name'],
                 'team_id_type': 'BETPILOT_DETERMINISTIC_LOCAL_SURROGATE_NOT_FOOTYSTATS_NATIVE',
                 'source': 'FootyStats Premium manually downloaded Match CSV',
                 'teams': ids, 'review_required': 'Verify every alias/club against API-Football; then separately attest and edit mapping_verified_by_operator to true'}
    audit = {'status': 'PREPARED_REQUIRES_MANUAL_MAPPING', 'source': 'FootyStats Premium manually exported Match CSV',
             'has_automated_website_access': False, 'has_native_footystats_ids': False,
             'competition_key': config['league'], 'api_football_competition': config['api_name'],
             'manifest_sha256': config['manifest_sha256'],
             'sources': inputs, 'complete_matches': len(matches), 'teams': len(teams),
             'skipped': dict(skipped), 'name_aliases_applied': config['aliases'],
             'local_team_ids_are_surrogates': True, 'historical_point_in_time_verified': False,
             'data_adequacy_floor_500': len(matches) >= 500,
             'note': 'Match timestamp is scheduled kickoff. Source is an exported present-day snapshot, not proof of historical publication. Frozen GA2 applies cutoff/lag and monthly refit independently.'}
    return matches, candidate, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description='Import manually downloaded FootyStats Premium Match CSV files. No website access.')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--cutoff', help='UTC cutoff ISO8601; optional initial filtering, GA2 applies its own stricter refit cutoff')
    parser.add_argument('--verified-team-map', type=Path, help='Operator-attested mapping JSON for live Step5 integration')
    parser.add_argument('--rc15-input', type=Path, help='Existing authenticated RC15 event input')
    args = parser.parse_args(argv)
    try:
        rows, map_draft, audit = convert(args.manifest, cutoff=args.cutoff)
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        p = out / 'footystats_history_ga2.json'
        p.write_text(json.dumps(rows, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        (out / 'team_map_REVIEW_REQUIRED.json').write_text(json.dumps(map_draft, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        (out / 'FOOTYSTATS_PREMIUM_IMPORT_AUDIT.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        if bool(args.verified_team_map) != bool(args.rc15_input):
            raise ImportErrorFS('--verified-team-map and --rc15-input must be supplied together')
        if args.verified_team_map:
            # Import the frozen Step5 orchestration only after operator attests mapping.
            vendor_dir = Path(__file__).resolve().parents[1] / 'vendor' / 'BETPILOT_STEP5'
            if not vendor_dir.is_dir():
                raise ImportErrorFS('Frozen Step5 module is not installed at vendor/BETPILOT_STEP5')
            sys.path.insert(0, str(vendor_dir))
            from step5_operational import run
            report = run([p], args.verified_team_map, args.rc15_input, out / 'native_research')
            print(json.dumps({'import': audit['status'], 'native': report}, ensure_ascii=False))
        else:
            print(json.dumps({'import': audit['status'], 'complete_matches': len(rows),
                              'team_map': 'REVIEW_REQUIRED', 'ga2_not_run': True}, ensure_ascii=False))
        return 0
    except (ImportErrorFS, OSError, ValueError, RuntimeError) as error:
        print('STEP6_ABSTAIN: '+str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
