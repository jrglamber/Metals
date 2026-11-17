"""Browser-less DOM regression for the production Metals combined-P&L widget.

Exercises the REAL embedded JavaScript against a minimal DOM. No network,
broker commands, risk settings, or runtime side effects.
"""
import base64
import shutil
import subprocess
import unittest

from metals_live_open_pnl_display import _SCRIPT


NODE_TEST = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const htmlScript=Buffer.from(process.argv[1], 'base64').toString('utf8');
const script=htmlScript.replace(/^\s*<script[^>]*>/,'').replace(/<\/script>\s*$/,'');

class Element {
  constructor(text='', children=[]) {
    this._text=text; this.children=children; this.parentElement=null;
    this.style={}; this.attrs={};
    for(const child of children) child.parentElement=this;
  }
  get textContent() {
    return this.children.length ? this.children.map(c=>c.textContent).join(' ') : this._text;
  }
  set textContent(value) {this._text=String(value); this.children=[];}
  querySelectorAll(selector) {
    if(selector!=='*') throw Error('Unexpected selector '+selector);
    const all=[];
    for(const child of this.children) {all.push(child,...child.querySelectorAll('*'));}
    return all;
  }
  getAttribute(k) {return this.attrs[k] ?? null;}
  setAttribute(k,v) {this.attrs[k]=v;}
}

function card(label,value,note) {
  const labelEl=new Element(label), valueEl=new Element(value), noteEl=new Element(note);
  return {root:new Element('',[labelEl,valueEl,noteEl]),label:labelEl,value:valueEl,note:noteEl};
}
async function run(givebackValue) {
  const nav=card('NAV','£5,427.62','Bal £5,423.88 · Account-wide UPL £3.74');
  const broker=card('Broker P&L','£2.25','LIVE Metals open P&L');
  const high=card('High-Water · LIVE XAU + XAG SHORT','£72.24','XAU + XAG SHORT historic peak');
  const give=card('Giveback · LIVE XAU + XAG SHORT',givebackValue,'8.96R');
  const others=card('This Month','£37.63','LIVE Metals realised this month');
  const body=new Element('',[new Element('',[nav.root,broker.root,high.root,give.root,others.root])]);
  const document={
    readyState:'complete',
    querySelectorAll(selector) {
      if(selector!=='body *') throw Error('Unexpected document selector '+selector);
      return body.querySelectorAll('*');
    },
  };
  const snapshot={
    ok:true,open_pnl_gbp:4.36,high_water_status:'OBSERVED_COMBINED_LIVE',
    high_water_gbp:72.24,giveback_gbp:67.88,giveback_pct:94.0,
    observed_at_utc:'2026-11-17T18:31:00+00:00',
    high_water_tracking_started_at_utc:'2026-11-16T18:38:00+00:00',
    high_water_at_utc:'2026-11-17T01:26:00+00:00',
  };
  vm.runInNewContext(script,{
    document, console, setInterval:()=>{},
    fetch: async ()=>({ok:true,json:async ()=>snapshot}),
  });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nav.label.textContent,'NAV');
  assert.equal(nav.value.textContent,'£5,427.62','NAV must NEVER contain giveback');
  assert.equal(broker.value.textContent,'£4.36');
  assert.equal(high.value.textContent,'£72.24');
  assert.equal(others.value.textContent,'£37.63');
  if(givebackValue==='£58.81 · 95.2%') {
    assert.equal(give.value.textContent,'£67.88','combined giveback must replace XAU-only value');
    assert.match(give.note.textContent,/94\.0%/);
    assert.match(broker.label.textContent,/Broker P&L/);
  } else {
    assert.equal(give.value.textContent,givebackValue,'Unrecognised metric must not borrow NAV');
  }
}
(async ()=>{
  await run('£58.81 · 95.2%');
  await run('not a currency value');
  console.log('PASS: NAV safe; combined P&L, high-water and giveback stay within own cards');
})().catch(err=>{console.error(err);process.exit(1);});
"""

class CombinedDashboardDomTests(unittest.TestCase):
    def test_metric_cards_are_never_cross_written(self):
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable")
        proc=subprocess.run(
            ["node","-e",NODE_TEST,base64.b64encode(_SCRIPT.encode()).decode()],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode,0,proc.stdout+"\n"+proc.stderr)
        self.assertIn("NAV safe",proc.stdout)

if __name__=="__main__":
    unittest.main()
