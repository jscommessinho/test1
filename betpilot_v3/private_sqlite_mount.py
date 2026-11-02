"""Optional read-only, ephemeral import of the ORIGINAL private PitchAPI SQLite branch.

GitHub Actions GITHUB_TOKEN for `test1` cannot access a separate private repo.
Use a separate read-only secret PITCHAPI_SOURCE_READ_TOKEN with source-repo
Contents: read permission. This tool never writes source, rewrites SQLite,
or stores credentials in Git remotes. No provider calls are performed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

SOURCE='https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer.git'
BRANCH='pitchapi-warehouse'

class MountError(RuntimeError):
    pass


def mount(output: Path, *, token: str, league: str | None = None,
          seasons: int = 3, runner=subprocess.run):
    """Select recent original seasons for one league, or all in manual backfill.

    Token is passed ONLY through process env to GIT_ASKPASS and never interpolated
    into command arguments, remote URL, or printed logs.
    """
    if not token or len(token)<12:
        raise MountError('PITCHAPI_SOURCE_READ_TOKEN is not configured')
    if league is not None and (league not in ('eng-premier','esp-laliga','ger-bundesliga',
                                             'fra-ligue1','ita-seriea')):
        raise MountError('League does not exist in the verified original private warehouse')
    if not 1<=seasons<=7:
        raise MountError('Unsafe seasons window')
    dest=Path(output).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise MountError('Refusing to overwrite an existing private warehouse snapshot')
    dest.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='betpilot-askpass-') as temp:
        script=Path(temp)/'askpass.sh'
        script.write_text('#!/bin/sh\ncase "$1" in *sername*) printf "%s\\n" x-access-token;; *) printf "%s\\n" "$BETPILOT_SOURCE_READ_TOKEN";; esac\n')
        script.chmod(0o700)
        env={**os.environ,'GIT_ASKPASS':str(script),
             'GIT_TERMINAL_PROMPT':'0', 'BETPILOT_SOURCE_READ_TOKEN':token}
        def git(*args):
            try:
                p=runner(['git','-C',str(dest),*args],env=env,
                         capture_output=True,text=True,timeout=240,check=False)
            except (OSError,subprocess.TimeoutExpired) as e:
                raise MountError('Source repository transport or timeout error') from e
            if p.returncode:
                # NEVER print process stderr; transport errors may contain URL headers.
                raise MountError('Private PitchAPI source fetch not authorized or unavailable')
            return p.stdout.strip()
        git('init','-q')
        git('remote','add','origin',SOURCE)
        git('config','core.sparseCheckout','true')
        git('config','core.sparseCheckoutCone','false')
        # Fetch only tree metadata; scoped SQLite blobs are fetched on checkout.
        git('fetch','-q','--depth','1','--filter=blob:none','origin',BRANCH)
        entries=git('ls-tree','-r','--name-only','FETCH_HEAD','data/').splitlines()
        candidates=[p for p in entries if p.startswith('data/') and p.endswith('.sqlite')]
        chosen=['data/catalogue.sqlite'] if 'data/catalogue.sqlite' in candidates else []
        grouped={}
        for path in candidates:
            parts=Path(path).parts
            if len(parts)==3:
                grouped.setdefault(parts[1],[]).append(path)
        for slug,rows in grouped.items():
            if league is None or slug==league:
                chosen.extend(sorted(rows,reverse=True)[:seasons])
        if not any(p.endswith('.sqlite') and p!='data/catalogue.sqlite' for p in chosen):
            raise MountError('Selected league has no original archived seasonal SQLite files')
        # Non-cone sparse rules; checkout only selected archive paths.
        (dest/'.git/info/sparse-checkout').write_text(''.join('/'+p+'\n' for p in chosen))
        git('read-tree','-mu','FETCH_HEAD')
    return {'source_repository':'jscommessinho/BET-PILOT-Prematch-Analyzer',
            'source_branch':BRANCH,'mode':'READ_ONLY_EXTERNAL_IMPORT',
            'database_count':len(chosen),'database_paths':chosen,
            'seasons_per_league_max':seasons,
            'original_repository_modified':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--league',choices=['eng-premier','ita-seriea','fra-ligue1','esp-laliga','ger-bundesliga'])
    p.add_argument('--seasons',type=int,default=3)
    args=p.parse_args(argv)
    try:
        report=mount(args.output,token=os.environ.get('PITCHAPI_SOURCE_READ_TOKEN',''),
                     league=args.league,seasons=args.seasons)
    except MountError as e:
        print({'status':'SOURCE_MOUNT_BLOCKED','reason_code':type(e).__name__,
               'credential_exposed':False,'original_repository_modified':False})
        return 2
    # Never enumerate source contents or credentials in CI logs.
    print({'status':'ORIGINAL_SQLITE_MOUNTED_READ_ONLY',
           'count':report['database_count'],
           'original_repository_modified':False})
    return 0

if __name__=='__main__':
    raise SystemExit(main())
