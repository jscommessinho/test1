"""Read-only historical goals export for a locally installed GA3 engine.

Only prints compressed fixture metadata (no API keys, no raw provider responses).
The source is the exact authenticated private PitchAPI database warehouse.
"""
from __future__ import annotations
import base64, gzip, json, sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CUTOFF = datetime(2026, 11, 1, 19, 45, tzinfo=timezone.utc)
warehouse = Path("../warehouse")
shards = sorted((warehouse / "data" / "ita-seriea").glob("*.sqlite"))
assert len(shards) == 6, f"Expected six published Serie A seasons, found {len(shards)}"
catalogue = warehouse / "data" / "catalogue.sqlite"
with sqlite3.connect(f"file:{catalogue.absolute()}?mode=ro", uri=True) as con:
    league_id = con.execute("SELECT pitchapi_league_id FROM leagues WHERE slug='ita-seriea'").fetchone()[0]
assert league_id and str(league_id).startswith("l_"), "Missing provider league ID"

items, teams, seen, skipped = [], {}, {}, Counter()
for path in shards:
    with sqlite3.connect(f"file:{path.absolute()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        season_ids = {r[0] for r in con.execute(
            "SELECT season_label FROM seasons WHERE league_slug='ita-seriea'")}
        id_record = con.execute(
            "SELECT pitchapi_league_id FROM leagues WHERE slug='ita-seriea'").fetchone()[0]
        if id_record and str(id_record) != str(league_id):
            raise ValueError(f"League ID conflict in {path.name}")
        q = """SELECT f.*,a.team_name home_name,b.team_name away_name
               FROM fixtures f LEFT JOIN teams a ON a.pitchapi_team_id=f.home_team_id
               LEFT JOIN teams b ON b.pitchapi_team_id=f.away_team_id
               WHERE f.league_slug='ita-seriea'"""
        for row in con.execute(q):
            match_id = str(row["pitchapi_fixture_id"])
            snapshot = [row[x] for x in (
                "season_label","kickoff_utc","fixture_status","home_goals",
                "away_goals","home_team_id","away_team_id","raw_json")]
            if match_id in seen:
                if seen[match_id] != snapshot:
                    raise ValueError("Conflicting duplicate fixture " + match_id)
                skipped["identical_duplicates"] += 1
                continue
            seen[match_id] = snapshot
            if str(row["fixture_status"] or "").lower() not in (
                "finished","after_extra_time","after_penalties"):
                skipped["not_final"] += 1
                continue
            dt = datetime.fromisoformat(str(row["kickoff_utc"]).replace("Z","+00:00"))
            if dt.tzinfo is None:
                skipped["naive_time"] += 1
                continue
            if dt.astimezone(timezone.utc) >= CUTOFF:
                skipped["after_prematch_cutoff"] += 1
                continue
            season = row["season_label"]
            if not season or season not in season_ids:
                skipped["unregistered_season"] += 1
                continue
            h,a = row["home_team_id"],row["away_team_id"]
            hg,ag = row["home_goals"],row["away_goals"]
            if not h or not a or h == a or type(hg)!=int or type(ag)!=int:
                skipped["invalid_result_or_teams"] += 1
                continue
            if not 0 <= hg <= 30 or not 0 <= ag <= 30:
                skipped["invalid_goal_range"] += 1
                continue
            try:
                original = json.loads(row["raw_json"])
                if (str(original["id"])!=match_id or original["home_team"]["id"]!=h or
                    original["away_team"]["id"]!=a or original["score_home"]!=hg or
                    original["score_away"]!=ag):
                    raise ValueError("Source mismatch")
            except (TypeError,ValueError,KeyError,AttributeError):
                skipped["unverified_source"] += 1
                continue
            if row["home_name"]: teams[h]=row["home_name"]
            if row["away_name"]: teams[a]=row["away_name"]
            items.append((int(dt.timestamp()),h,a,hg,ag,season))

assert items, "No verified completed pre-kickoff matches in downloaded database"
assert len(items) > 100, "Unexpectedly small historical coverage: investigate"
team_ids = sorted(set(x[1] for x in items) | set(x[2] for x in items))
season_names = sorted(set(x[5] for x in items))
id_idx = {v:i for i,v in enumerate(team_ids)}
s_idx = {v:i for i,v in enumerate(season_names)}
compact = {
    "provider":"PitchAPI","league_pin":league_id,
    "league_slug":"ita-seriea","prematch_cutoff":CUTOFF.isoformat(),
    "team_ids":team_ids,"team_names":[teams.get(t) for t in team_ids],
    "seasons":season_names,"matches":[[x[0],id_idx[x[1]],id_idx[x[2]],x[3],x[4],s_idx[x[5]]] for x in sorted(items)],
    "skipped":dict(skipped),"shards":len(shards),
    "point_in_time_original_publication_verified":False
}
payload = json.dumps(compact,separators=(",",":"),ensure_ascii=False).encode()
encoded=base64.b64encode(gzip.compress(payload,compresslevel=9,mtime=0)).decode()
print("VERIFIED_PREMATCH_MATCHES="+str(len(items)))
print("UNIQUE_TEAM_COUNT="+str(len(team_ids)))
print("VERIFIED_SEASONS="+str(season_names))
print("KNOWN_CLUBS="+str([z for z in compact["team_names"] if z and any(n in z.casefold() for n in ("juventus","napoli"))]))
print("EXCLUDED="+json.dumps(dict(skipped)))
print("GZ_BASE64_EXPORT_START")
for i in range(0,len(encoded),500):
    print(encoded[i:i+500])
print("GZ_BASE64_EXPORT_END")
