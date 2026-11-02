"""Offline reproducible native-output replay of five frozen BETPILOT engines.

This runs unchanged BA3 RC1 CLI and MA3 source on archived original GA3,
GTS3, corrected RC15 native outputs. All sample inputs are SYNTHETIC.
It does not fetch datasets, log into sportsbooks, or place bets.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def unpack_frozen(temp):
    frozen=HERE/'frozen'
    manifest=json.loads((HERE/'SOURCE_ZIPS_SHA256.json').read_text('utf8'))
    for name,expected in manifest['original_zip_sha256'].items():
        p=frozen/name
        if digest(p)!=expected:raise RuntimeError('Frozen original ZIP checksum mismatch: '+name)
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                target=(temp/info.filename).resolve()
                if not target.is_relative_to(temp.resolve()):
                    raise RuntimeError('Unsafe ZIP path: '+info.filename)
            z.extractall(temp)
    # Verify original GA3 RC2 and GTS3 RC1 internal release manifests too.
    goal=temp/'GA3_PITCHAPI_ONLY_RC2_2026-11-01'
    gts=temp/'BETPILOT_GTS3_RC1'
    ga_manifest=json.loads((goal/'MANIFEST_SHA256.json').read_text('utf8'))
    gts_manifest=json.loads((gts/'MANIFEST.json').read_text('utf8'))
    for name,expected in ga_manifest['files'].items():
        if digest(goal/name)!=expected:raise RuntimeError('Original GA3 file mutated: '+name)
    for name,expected in gts_manifest['files'].items():
        if digest(gts/name)!=expected:raise RuntimeError('Original GTS3 file mutated: '+name)
    return temp/'BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2', temp/'BETPILOT_BETANALYZER_3_0_RC1'


def replay(output_dir):
    with tempfile.TemporaryDirectory(prefix='betpilot_native_') as temp_dir:
        temp=Path(temp_dir)
        ma,ba=unpack_frozen(temp)
        native=ma/'examples'/'native_synthetic'
        env=os.environ.copy()
        env['PYTHONPATH']=os.pathsep.join([str(HERE),str(ma),str(ba),env.get('PYTHONPATH','')])
        cmd=[sys.executable,'-m','bridge.native_handoff',
             '--ma3-root',str(ma),'--ba3-root',str(ba),
             '--ma3-dir',str(native/'full_native_replay'),
             '--native-dir',str(native),'--bridge-file',str(native/'rc15_native_verdict_c_bridge.json'),
             '--output-dir',str(Path(output_dir).resolve())]
        result=subprocess.run(cmd,cwd=HERE,env=env,text=True,check=False)
        if result.returncode:raise RuntimeError('BA3 full native output replay failed')
        audit=json.loads((Path(output_dir)/'INTEGRATION_AUDIT.json').read_text())
        return audit


def tests():
    with tempfile.TemporaryDirectory(prefix='betpilot_test_') as temp_dir:
        ma,ba=unpack_frozen(Path(temp_dir))
        env=os.environ.copy()
        env['BETPILOT_UPSTREAM_WORKDIR']=temp_dir
        env['PYTHONPATH']=os.pathsep.join([str(HERE),str(ma),str(ba)])
        ga=Path(temp_dir)/'GA3_PITCHAPI_ONLY_RC2_2026-11-01'
        gts=Path(temp_dir)/'BETPILOT_GTS3_RC1'
        commands=[
          [sys.executable,'-m','unittest','discover','-s',str(HERE/'tests'),'-v'],
          [sys.executable,'-m','unittest','discover','-s',str(ba/'tests'),'-v'],
          [sys.executable,'-m','unittest','discover','-s',str(ma/'tests'),'-v'],
          [sys.executable,'-m','pytest','-q'],
          [sys.executable,'-m','unittest','discover','-s',str(gts/'tests'),'-v'],
        ]
        for i,cmd in enumerate(commands):
            print(f'=== Test suite {i+1}/{len(commands)} ===',flush=True)
            child_env=env.copy()
            if i==4:child_env['PYTHONPATH']=os.pathsep.join([str(gts/'src'),child_env['PYTHONPATH']])
            p=subprocess.run(cmd,cwd={1:ba,2:ma,3:ga,4:gts}.get(i,HERE),env=child_env,check=False)
            if p.returncode:raise RuntimeError('Test suite failed, index '+str(i+1))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=HERE/'output'/'synthetic_replay')
    p.add_argument('--test',action='store_true')
    args=p.parse_args()
    if args.test: tests()
    else: print(json.dumps(replay(args.output_dir),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
