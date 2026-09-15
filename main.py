from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx, time, math
from typing import Optional

app=FastAPI(title="Option Strategy Lab API",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

BASE="https://webgw.tse.ir/InstrumentProvider/api/v1"
HEAD={"User-Agent":"Mozilla/5.0 (Android) AppleWebKit/537.36 Chrome/120 Safari/537.36","Accept":"application/json"}
cache={}
TTL=5

def num(v):
    if v is None:return None
    if isinstance(v,(int,float)):return float(v)
    try:return float(str(v).replace(",","").replace("٬",""))
    except:return None

def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and k in d and d[k] not in ("",None):return d[k]
    low={str(k).lower():v for k,v in d.items()} if isinstance(d,dict) else {}
    for k in keys:
        if str(k).lower() in low and low[str(k).lower()] not in ("",None):return low[str(k).lower()]
    return None

def rows(obj):
    if isinstance(obj,list): return obj
    if isinstance(obj,dict):
        for k,v in obj.items():
            if isinstance(v,list): return v
    return []

async def get_json(path):
    now=time.time()
    if path in cache and now-cache[path][0]<TTL:return cache[path][1]
    async with httpx.AsyncClient(timeout=15,headers=HEAD) as c:
        r=await c.get(BASE+path)
        r.raise_for_status()
        j=r.json()
    cache[path]=(now,j)
    return j

def option_row(r):
    # MarketWatchTradeOption: buy* = call leg, sell* = put leg.
    return {
      "underlying":first(r,"lVal18AFC","symbol","namad","underlyingSymbol","baseSymbol"),
      "underlying_name":first(r,"companyNamePersian","lVal30","baseName"),
      "strike":num(first(r,"buyQeymateEmal","qeymateEmal","strike","strikePrice")),
      "contract_size":num(first(r,"buyAndazeyeQarardad","andazeyeQarardad","contractSize")),
      "expiry":first(r,"buyTarixSarresid","tarixSarresid","expiry","expiryDate"),
      "dte":num(first(r,"buyBaqimandeTaSarresId","baghimandetasarresid","dte","daysToExpiry")),
      "call":{
        "symbol":first(r,"buyLVal18AFC","buySymbol","buyNamad","callSymbol"),
        "last":num(first(r,"buyPDrCotVal","buyLastPrice","buyLast","callLast")),
        "close":num(first(r,"buyPClosing","buyClosingPrice","callClose")),
        "best_buy":num(first(r,"buyPrice","buyBestBuy","buyPMeDem")),
        "best_sell":num(first(r,"buyPrice2","buyBestSell","buyPMeOf")),
        "volume":num(first(r,"buyQTotTran5J","buyVolume","callVolume")),
        "value":num(first(r,"buyQTotCap","buyTradeValue","callValue")),
        "ins_code":first(r,"buyInsCode","callInsCode")
      },
      "put":{
        "symbol":first(r,"sellLVal18AFC","sellSymbol","sellNamad","putSymbol"),
        "last":num(first(r,"sellPDrCotVal","sellLastPrice","sellLast","putLast")),
        "close":num(first(r,"sellPClosing","sellClosingPrice","putClose")),
        "best_buy":num(first(r,"sellPrice","sellBestBuy","sellPMeDem")),
        "best_sell":num(first(r,"sellPrice2","sellBestSell","sellPMeOf")),
        "volume":num(first(r,"sellQTotTran5J","sellVolume","putVolume")),
        "value":num(first(r,"sellQTotCap","sellTradeValue","putValue")),
        "ins_code":first(r,"sellInsCode","putInsCode")
      }
    }

@app.get("/health")
async def health():return {"ok":True,"version":"1.0.0","data_source":"webgw.tse.ir"}

@app.get("/api/v1/options")
async def options(underlying:str=""):
    try:j=await get_json("/MarketWatch/MarketWatchTradeOption/fa")
    except Exception as e:raise HTTPException(502,f"market source unavailable: {e}")
    q=underlying.strip().lower()
    items=[]
    for r in rows(j):
        if not isinstance(r,dict):continue
        x=option_row(r)
        hay=" ".join(str(v or "") for v in [x["underlying"],x["underlying_name"],x["call"]["symbol"],x["put"]["symbol"]]).lower()
        if not q or q in hay:items.append(x)
    return {"source":"webgw.tse.ir","updated_at":time.time(),"count":len(items),"items":items}

@app.get("/api/v1/options/separate")
async def separate(underlying:str=""):
    try:j=await get_json("/MarketWatch/MarketWatchOption/fa")
    except Exception as e:raise HTTPException(502,f"market source unavailable: {e}")
    q=underlying.strip().lower(); out=[]
    for r in rows(j):
        if not isinstance(r,dict):continue
        symbol=first(r,"lVal18AFC","symbol","namad")
        name=first(r,"companyNamePersian","lVal30","name")
        hay=f"{symbol or ''} {name or ''}".lower()
        if q and q not in hay:continue
        out.append({
          "symbol":symbol,"name":name,"strike":num(first(r,"qeymateEmal","strike","strikePrice")),
          "base_price":num(first(r,"qeymateMabna","basePrice")),
          "expiry":first(r,"tarixSarresid","expiry","expiryDate"),
          "dte":num(first(r,"baghimandetasarresid","dte","daysToExpiry")),
          "last":num(first(r,"lastPrice","pDrCotVal","last")),
          "close":num(first(r,"closingPrice","pClosing","close")),
          "buy":num(first(r,"buyPrice","bestBuy")),
          "sell":num(first(r,"sellPrice","bestSell")),
          "volume":num(first(r,"tradeVolume","qTotTran5J","volume")),
          "value":num(first(r,"tradeValue","qTotCap","value")),
          "instrument_id":first(r,"instrumentId","insCode")
        })
    return {"source":"webgw.tse.ir","updated_at":time.time(),"count":len(out),"items":out}

@app.get("/api/v1/strategy/metrics")
async def metrics(spot:float,strike:float,premium:float,side:str="call",position:str="short",rate:float=0):
    # Black-Scholes helper for European option analytics; rate is decimal, T in years.
    T=1/365
    if spot<=0 or strike<=0 or premium<0: raise HTTPException(400,"invalid inputs")
    intrinsic=max(spot-strike,0) if side=="call" else max(strike-spot,0)
    return {"intrinsic":intrinsic,"time_value":max(premium-intrinsic,0),"break_even":strike+premium if side=="call" and position=="long" else (strike-premium if side=="put" and position=="long" else None)}

@app.get("/api/v1/scanner")
async def scanner(underlying:str,spot:float,budget:float,view:str="neutral_bullish"):
    # Returns a ranked, transparent shortlist based on fetched chain; not investment advice.
    try:j=await options(underlying)
    except Exception as e:raise HTTPException(502,str(e))
    candidates=[]
    for x in j["items"]:
        c=x["call"]; p=x["put"]; cs=x.get("contract_size") or 1000
        for leg,typ in [(c,"call"),(p,"put")]:
            prem=leg.get("last")
            if not prem or prem<=0:continue
            cost=prem*cs
            if cost>budget:continue
            k=x.get("strike")
            if not k:continue
            distance=abs(k-spot)/spot
            score=max(0,100-100*distance)
            if view=="neutral_bullish" and typ=="call":score+=10
            if view=="neutral" and distance<.1:score+=8
            candidates.append({"type":typ,"symbol":leg.get("symbol"),"strike":k,"premium":prem,"contract_size":cs,"cost":cost,"score":round(score,2),"expiry":x.get("expiry"),"dte":x.get("dte")})
    candidates.sort(key=lambda z:z["score"],reverse=True)
    return {"underlying":underlying,"budget":budget,"view":view,"count":len(candidates),"items":candidates[:20],"disclaimer":"Ranking is a screening heuristic, not a recommendation."}
