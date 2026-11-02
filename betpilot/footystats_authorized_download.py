#!/usr/bin/env python3
"""Authorized FootyStats Premium Match CSV refresh for a known existing league season.

No browser automation or login bypass. Credentials in process environment; only
HTTPS URLs hosted by FootyStats are accepted. Redirects are deliberately blocked
so cookies cannot be forwarded to another host. Fail closed on schema/regressions.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

REQUIRED = {'timestamp', 'status', 'home_team_name', 'away_team_name',
            'home_team_goal_count', 'away_team_goal_count'}
MAX_BYTES = 5_000_000

class DownloadError(ValueError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DownloadError('Redirect blocked to prevent sharing session credentials')


def safe_footystats_url(url):
    if not isinstance(url, str) or not url.strip():
        raise DownloadError('FOOTYSTATS_PREMIUM_CSV_URL is not configured')
    try:
        u = urlsplit(url.strip())
        hostname = (u.hostname or '').lower()
        if (u.scheme != 'https' or not hostname or
            not (hostname == 'footystats.org' or hostname.endswith('.footystats.org')) or
            u.username is not None or u.password is not None or
            u.port not in (None, 443) or u.fragment or any(x in url for x in ('\r', '\n'))):
            raise DownloadError('Only HTTPS FootyStats download URLs are allowed')
    except ValueError as exc:
        raise DownloadError('Invalid authorized FootyStats URL') from exc
    return url.strip()


def download(url, cookie='', opener=None):
    url=safe_footystats_url(url)
    hdr={'Accept':'text/csv,application/csv,application/octet-stream;q=0.9,*/*;q=0.1',
         'User-Agent':'BETPILOT-PREMATCH-authorized-premium-csv/1.0'}
    if cookie:
        cookie=cookie.strip()
        if '\r' in cookie or '\n' in cookie:
            raise DownloadError('Invalid cookie header')
        hdr['Cookie']=cookie
    opener=opener or build_opener(NoRedirect())
    try:
        with opener.open(Request(url,headers=hdr,method='GET'),timeout=25) as response:
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:
                raise DownloadError('CSV size exceeds 5 MB limit')
            if response.status!=200:
                raise DownloadError('Provider HTTP status is not 200')
    except HTTPError as exc:
        raise DownloadError('Provider HTTP error '+str(exc.code)) from None
    except (TimeoutError, URLError) as exc:
        raise DownloadError('Provider request unavailable ('+type(exc).__name__+')') from None
    if not raw or raw[:200].lstrip().lower().startswith((b'<!doctype',b'<html',b'<script')):
        raise DownloadError('Provider returned HTML/login page rather than CSV')
    return raw


def rows_from_bytes(content):
    try:
        text=content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise DownloadError('CSV is not valid UTF-8') from exc
    reader=csv.DictReader(io.StringIO(text,newline=''))
    names=set(reader.fieldnames or [])
    if not REQUIRED.issubset(names):
        raise DownloadError('Downloaded file is not a Premium Match Stats CSV with the expected headers')
    rows=list(reader)
    if len(rows)!=380:
        raise DownloadError('Serie A 2026/27 must contain exactly 380 scheduled fixtures')
    if any(None in row for row in rows):
        raise DownloadError('Malformed CSV row')
    return rows


def validate_history_refresh(original, replacement, now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise DownloadError('Current time must be timezone-aware')
    before=rows_from_bytes(original)
    after=rows_from_bytes(replacement)
    def get_mapping(rows):
        found={};teams=set();complete=0
        for r in rows:
            home=(r['home_team_name'] or '').strip()
            away=(r['away_team_name'] or '').strip()
            if not home or not away or home==away:
                raise DownloadError('Missing or identical team labels')
            ident=(home,away)
            if ident in found:
                raise DownloadError('Duplicate home/away pairing in this season')
            status=(r['status'] or '').strip().lower()
            if status not in ('complete','incomplete'):
                raise DownloadError('Unexpected fixture status')
            try:
                stamp=int(r['timestamp'])
                kickoff=datetime.fromtimestamp(stamp,timezone.utc)
            except (ValueError,OverflowError,TypeError) as exc:
                raise DownloadError('Invalid fixture kickoff timestamp') from exc
            if status=='complete':
                if kickoff>now:
                    raise DownloadError('Future fixture incorrectly marked complete')
                try:
                    hg=int(r['home_team_goal_count']); ag=int(r['away_team_goal_count'])
                except (ValueError,TypeError) as exc:
                    raise DownloadError('Complete fixture has missing/invalid score') from exc
                if not 0<=hg<=30 or not 0<=ag<=30:
                    raise DownloadError('Invalid score range')
                complete+=1
                score=(hg,ag)
            else:
                score=None
            found[ident]=(status,score)
            teams.update((home,away))
        if len(teams)!=20:
            raise DownloadError('Unexpected number of 2026/27 Serie A teams')
        return found,complete
    orig,before_count=get_mapping(before)
    new,after_count=get_mapping(after)
    if set(orig)!=set(new):
        raise DownloadError('Downloaded CSV changes the list of home/away fixtures')
    if after_count<before_count:
        raise DownloadError('Downloaded CSV loses previously completed fixtures')
    for match,(oldstatus,oldscore) in orig.items():
        status,score=new[match]
        if oldstatus=='complete' and (status!='complete' or score!=oldscore):
            raise DownloadError('Previously completed result changed; manual review required')
    return {'status':'VERIFIED_PREMIUM_CSV_SNAPSHOT', 'old_completed':before_count,
            'new_completed':after_count, 'new_results':after_count-before_count,
            'file_sha256':hashlib.sha256(replacement).hexdigest(),
            'changed':original!=replacement,
            'source':'FootyStats Premium owner-authorized session download',
            'point_in_time_verified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Strictly validated authorized Premium CSV refresh')
    parser.add_argument('--existing',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        current=args.existing.read_bytes()
        updated=download(os.environ.get('FOOTYSTATS_PREMIUM_CSV_URL',''),
                         os.environ.get('FOOTYSTATS_PREMIUM_COOKIE',''))
        audit=validate_history_refresh(current,updated)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.audit.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(updated)
        args.audit.write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
        print('AUTHORIZED_PREMIUM_REFRESH_OK:',audit['new_completed'],'completed;',
              audit['new_results'],'new results; changed =',audit['changed'])
        return 0
    except (DownloadError,OSError) as exc:
        # Never reveal URL, cookies, response body or auth header in logs.
        print('AUTHORIZED_PREMIUM_REFRESH_ABSTAIN:',exc,file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
