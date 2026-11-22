"""Combinatorial 5m underlying-direction research. No option-premium or order execution."""
from __future__ import annotations
import argparse
import itertools
import json
import math
import time
import os
import requests
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

IST = 'Asia/Kolkata'
EXIT_VARIANTS = ['ST7', 'SR', 'EMA9', 'SMA20', 'ST7_OR_SR', 'ST7_OR_EMA9',
                 'ST7_OR_SR_OR_EMA9', 'ST7_OR_SMA20', 'TIME_ONLY', 'FIXED_5_15']


def normalize(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()
    d = data.copy()
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.columns = [str(x).strip().lower() for x in d.columns]
    if 'datetime' in d.columns:
        raw_idx = pd.to_datetime(d.pop('datetime'))
        d.index = raw_idx
    elif 'date' in d.columns:
        d.index = pd.to_datetime(d.pop('date'))
    else:
        d.index = pd.to_datetime(d.index)
    idx = pd.DatetimeIndex(d.index)
    d.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    # Yahoo returns BOTH Close and Adj Close when auto_adjust=False.
    # Never rename Adj Close to Close: it creates duplicate 'close' columns,
    # turning d.close into a DataFrame and crashing pandas indicator code.
    if 'adj close' in d.columns and 'close' not in d.columns:
        d = d.rename(columns={'adj close': 'close'})
    if 'adj close' in d.columns:
        d = d.drop(columns=['adj close'])
    if d.columns.duplicated().any():
        duplicates = list(d.columns[d.columns.duplicated()])
        raise ValueError(f'Duplicate OHLCV fields after normalization: {duplicates}')
    cols = ['open', 'high', 'low', 'close', 'volume']
    if not set(cols).issubset(d.columns):
        raise ValueError(f'Required OHLCV columns missing: {set(cols)-set(d.columns)}')
    d = d[cols].apply(pd.to_numeric, errors='coerce').dropna()
    d = d.loc[~d.index.duplicated(keep='last')].sort_index()
    d = d[(d.index.dayofweek < 5) & (d.index.strftime('%H:%M') >= '09:15') &
          (d.index.strftime('%H:%M') < '15:30')]
    d = d[(d.high >= d[['open', 'close', 'low']].max(axis=1)) &
          (d.low <= d[['open', 'close', 'high']].min(axis=1)) & (d.low > 0)]
    return d


def dhan_master() -> pd.DataFrame:
    """Resolve the CURRENT NSE equity security IDs from Dhan's official instrument master."""
    url = 'https://images.dhan.co/api-data/api-scrip-master.csv'
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    from io import BytesIO
    df = pd.read_csv(BytesIO(r.content), low_memory=False, dtype=str).fillna('')
    df.columns = df.columns.str.upper().str.strip()
    # Dhan compact-master columns, with alternates for an API format change.
    def field(*candidates):
        for c in candidates:
            if c in df.columns: return c
        raise ValueError(f'Dhan instrument-master missing {candidates}; columns={list(df.columns)[:30]}')
    exch = field('SEM_EXM_EXCH_ID','EXCH_ID')
    seg = field('SEM_SEGMENT','SEGMENT')
    sec = field('SEM_SMST_SECURITY_ID','SECURITY_ID','SEM_SECURITY_ID')
    sym = field('SEM_TRADING_SYMBOL','SM_SYMBOL_NAME','SYMBOL_NAME','DISPLAY_NAME','SEM_CUSTOM_SYMBOL')
    instrument = field('SEM_INSTRUMENT_NAME','INSTRUMENT')
    filtered = df[(df[exch].str.upper()=='NSE') & (df[seg].str.upper()=='E') &
                  (df[instrument].str.upper().str.contains('EQUITY',na=False))].copy()
    filtered['resolved_symbol'] = filtered[sym].str.upper().str.strip()
    filtered['resolved_sid'] = filtered[sec].str.strip()
    return filtered[filtered.resolved_sid.str.fullmatch(r'\d+')]


def dhan_lookup(symbol: str, master: pd.DataFrame) -> str:
    key=symbol.upper().strip()
    # Exact match only. Never guess a securityId, even for renamed/delisted companies.
    hit=master.loc[master.resolved_symbol.eq(key)]
    if len(hit)==0:
        raise ValueError(f'SYMBOL_NOT_FOUND_IN_CURRENT_DHAN_NSE_EQ_MASTER: {symbol}')
    ids=hit.resolved_sid.unique()
    if len(ids)!=1:
        raise ValueError(f'AMBIGUOUS_DHAN_ID: {symbol}: {list(ids)}')
    return str(ids[0])


def dhan_parse(payload:dict) -> pd.DataFrame:
    required=('timestamp','open','high','low','close','volume')
    if not isinstance(payload,dict): raise ValueError('Non-dict Dhan response')
    if any(k not in payload for k in required):
        raise ValueError('Dhan response missing candle fields: '+str(payload)[:450])
    lengths={len(payload[k]) for k in required}
    if len(lengths)>1: raise ValueError(f'Unequal Dhan candle-array lengths: {lengths}')
    if not lengths or next(iter(lengths))==0: return pd.DataFrame()
    ts=pd.to_datetime(payload['timestamp'],unit='s',utc=True).tz_convert(IST)
    df=pd.DataFrame({k:payload[k] for k in required if k!='timestamp'}, index=ts)
    df.index.name='datetime'
    return normalize(df)


def dhan_fetch(symbol:str, sec_id:str, days:int, root:Path) -> pd.DataFrame:
    token=os.environ.get('DHAN_ACCESS_TOKEN','').strip()
    client=os.environ.get('DHAN_CLIENT_ID','').strip()
    if not token: raise RuntimeError('DHAN_ACCESS_TOKEN is missing. Add GitHub Actions secret.')
    cache_dir=root/'data'/'dhan_cache'; cache_dir.mkdir(parents=True,exist_ok=True)
    # Exact 90-calendar-day maximum per request; 80 days for safe overlapping boundaries.
    # The 5 years stated in Dhan docs refers to ACTIVE NSE_EQ symbols only.
    end=pd.Timestamp.now(tz=IST).normalize()+pd.Timedelta(days=1)
    start=end-pd.Timedelta(days=days)
    chunks=[]; current=start
    sess=requests.Session()
    headers={'access-token':token,'Accept':'application/json','Content-Type':'application/json'}
    if client:headers['client-id']=client
    while current<end:
        nxt=min(current+pd.Timedelta(days=80),end)
        key=f'{symbol}_{sec_id}_{current:%Y%m%d}_{nxt:%Y%m%d}.csv'
        file=cache_dir/key
        chunk=pd.DataFrame()
        if file.exists():
            try:chunk=normalize(pd.read_csv(file))
            except Exception: file.unlink(missing_ok=True)
        if chunk.empty:
            req={'securityId':sec_id,'exchangeSegment':'NSE_EQ','instrument':'EQUITY',
                 'interval':'5','oi':False,
                 'fromDate':current.strftime('%Y-%m-%d 00:00:00'),
                 'toDate':nxt.strftime('%Y-%m-%d 00:00:00')}
            for attempt in range(5):
                try:
                    r=sess.post('https://api.dhan.co/v2/charts/intraday',json=req,headers=headers,timeout=60)
                    if r.status_code in (401,403):
                        raise RuntimeError(f'DHAN_AUTH_OR_PLAN_{r.status_code}: '+r.text[:300])
                    if r.status_code==429 or r.status_code>=500:
                        if attempt<4: time.sleep(min(30,2**attempt*2));continue
                    r.raise_for_status()
                    payload=r.json()
                    # Dhan errors sometimes arrive as JSON error payloads with HTTP 200.
                    if isinstance(payload,dict) and ('errorCode' in payload or 'errorType' in payload):
                        raise RuntimeError('DHAN_API_ERROR: '+str(payload)[:400])
                    chunk=dhan_parse(payload)
                    if not chunk.empty:chunk.reset_index().to_csv(file,index=False)
                    break
                except (requests.RequestException,ValueError) as exc:
                    if attempt==4:raise RuntimeError(f'DHAN_DOWNLOAD_FAILED {symbol} {current:%F}-{nxt:%F}: {exc}') from exc
                    time.sleep(min(30,2**attempt*2))
            time.sleep(0.15)
        if not chunk.empty: chunks.append(chunk)
        current=nxt
    if not chunks:return pd.DataFrame()
    return normalize(pd.concat(chunks))


def acquire(symbol: str, source: str, days: int, root: Path, security_id: str|None=None) -> pd.DataFrame:
    if source=='dhan':
        if not security_id:raise ValueError(f'No Dhan security ID for {symbol}')
        return dhan_fetch(symbol,security_id,days,root)
    if source!='csv':raise ValueError('Only Dhan and explicit CSV source supported; Yahoo removed.')
    file=root/'data'/'5m'/(symbol+'.csv')
    return normalize(pd.read_csv(file)) if file.exists() else pd.DataFrame()


def rma(x: pd.Series, length: int) -> pd.Series:
    return x.ewm(alpha=1/length, adjust=False, min_periods=length).mean()


def supertrend(d: pd.DataFrame, length: int, factor: float) -> tuple[np.ndarray, np.ndarray]:
    h, l, c = [d[k].to_numpy(dtype=float) for k in ('high', 'low', 'close')]
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.r_[np.nan,c[:-1]]), np.abs(l-np.r_[np.nan,c[:-1]])))
    atr = rma(pd.Series(tr, index=d.index).fillna(pd.Series(h-l,index=d.index)),length).to_numpy()
    hl = (h+l)/2
    upper = hl + factor*atr
    lower = hl - factor*atr
    out, direction = np.full(len(c), np.nan), np.zeros(len(c),dtype=int)
    prev_u, prev_l, prev_dir = np.nan, np.nan, 1
    for i in range(len(c)):
        if np.isnan(atr[i]):
            continue
        cur_u, cur_l = upper[i], lower[i]
        if np.isfinite(prev_u) and np.isfinite(prev_l):
            if cur_u > prev_u and c[i-1] <= prev_u:
                cur_u = prev_u
            if cur_l < prev_l and c[i-1] >= prev_l:
                cur_l = prev_l
            if prev_dir == 1:
                cur_dir = -1 if c[i] > prev_u else 1
            else:
                cur_dir = 1 if c[i] < prev_l else -1
        else:
            cur_dir = 1 if c[i] < hl[i] else -1
        out[i] = cur_u if cur_dir == 1 else cur_l
        direction[i] = cur_dir
        prev_u, prev_l, prev_dir = cur_u, cur_l, cur_dir
    # Pine supertrend convention: direction <0 = bullish, direction >0 = bearish
    return out, direction


def make_features(d: pd.DataFrame) -> pd.DataFrame:
    if len(d) < 210:
        return pd.DataFrame()
    x=d.copy()
    c,h,l,o,v=[x[k] for k in ['close','high','low','open','volume']]
    ds=x.index.strftime('%Y-%m-%d')
    group=x.groupby(ds, sort=True)
    daily=group.agg({'high':'max','low':'min','close':'last'})
    # Previous completed trading session, by position; never use current session's final values.
    prev=daily.shift(1)
    p=((prev.high+prev.low+prev.close)/3).rename('pivot')
    bc=((prev.high+prev.low)/2)
    top=pd.Series(np.maximum(bc,2*p-bc),index=prev.index,name='top')
    bot=pd.Series(np.minimum(bc,2*p-bc),index=prev.index,name='bot')
    all_levels=pd.DataFrame({'pivot':p, 'top':top,'bot':bot,
                             'pdh':prev.high,'pdl':prev.low,
                             'r1':2*p-prev.low,'s1':2*p-prev.high})
    for col in all_levels:
        x[col]=pd.Series(ds,index=x.index).map(all_levels[col]).to_numpy()
    x['e9']=c.ewm(span=9,adjust=False).mean()
    x['e21']=c.ewm(span=21,adjust=False).mean()
    x['e200']=c.ewm(span=200,adjust=False).mean()
    for n in (20,50,200): x[f'sma{n}']=c.rolling(n,min_periods=n).mean()
    delta=c.diff()
    gain=rma(delta.clip(lower=0),14)
    loss=rma((-delta).clip(lower=0),14)
    x['rsi']=100-100/(1+gain/loss.replace(0,np.nan))
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    plus=h.diff().where((h.diff()>-l.diff()) & (h.diff()>0),0)
    minus=(-l.diff()).where((-l.diff()>h.diff()) & (-l.diff()>0),0)
    atr=rma(tr,14)
    x['di_plus']=100*rma(plus,14)/atr.replace(0,np.nan)
    x['di_minus']=100*rma(minus,14)/atr.replace(0,np.nan)
    dx=100*(x.di_plus-x.di_minus).abs()/(x.di_plus+x.di_minus).replace(0,np.nan)
    x['adx']=rma(dx,14)
    st10,dir10=supertrend(x,10,3.0)
    st7,dir7=supertrend(x,7,2.0)
    x['st10'],x['st10dir'],x['st7'],x['st7dir']=st10,dir10,st7,dir7
    # Running session VWAP / day high-low BEFORE this bar (no future leakage).
    tp=(h+l+c)/3
    pv=tp*v
    den=v.groupby(ds).cumsum().replace(0,np.nan)
    x['vwap']=pv.groupby(ds).cumsum()/den
    x['prior_dayhigh']=group.high.cummax().groupby(ds).shift(1)
    x['prior_daylow']=group.low.cummin().groupby(ds).shift(1)
    # Shift across day boundary reset below; avoid yesterday's high at first bar.
    x.loc[~pd.Series(ds,index=x.index).eq(pd.Series(ds,index=x.index).shift()), ['prior_dayhigh','prior_daylow']]=np.nan
    x['volma20']=v.rolling(20,min_periods=20).mean().shift(1)
    x['day']=ds
    x['clock']=x.index.strftime('%H:%M')
    return x


def crossed_up(a,b): return (a>b) & (a.shift(1)<=b.shift(1))
def crossed_down(a,b): return (a<b) & (a.shift(1)>=b.shift(1))


def masks(x:pd.DataFrame):
    c,o,h,l=x.close,x.open,x.high,x.low
    b={}; s={}
    b['CPR']=c>x.top; s['CPR']=c<x.bot
    b['EMA']=(x.e9>x.e21)&(c>x.e9)&(c>x.e200)
    s['EMA']=(x.e9<x.e21)&(c<x.e9)&(c<x.e200)
    b['ST10']=(x.st10dir<0)&(c>x.st10)
    s['ST10']=(x.st10dir>0)&(c<x.st10)
    b['RSI']=x.rsi>55; s['RSI']=x.rsi<45
    b['ADX']=(x.adx>22)&(x.adx>x.adx.shift())
    s['ADX']=b['ADX']
    b['DI']=x.di_plus>x.di_minus; s['DI']=x.di_minus>x.di_plus
    b['VWAP']=c>x.vwap; s['VWAP']=c<x.vwap
    # Video opening confirmation strict: both R1 and PDH / both S1 and PDL.
    b['SR']=(c>x.r1)&(c>x.pdh)
    s['SR']=(c<x.s1)&(c<x.pdl)
    rtp_b=(x.sma20>x.sma50)&(x.sma20>x.sma20.shift(3))&(x.sma50>x.sma50.shift(3))
    rtp_s=(x.sma20<x.sma50)&(x.sma20<x.sma20.shift(3))&(x.sma50<x.sma50.shift(3))
    b['SMA_RTP']=rtp_b; s['SMA_RTP']=rtp_s
    b['SMA200']=c>x.sma200; s['SMA200']=c<x.sma200
    b['VOLUME']=x.volume>x.volma20; s['VOLUME']=b['VOLUME']
    up_cp=crossed_up(c,x.top); dn_cp=crossed_down(c,x.bot)
    up_ema=crossed_up(x.e9,x.e21); dn_ema=crossed_down(x.e9,x.e21)
    up_sr=crossed_up(c,x.r1)|crossed_up(c,x.pdh)
    dn_sr=crossed_down(c,x.s1)|crossed_down(c,x.pdl)
    early_b=up_cp|up_ema|up_sr
    early_s=dn_cp|dn_ema|dn_sr
    touch_b=((l<=x.e21)&(c>x.e21))|((l<=x.top)&(c>x.top))|((l<=x.pdh)&(c>x.pdh))|((l<=x.r1)&(c>x.r1))
    touch_s=((h>=x.e21)&(c<x.e21))|((h>=x.bot)&(c<x.bot))|((h>=x.pdl)&(c<x.pdl))|((h>=x.s1)&(c<x.s1))
    # 2 bar including current, avoid previous session contaminating early-day setup
    same=x.day==x.day.shift()
    ret_b=(touch_b|(touch_b.shift().eq(True)&same))&(c>o)&(c>c.shift())
    ret_s=(touch_s|(touch_s.shift().eq(True)&same))&(c<o)&(c<c.shift())
    b_strong=b['CPR']&b['EMA']&b['ST10']&b['RSI']&b['ADX']&b['DI']&b['VWAP']&( (c>x.r1)|(c>x.pdh))
    s_strong=s['CPR']&s['EMA']&s['ST10']&s['RSI']&s['ADX']&s['DI']&s['VWAP']&( (c<x.s1)|(c<x.pdl))
    # Original STRONG first-eligible episode only. Many 2/3 extras are redundant on this base.
    strong_b=b_strong&~b_strong.shift().eq(True)
    strong_s=s_strong&~s_strong.shift().eq(True)
    # EARLY and RETEST are intentionally looser base patterns for incremental filters.
    base={
      ('EARLY','CE'):early_b & (c>x.top),
      ('EARLY','PE'):early_s & (c<x.bot),
      ('RETEST','CE'):ret_b & (c>x.top),
      ('RETEST','PE'):ret_s & (c<x.bot),
      ('STRONG','CE'):strong_b,
      ('STRONG','PE'):strong_s,
      ('RTP','CE'):rtp_b & (c>x.prior_dayhigh) & (c.shift()<=x.prior_dayhigh.shift()),
      ('RTP','PE'):rtp_s & (c<x.prior_daylow) & (c.shift()>=x.prior_daylow.shift()),
    }
    # RTP condition is an experimental approximation, not a verified indicator formula.
    return {side:{k:v.fillna(False).to_numpy(bool) for k,v in src.items()}
            for side,src in [('CE',b),('PE',s)]}, {k:v.fillna(False).to_numpy(bool) for k,v in base.items()}


def simulate(x, signal:np.ndarray, side:str, exit_name:str, cfg:dict, collect=False):
    n=len(x)
    if n<4: return {'trades':0,'wins':0,'net_pct_sum':0.0,'gross_win':0.0,'gross_loss':0.0,'max_dd_pct':0.0,'avg_trade_pct':0.0,'profit_factor':0.0}, []
    a={key:x[key].to_numpy() for key in ['open','high','low','close','st7dir','e9','sma20','top','bot','r1','s1','pdh','pdl','e21']}
    clocks=x.clock.to_numpy(); days=x.day.to_numpy()
    d=1 if side=='CE' else -1
    stop=float(cfg.get('risk_stop_pct',5))/100
    take=float(cfg.get('reward_target_pct',15))/100
    bps=(float(cfg.get('commission_bps_per_side',2))+float(cfg.get('slippage_bps_per_side',2)))/10000
    max_per_day=int(cfg.get('max_entries_per_day',2))
    start=cfg.get('entry_start_ist','09:30'); last=cfg.get('last_entry_bar_start_ist','15:00')
    flat=cfg.get('square_off_ist','15:15')
    trade_log=[]; vals=[]; costs=[]; outcomes=[]; positions=0
    current_day=''; day_entries=0; entry_price=0; entry_bar=0; entry_time=''; held_level=np.nan
    pending=False; pending_bar=-1
    for i in range(n):
        if current_day!=days[i]:
            current_day=days[i];day_entries=0
            if positions: # Data gap: close at next open; show issue through coverage report.
                exit_px=a['open'][i]
                raw=d*(exit_px-entry_price)/entry_price
                net=raw-2*bps
                vals.append(net*100); outcomes.append((entry_time,str(x.index[i]),'OVERNIGHT_GAP',entry_price,exit_px,net*100))
                positions=0
        # Fill on NEXT candle open (signal evaluated at prior candle close).
        if pending and not positions and day_entries<max_per_day and days[pending_bar]==days[i]:
            entry_price=a['open'][i]*(1+d*bps)
            # Price already inclusive of entry slippage; explicit commissions deducted at final step.
            entry_bar=i; entry_time=str(x.index[i]);positions=1;day_entries+=1
            if d==1:
                held_level=max(z for z in [a['top'][pending_bar],a['e21'][pending_bar]] if np.isfinite(z))
            else:
                held_level=min(z for z in [a['bot'][pending_bar],a['e21'][pending_bar]] if np.isfinite(z))
        pending=False
        if positions:
            lo=a['low'][i];hi=a['high'][i]; close=a['close'][i]
            stop_px=entry_price*(1-d*stop)
            take_px=entry_price*(1+d*take)
            stop_hit=lo<=stop_px if d==1 else hi>=stop_px
            take_hit=hi>=take_px if d==1 else lo<=take_px
            why=None; exit_px=np.nan
            # Bar gaps through stop or target fill at opening if worse than limit/stop.
            if stop_hit:
                exit_px=min(stop_px,a['open'][i]) if d==1 else max(stop_px,a['open'][i])
                why='STOP_5'
            elif take_hit:
                exit_px=take_px
                why='TARGET_15'
            elif i>entry_bar:
                st=(a['st7dir'][i]>0 if d==1 else a['st7dir'][i]<0)
                sr=(close<held_level if d==1 else close>held_level)
                ema=(close<a['e9'][i] if d==1 else close>a['e9'][i])
                sma=(close<a['sma20'][i] if d==1 else close>a['sma20'][i])
                which={'ST7':st,'SR':sr,'EMA9':ema,'SMA20':sma,
                       'ST7_OR_SR':st or sr,'ST7_OR_EMA9':st or ema,
                       'ST7_OR_SR_OR_EMA9':st or sr or ema,'ST7_OR_SMA20':st or sma,
                       'TIME_ONLY':False,'FIXED_5_15':False}[exit_name]
                if which: why='SIGNAL_'+exit_name;exit_px=close
            # 15:10 starting bar closes 15:15; protect against holiday early closes.
            if why is None and (clocks[i]>='15:10' or i==n-1 or (i+1<n and days[i+1]!=days[i])):
                why='SESSION';exit_px=close
            if why is not None:
                # Add exit-side slippage and two commissions. Entry slippage was included in entry_price.
                exit_px=exit_px*(1-d*bps)
                raw=d*(exit_px-entry_price)/entry_price
                net=raw-(2*float(cfg.get('commission_bps_per_side',2))/10000)
                vals.append(net*100)
                outcomes.append((entry_time,str(x.index[i]),why,entry_price,exit_px,net*100))
                positions=0
        if not positions and not pending and i+1<n and days[i+1]==days[i] and start<=clocks[i]<=last and day_entries<max_per_day and signal[i]:
            pending=True;pending_bar=i
    arr=np.array(vals,dtype=float)
    plus=float(arr[arr>0].sum()) if len(arr) else 0.
    minus=float(-arr[arr<0].sum()) if len(arr) else 0.
    equity=np.cumprod(1+arr/100) if len(arr) else np.array([1.])
    highs=np.maximum.accumulate(np.r_[1.,equity]);dd=np.r_[1.,equity]/highs-1
    metrics={
        'trades':len(arr),'wins':int((arr>0).sum()),
        'win_pct':float(100*(arr>0).mean()) if len(arr) else 0,
        'compounded_pct':float(100*(equity[-1]-1)) if len(arr) else 0,
        'net_pct_sum':float(arr.sum()),'avg_trade_pct':float(arr.mean()) if len(arr) else 0,
        'median_trade_pct':float(np.median(arr)) if len(arr) else 0,
        'gross_win':plus,'gross_loss':minus,
        'profit_factor':plus/minus if minus>0 else (999. if plus>0 else 0.),
        'max_dd_pct':float(-100*dd.min())
    }
    logs=[dict(entry_time=et,exit_time=xt,exit_reason=why,entry_price=ep,exit_price=xp,net_return_pct=net,side=side,exit_rule=exit_name) for et,xt,why,ep,xp,net in outcomes] if collect else []
    return metrics,logs


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',default='config.json');ap.add_argument('--universe',default='universe.csv')
    ap.add_argument('--output',default='results');ap.add_argument('--max-stocks',type=int,default=0)
    args=ap.parse_args()
    cfg=json.loads(Path(args.config).read_text())
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent
    universe=pd.read_csv(args.universe).dropna(subset=['sector','symbol'])
    if args.max_stocks:universe=universe.head(args.max_stocks)
    combos=list(itertools.chain.from_iterable(itertools.combinations(cfg['filters'],n) for n in cfg['combination_sizes']))
    coverage=[];screen=[];detail=[];trades=[]
    master=dhan_master() if cfg['source']=='dhan' else None
    for j,r in enumerate(universe.itertuples(index=False),1):
        sector,symbol=r.sector,r.symbol
        print(f'[{j}/{len(universe)}] {symbol}',flush=True)
        try:
            sec_id=dhan_lookup(symbol,master) if master is not None else None
            d=acquire(symbol,cfg['source'],cfg['lookback_days'],root,sec_id)
            if d.empty or len(d)<220:
                coverage.append(dict(sector=sector,symbol=symbol,bars=len(d),first='',last='',trading_days=0,status='NO_DATA_OR_TOO_FEW_BARS'))
                continue
            x=make_features(d)
            coverage.append(dict(sector=sector,symbol=symbol,bars=len(x),first=str(x.index[0]),last=str(x.index[-1]),trading_days=x.day.nunique(),status='OK'))
            filt,base=masks(x)
            split_at=np.unique(x.day)[max(1,int(x.day.nunique()*0.7))-1]
            train=x.day.to_numpy()<=split_at
            test=~train
            for pattern in cfg['base_patterns']:
                for side in cfg['sides']:
                    candidates=[]
                    for comb in combos:
                        signal=base[(pattern,side)].copy()
                        for f in comb: signal &= filt[side][f]
                        if not signal.any():continue
                        tr_metrics,_=simulate(x,signal&train,side,'ST7',cfg)
                        te_metrics,_=simulate(x,signal&test,side,'ST7',cfg)
                        row=dict(sector=sector,symbol=symbol,pattern=pattern,side=side,
                                 filters='+'.join(comb),n_filters=len(comb),exit_rule='ST7',
                                 train_trades=tr_metrics['trades'],train_pf=tr_metrics['profit_factor'],
                                 train_expectancy=tr_metrics['avg_trade_pct'],train_win_pct=tr_metrics['win_pct'],
                                 test_trades=te_metrics['trades'],test_pf=te_metrics['profit_factor'],
                                 test_expectancy=te_metrics['avg_trade_pct'],test_win_pct=te_metrics['win_pct'],
                                 test_drawdown=te_metrics['max_dd_pct'],
                                 test_compounded_pct=te_metrics['compounded_pct'])
                        screen.append(row)
                        if tr_metrics['trades']>=cfg['minimum_train_trades']:
                            # Rank solely on training period, never choose using test-period returns.
                            rank=tr_metrics['avg_trade_pct']*min(tr_metrics['trades'],40)/40 - tr_metrics['max_dd_pct']*0.025
                            candidates.append((rank,comb,signal))
                    if cfg.get('exit_mode','shortlist')=='full':
                        selected=candidates
                    else:
                        selected=sorted(candidates,key=lambda z:z[0],reverse=True)[:cfg.get('max_results_per_pattern_per_symbol',8)]
                    for _,comb,signal in selected:
                        for ex in EXIT_VARIANTS:
                            mt,_=simulate(x,signal&train,side,ex,cfg)
                            mv,log=simulate(x,signal&test,side,ex,cfg,collect=True)
                            detail.append(dict(sector=sector,symbol=symbol,pattern=pattern,side=side,
                                               filters='+'.join(comb),exit_rule=ex,
                                               train_trades=mt['trades'],train_expectancy=mt['avg_trade_pct'],
                                               train_profit_factor=mt['profit_factor'],train_drawdown_pct=mt['max_dd_pct'],
                                               test_trades=mv['trades'],test_win_pct=mv['win_pct'],
                                               test_expectancy_pct=mv['avg_trade_pct'],test_profit_factor=mv['profit_factor'],
                                               test_return_pct=mv['compounded_pct'],test_drawdown_pct=mv['max_dd_pct']))
                            for t in log:t.update(sector=sector,symbol=symbol,pattern=pattern,filters='+'.join(comb));trades.append(t)
        except Exception as e:
            import traceback
            coverage.append(dict(sector=sector,symbol=symbol,bars=0,first='',last='',trading_days=0,status='ERROR: '+str(e)[:160]))
            print(symbol, 'FAILED',str(e),flush=True)
            traceback.print_exc()
    for name,rows in [('coverage',coverage),('combination_screen',screen),('shortlisted_exit_results',detail),('trades',trades)]:
        pd.DataFrame(rows).to_csv(output/(name+'.csv'),index=False)
    if detail:
        df=pd.DataFrame(detail)
        min_tr=int(cfg['minimum_train_trades']); min_te=int(cfg['minimum_test_trades'])
        eligible=df[(df.train_trades>=min_tr)&(df.test_trades>=min_te)].copy()
        eligible['eligible_positive_holdout']=(eligible.test_expectancy_pct>0)&(eligible.test_profit_factor>1)
        # No magic proof: ranking is only a held-out descriptive shortlist.
        eligible=eligible.sort_values(['eligible_positive_holdout','test_expectancy_pct','test_profit_factor','test_trades'],ascending=[False,False,False,False])
        eligible.to_csv(output/'summary.csv',index=False)
    else:
        (output/'summary.csv').write_text('No qualifying results. Review coverage.csv and data availability.\n')
    print(f'Finished. Results: {output.resolve()} | coverage={len(coverage)}, screen={len(screen)}, details={len(detail)}, trade rows={len(trades)}')

if __name__=='__main__':main()
