"""Five-motor serial bridge from supplied verified native JSON (RESEARCH ONLY).

Requires a local checkout of jscommessinho/betpilot-prematch-analyzer with the
original corrected RC15+Verdetto C adapter installed. Does not fetch data or
write/modify any upstream engine. Not authenticated or tested with real inputs.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from run_replay import unpack_frozen


def run(args):
    repo=Path(args.repo_root).resolve()
    if not (repo/'betpilot'/'bookmaker_verdict_c.py').is_file():
        raise RuntimeError('Bookmaker RC15+Verdetto C missing in provided repository')
    out=Path(args.output_dir).resolve()
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='betpilot_verified_inputs_') as td:
        tmp=Path(td)
        ma,ba=unpack_frozen(tmp)
        native=tmp/'actual_native_inputs';native.mkdir()
        for name,src in [('match.json',args.match),('pitchapi_identity.json',args.identity),
                         ('ga3_native_output.json',args.ga3_native),
                         ('gts3_native_output.json',args.gts3_native),
                         ('GTS3_FROZEN_MODEL_PARAMS.json',args.gts3_parameters),
                         ('RC15_EVENT_INPUT.json',args.rc15_input)]:
            shutil.copy2(src,native/name)
        env=os.environ.copy()
        env['PYTHONPATH']=os.pathsep.join([str(ma),str(ba),str(repo),env.get('PYTHONPATH','')])
        # Frozen RC8 code calls Numba set_num_threads(4), even on 2-vCPU hosts.
        env['NUMBA_NUM_THREADS']='4'
        mdir=out/'MA3'
        cmd=[sys.executable,'-m','ma3.cli_native',
             '--match',str(native/'match.json'),
             '--identity',str(native/'pitchapi_identity.json'),
             '--ga3-native',str(native/'ga3_native_output.json'),
             '--gts3-native',str(native/'gts3_native_output.json'),
             '--gts3-parameters',str(native/'GTS3_FROZEN_MODEL_PARAMS.json'),
             '--rc15-input',str(native/'RC15_EVENT_INPUT.json'),
             '--output-dir',str(mdir)]
        if subprocess.run(cmd,cwd=repo,env=env).returncode:
            raise RuntimeError('MA3 native/RC15 Verdict C stage failed; BA3 not executed')
        # Additive markets are evaluated by the unchanged MA3 settlement
        # engine on the verified original GA3 matrix. Never invent prices.
        sys.path.insert(0, str(ma))
        from derived_markets import write_verified
        additional = write_verified(mdir/'GA3_NORMALIZED.json',
                                    out/'ADDITIONAL_MARKETS.json')
        src=mdir/'rc15_verdict_c'/'BOOKMAKER_MA3_BRIDGE.json'
        if not src.exists():raise RuntimeError('Corrected RC15 bridge missing')
        env['PYTHONPATH']=os.pathsep.join([str(Path(__file__).resolve().parent),env['PYTHONPATH']])
        from bridge.native_handoff import run as finish
        audit=finish(ma,ba,mdir,native,src,out/'BA3')
        audit['additional_unquoted_markets'] = additional['additional_market_count']
        audit['additional_markets_file'] = str(out/'ADDITIONAL_MARKETS.json')
        print(json.dumps(audit,ensure_ascii=False,indent=2))
        return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo-root','match','identity','ga3-native','gts3-native','gts3-parameters','rc15-input','output-dir'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args(argv)
    run(args)

if __name__=='__main__':main()
