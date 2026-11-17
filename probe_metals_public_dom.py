"""Read-only public dashboard markup probe. No broker credentials or writes."""
from html.parser import HTMLParser
import re
from urllib.request import Request, urlopen

BASE="https://metals-production-bb81.up.railway.app"
class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags=[]
        self.match=[]
    def handle_starttag(self, tag, attrs):
        d=dict(attrs)
        self.tags.append((tag,d.get("class",""),d.get("id","")))
    def handle_startendtag(self, tag, attrs): pass
    def handle_endtag(self,tag):
        for i in range(len(self.tags)-1,-1,-1):
            if self.tags[i][0]==tag:
                del self.tags[i:]
                break
    def handle_data(self,data):
        txt=re.sub(r"\\s+"," ",data).strip()
        if not txt:return
        if (re.fullmatch(r"NAV|Broker P&L|High-Water|Giveback|Open Trades",txt,re.I) or
            (txt.startswith("High-Water · LIVE") or txt.startswith("Giveback · LIVE"))):
            self.match.append((txt,self.tags[-5:]))

for path in ["/", "/dashboard", "/dashboard/top"]:
    try:
        req=Request(BASE+path,headers={"User-Agent":"MetalsMarkupProbe/1.0","Accept":"text/html"},method="GET")
        with urlopen(req,timeout=25) as r:
            html=r.read(1000000).decode("utf-8","replace")
            print(f"PATH {path} code={r.status} content_type={r.headers.get('content-type')} chars={len(html)}")
        print("script_count",html.count("pep-combined-live-metals-pnl-v2"),"body_close",("</body>" in html),"dashboard_top_fragment",("dashboard/top" in html))
        print("text_Broker_PNL",html.count("Broker P&L"),"giveback_occurrences",html.count("Giveback"))
        p=P();p.feed(html)
        print("KPI ancestry:",p.match[:20])
        for label in ("Broker P&L","High-Water","Giveback"):
            idx=html.find(">"+label)
            if idx>=0:
                print("HTML_EXCERPT",label,repr(html[max(0,idx-100):min(len(html),idx+520)]))
    except Exception as e:print("PATH_ERROR",path,str(e))

print("DEEP_DASHBOARD_PROBE")
for path in ("/dashboard", "/dashboard/top"):
    try:
        with urlopen(Request(BASE+path,headers={"Accept":"text/html,application/json"},method="GET"),timeout=30) as rr:
            html=rr.read(1000000).decode("utf-8","replace")
        if path.endswith("/top"):
            import json
            d=json.loads(html)
            print("top_payload_type",type(d).__name__,"keys",list(d.keys())[:40])
            for k,v in d.items():
                if isinstance(v,dict):print("top_sub_keys",k,list(v.keys())[:45])
            print("top_payload_prefix",repr(html[:1200]))
        else:
            for pat in ('id="topTiles"', "dashboard/top", "Basket High-Water", "Giveback"):
                ix=html.find(pat)
                if ix>=0:print("PROBE",pat,repr(html[max(0,ix-260):ix+1300]))
            for m in re.finditer(r"[^\\n]{0,200}topTiles[^\\n]{0,200}",html):
                print("TOP_JS",repr(m.group()[:550]))
    except Exception as e: print("DEEP_ERR",path,type(e).__name__,str(e))

try:
  with urlopen(Request(BASE+"/dashboard",headers={"Accept":"text/html"},method="GET"),timeout=25) as rr:
    html=rr.read().decode("utf-8","replace")
  ix=html.find("async function loadTop")
  print("LOAD_TOP_JS",repr(html[ix:ix+5300]))
  with urlopen(Request(BASE+"/api/live-metals-open-pnl",headers={"Accept":"application/json"},method="GET"),timeout=25) as rr:
    import json
    obj=json.loads(rr.read().decode("utf-8"))
    print("COMBINED_PROOF", {k:obj.get(k) for k in ("ok","scope","open_count","open_pnl_gbp","high_water_status","high_water_gbp","giveback_gbp","giveback_pct","reason")})
except Exception as e: print("DEEP_JS_ERR",type(e).__name__,str(e))
