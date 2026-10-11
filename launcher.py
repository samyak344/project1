"""
HaqSetu LAUNCHER - one window for the whole empire.
Spawns every backend that isn't already up, then serves ONE page (port 8090)
with a tab bar + iframe. No existing file is edited. iframes avoid the
mount-prefix problem entirely. Ctrl+C shuts everything down.
"""
import os, sys, time, socket, subprocess, threading, urllib.request, atexit
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# (port, script_or_None, has_page, group, label)
MODULES = [
    (8000, "api/main.py",            False, "live", "API (deny-me + evidence backend)"),
    (8080, None,                     True,  "live", "Intake Booth + Try-to-Deny-Me"),  # http.server web/
    (8010, "box/orchestrator.py",    True,  "live", "1. Speak / Split / Abstain"),
    (8011, "box/doc_camera.py",      True,  "live", "2. Camera + Crop Loss + KYC"),
    (8012, "box/civic_escalation.py",True,  "live", "3. Civic + Ladder + Route D + Public"),
    (8006, "engine/closure_loop.py", True,  "live", "4. Decision buttons + closure"),
    (8007, "engine/planner_visualiser.py", True, "view", "5. One-Visit Planner"),
    (8002, "engine/compiler_wall.py",True,  "lab",  "6. Rule Compiler Wall (Z3)"),
    (8008, "ledger/tamper_challenge.py", True, "lab", "7. Tamper Challenge"),
    (8004, "engine/paper_trail.py",  True,  "lab",  "8. Haq Receipt (QR)"),
    (8003, "mesh/district_twin.py",  True,  "view", "9. District Twin (KM curves)"),
    (8001, None,                     True,  "view", "10. Heatwave Map (run separately)"),
]
CHILDREN = []

def port_open(p, t=0.4):
    s = socket.socket(); s.settimeout(t)
    try: return s.connect_ex(("127.0.0.1", p)) == 0
    finally: s.close()

def spawn(port, script):
    if port_open(port):           # already running -> leave it
        return
    if script is None:            # manual / external
        return
    path = os.path.join(ROOT, script)
    if not os.path.exists(path):  # missing file -> skip, don't crash
        print(f"  skip {script} (not found)"); return
    cmd = [sys.executable, path] if script.endswith(".py") else [sys.executable, "-m", "http.server", str(port), "--directory", "web"]
    try:
        CHILDREN.append(subprocess.Popen(cmd, cwd=ROOT))
        print(f"  + :{port} {script or 'web/'}")
    except Exception as e:
        print(f"  ! :{port} failed: {e}")

def shutdown():
    for c in CHILDREN:
        try: c.terminate()
        except Exception: pass

atexit.register(shutdown)

app = FastAPI()

@app.get("/status")
def status():
    return JSONResponse({str(p): port_open(p) for p, *_ in MODULES})

PAGE = r"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>HaqSetu Console</title>
<style>
body{margin:0;font-family:system-ui,sans-serif;background:#0f172a;color:#f8fafc;height:100vh;display:flex;flex-direction:column;}
header{background:#020617;padding:8px 14px;border-bottom:1px solid #1e293b;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
header b{color:#38bdf8;font-size:15px;}
.grp{display:flex;gap:4px;align-items:center;} .grp span{font-size:10px;color:#64748b;text-transform:uppercase;margin-right:2px;}
button.tab{padding:6px 11px;border:1px solid #334155;background:#1e293b;color:#cbd5e1;border-radius:7px;cursor:pointer;font-size:12px;font-weight:bold;}
button.tab.on{background:#38bdf8;color:#0f172a;border-color:#38bdf8;}
button.tab:disabled{opacity:.35;cursor:not-allowed;}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#475569;margin-right:5px;vertical-align:middle;}
.dot.up{background:#10b981;box-shadow:0 0 6px #10b981;}
iframe{flex:1;border:0;width:100%;background:#000;}
#boot{position:fixed;inset:0;background:#0f172a;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px;z-index:9;}
#boot.hide{display:none;} .spin{width:34px;height:34px;border:3px solid #1e293b;border-top-color:#38bdf8;border-radius:50%;animation:s 1s linear infinite;}
@keyframes s{to{transform:rotate(360deg)}}
</style></head><body>
<div id='boot'><div class='spin'></div><div id='bootmsg' style='color:#94a3b8;font-size:13px'>starting backends…</div></div>
<header><b>🌉 HaqSetu Console</b><div id='tabs' style='display:flex;gap:10px;flex-wrap:wrap'></div>
<button class='tab' onclick='location.reload()' style='margin-left:auto'>↻ reload</button></header>
<iframe id='fr' src='about:blank' allow='microphone'></iframe>
<script>
const MODS=__MODS__;
let cur=null;
function build(){
 const order=['live','view','lab']; const box=document.getElementById('tabs'); box.innerHTML='';
 order.forEach(g=>{
   const gmods=MODS.filter(m=>m[3]===g); if(!gmods.length)return;
   const d=document.createElement('div'); d.className='grp'; d.innerHTML=`<span>${g}</span>`;
   gmods.forEach(m=>{const b=document.createElement('button');b.className='tab';b.id='t'+m[0];
     b.innerHTML=`<i class='dot' id='d${m[0]}'></i>${m[4]}`;b.onclick=()=>go(m[0]);d.appendChild(b);});
   box.appendChild(d);
 });
}
function go(p){cur=p;document.getElementById('fr').src='http://127.0.0.1:'+p+'/';
 MODS.forEach(m=>document.getElementById('t'+m[0])?.classList.toggle('on',m[0]===p));}
async function poll(){
 const r=await (await fetch('/status')).json();
 MODS.forEach(m=>{const up=r[String(m[0])];const d=document.getElementById('d'+m[0]);if(d)d.classList.toggle('up',up);
   const b=document.getElementById('t'+m[0]);if(b)b.disabled=!m[2]?!up:!up;});
 const pages=MODS.filter(m=>m[2]); const allUp=pages.every(m=>r[String(m[0])]);
 if(allUp){document.getElementById('boot').classList.add('hide'); if(!cur)go(pages[0][0]);}
 else document.getElementById('bootmsg').textContent='starting backends… '+pages.filter(m=>r[String(m[0])]).length+'/'+pages.length+' up';
}
build(); poll(); setInterval(poll,1500);
</script></body></html>"""

@app.get("/")
def home():
    import json
    return HTMLResponse(PAGE.replace("__MODS__", json.dumps(MODULES)))

if __name__ == "__main__":
    print("🚀 booting HaqSetu backends…")
    for p, s, *_ in MODULES:
        if s == "api/main.py" or (s and s.endswith(".py")):
            spawn(p, s)
        elif s is None and p == 8080:
            spawn(p, "web/")   # http.server marker
    # 8080 special: launch http.server
    if not port_open(8080):
        try: CHILDREN.append(subprocess.Popen([sys.executable, "-m", "http.server", "8080", "--directory", "web"], cwd=ROOT)); print("  + :8080 web/")
        except Exception as e: print("  ! 8080:", e)
    print("✅ console on http://127.0.0.1:8090  (Ctrl+C to stop all)")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")