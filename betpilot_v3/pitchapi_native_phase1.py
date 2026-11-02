"""One-command, fail-closed PitchAPI -> original GA3/GTS3 -> native MA3 five-file publisher.

All frozen releases are independently SHA-verified; we copy their genuine
output bytes and never simulate odds, probabilities, model parameters or data.
This is a RESEARCH_ONLY source publisher, not the bookmaker/five-engine stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from betpilot.pitchapi_gts_probe import Client
from betpilot_v3.pitchapi_preflight import (
    PreflightBlocked, publish as preflight, instant,
    find_existing_league_archives, require
)
from betpilot_v3.ga3_original_adapter import execute as run_ga3, verify_ga3_original
from betpilot_v3.gts3_original_adapter import execute as run_gts3, verify_gts3_original
from betpilot_v3.run_replay import unpack_frozen
from betpilot_v3.bridge.native_handoff import check_sha_manifest_ma3, check_sha_manifest_ba3

SCHEMA = 'BETPILOT_PITCHAPI_MA3_NATIVE_PUBLISHER_V1'
EXPORTS = (
    'match.json', 'pitchapi_identity.json', 'ga3_native_output.json',
    'gts3_native_output.json', 'GTS3_FROZEN_MODEL_PARAMS.json',
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    try:
        return json.loads(Path(path).read_text('utf8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise PreflightBlocked('SOURCE_JSON_INVALID', Path(path).name) from exc


def write_json(path, obj):
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n',
        encoding='utf8')


def select_original_gts_period(params_path, season, kickoff):
    """Choose one frozen period trained strictly before the actual analysis cutoff."""
    params = read(params_path)
    require(isinstance(season, str) and re.fullmatch(r'20\d\d/20\d\d', season),
            'GTS3_SEASON_UNSUPPORTED', 'Original GTS period requires YYYY/YYYY season')
    a, b = season.split('/')
    suffix = a + '_' + b[2:]
    candidates = [
        k for k, v in params.items()
        if isinstance(v, dict) and k.startswith(suffix + '_')
        and v.get('name') == 'odh' and v.get('features') == ['O', 'D', 'H']
        and isinstance(v.get('training_max_date'), str)
        and v['training_max_date'] < instant(kickoff).date().isoformat()
    ]
    require(len(candidates) == 1, 'GTS3_FROZEN_PERIOD_UNAVAILABLE',
            'No unique pre-target frozen ODH period for season ' + season)
    return candidates[0]


def assemble_native(gate_dir, ga_dir, gts_dir, ma_root, gts_root, destination):
    """Atomically publish ONLY when the two ORIGINAL MA3 parsers both accept."""
    gate_dir, ga_dir, gts_dir = map(Path, (gate_dir, ga_dir, gts_dir))
    destination = Path(destination).resolve()
    require(not destination.exists(), 'NATIVE_DESTINATION_EXISTS',
            'Refuse to overwrite an existing fixture bundle')
    status = read(gate_dir / 'PITCHAPI_PREFLIGHT_STATUS.json')
    require(status.get('status') == 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING'
            and status.get('full_analysis_ready') is False,
            'PREFLIGHT_NOT_VERIFIED', 'Preflight did not pass research source checks')
    for name in ('PITCHAPI_FIXTURE_CANDIDATE.json', 'PITCHAPI_ASOF_HISTORY.json'):
        require(digest(gate_dir / name) == (status.get('file_sha256') or {}).get(name),
                'PREFLIGHT_DIGEST_CHANGED', 'Verified preflight source modified')
    target = read(gate_dir / 'PITCHAPI_FIXTURE_CANDIDATE.json')
    history = read(gate_dir / 'PITCHAPI_ASOF_HISTORY.json')
    require(target.get('provider') == 'PitchAPI'
            and history.get('target_event_id') == target.get('pitchapi_event_id'),
            'PITCHAPI_IDENTITY_MISMATCH', 'Native event source identity mismatch')
    cutoff = instant(history.get('cutoff'))
    kickoff = instant(target.get('kickoff'))
    require(cutoff < kickoff and cutoff >= instant(target['fixture_captured_at_utc']),
            'NATIVE_TIME_ORDER', 'Identity/cutoff/kickoff inconsistent')
    ga_status = read(ga_dir / 'GA3_ORIGINAL_BRIDGE_STATUS.json')
    gts_status = read(gts_dir / 'GTS3_ORIGINAL_BRIDGE_STATUS.json')
    require(ga_status.get('status') == 'GA3_ORIGINAL_RESEARCH_EXECUTED'
            and ga_status.get('original_ga3_cli_executed') is True
            and ga_status.get('ma3_native_export_created') is False,
            'GA3_ORIGINAL_NOT_EXECUTED', 'Authentic original GA3 CLI status required')
    require(gts_status.get('status') == 'GTS3_ORIGINAL_RESEARCH_EXECUTED'
            and gts_status.get('original_gts3_cli_executed') is True
            and gts_status.get('ga3_probability_modified') is False
            and gts_status.get('ma3_native_export_created') is False,
            'GTS3_ORIGINAL_NOT_EXECUTED', 'Authentic original GTS3 CLI status required')
    ga_original = ga_dir / 'GA3_RC2_ORIGINAL_PREDICTION_RESEARCH.json'
    gts_original = gts_dir / 'GTS3_RC1_ORIGINAL_SCORE_RESEARCH.json'
    require(digest(ga_original) == ga_status.get('original_prediction_sha256')
            and digest(gts_original) == gts_status.get('score_sha256'),
            'ORIGINAL_OUTPUT_HASH_MISMATCH', 'Original analyzer output bytes changed')
    params_source = Path(gts_root) / 'reports' / 'selected_model_parameters.json'
    require(params_source.is_file(), 'GTS3_FROZEN_PARAMS_MISSING', 'Original GTS3 parameters absent')
    from ma3.native_exports import (
        GTS3_FROZEN_PARAMETER_SHA256, export_ga3, export_gts3
    )
    from ma3.runtime import verify_source_file
    require(digest(params_source) == GTS3_FROZEN_PARAMETER_SHA256,
            'GTS3_FROZEN_PARAMS_MISMATCH',
            'Frozen model bytes differ from MA3 pinned SHA256; no reserialization permitted')
    provider_event_id = target['pitchapi_event_id']
    match = {
        'event_id': provider_event_id, 'home': target['home']['name'],
        'away': target['away']['name'], 'competition': target['competition'],
        'kickoff': target['kickoff'], 'cutoff': cutoff.isoformat()
    }
    identity = {
        'league_id': target['pitchapi_league_id'],
        'home_team_id': target['home']['id'], 'away_team_id': target['away']['id'],
        'season': target['season'],
        'identity_reference':
            'PitchAPI:' + provider_event_id + ':sha256:' +
            (status['file_sha256']['PITCHAPI_FIXTURE_CANDIDATE.json']),
        # Provider-verified external identity, NOT independent bookmaker cross-ID.
        'identity_provenance': 'EXTERNAL_VERIFIED'
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pitchapi_native_staging_', dir=destination.parent) as td:
        tmp = Path(td)
        write_json(tmp/'match.json', match)
        write_json(tmp/'pitchapi_identity.json', identity)
        shutil.copyfile(ga_original, tmp/'ga3_native_output.json')
        shutil.copyfile(gts_original, tmp/'gts3_native_output.json')
        shutil.copyfile(params_source, tmp/'GTS3_FROZEN_MODEL_PARAMS.json')
        ga = export_ga3(tmp/'ga3_native_output.json', match, identity)
        gts = export_gts3(tmp/'gts3_native_output.json', match, identity,
                          tmp/'GTS3_FROZEN_MODEL_PARAMS.json')
        verify_source_file(ga, tmp/'ga3_native_output.json', 'GA3')
        verify_source_file(gts, tmp/'gts3_native_output.json', 'GTS3')
        require(ga.get('native_schema_parity_verified') is True
                and gts.get('native_schema_parity_verified') is True,
                'NATIVE_PARSER_PARITY_MISSING', 'Original MA3 contract parity failed')
        require(ga['source_artifact_sha256'] == digest(tmp/'ga3_native_output.json')
                and gts['source_artifact_sha256'] == digest(tmp/'gts3_native_output.json'),
                'NATIVE_PARSER_DIGEST_MISMATCH', 'MA3 normalized output source hashes differ')
        manifest = {
            'schema': SCHEMA + '_MANIFEST',
            'status': 'ORIGINAL_MA3_PARSERS_ACCEPTED_RESEARCH_ONLY',
            'operational_eligible': False, 'betting_enabled': False,
            'model_probability_fabricated': False, 'ga3_probability_modified_by_gts3': False,
            'five_file_sha256': {n:digest(tmp/n) for n in EXPORTS},
            'fixture_source_sha256': status['file_sha256'],
            'ga3_original_sha256': digest(ga_original),
            'gts3_original_sha256': digest(gts_original),
            'frozen_gts3_parameters_sha256': digest(params_source),
            'ma3_parser_ga3_markets': len(ga['event_probabilities']),
            'ma3_parser_gts3_verified': gts['native_schema_parity_verified'],
            'pitchapi_event_id': provider_event_id,
            'pitchapi_identity_verified_against_fixture_detail_and_catalogue': True,
            'independent_bookmaker_identity_reconciled': False,
            'point_in_time_provider_publication_independently_proven': False,
            'bookmaker_rc15_input_present': False,
        }
        write_json(tmp/'PITCHAPI_NATIVE_MANIFEST.json', manifest)
        os.rename(tmp, destination)  # same filesystem, publishes complete tree only
    return manifest


def execute(args):
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    status = {'schema': SCHEMA, 'mode': 'RESEARCH_ONLY', 'betting_enabled': False,
              'full_analysis_completed': False, 'bookmaker_executed': False}
    try:
        require(bool(os.environ.get('PITCHAPI_API_KEY')),
                'PITCHAPI_KEY_UNAVAILABLE', 'No authorized PitchAPI credential')
        require(Path(args.warehouse_root).is_dir(), 'WAREHOUSE_MISSING',
                'Pre-existing worldwide daily warehouse unavailable')
        require(Path(args.history_root).is_dir(), 'HISTORY_ROOT_MISSING',
                'Pre-existing pinned league archives unavailable')
        with tempfile.TemporaryDirectory(prefix='betpilot_phase1_source_') as td:
            root = Path(td)
            (root/'frozen').mkdir()
            ma, ba = unpack_frozen(root/'frozen')
            check_sha_manifest_ma3(ma)
            check_sha_manifest_ba3(ba)
            ga_root = verify_ga3_original(root/'frozen'/'GA3_PITCHAPI_ONLY_RC2_2026-11-01')
            gts_root = verify_gts3_original(root/'frozen'/'BETPILOT_GTS3_RC1')
            sys.path.insert(0, str(ma))
            from ma3.native_exports import GTS3_FROZEN_PARAMETER_SHA256
            params = gts_root/'reports'/'selected_model_parameters.json'
            require(digest(params) == GTS3_FROZEN_PARAMETER_SHA256,
                    'GTS3_FROZEN_PARAMS_MISMATCH', 'Original GTS model not MA3 pinned release')
            # Existing API client: exactly 3 authenticated, budgeted GETs.
            client = Client(os.environ['PITCHAPI_API_KEY'], budget=3, pause=.2)
            gate = root/'preflight'
            first = preflight(client, args.request, args.day_utc, args.country_code,
                              args.warehouse_root, gate, competition=args.competition,
                              history_root=args.history_root)
            status['pitchapi_requests_used'] = client.used
            require(first.get('status') == 'PITCHAPI_ASOF_PREFLIGHT_PASSED_NATIVE_PENDING',
                    first.get('reason_code', 'PREFLIGHT_BLOCKED'), first.get('reason', 'Source unavailable'))
            source = read(gate/'PITCHAPI_FIXTURE_CANDIDATE.json')
            as_of = instant(read(gate/'PITCHAPI_ASOF_HISTORY.json')['cutoff'])
            archive = find_existing_league_archives(args.history_root, source, as_of)
            # GA3 reads a newly materialized read-only overlay: original prior
            # seasons plus recent mature PASS daily snapshots (same PitchAPI).
            # GTS3 continues to see original raw /stats and season provenance.
            from betpilot_v3.ga3_rolling_archive import merged_original_and_daily
            rolling_archive = root/'rolling_history'/'archive.json'
            rolling_audit = merged_original_and_daily(
                archive, args.warehouse_root, source, as_of, rolling_archive)
            status['ga3_rolling_history'] = rolling_audit
            team_stats = getattr(args, 'team_stats_archive', None)
            if team_stats is None and getattr(args, 'team_stats_root', None) is not None:
                from betpilot_v3.general_fixture import load_registry, pinned
                pins = load_registry(Path(__file__).resolve().parents[1] /
                                     'data/pitchapi/league_pins_77.json')
                entry = pinned(source['pitchapi_league_id'], source['competition'],
                               source['country'], pins)
                possible = Path(args.team_stats_root) / entry['registry_key'] / 'archive.json'
                if possible.is_file() and not possible.is_symlink():
                    team_stats = possible
            period = select_original_gts_period(params, source['season'], as_of.isoformat())
            # Independent original engines run in parallel after the shared
            # PitchAPI archive snapshot has been verified. Each worker writes
            # to its own directory; no changes to the frozen model cores.
            from concurrent.futures import ThreadPoolExecutor
            import time
            parallel_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="betpilot-native") as pool:
                ga_future = pool.submit(run_ga3, gate, rolling_archive, ga_root, root/'ga3')
                gts_future = pool.submit(
                    run_gts3, gate, args.warehouse_root, gts_root, root/'gts3', period,
                    league_archive=archive, team_stats_archive=team_stats)
                ga_result = ga_future.result()
                gts_result = gts_future.result()
            status['parallel_ga3_gts3_elapsed_ms'] = round(
                (time.perf_counter()-parallel_start)*1000, 2)
            status['parallel_native_execution'] = True
            status['ga3_status'] = ga_result.get('status')
            status['gts3_status'] = gts_result.get('status')
            require(ga_result.get('status') == 'GA3_ORIGINAL_RESEARCH_EXECUTED',
                    ga_result.get('reason_code', 'GA3_ORIGINAL_BLOCKED'),
                    ga_result.get('reason', 'Original GA3 did not produce verified prediction'))
            require(gts_result.get('status') == 'GTS3_ORIGINAL_RESEARCH_EXECUTED',
                    gts_result.get('reason_code', 'GTS3_ORIGINAL_BLOCKED'),
                    gts_result.get('reason', 'Original GTS3 did not produce verified score'))
            sys.path.insert(0, str(ma))
            target = 'fixture_' + source['pitchapi_event_id']
            native = out/'native'/target
            manifest = assemble_native(gate, root/'ga3', root/'gts3', ma, gts_root, native)
            status.update(status='PITCHAPI_NATIVE_FIVE_EXPORTS_VERIFIED',
                          native_bundle=str(native), manifest=manifest,
                          period=period, full_analysis_completed=False)
    except (PreflightBlocked, ValueError, KeyError, TypeError, OSError, ImportError) as exc:
        status.update(status='NO_BET_NATIVE_INPUTS_BLOCKED',
                      reason_code=getattr(exc,'code','PHASE1_EXECUTION_FAILED'),
                      reason=str(exc)[:850])
    write_json(out/'PITCHAPI_PHASE1_STATUS.json', status)
    return status


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', required=True)
    p.add_argument('--day-utc', required=True)
    p.add_argument('--country-code', required=True)
    p.add_argument('--competition', required=True)
    p.add_argument('--warehouse-root', type=Path, required=True)
    p.add_argument('--history-root', type=Path, required=True,
                   help='Existing league history JSON artifacts, no new collection')
    p.add_argument('--team-stats-archive', type=Path,
                   help='Previously archived original PitchAPI /stats team records')
    p.add_argument('--team-stats-root', type=Path,
                   help='Optional existing raw /stats archive root indexed by pinned league key')
    p.add_argument('--output-dir', type=Path, required=True)
    args=p.parse_args(argv)
    result=execute(args)
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if result['status']=='PITCHAPI_NATIVE_FIVE_EXPORTS_VERIFIED' else 2


if __name__=='__main__':
    raise SystemExit(main())
