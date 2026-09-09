"""Pose UI — live camera streams + joint readout, with a one-click rest-pose snapshot.

Operator tool for the failure-recovery mechanism (coordinator, D021): shows the two
camera MJPEG streams from the camera bridge beside the live /joint_states readout in
CONTROLLER order, and a "Snapshot as rest pose" button that writes the current pose to
REST_POSE_FILE (the file the coordinator loads) with {"pinned": true} so the learned
pose never overwrites a deliberately chosen one. "Unpin" hands control back to learning.

Runs on the host in the act-inference image, host network, zenoh client to the sim.
  GET  /          the page
  GET  /pose      {"joints": {name: pos} (controller order), "raw": {...}, "saved": {...}}
  POST /snapshot  save current pose (pinned)
  POST /unpin     drop the pinned flag (learned pose may replace it)
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

PORT = int(os.environ.get("POSE_UI_PORT", "8090"))
CAMERA_PORT = int(os.environ.get("CAMERA_BRIDGE_PORT", "8081"))
REST_POSE_FILE = os.environ.get("REST_POSE_FILE", "/data/rest_pose.json")
CTRL_JOINTS = [j.strip() for j in os.environ.get(
    "CTRL_JOINTS",
    "shoulder_pan_joint,shoulder_lift_joint,elbow_flex_joint,"
    "wrist_flex_joint,wrist_roll_joint,gripper_joint").split(",") if j.strip()]

_latest = {}          # {joint: position} from /joint_states
_lock = threading.Lock()


class PoseNode(Node):
    def __init__(self):
        super().__init__("pose_ui")
        self.create_subscription(JointState, "/joint_states", self._on_js, 10)

    def _on_js(self, msg):
        with _lock:
            _latest.clear()
            _latest.update(zip(msg.name, msg.position))


def _read_saved():
    try:
        with open(REST_POSE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _write_saved(doc):
    os.makedirs(os.path.dirname(REST_POSE_FILE), exist_ok=True)
    with open(REST_POSE_FILE, "w") as f:
        json.dump(doc, f, indent=1)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Rest pose picker</title>
<style>
 body{font-family:system-ui,sans-serif;margin:16px;background:#111;color:#eee}
 .row{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
 img{width:480px;height:480px;background:#000;border:1px solid #333}
 table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:15px}
 td,th{padding:4px 10px;border-bottom:1px solid #333;text-align:right}
 th{text-align:left} td.n{text-align:left}
 button{font-size:16px;padding:10px 16px;margin:8px 8px 8px 0;cursor:pointer}
 #snap{background:#2a7;color:#000;font-weight:600}
 #msg{margin-top:8px;color:#9d9} .pin{color:#fc6}
</style></head><body>
<h2>Rest pose picker</h2>
<div class="row">
 <div><div>static</div><img id="s" alt="static"></div>
 <div><div>wrist</div><img id="w" alt="wrist"></div>
 <div>
  <table><thead><tr><th>joint (controller order)</th><th>live</th><th>saved</th></tr></thead>
  <tbody id="t"></tbody></table>
  <div id="status"></div>
  <button id="snap">Snapshot as rest pose</button>
  <button id="unpin">Unpin</button>
  <div id="msg"></div>
 </div>
</div>
<script>
const h=location.hostname;
document.getElementById('s').src=`http://${h}:__CAMPORT__/static`;
document.getElementById('w').src=`http://${h}:__CAMPORT__/wrist`;
async function tick(){
  try{
    const r=await fetch('/pose'); const d=await r.json();
    const saved=d.saved||{}; let rows='';
    for(const j of d.order){
      const lv=d.joints[j]; const sv=saved[j];
      rows+=`<tr><td class="n">${j}</td><td>${lv===undefined?'—':lv.toFixed(3)}</td><td>${sv===undefined?'—':(+sv).toFixed(3)}</td></tr>`;
    }
    document.getElementById('t').innerHTML=rows;
    document.getElementById('status').innerHTML= d.saved ? (d.saved.pinned?'<span class="pin">saved pose is PINNED (learning will not overwrite it)</span>':'saved pose is learned (not pinned)') : 'no saved rest pose yet';
  }catch(e){}
}
setInterval(tick,200); tick();
document.getElementById('snap').onclick=async()=>{const r=await fetch('/snapshot',{method:'POST'});document.getElementById('msg').textContent='saved: '+await r.text();};
document.getElementById('unpin').onclick=async()=>{const r=await fetch('/unpin',{method:'POST'});document.getElementById('msg').textContent=await r.text();};
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.replace("__CAMPORT__", str(CAMERA_PORT)), "text/html; charset=utf-8")
        elif self.path == "/pose":
            with _lock:
                raw = dict(_latest)
            self._send(200, json.dumps({
                "order": CTRL_JOINTS,
                "joints": {j: raw[j] for j in CTRL_JOINTS if j in raw},
                "raw": raw,
                "saved": _read_saved(),
            }))
        elif self.path == "/health":
            self._send(200, "ok", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/snapshot":
            with _lock:
                raw = dict(_latest)
            if any(j not in raw for j in CTRL_JOINTS):
                self._send(503, "no /joint_states yet", "text/plain"); return
            doc = {j: float(raw[j]) for j in CTRL_JOINTS}
            doc["pinned"] = True
            _write_saved(doc)
            print(f"[pose-ui] snapshot saved (pinned): {doc}", flush=True)
            self._send(200, json.dumps(doc))
        elif self.path == "/unpin":
            doc = _read_saved()
            if not doc:
                self._send(404, "nothing saved", "text/plain"); return
            doc.pop("pinned", None)
            _write_saved(doc)
            self._send(200, "unpinned — the learned pose may now replace it", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *a):  # quiet
        pass


def main():
    rclpy.init()
    node = PoseNode()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    print(f"[pose-ui] http://0.0.0.0:{PORT}/  (cameras from :{CAMERA_PORT}, file {REST_POSE_FILE})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
