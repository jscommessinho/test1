"""Read-only BET PILOT SQLite -> unmodified Goal Analyzer 3.0 RC2.

Install the user's original, checksum-verified GA3 RC2 Python package into
the Python environment first. This bridge does not copy, patch or recalibrate GA3.
Only finished-match goals are read from PitchAPI shards. Team xG/xGA are never read.
"""
from __future__ import annotations
import argparse, json, sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from goal_analyzer3.data import Match, require_pitchapi
from goal_analyzer3.model import ModelConfig, RidgePoisson
from goal_analyzer3.probability import predict_markets

FINAL = {"finished","after_extra_time","after_penalties"}


class IntegrationError(ValueError):
    pass


def ro(path: Path):
    con=sqlite3.connect(path.absolute().as_uri()+"?mode=ro",uri=True)
    con.row_factory=sqlite3.Row
    return con


def _norm(text: str) -> str:
    """Exact-with-punctuation-insensitive lookup; no fuzzy guessed matches."""
    import unicodedata
    value = unicodedata.normalize("NFKD", str(text)).casefold()
    return "".join(ch for ch in value if ch.isalnum())


def available_competitions(warehouse: Path) -> list[dict]:
    """Discover *any* leagues with SQLite season archives, including future additions.

    Query the provider catalogue once and only one shard per league; do not open
    every season of every league on every single prediction.
    """
    root = warehouse / "data"
    candidates: dict[str, dict] = {}
    catalog = root / "catalogue.sqlite"
    if catalog.is_file():
        with ro(catalog) as conn:
            for row in conn.execute("SELECT slug,name,pitchapi_league_id FROM leagues"):
                candidates[row["slug"]] = {
                    "slug": row["slug"], "name": row["name"],
                    "pitchapi_league_id": row["pitchapi_league_id"],
                    "has_history": False,
                }
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            shard = next(folder.glob("*.sqlite"), None)
            if shard is None:
                continue
            with ro(shard) as conn:
                row = conn.execute(
                    "SELECT slug,name,pitchapi_league_id FROM leagues WHERE slug=?",
                    (folder.name,),
                ).fetchone()
            if row is None:
                raise IntegrationError("Archive league metadata missing: " + folder.name)
            existing = candidates.get(folder.name)
            if existing and existing["pitchapi_league_id"] and row["pitchapi_league_id"] and existing["pitchapi_league_id"] != row["pitchapi_league_id"]:
                raise IntegrationError("Contradictory PitchAPI league IDs: " + folder.name)
            candidates[folder.name] = {
                "slug": folder.name, "name": row["name"],
                "pitchapi_league_id": row["pitchapi_league_id"] or
                                     (existing["pitchapi_league_id"] if existing else None),
                "has_history": True,
            }
    return sorted(candidates.values(), key=lambda r: (r["name"].casefold(), r["slug"]))


def resolve_league(warehouse: Path, query: str) -> str:
    """Fast single-catalogue lookup. Read only the requested league's shards."""
    root = warehouse / "data"
    normalized = _norm(query)
    if not normalized:
        raise IntegrationError("Empty competition")
    catalogue = root / "catalogue.sqlite"
    matches = set()
    if catalogue.is_file():
        with ro(catalogue) as db:
            rows = db.execute("SELECT slug,name,pitchapi_league_id FROM leagues")
            for slug, name, pin in rows:
                if normalized in {_norm(slug), _norm(name), _norm(pin or "")}:
                    matches.add(slug)
    # Future competitions can be present on disk before catalogue registration.
    # First check exact slug (O(1)) and scan shard metadata only if needed.
    if not matches and (root / query).is_dir():
        matches.add(query)
    if not matches and root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            shard = next(folder.glob("*.sqlite"), None)
            if shard is None:
                continue
            with ro(shard) as db:
                for row in db.execute("SELECT slug,name,pitchapi_league_id FROM leagues"):
                    if normalized in {_norm(row[0]), _norm(row[1]), _norm(row[2] or "")}:
                        matches.add(row[0])
    if len(matches) != 1:
        raise IntegrationError(
            "Competition not uniquely identified in PitchAPI database: " + query)
    slug = next(iter(matches))
    if not next((root / slug).glob("*.sqlite"), None):
        raise IntegrationError(
            "WAITING_FOR_DATABASE: no imported PitchAPI history for " + slug)
    return slug


def native_pin(warehouse: Path, slug: str) -> str:
    """Look up official ID only for the selected competition."""
    root = warehouse / "data"
    found = set()
    catalogue = root / "catalogue.sqlite"
    if catalogue.is_file():
        with ro(catalogue) as db:
            row = db.execute(
                "SELECT pitchapi_league_id FROM leagues WHERE slug=?", (slug,)
            ).fetchone()
            if row and row[0]:
                found.add(row[0])
    if not found:
        first_shard = next((root / slug).glob("*.sqlite"), None)
        if first_shard:
            with ro(first_shard) as db:
                row = db.execute(
                    "SELECT pitchapi_league_id FROM leagues WHERE slug=?", (slug,)
                ).fetchone()
                if row and row[0]:
                    found.add(row[0])
    if len(found) != 1:
        raise IntegrationError("Official PitchAPI ID unavailable: " + slug)
    return next(iter(found))


def season_from_calendar(db, fixture, when: datetime, league: str):
    """Resolve missing season only within observed provider-registered season dates."""
    known={r[0] for r in db.execute(
        "SELECT season_label FROM seasons WHERE league_slug=?", (league,))}
    label=str(fixture["season_label"] or "").strip()
    if label:
        if label not in known:
            raise IntegrationError("Unregistered season: "+label)
        return label
    intervals={}
    rows=db.execute(
        """SELECT season_label,kickoff_utc FROM fixtures
           WHERE league_slug=? AND season_label IS NOT NULL
             AND kickoff_utc IS NOT NULL""",(league,))
    for season,dt_text in rows:
        if season not in known:
            continue
        try:
            moment=datetime.fromisoformat(dt_text.replace("Z","+00:00")).astimezone(timezone.utc)
        except (ValueError,TypeError):
            continue
        intervals.setdefault(season,[]).append(moment)
    candidate=[season for season,dates in intervals.items()
               if min(dates)<=when<=max(dates)]
    return candidate[0] if len(candidate)==1 else None


def load_from_warehouse(warehouse: Path, league: str, as_of: datetime):
    league=resolve_league(warehouse,league)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise IntegrationError("Timezone-aware --as-of required")
    pin=native_pin(warehouse,league)
    db_files=sorted((warehouse/"data"/league).glob("*.sqlite"))
    if not db_files: raise IntegrationError("No historical PitchAPI SQLite shards: "+league)
    matches=[]; teams={};skipped=Counter();seen={}
    for path in db_files:
        with ro(path) as db:
            recorded=db.execute("SELECT pitchapi_league_id FROM leagues WHERE slug=?",(league,)).fetchone()
            if recorded and recorded[0] and recorded[0]!=pin:
                raise IntegrationError("Inconsistent PitchAPI league ID: "+str(path))
            sql="""SELECT f.*,h.team_name AS home_name,a.team_name AS away_name
                   FROM fixtures f LEFT JOIN teams h ON h.pitchapi_team_id=f.home_team_id
                   LEFT JOIN teams a ON a.pitchapi_team_id=f.away_team_id
                   WHERE f.league_slug=? ORDER BY f.kickoff_utc,f.pitchapi_fixture_id"""
            known_seasons = {
                item[0] for item in db.execute(
                    "SELECT season_label FROM seasons WHERE league_slug=?", (league,)
                )
            }
            for f in db.execute(sql,(league,)):
                mid=f["pitchapi_fixture_id"]
                snapshot=tuple(f[k] for k in f.keys()
                               if k not in ("home_name","away_name"))
                if mid in seen:
                    if seen[mid]==snapshot:
                        skipped["identical_duplicates_deduplicated"]+=1
                        continue
                    raise IntegrationError("Conflicting duplicate fixture ID across seasons: "+mid)
                seen[mid]=snapshot
                if str(f["fixture_status"] or "").lower() not in FINAL:
                    skipped["not_final"]+=1;continue
                if not all(f[k] for k in ("kickoff_utc","home_team_id","away_team_id","raw_json")):
                    skipped["missing_identity_or_source"]+=1;continue
                h,a=f["home_team_id"],f["away_team_id"]
                hg,ag=f["home_goals"],f["away_goals"]
                if h==a or type(hg)!=int or type(ag)!=int or not(0<=hg<=30 and 0<=ag<=30):
                    skipped["invalid_score_or_teams"]+=1;continue
                try:
                    dt=datetime.fromisoformat(f["kickoff_utc"].replace("Z","+00:00"))
                    if dt.tzinfo is None: raise ValueError("naive kickoff")
                    dt=dt.astimezone(timezone.utc)
                    raw=json.loads(f["raw_json"])
                    if (raw.get("id")!=mid or (raw.get("home_team") or {}).get("id")!=h
                        or (raw.get("away_team") or {}).get("id")!=a
                        or raw.get("score_home")!=hg or raw.get("score_away")!=ag):
                        raise ValueError("Provenance mismatch")
                except (ValueError,TypeError,AttributeError):
                    skipped["invalid_pitchapi_source"]+=1;continue
                if dt>=as_of:
                    skipped["not_before_asof"]+=1;continue
                # Goals-only: no statistical feature tables are queried.
                # Original GA3 Match defaults hxg/axg to None.
                season = str(f["season_label"] or "").strip()
                if season and season not in known_seasons:
                    raise IntegrationError("Unregistered season: " + season)
                if not season:
                    season = season_from_calendar(db, f, dt, league)
                if season is None:
                    skipped["unresolvable_season"]+=1
                    continue
                matches.append(Match(dt,h,a,hg,ag,season,
                                     "PitchAPI:"+pin,"PitchAPI"))
                if f["home_name"]:teams[h]=f["home_name"]
                if f["away_name"]:teams[a]=f["away_name"]
    if not matches: raise IntegrationError("Database contains no completed, valid PitchAPI history")
    matches.sort(key=lambda m:(m.kickoff,m.home,m.away))
    require_pitchapi(matches)
    quality={"provider":"PitchAPI","league_slug":league,"pitchapi_league_id":pin,
             "read_only":True,"ga3_engine_changed":False,"season_shards":len(db_files),
             "completed_valid_matches":len(matches),
             "model_inputs":"finished match goals only (no xG / xGA)",
             "skipped":dict(skipped),
             "point_in_time_original_publication_verified":False}
    return matches,teams,quality


def choose_team(query: str, teams: dict):
    if query in teams:return query
    found=[k for k,v in teams.items() if _norm(v)==_norm(query)]
    if len(found)!=1:raise IntegrationError("Team ID/name not found uniquely: "+query)
    return found[0]


def predict(warehouse: Path,league: str,home: str,away: str,as_of: datetime,
            config: ModelConfig|None=None):
    config=config or ModelConfig()
    if config.xg_blend != 0:
        raise IntegrationError("GA3 is goals-only: xG blend is forbidden")
    rows,teams,quality=load_from_warehouse(warehouse,league,as_of)
    home,away=choose_team(home,teams),choose_team(away,teams)
    if home==away:raise IntegrationError("Identical home/away")
    cutoff=as_of.astimezone(timezone.utc)  # no monthly cutoff
    eligible=[m for m in rows if m.available<=cutoff and m.kickoff<cutoff]
    quality["fit_cutoff"]=cutoff.isoformat()
    quality["eligible_training_matches"]=len(eligible)
    counts=Counter(t for m in eligible for t in (m.home,m.away))
    quality["per_team_historical_matches"]={home:counts[home],away:counts[away]}
    model=RidgePoisson(config).fit(rows,cutoff)
    expected_home,expected_away=model.predict(home,away,as_of=as_of)
    return {"status":"RESEARCH_ONLY_NOT_VALIDATED_FOR_PRODUCTION","provider":"PitchAPI",
            "as_of":as_of.isoformat(),"teams":[home,away],
            "team_names":[teams.get(home),teams.get(away)],
            "quality":quality,"model":model.metadata(),
            "prediction":predict_markets(expected_home,expected_away),
            "point_in_time_original_publication_verified":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description="Original GA3 RC2 + BET PILOT warehouse")
    parser.add_argument("--warehouse",type=Path,required=True)
    parser.add_argument("--league",required=False)
    parser.add_argument("--as-of",required=True,help="UTC ISO time")
    parser.add_argument("--home");parser.add_argument("--away")
    parser.add_argument("--list-teams",action="store_true")
    parser.add_argument("--list-leagues",action="store_true")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--config-json")
    args=parser.parse_args(argv)
    try:
        as_of=datetime.fromisoformat(args.as_of.replace("Z","+00:00"))
        if as_of.tzinfo is None:raise IntegrationError("Timezone missing from as-of")
        as_of=as_of.astimezone(timezone.utc)
        if args.list_leagues:
            result={"leagues":available_competitions(args.warehouse)}
        elif args.list_teams:
            if not args.league:raise IntegrationError("--league is required for team list")
            _,teams,quality=load_from_warehouse(args.warehouse,args.league,as_of)
            result={"teams":[{"id":k,"name":v} for k,v in sorted(teams.items())],
                    "quality":quality}
        else:
            if not args.league or not args.home or not args.away:
                raise IntegrationError("--league, --home and --away required")
            cfg=ModelConfig(**json.loads(args.config_json)) if args.config_json else ModelConfig()
            if cfg.xg_blend != 0:
                raise IntegrationError("GA3 is goals-only: xG blend is forbidden")
            result=predict(args.warehouse,args.league,args.home,args.away,as_of,cfg)
        output=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(output+"\n",encoding="utf-8")
        else:print(output)
        return 0
    except (IntegrationError,ValueError,sqlite3.Error,OSError) as exc:
        parser.exit(2,"GA3 bridge: "+str(exc)+"\n")


if __name__=="__main__":
    raise SystemExit(main())
