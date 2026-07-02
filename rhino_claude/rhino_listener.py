# -*- coding: utf-8 -*-
"""
rhino_listener.py  —  Rhino 8 <-> Claude 브릿지 (Rhino 내부에서 실행)

이 스크립트는 Rhino 8 의 ScriptEditor(Python 3) 안에서 실행합니다.
127.0.0.1 의 TCP 소켓 서버를 열고, MCP 서버(rhino_mcp_server.py)가 보내는
JSON 명령을 받아 Rhino 문서에 대해 실행한 뒤 결과를 되돌려줍니다.

핵심 설계
---------
* 소켓 accept / 클라이언트 처리는 백그라운드 스레드에서 수행합니다.
  (Rhino UI 가 멈추지 않도록)
* 그러나 Rhino 문서(geometry) 조작은 반드시 "메인 스레드"에서 해야 하므로,
  각 명령을 큐에 넣고 Rhino.RhinoApp.Idle 이벤트(메인 스레드에서 발생)에서
  꺼내 실행합니다. 결과는 요청별 응답 큐로 돌려줍니다.
* 재실행/중복 실행에 안전하도록 상태를 scriptcontext.sticky 에 보관합니다.

사용법
------
1) Rhino 8 실행 → _ScriptEditor 명령
2) 이 파일을 열고 "실행(Run)"
3) 명령줄에 "[Claude Bridge] listening on 127.0.0.1:1999" 가 뜨면 준비 완료
4) Rhino 를 켜 둔 상태로 Claude(Claude Desktop / Claude Code)에서 사용

중지하려면 이 파일 안의 stop_bridge() 를 실행하거나 Rhino 를 종료합니다.

English: run this inside Rhino 8's ScriptEditor (Python 3). It starts a TCP
socket server that the MCP server talks to. Document edits are marshalled to
Rhino's main thread via the RhinoApp.Idle event. Keep Rhino open while using it.
"""

import socket
import threading
import json
import traceback

try:
    import queue
except ImportError:  # 아주 오래된 파이썬 대비
    import Queue as queue

import System
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

HOST = "127.0.0.1"
PORT = 1999

# scriptcontext.sticky 는 스크립트 재실행 사이에 값이 유지됩니다.
_STICKY_KEY = "claude_rhino_bridge"


# ==========================================================================
# 명령 핸들러 (모두 메인 스레드에서 호출됨)
# ==========================================================================
def _vec(v, default=(0.0, 0.0, 0.0)):
    """리스트/튜플을 3D 좌표로 정규화."""
    if v is None:
        return list(default)
    v = list(v)
    while len(v) < 3:
        v.append(0.0)
    return [float(v[0]), float(v[1]), float(v[2])]


def _gid(guid):
    return str(guid) if guid else None


def h_ping(p):
    return {"pong": True, "rhino": str(Rhino.RhinoApp.Version)}


def h_get_document_info(p):
    doc = sc.doc
    return {
        "name": doc.Name or "Untitled",
        "path": doc.Path or "",
        "object_count": doc.Objects.Count,
        "units": str(doc.ModelUnitSystem),
        "tolerance": doc.ModelAbsoluteTolerance,
        "layers": [layer.Name for layer in doc.Layers if not layer.IsDeleted],
    }


def h_list_objects(p):
    doc = sc.doc
    out = []
    for obj in doc.Objects:
        try:
            layer_name = doc.Layers[obj.Attributes.LayerIndex].Name
        except Exception:
            layer_name = ""
        out.append({
            "id": str(obj.Id),
            "type": str(obj.ObjectType),
            "layer": layer_name,
            "name": obj.Attributes.Name or "",
        })
    return {"count": len(out), "objects": out}


def h_get_selected_objects(p):
    ids = rs.SelectedObjects() or []
    out = []
    for oid in ids:
        out.append({
            "id": str(oid),
            "type": str(rs.ObjectType(oid)),
            "name": rs.ObjectName(oid) or "",
        })
    return {"count": len(out), "objects": out}


# --- 생성 -----------------------------------------------------------------
def h_create_point(p):
    return {"id": _gid(rs.AddPoint(_vec(p.get("point"))))}


def h_create_line(p):
    return {"id": _gid(rs.AddLine(_vec(p.get("start")), _vec(p.get("end"))))}


def h_create_polyline(p):
    pts = [_vec(pt) for pt in p.get("points", [])]
    if len(pts) < 2:
        raise ValueError("polyline 은 점이 2개 이상 필요합니다.")
    return {"id": _gid(rs.AddPolyline(pts))}


def h_create_curve(p):
    pts = [_vec(pt) for pt in p.get("points", [])]
    degree = int(p.get("degree", 3))
    if len(pts) < 2:
        raise ValueError("curve 는 점이 2개 이상 필요합니다.")
    return {"id": _gid(rs.AddCurve(pts, degree))}


def h_create_circle(p):
    return {"id": _gid(rs.AddCircle(_vec(p.get("center")), float(p.get("radius", 1.0))))}


def h_create_rectangle(p):
    cx, cy, cz = _vec(p.get("corner"))
    w = float(p.get("width", 1.0))
    h = float(p.get("height", 1.0))
    pts = [[cx, cy, cz], [cx + w, cy, cz], [cx + w, cy + h, cz],
           [cx, cy + h, cz], [cx, cy, cz]]
    return {"id": _gid(rs.AddPolyline(pts))}


def h_create_sphere(p):
    return {"id": _gid(rs.AddSphere(_vec(p.get("center")), float(p.get("radius", 1.0))))}


def h_create_box(p):
    cx, cy, cz = _vec(p.get("corner"))
    x = float(p.get("x_size", 1.0))
    y = float(p.get("y_size", 1.0))
    z = float(p.get("z_size", 1.0))
    corners = [
        [cx, cy, cz], [cx + x, cy, cz], [cx + x, cy + y, cz], [cx, cy + y, cz],
        [cx, cy, cz + z], [cx + x, cy, cz + z], [cx + x, cy + y, cz + z], [cx, cy + y, cz + z],
    ]
    return {"id": _gid(rs.AddBox(corners))}


def h_create_cylinder(p):
    return {"id": _gid(rs.AddCylinder(_vec(p.get("base")),
                                      float(p.get("height", 1.0)),
                                      float(p.get("radius", 0.5))))}


def h_create_cone(p):
    return {"id": _gid(rs.AddCone(_vec(p.get("base")),
                                  float(p.get("height", 1.0)),
                                  float(p.get("radius", 0.5))))}


def h_add_text_dot(p):
    return {"id": _gid(rs.AddTextDot(str(p.get("text", "")), _vec(p.get("point"))))}


# --- 변형 -----------------------------------------------------------------
def h_move_object(p):
    rs.MoveObject(p["id"], _vec(p.get("translation")))
    return {"id": p["id"], "moved": True}


def h_copy_object(p):
    return {"id": _gid(rs.CopyObject(p["id"], _vec(p.get("translation"))))}


def h_rotate_object(p):
    axis = _vec(p["axis"]) if p.get("axis") else None
    rs.RotateObject(p["id"], _vec(p.get("center")), float(p.get("angle_degrees", 0.0)), axis)
    return {"id": p["id"], "rotated": True}


def h_scale_object(p):
    rs.ScaleObject(p["id"], _vec(p.get("origin")), _vec(p.get("scale", [1, 1, 1])))
    return {"id": p["id"], "scaled": True}


def h_delete_object(p):
    ok = rs.DeleteObject(p["id"])
    return {"id": p["id"], "deleted": bool(ok)}


# --- 외형 / 레이어 --------------------------------------------------------
def h_set_object_color(p):
    rs.ObjectColor(p["id"], [int(c) for c in p.get("color", [0, 0, 0])])
    return {"id": p["id"], "ok": True}


def h_set_object_name(p):
    rs.ObjectName(p["id"], str(p.get("name", "")))
    return {"id": p["id"], "ok": True}


def h_set_object_layer(p):
    rs.ObjectLayer(p["id"], str(p["layer"]))
    return {"id": p["id"], "ok": True}


def h_create_layer(p):
    name = str(p["name"])
    color = [int(c) for c in p["color"]] if p.get("color") else None
    if not rs.IsLayer(name):
        rs.AddLayer(name, color)
    elif color:
        rs.LayerColor(name, color)
    return {"layer": name, "ok": True}


# --- 선택 / 뷰 ------------------------------------------------------------
def h_select_objects(p):
    rs.UnselectAllObjects()
    ids = p.get("ids", [])
    n = rs.SelectObjects(ids) if ids else 0
    return {"selected": n}


def h_clear_selection(p):
    rs.UnselectAllObjects()
    return {"ok": True}


def h_zoom_extents(p):
    rs.ZoomExtents(None, True)
    return {"ok": True}


def h_capture_viewport(p):
    width = int(p.get("width", 800))
    height = int(p.get("height", 600))
    view = sc.doc.Views.ActiveView
    if view is None:
        raise RuntimeError("활성 뷰포트가 없습니다.")
    size = System.Drawing.Size(width, height)
    bmp = view.CaptureToBitmap(size)
    if bmp is None:
        raise RuntimeError("뷰포트 캡처 실패.")
    ms = System.IO.MemoryStream()
    try:
        bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png)
        b64 = System.Convert.ToBase64String(ms.ToArray())
    finally:
        ms.Dispose()
        bmp.Dispose()
    return {"image_base64": b64, "width": width, "height": height}


def h_execute_python(p):
    """임의의 Python(rhinoscriptsyntax) 코드를 Rhino 안에서 실행.

    스코프에 rs / sc / Rhino 가 미리 주입됩니다. 코드에서 변수 result 를
    설정하면 그 값이 문자열로 반환됩니다. print 출력도 함께 반환합니다.
    """
    import io
    import contextlib
    code = p.get("code", "")
    buf = io.StringIO()
    scope = {"rs": rs, "sc": sc, "Rhino": Rhino, "System": System,
             "__name__": "__rhino_exec__"}
    with contextlib.redirect_stdout(buf):
        exec(code, scope)
    result_val = scope.get("result", None)
    return {
        "stdout": buf.getvalue(),
        "result": None if result_val is None else str(result_val),
    }


HANDLERS = {
    "ping": h_ping,
    "get_document_info": h_get_document_info,
    "list_objects": h_list_objects,
    "get_selected_objects": h_get_selected_objects,
    "create_point": h_create_point,
    "create_line": h_create_line,
    "create_polyline": h_create_polyline,
    "create_curve": h_create_curve,
    "create_circle": h_create_circle,
    "create_rectangle": h_create_rectangle,
    "create_sphere": h_create_sphere,
    "create_box": h_create_box,
    "create_cylinder": h_create_cylinder,
    "create_cone": h_create_cone,
    "add_text_dot": h_add_text_dot,
    "move_object": h_move_object,
    "copy_object": h_copy_object,
    "rotate_object": h_rotate_object,
    "scale_object": h_scale_object,
    "delete_object": h_delete_object,
    "set_object_color": h_set_object_color,
    "set_object_name": h_set_object_name,
    "set_object_layer": h_set_object_layer,
    "create_layer": h_create_layer,
    "select_objects": h_select_objects,
    "clear_selection": h_clear_selection,
    "zoom_extents": h_zoom_extents,
    "capture_viewport": h_capture_viewport,
    "execute_python": h_execute_python,
}

# 문서를 변경하지 않는(읽기 전용) 명령 — 실행 후 Redraw 를 건너뜁니다.
_READ_ONLY = {"ping", "get_document_info", "list_objects",
              "get_selected_objects", "capture_viewport"}


# ==========================================================================
# 브릿지 서버 (스레드 + Idle 큐)
# ==========================================================================
class RhinoClaudeBridge(object):
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.running = False
        self.server_sock = None
        self.cmd_queue = queue.Queue()      # (command_dict, response_queue)
        self._idle_handler = None

    # -- 메인 스레드: Idle 이벤트에서 큐를 비움 --
    def _on_idle(self, sender, args):
        did_work = False
        while True:
            try:
                command, resp_q = self.cmd_queue.get_nowait()
            except queue.Empty:
                break
            cmd_type = command.get("type", "")
            params = command.get("params", {}) or {}
            try:
                handler = HANDLERS.get(cmd_type)
                if handler is None:
                    raise ValueError("알 수 없는 명령: %s" % cmd_type)
                result = handler(params)
                resp_q.put({"status": "success", "result": result})
                if cmd_type not in _READ_ONLY:
                    did_work = True
            except Exception as ex:
                resp_q.put({"status": "error",
                            "message": "%s: %s" % (type(ex).__name__, ex),
                            "traceback": traceback.format_exc()})
        if did_work and sc.doc is not None:
            sc.doc.Views.Redraw()

    # -- 백그라운드 스레드: 클라이언트 하나 처리 --
    def _handle_client(self, conn):
        conn.settimeout(None)
        buffer = b""
        try:
            while self.running:
                data = conn.recv(65536)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._process_line(conn, line)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _process_line(self, conn, line):
        try:
            command = json.loads(line.decode("utf-8"))
        except Exception as ex:
            self._send(conn, {"status": "error",
                              "message": "잘못된 JSON: %s" % ex})
            return
        resp_q = queue.Queue()
        self.cmd_queue.put((command, resp_q))
        try:
            response = resp_q.get(timeout=120)
        except queue.Empty:
            response = {"status": "error", "message": "명령 처리 시간 초과."}
        self._send(conn, response)

    def _send(self, conn, obj):
        payload = json.dumps(obj, ensure_ascii=False) + "\n"
        conn.sendall(payload.encode("utf-8"))

    # -- 백그라운드 스레드: accept 루프 --
    def _serve(self):
        while self.running:
            try:
                conn, _addr = self.server_sock.accept()
            except OSError:
                break
            except Exception:
                break
            t = threading.Thread(target=self._handle_client, args=(conn,))
            t.daemon = True
            t.start()

    def start(self):
        if self.running:
            return
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(5)
        self.running = True

        self._idle_handler = System.EventHandler(self._on_idle)
        Rhino.RhinoApp.Idle += self._idle_handler

        t = threading.Thread(target=self._serve)
        t.daemon = True
        t.start()

        msg = "[Claude Bridge] listening on %s:%d" % (self.host, self.port)
        Rhino.RhinoApp.WriteLine(msg)
        print(msg)

    def stop(self):
        self.running = False
        if self._idle_handler is not None:
            try:
                Rhino.RhinoApp.Idle -= self._idle_handler
            except Exception:
                pass
            self._idle_handler = None
        if self.server_sock is not None:
            try:
                self.server_sock.close()
            except Exception:
                pass
            self.server_sock = None
        Rhino.RhinoApp.WriteLine("[Claude Bridge] stopped.")
        print("[Claude Bridge] stopped.")


def start_bridge():
    """브릿지를 시작(이미 있으면 재시작)."""
    existing = sc.sticky.get(_STICKY_KEY)
    if existing is not None:
        try:
            existing.stop()
        except Exception:
            pass
    bridge = RhinoClaudeBridge(HOST, PORT)
    bridge.start()
    sc.sticky[_STICKY_KEY] = bridge
    return bridge


def stop_bridge():
    """브릿지를 중지."""
    existing = sc.sticky.get(_STICKY_KEY)
    if existing is not None:
        existing.stop()
        sc.sticky[_STICKY_KEY] = None


if __name__ == "__main__":
    start_bridge()
