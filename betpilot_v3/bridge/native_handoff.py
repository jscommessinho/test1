"""BETPILOT MA3 RC2 -> BA3 RC1 external integrity-checked adapter.

Never mutates upstream engines, fabricates OOS calibration/uncertainty,
converts unverified quotes to verified quotes, or relies on legacy GA2/MA2.
Caller supplies original MA3 and BA3 source roots plus genuine native output
files; included reproduction is explicitly SYNTHETIC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


class IntegrationError(ValueError):
    pass


def sha(path: Path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def ensure(cond: bool, message: str):
    if not cond:
        raise IntegrationError(message)


def check_sha_manifest_ba3(ba3_root: Path):
    manifest = read(ba3_root / 'RELEASE_MANIFEST.json')
    ensure(manifest['version'] == 'RC1_RESEARCH_CHECKPOINT_2026-11-01', 'Unexpected BA3 release')
    for filename, expected in manifest['sha256'].items():
        ensure(sha(ba3_root / filename) == expected, f'BA3 original mutated: {filename}')
    return {'files':len(manifest['sha256']), 'version': manifest['version']}


def check_sha_manifest_ma3(ma3_root: Path):
    lines = (ma3_root / 'MANIFEST_SHA256.txt').read_text('utf-8').splitlines()
    verified=0
    for line in lines:
        expected, name=line.split('  ', 1)
        ensure(sha(ma3_root / name) == expected, f'MA3 original mutated: {name}')
        verified+=1
    ensure(verified >= 20, 'Unusually short MA3 source manifest')
    return {'files':verified,'version':'MA3 Native RC2'}


def audit_native_sources(ma3_root, native_dir, match, norm):
    """Replay unmodified GA3/GTS3 *parsers* + native probability/score parity."""
    from ma3.native_exports import export_ga3, export_gts3
    from ma3.runtime import verify_source_file
    ids=read(native_dir/'pitchapi_identity.json')
    ga_path=native_dir/'ga3_native_output.json'
    gts_path=native_dir/'gts3_native_output.json'
    frozen=native_dir/'GTS3_FROZEN_MODEL_PARAMS.json'
    ga=export_ga3(ga_path,match,ids)
    gts=export_gts3(gts_path,match,ids,frozen)
    verify_source_file(ga,ga_path,'GA3')
    verify_source_file(gts,gts_path,'GTS3')
    ensure(ga == norm['ga3_rc2'],'MA3 GA3 normalized output disagrees with authenticated native parser')
    ensure(gts == norm['gts3_rc1'],'MA3 GTS3 normalized output disagrees with authenticated native parser')
    return {'ga3_file_sha256':sha(ga_path),'gts3_file_sha256':sha(gts_path),
            'gts3_model_parameter_sha256':sha(frozen), 'native_schema_parity': True}


def audit_ma3(ma3_dir, native_dir, bridge_file, ma3_root):
    from ma3.betanalyzer_contract import accept_handoff
    from ma3.runtime import build_handoff
    from ma3.final_sources import analyze_final_sources,from_final_sources
    names=('MA3_INPUT_NORMALIZED.json','MA3_ANALYSIS.json','BETANALYZER_HANDOFF.json')
    manifest=read(ma3_dir/'MA3_RUN_MANIFEST.json')
    ensure(manifest.get('ga3_gts3_native_parity_verified') is True, 'MA3 native parity unverified')
    ensure(manifest.get('source_engines_modified') is False and manifest.get('selection_automated') is False,'MA3 unsafe manifest')
    for n in names:
        ensure(sha(ma3_dir/n) == manifest.get('files_sha256',{}).get(n), 'MA3 output integrity mismatch: '+n)
    norm=read(ma3_dir/names[0]); ma3=read(ma3_dir/names[1]);handoff=read(ma3_dir/names[2]);match=norm['match']
    ensure(match == ma3['match'] == handoff['event'], 'MA3 match identity changed')
    ensure(handoff == build_handoff(ma3), 'MA3 handoff does not match MA3 analysis')
    accepted=accept_handoff(handoff)
    ensure(accepted['status']=='BETANALYZER_HANDOFF_ACCEPTED_RESEARCH_ONLY','MA3 native handoff not accepted')
    ensure(ma3.get('operational_eligible') is False and handoff.get('operational_eligible') is False,'MA3 must be research-only')
    native_audit=audit_native_sources(ma3_root,native_dir,match,norm)
    ensure(read(bridge_file) == norm['bookmaker_rc15_verdict_c'], 'Bookmaker bridge does not match MA3 source')
    # Deep semantic verification: recalculate all 47 MA3 markets from the EXACT
    # original GA3 score matrix and corrected native RC15 reported prices.
    from_final_sources(norm)  # requires full Verdict C masking and quote checks
    replay=analyze_final_sources(norm)
    ensure(replay['markets'] == ma3['markets'], 'MA3 numeric results differ from native engine replay')
    ensure(replay['bookmaker_rc15_market_methods'] == ma3['bookmaker_rc15_market_methods'], 'Bookmaker probabilities/methods changed')
    ensure(replay['source_input_sha256'] == ma3['source_input_sha256'],'MA3 normalized source hash differs')
    # For each market preserve all five settlement class probabilities exactly.
    ga_ids=set(norm['ga3_rc2'].get('event_probabilities',{}))
    ma_ids={x['market_id'] for x in ma3['markets']}
    ensure(len(ma_ids)==len(ma3['markets']), 'Duplicate MA3 markets')
    ensure(ma_ids.issubset(ga_ids), 'MA3 contains unsupported GA3 market')
    return norm,ma3,handoff,accepted,{**native_audit,'ma3_markets':len(ma_ids),
        'quoted_markets':sum(x.get('odds') is not None for x in ma3['markets'])}


def _quote_index(norm, match):
    bridge=norm['bookmaker_rc15_verdict_c']
    quotes={}
    for q in bridge['quotes']:
        mid=q['market_id']
        ensure(mid not in quotes,'Duplicate bookmaker quoted market')
        quotes[mid]=q
    return quotes


def convert(norm,ma3,handoff):
    """Strict BA3 schema adapter. Never upgrade provenance/calibration flags."""
    from ma3.core import timestamp
    from betanalyzer3.contracts import EXPECTED,validate
    from betanalyzer3.decision import ev
    match=norm['match'];cutoff=match['cutoff']
    ga=norm['ga3_rc2'];gts=norm['gts3_rc1'];book=norm['bookmaker_rc15_verdict_c']
    ensure(ga['native_schema_parity_verified'] and gts['native_schema_parity_verified'], 'Native schema parity missing')
    ensure(ga['source']=='PitchAPI' and gts['source']=='PitchAPI','Non-PitchAPI inputs forbidden')
    ensure(handoff.get('native_betanalyzer_integration_verified') is False,'Claim of prior BA3 execution not allowed')
    ensure(handoff.get('gts3_descriptive',{}).get('numeric_fusion_with_ga3') is False,'GTS improperly fused with GA3')
    ensure(book['verdict_c_audit']['schema']=='BETPILOT_BOOKMAKER_VERDICT_C_V1', 'Wrong native Verdict C audit schema')
    ensure(book['verdict_c_audit']['coefficients_changed'] is False, 'RC15 coefficients changed')
    quality=book['rc15_report']['data_quality']
    ensure(quality.get('verdict_c_reference_mismatches') == book['verdict_c_audit'].get('unavailable_reference_events'),'Verdict C reference audit mismatch')
    # All engines attest *file parity*, not real-time history quality.
    meta={key:{'version':version,'match':dict(match),'native_output_verified':True}
          for key,version in EXPECTED.items()}
    meta['goal_analyzer'].update(stats_provider='PitchAPI',source_sha256=ga['source_artifact_sha256'],
         data_cutoff=ga['cutoff'],supported_markets=sorted(ga.get('event_probabilities',{})))
    meta['gts'].update(stats_provider='PitchAPI',source_sha256=gts['source_artifact_sha256'],
         data_cutoff=gts['cutoff'],probability_adjustment=False)
    meta['bookmaker_analyzer'].update(source_sha256=hashlib.sha256(json.dumps(book,sort_keys=True,allow_nan=False).encode()).hexdigest(),
         data_cutoff=max((q['timestamp'] for q in book['quotes']),key=lambda z:timestamp(z)),
         verdict_c_audit={'applied':True,'reference_alignment_handled':True,'coefficients_changed':False})
    meta['market_analyzer'].update(source_sha256=hashlib.sha256(json.dumps(ma3,sort_keys=True,allow_nan=False).encode()).hexdigest(),
         data_cutoff=cutoff,supported_markets=sorted(x['market_id'] for x in ma3['markets']))
    quoted=_quote_index(norm,match)
    selection=[]
    supported=set(meta['goal_analyzer']['supported_markets']) & set(meta['market_analyzer']['supported_markets'])
    conversion={'full_win':'win','full_loss':'loss','push':'push','half_win':'half_win','half_loss':'half_loss'}
    for row in ma3['markets']:
        mid=row['market_id']
        if mid not in supported: continue
        ds=row.get('settlement_probabilities')
        ensure(isinstance(ds,dict), f'{mid} settlement distribution missing')
        out={to:ds[src] for src,to in conversion.items() if ds.get(src,0)>0 or src in ('full_win','full_loss')}
        ensure(abs(math.fsum(out.values())-1)<1e-8,f'{mid}: settlement sum invalid')
        if row.get('model_probability') is not None:
            ensure(abs(row['model_probability']-out['win'])<1e-8, f'{mid}: distribution and model_probability mismatch')
        ensure(row.get('reliability')=='NOT_CALIBRATED', f'{mid}: Unexpected calibration: inspect before promotion')
        qsrc=quoted.get(mid);price=row.get('odds');quote=None
        if price is not None:
            ensure(qsrc is not None and abs(qsrc['decimal_odds']-price)<1e-12,f'{mid} quote mismatch')
            ensure(qsrc['provenance']==row.get('quote_provenance'), f'{mid} quote provenance mismatch')
            ensure(timestamp(qsrc['timestamp'])<=timestamp(cutoff), f'{mid} quote later than cutoff')
            quote={'market_id':mid,'decimal_odds':price,'source':'Bet365',
                   'timestamp':qsrc['timestamp'], 'provenance':qsrc['provenance'],
                   'capture_reference':qsrc.get('external_capture_reference'),
                   # No independently audited and signed quote availability record.
                   'valid_at_cutoff':False}
            ensure(abs(ev(out,price)-row['expected_profit_per_unit'])<1e-8,
                   f'{mid}: BA3 vs MA3 expected value numerical mismatch')
        else:
            ensure(qsrc is None, f'{mid}: quoted in RC15 but MA3 reports missing quote')
        selection.append({'market_id':mid,'status':'EVALUATED' if row['technical_status']=='VALID' else 'REJECTED',
          'probability_source':'goal_analyzer','market_analyzer_evaluated':True,
          'settlement_type':'DECIMAL_FIXED','outcomes':out,
          'uncertainty_bounds':None,'ga3_calibration':'UNVALIDATED',
          'validation_reference':None,'quote':quote,
          'conflicts':['BLOCKING'] if ma3.get('coherence_issues') else [],
          'dependence_group':'UNKNOWN_CORRELATION', 'dependence_reviewed':False,
          'ma3_rank':None,'gts_diagnostic':{'role':'DESCRIPTIVE_ONLY','no_probability_adjustment':True}})
    result={'schema':'BETPILOT_BA3_HANDOFF_V1','match':dict(match),'engines':meta,
            'markets':selection}
    validate(result)
    return result


def run(ma3_root,ba3_root,ma3_dir,native_dir,bridge_file,output):
    ma3_root=Path(ma3_root);ba3_root=Path(ba3_root);ma3_dir=Path(ma3_dir)
    native_dir=Path(native_dir);bridge_file=Path(bridge_file);output=Path(output)
    upstream={'ma3':check_sha_manifest_ma3(ma3_root),'ba3':check_sha_manifest_ba3(ba3_root)}
    # The two frozen code trees are imports only; no files are overwritten.
    sys.path.insert(0,str(ma3_root.resolve()))
    sys.path.insert(0,str(ba3_root.resolve()))
    from betanalyzer3.decision import analyze,markdown_report
    norm,ma3,handoff,accepted,native=audit_ma3(ma3_dir,native_dir,bridge_file,ma3_root)
    src=convert(norm,ma3,handoff)
    decision=analyze(src) # unmodified BA3 RC1 decision engine
    ensure(decision['research_only'] and not decision['operational_eligible'],'BA3 has inappropriate production status')
    ensure(decision['primary_research_candidate'] is None and decision['secondary_research_candidate'] is None,
           'Synthetic/unvalidated source must never become a betting candidate')
    output.mkdir(parents=True,exist_ok=True)
    dump(output/'BA3_HANDOFF_FROM_MA3.json',src)
    # The original, unchanged BA3 RC1 CLI must run as a separate Python process.
    # This verifies the actual standalone executable, not merely a copied report.
    ba3_cli_dir=output/'BA3_NATIVE_CLI'
    cmd=[sys.executable,'-m','betanalyzer3.cli','--input',str((output/'BA3_HANDOFF_FROM_MA3.json').resolve()),
         '--output-dir',str(ba3_cli_dir.resolve())]
    result=subprocess.run(cmd,cwd=ba3_root.resolve(),capture_output=True,text=True,check=False)
    ensure(result.returncode==0,'BA3 original native CLI failed: '+result.stderr+' '+result.stdout)
    native_decision=read(ba3_cli_dir/'BETANALYZER_3_ANALYSIS.json')
    ensure(native_decision == decision,'BA3 CLI output differs from original BA3 decision function')
    cli_manifest=read(ba3_cli_dir/'RUN_MANIFEST.json')
    for n,h in cli_manifest['sha256'].items():
        ensure(sha(ba3_cli_dir/n)==h,'BA3 native CLI output manifest mismatch: '+n)
    dump(output/'BETANALYZER_3_ANALYSIS.json',decision)
    (output/'BETANALYZER_3_REPORT.md').write_text(markdown_report(decision)+'\n','utf8')
    audit={'status':'NATIVE_MA3_TO_BA3_DECISION_PASS_RESEARCH_ONLY',
      'sources_validated':upstream,'native_parity':native,'intake_audit':accepted,
      'decision':decision['decision'],'research_only':True,'operational_eligible':False,
      'original_engine_files_modified':False,'no_ga2_ma2_fallback':True,
      'quote_capture_independently_verified':False,'real_oos_calibration_validated':False,
      'sources_verified_at_runtime':True,'ba3_original_cli_executed':True}
    dump(output/'INTEGRATION_AUDIT.json',audit)
    fname=['BA3_HANDOFF_FROM_MA3.json','BETANALYZER_3_ANALYSIS.json','BETANALYZER_3_REPORT.md','INTEGRATION_AUDIT.json']
    dump(output/'INTEGRATION_MANIFEST.json',{'file_sha256':{n:sha(output/n) for n in fname},'engines_modified':False})
    return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ['ma3-root','ba3-root','ma3-dir','native-dir','bridge-file','output-dir']:
        parser.add_argument('--'+key,required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        result=run(args.ma3_root,args.ba3_root,args.ma3_dir,args.native_dir,args.bridge_file,args.output_dir)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (IntegrationError,ValueError,KeyError,TypeError,ImportError,FileNotFoundError) as err:
        print(json.dumps({'status':'INTEGRATION_BLOCKED','error':str(err)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
