"""Read-only broker-backed portfolio risk telemetry.

No order/close/stop authority. Reads OANDA open trades and calculates capital
still at risk to each trade's current broker stop from actual entry and units.
Unquantifiable trades are reported explicitly rather than treated as zero risk.
"""
from __future__ import annotations
import json, os, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional
VERSION = "portfolio_live_risk_v1_2026_10_15"
_CACHE_TTL_SECONDS = 20.0
_cache: Dict[str, Any] = {}; _cache_at = 0.0; _lock = threading.Lock()
def _now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def _f(v):
    try: return None if v is None or v == "" else float(v)
    except Exception: return None
def _api_base(mode):
    if mode == "metals":
        explicit=os.getenv("METALS_XAU_LIVE_OANDA_API_BASE","").strip(); env=os.getenv("METALS_XAU_LIVE_OANDA_ENV","live").strip().lower()
    else:
        explicit=os.getenv("OANDA_API_BASE","").strip(); env=os.getenv("OANDA_ENV","live").strip().lower()
    if explicit: return explicit.rstrip("/")
    return "https://api-fxpractice.oanda.com" if env in {"practice","demo","sandbox"} else "https://api-fxtrade.oanda.com"
def _credentials(mode):
    if mode == "metals": return os.getenv("METALS_XAU_LIVE_OANDA_ACCOUNT_ID","").strip(), os.getenv("METALS_XAU_LIVE_OANDA_API_TOKEN","").strip()
    return os.getenv("OANDA_ACCOUNT_ID","").strip(), os.getenv("OANDA_API_TOKEN","").strip()
def _get_json(base,account,token,path,params=None):
    q=urllib.parse.urlencode(params or {}); url=f"{base}/v3/accounts/{urllib.parse.quote(account,safe='')}/{path.lstrip('/')}"+(('?'+q) if q else '')
    req=urllib.request.Request(url,headers={"Authorization":f"Bearer {token}","Accept":"application/json"},method="GET")
    with urllib.request.urlopen(req,timeout=6.0) as r: return json.loads(r.read().decode("utf-8"))
def _allowed(mode,instrument):
    s=str(instrument or '').upper()
    if mode=='indices':
        cfg={x.strip().upper() for x in os.getenv('BROKER_ALLOWED_INSTRUMENTS','').split(',') if x.strip()}
        return s in cfg if cfg else s in {'NAS100_USD','SPX500_USD','US500_USD'}
    if mode=='metals': return s=='XAU_USD'
    cfg=os.getenv('BCO_OANDA_INSTRUMENT','').strip().upper(); return s==cfg if cfg else ('BCO' in s or 'BRENT' in s)
def _mid(base,account,token,instrument):
    try:
        p=(_get_json(base,account,token,'pricing',{'instruments':instrument}).get('prices') or [])[0]
        b=_f(p.get('closeoutBid')); a=_f(p.get('closeoutAsk'))
        if b and a: return (b+a)/2.0
        bids=p.get('bids') or []; asks=p.get('asks') or []; b=_f((bids[0] if bids else {}).get('price')); a=_f((asks[0] if asks else {}).get('price'))
        return (b+a)/2.0 if b and a else None
    except Exception: return None
def _factor(base,account,token,quote,home,memo):
    key=(quote,home)
    if key in memo: return memo[key]
    if quote==home: memo[key]=1.0; return 1.0
    d=_mid(base,account,token,f'{quote}_{home}')
    if d and d>0: memo[key]=d; return d
    inv=_mid(base,account,token,f'{home}_{quote}')
    memo[key]=(1.0/inv) if inv and inv>0 else None; return memo[key]
def _stop(t):
    for k in ('stopLossOrder','guaranteedStopLossOrder','trailingStopLossOrder'):
        o=t.get(k)
        if isinstance(o,dict):
            p=_f(o.get('price') or o.get('trailingStopValue'))
            if p and p>0: return p
    return None
def _calculate(mode):
    account,token=_credentials(mode)
    if not account or not token: return {'ok':False,'version':VERSION,'scope':mode,'calculated_at_utc':_now(),'error':'OANDA credentials not configured for live risk telemetry','capital_at_risk_gbp':None}
    base=_api_base(mode); home=str(((_get_json(base,account,token,'summary').get('account') or {}).get('currency') or 'GBP')).upper()
    trades=[t for t in (_get_json(base,account,token,'openTrades').get('trades') or []) if _allowed(mode,t.get('instrument'))]
    memo={}; total=0.0; floor=0.0; quant=0; no_stop=0; no_conv=0; by={}
    for t in trades:
        ins=str(t.get('instrument') or 'UNKNOWN').upper(); entry=_f(t.get('price')); units=_f(t.get('currentUnits')); stop=_stop(t)
        row=by.setdefault(ins,{'open_trade_count':0,'quantified_trade_count':0,'without_stop_trade_count':0,'conversion_missing_trade_count':0,'capital_at_risk_gbp':0.0,'locked_profit_floor_gbp':0.0}); row['open_trade_count']+=1
        if entry is None or units is None or abs(units)<=0 or stop is None: no_stop+=1; row['without_stop_trade_count']+=1; continue
        quote=ins.rsplit('_',1)[-1] if '_' in ins else home; fac=_factor(base,account,token,quote,home,memo)
        if fac is None: no_conv+=1; row['conversion_missing_trade_count']+=1; continue
        if units>0: loss=max(0.0,entry-stop); gain=max(0.0,stop-entry)
        else: loss=max(0.0,stop-entry); gain=max(0.0,entry-stop)
        r=loss*abs(units)*fac; f=gain*abs(units)*fac; total+=r; floor+=f; quant+=1; row['quantified_trade_count']+=1; row['capital_at_risk_gbp']+=r; row['locked_profit_floor_gbp']+=f
    for row in by.values(): row['capital_at_risk_gbp']=round(row['capital_at_risk_gbp'],2); row['locked_profit_floor_gbp']=round(row['locked_profit_floor_gbp'],2)
    return {'ok':True,'version':VERSION,'scope':mode,'calculated_at_utc':_now(),'account_currency':home,'open_trade_count':len(trades),'quantified_trade_count':quant,'without_stop_trade_count':no_stop,'conversion_missing_trade_count':no_conv,'unquantified_trade_count':no_stop+no_conv,'capital_at_risk_gbp':round(total,2),'locked_profit_floor_gbp':round(floor,2),'by_instrument':by,'method':'broker_entry_to_current_stop_x_actual_units_converted_to_account_currency','execution_authority':False}
def _read(mode):
    global _cache,_cache_at
    now=time.monotonic()
    with _lock:
        if _cache.get('scope')==mode and now-_cache_at<_CACHE_TTL_SECONDS: out=dict(_cache); out['cached']=True; return out
    try: out=_calculate(mode)
    except Exception as exc:
        with _lock:
            if _cache.get('scope')==mode and _cache.get('ok'): stale=dict(_cache); stale.update({'cached':True,'stale':True,'error':f'{type(exc).__name__}: {exc}'}); return stale
        out={'ok':False,'version':VERSION,'scope':mode,'calculated_at_utc':_now(),'error':f'{type(exc).__name__}: {exc}','capital_at_risk_gbp':None}
    if out.get('ok'):
        with _lock: _cache=dict(out); _cache_at=now
    return out
def install(app:Any,mode:str):
    mode=str(mode).strip().lower()
    if mode not in {'indices','metals','bco'}: raise ValueError(mode)
    def endpoint(): return _read(mode)
    try: app.add_api_route('/api/portfolio-risk',endpoint,methods=['GET'],include_in_schema=False)
    except Exception: pass
    return {'installed':True,'version':VERSION,'mode':mode,'execution_authority':False}
