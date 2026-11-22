import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from backtest import normalize,make_features,masks,simulate,supertrend

def sample():
    rng=np.random.default_rng(51)
    dates=pd.bdate_range('2026-08-03',periods=12)
    idx=[]
    for day in dates:
        idx.extend(pd.date_range(f'{day.date()} 09:15',periods=75,freq='5min',tz='Asia/Kolkata'))
    n=len(idx)
    c=100+np.cumsum(rng.normal(0,.14,n))+np.linspace(0,5,n)
    o=c-rng.normal(0,.07,n)
    high=np.maximum(o,c)+.1
    low=np.minimum(o,c)-.1
    return pd.DataFrame({'open':o,'high':high,'low':low,'close':c,'volume':rng.integers(100,1000,n)},index=pd.DatetimeIndex(idx))

def test_features_nonleaking():
    d=sample();x=make_features(d)
    assert len(x)==len(d)
    assert x.iloc[0].top!=x.iloc[0].top # no future daily CPR
    assert np.isfinite(x.iloc[75].top)
    assert pd.isna(x.iloc[0].prior_dayhigh)
    assert pd.isna(x.iloc[75].prior_dayhigh)
    assert np.isfinite(x.iloc[76].prior_dayhigh)
    ff,base=masks(x)
    assert len(ff['CE']['RSI'])==len(d)
    assert len(base['STRONG','PE'])==len(d)

def test_no_signal_has_no_trades():
    x=make_features(sample());signal=np.zeros(len(x),bool)
    m,t=simulate(x,signal,'CE','ST7',{'risk_stop_pct':5,'reward_target_pct':15})
    assert m['trades']==0
    assert not t

def test_forced_square_off():
    x=make_features(sample());signal=np.zeros(len(x),bool)
    ix=np.where(x.clock.to_numpy()=='09:30')[0]
    signal[ix]=True
    m,t=simulate(x,signal,'CE','TIME_ONLY',{'risk_stop_pct':90,'reward_target_pct':90,'max_entries_per_day':2},True)
    assert m['trades']==len(ix)
    assert all(z['exit_reason']=='SESSION' for z in t)

def test_timezone_conversion():
    d=sample().reset_index(names='Datetime')
    normalized=normalize(d)
    assert str(normalized.index.tz)=='Asia/Kolkata'
