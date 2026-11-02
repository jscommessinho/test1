"""Independent, quota-constrained API-Football client. No credential logging."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

API_BASE='https://v3.football.api-sports.io'
class BudgetError(ValueError): pass


def utc(dt):
    if dt.tzinfo is None or dt.utcoffset() is None:raise BudgetError('timezone required')
    return dt.astimezone(timezone.utc)


def timestamp(s):
    try:return utc(datetime.fromisoformat(str(s).replace('Z','+00:00')))
    except (TypeError,ValueError,AttributeError) as e:raise BudgetError('malformed provider timestamp') from e


def read_cache(path, now, max_age_seconds):
    p=Path(path)
    if not p.is_file():return None
    try:
        item=json.loads(p.read_text('utf-8'))
        captured=timestamp(item['capture_utc'])
        if captured>now or (now-captured).total_seconds()>max_age_seconds:return None
        data=item['data']
        if not isinstance(data,dict) or not isinstance(data.get('response'),list):return None
        return data
    except (ValueError, KeyError, OSError, TypeError):return None


def save_cache(path, now, data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix('.part')
    temp.write_text(json.dumps({'capture_utc':utc(now).isoformat(),'data':data},ensure_ascii=False),encoding='utf8')
    temp.replace(p)


class QuotaClient:
    """Fail-closed per-job quota guard; serialize jobs at GH Actions level.

    Other tools using same API key can consume credits concurrently. Recheck
    daily remaining via response header and /status for each job, stop early.
    """
    def __init__(self,key,*,reserve=20,max_calls=30,timeout=20,opener=None):
        if not key or not key.strip():raise BudgetError('APISPORTS_KEY not set')
        if not 10<=reserve<=90 or not 1<=max_calls<=80:raise BudgetError('unsafe quota configuration')
        self._key=key.strip();self.reserve=reserve;self.max_calls=max_calls
        self.timeout=timeout;self._open=opener or urlopen
        self.used=0;self.remaining=None;self.limit=None

    def _request(self,path,params):
        req=Request(API_BASE+path+('?' + urlencode(params) if params else ''),headers={
            'x-apisports-key':self._key,'Accept':'application/json',
            'User-Agent':'BETPILOT-PREMATCH-research/1.0'})
        try:
            with self._open(req,timeout=self.timeout) as response:
                raw=response.read(15_000_001)
                if len(raw)>15_000_000:raise BudgetError('provider response too large')
                # 'getheader' optional on mocks; do not leak provider headers to output.
                hdr=response.headers
                hdrremain=hdr.get('x-ratelimit-requests-remaining') if hdr else None
                obj=json.loads(raw)
                if not isinstance(obj,dict) or obj.get('errors'):
                    raise BudgetError('provider error, quota or parameters rejected')
                return obj,hdrremain
        except HTTPError as e:raise BudgetError('provider HTTP '+str(e.code)) from None
        except (URLError,TimeoutError,UnicodeError,ValueError) as e:
            if isinstance(e,BudgetError):raise
            raise BudgetError('provider connection/data error') from None

    def preflight(self):
        obj,_=self._request('/status',{})  # API-SPORTS docs: not counted.
        try:
            requests=obj['response']['requests']
            limit=int(requests['limit_day']);used=int(requests['current'])
            if limit<=0 or used<0 or used>limit:raise ValueError()
        except (KeyError,ValueError,TypeError) as e:
            raise BudgetError('cannot verify API daily quota: refusing billable request') from e
        self.limit=limit;self.remaining=limit-used
        if self.remaining<=self.reserve:
            raise BudgetError('daily reserved API quota reached; abstaining')
        return {'quota_limit':limit,'quota_remaining_at_start':self.remaining,'reserve':self.reserve}

    def get(self,path,**params):
        if path not in ('/fixtures','/odds'):raise BudgetError('endpoint not allowlisted')
        if self.remaining is None:raise BudgetError('/status quota check required')
        if self.used>=self.max_calls or self.remaining<=self.reserve:
            raise BudgetError('daily quota reserve or per-run cap reached')
        # No automatic retry (avoids accidental duplicate consumption).
        self.used+=1
        try:obj,header_remaining=self._request(path,params)
        finally:self.remaining-=1  # pessimistic even on network failure
        if header_remaining is not None:
            try:
                reported=int(header_remaining)
                if reported<0:raise ValueError()
                self.remaining=min(self.remaining,reported)
            except ValueError:raise BudgetError('invalid provider daily remaining header')
        return obj


