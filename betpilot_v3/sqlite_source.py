"""Read-only access to authentic PitchAPI SQLite seasonal archives.

This adapter ONLY indexes original fixture identity/UTC kickoff. It does not
infer missing statistical values or convert SQLite projections into GA3/GTS3
native history; the frozen history schemas are different. Neither provider
responses nor existing SQLite files are modified.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from betpilot_v3.full_analysis import parse_request
from betpilot_v3.general_fixture import team
from betpilot_v3.pitchapi_preflight import instant


class SqliteSourceError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _files(root: Path, *, max_archives=150):
    raw_root = Path(root)
    if raw_root.is_symlink():
        raise SqliteSourceError('SQLITE_ROOT_INVALID', 'Symlink roots forbidden')
    root = raw_root.resolve(strict=True)
    if not root.is_dir():
        raise SqliteSourceError('SQLITE_ROOT_INVALID', 'Archive root must be a directory')
    paths = sorted(root.glob('**/*.sqlite'))
    if len(paths) > max_archives:
        raise SqliteSourceError('SQLITE_ARCHIVES_LIMIT', 'Unexpected number of SQLite files')
    for path in paths:
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            raise SqliteSourceError('SQLITE_UNSAFE_PATH', 'Unsafe SQLite path')
        yield path


def match_candidates(root: Path, request: str, *, now: datetime | None = None, upcoming=True):
    """Look up ANY requested team names in original read-only seasonal databases.

    Only unique exact normalized home/away club labels qualify. Past matches
    are omitted for default PREMATCH discovery; all metadata remains sourced.
    """
    home, away = parse_request(request)
    expected = (team(home), team(away))
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise SqliteSourceError('INVALID_TIMESTAMP', 'Timezone-aware UTC required')
    found: dict[str, dict] = {}
    inspected = 0
    for path in _files(root):
        inspected += 1
        # mode=ro guarantees no database mutation. No user-defined SQL content.
        uri = 'file:' + quote(str(path), safe='/') + '?mode=ro'
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as con:
                con.execute('PRAGMA query_only=ON')
                required = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('fixtures','teams','leagues')")}
                if required != {'fixtures','teams','leagues'}:
                    continue  # original catalogue-only DB; not a seasonal fixture archive
                rows = con.execute('''
                    SELECT f.pitchapi_fixture_id, f.kickoff_utc,
                           ht.team_name, at.team_name, f.fixture_status,
                           f.league_slug, f.season_label, l.pitchapi_league_id,
                           l.name, l.country_code
                      FROM fixtures f
                      JOIN teams ht ON ht.pitchapi_team_id=f.home_team_id
                      JOIN teams at ON at.pitchapi_team_id=f.away_team_id
                      JOIN leagues l ON l.slug=f.league_slug
                     WHERE f.kickoff_utc IS NOT NULL
                ''')
                for (fid, kickoff, hname, aname, status, slug, season,
                     league_id, lname, country) in rows:
                    if (team(hname), team(aname)) != expected:
                        continue
                    try:
                        dt = instant(kickoff)
                    except ValueError:
                        continue
                    if upcoming and (dt <= timestamp or str(status).casefold() in
                            ('finished', 'ft', 'played', 'live', 'in_progress')):
                        continue
                    row = {'provider':'PitchAPI', 'pitchapi_event_id':fid,
                           'home':hname, 'away':aname, 'kickoff':dt.isoformat(),
                           'status':status, 'league_slug':slug, 'league_id':league_id,
                           'competition':lname, 'country_code':country,
                           'season':season, 'source':'ORIGINAL_SQLITE_READ_ONLY',
                           'sqlite_archive':str(path.relative_to(Path(root).resolve()))}
                    if fid in found and any(found[fid][k] != row[k] for k in
                                            ('home','away','kickoff','league_slug','season')):
                        raise SqliteSourceError('SQLITE_IDENTITY_CONFLICT', fid)
                    found[fid] = row
        except sqlite3.DatabaseError as exc:
            raise SqliteSourceError('SQLITE_ARCHIVE_INVALID', path.name) from exc
    return sorted(found.values(),key=lambda x:(x['kickoff'],str(x['pitchapi_event_id'])))


def select_fixture(root: Path, request: str, *, now=None):
    candidates=match_candidates(root,request,now=now)
    if not candidates:
        return None
    # Choose closest future match; if same kickoff appears twice, ambiguous.
    first = candidates[0]
    if len(candidates)>1 and candidates[1]['kickoff']==first['kickoff']:
        raise SqliteSourceError('AMBIGUOUS_PITCHAPI_FIXTURE', 'Multiple fixtures at earliest kickoff')
    return first


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--request',required=True)
    p.add_argument('--include-past',action='store_true')
    args=p.parse_args(argv)
    rows=match_candidates(args.root,args.request,upcoming=not args.include_past)
    print(json.dumps({'source':'PitchAPI_SQLITE_READ_ONLY','candidates':rows},ensure_ascii=False))
    return 0 if rows else 2

if __name__=='__main__':
    raise SystemExit(main())
