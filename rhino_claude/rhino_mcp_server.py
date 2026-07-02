#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rhino_mcp_server.py  —  Rhino 8 를 위한 MCP(Model Context Protocol) 서버

Claude Desktop / Claude Code 가 이 서버를 stdio 로 실행하면, Claude 가
여기서 정의한 도구(tool)들을 자연어로 호출할 수 있습니다. 각 도구는
127.0.0.1:1999 에서 대기 중인 Rhino 내부 리스너(rhino_listener.py)로
명령을 전달하고 결과를 돌려받습니다.

  Claude Desktop / Claude Code
          │  (MCP, stdio)
          ▼
  rhino_mcp_server.py   ← 이 파일
          │  (TCP JSON, 127.0.0.1:1999)
          ▼
  rhino_listener.py  (Rhino 8 내부에서 실행)
          │
          ▼
        Rhino 문서

설치:  pip install -r requirements.txt
실행:  Claude Desktop 설정(claude_desktop_config.json)에 등록하거나
       Claude Code 에서  `claude mcp add rhino -- python /경로/rhino_mcp_server.py`

환경변수 RHINO_HOST / RHINO_PORT 로 접속 대상을 바꿀 수 있습니다.
"""

import os
import json
import socket
import base64

from mcp.server.fastmcp import FastMCP, Image

RHINO_HOST = os.environ.get("RHINO_HOST", "127.0.0.1")
RHINO_PORT = int(os.environ.get("RHINO_PORT", "1999"))

mcp = FastMCP("rhino")


# --------------------------------------------------------------------------
# Rhino 리스너와의 통신
# --------------------------------------------------------------------------
def rhino_request(command_type, params=None, recv_timeout=125):
    """Rhino 리스너에 명령 1건을 보내고 result 를 반환.

    새 연결을 매번 열고 닫는다(명령 빈도가 낮아 단순/견고함이 우선).
    실패 시 RuntimeError 를 던진다(MCP 가 도구 에러로 Claude 에게 전달).
    """
    payload = json.dumps({"type": command_type, "params": params or {}}) + "\n"
    try:
        conn = socket.create_connection((RHINO_HOST, RHINO_PORT), timeout=5)
    except OSError as exc:
        raise RuntimeError(
            "Rhino 리스너에 연결할 수 없습니다 (%s:%d). Rhino 8 을 켜고 "
            "ScriptEditor 에서 rhino_listener.py 를 실행했는지 확인하세요. (%s)"
            % (RHINO_HOST, RHINO_PORT, exc)
        )
    try:
        conn.sendall(payload.encode("utf-8"))
        conn.settimeout(recv_timeout)
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buffer += chunk
    finally:
        conn.close()

    if not buffer:
        raise RuntimeError("Rhino 리스너로부터 응답이 없습니다.")
    line = buffer.split(b"\n", 1)[0]
    response = json.loads(line.decode("utf-8"))
    if response.get("status") != "success":
        raise RuntimeError(response.get("message", "알 수 없는 Rhino 오류"))
    return response.get("result")


def _ok(result):
    """도구 반환값을 사람이 읽기 쉬운 JSON 문자열로."""
    return json.dumps(result, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# 조회 도구
# --------------------------------------------------------------------------
@mcp.tool()
def get_document_info() -> str:
    """현재 Rhino 문서 정보(이름/경로/객체 수/단위/레이어 목록)를 반환합니다."""
    return _ok(rhino_request("get_document_info"))


@mcp.tool()
def list_objects() -> str:
    """문서 안의 모든 객체(id, 종류, 레이어, 이름)를 나열합니다.

    다른 도구에서 대상 객체를 지정하려면 여기서 얻은 id 를 사용하세요.
    """
    return _ok(rhino_request("list_objects"))


@mcp.tool()
def get_selected_objects() -> str:
    """현재 Rhino 에서 사용자가 선택한 객체 목록을 반환합니다."""
    return _ok(rhino_request("get_selected_objects"))


# --------------------------------------------------------------------------
# 생성 도구
# --------------------------------------------------------------------------
@mcp.tool()
def create_point(x: float, y: float, z: float = 0.0) -> str:
    """점(point) 객체를 (x, y, z) 위치에 만듭니다. 새 객체 id 를 반환합니다."""
    return _ok(rhino_request("create_point", {"point": [x, y, z]}))


@mcp.tool()
def create_line(start: list[float], end: list[float]) -> str:
    """직선을 만듭니다. start / end 는 각각 [x, y, z] 좌표입니다."""
    return _ok(rhino_request("create_line", {"start": start, "end": end}))


@mcp.tool()
def create_polyline(points: list[list[float]]) -> str:
    """여러 점을 잇는 폴리라인을 만듭니다. points 는 [[x,y,z], ...] 형식입니다."""
    return _ok(rhino_request("create_polyline", {"points": points}))


@mcp.tool()
def create_curve(points: list[list[float]], degree: int = 3) -> str:
    """제어점을 지나는 NURBS 곡선을 만듭니다. degree 는 곡선 차수(기본 3)."""
    return _ok(rhino_request("create_curve", {"points": points, "degree": degree}))


@mcp.tool()
def create_circle(center: list[float], radius: float) -> str:
    """원을 만듭니다. center 는 [x, y, z], radius 는 반지름입니다."""
    return _ok(rhino_request("create_circle", {"center": center, "radius": radius}))


@mcp.tool()
def create_rectangle(corner: list[float], width: float, height: float) -> str:
    """XY 평면에 사각형(닫힌 폴리라인)을 만듭니다. corner 는 좌하단 [x, y, z]."""
    return _ok(rhino_request("create_rectangle",
                             {"corner": corner, "width": width, "height": height}))


@mcp.tool()
def create_sphere(center: list[float], radius: float) -> str:
    """구(sphere)를 만듭니다. center 는 [x, y, z], radius 는 반지름입니다."""
    return _ok(rhino_request("create_sphere", {"center": center, "radius": radius}))


@mcp.tool()
def create_box(corner: list[float], x_size: float, y_size: float, z_size: float) -> str:
    """직육면체(box)를 만듭니다. corner 는 한 모서리 [x, y, z], 각 변의 길이는
    x_size / y_size / z_size 입니다."""
    return _ok(rhino_request("create_box", {
        "corner": corner, "x_size": x_size, "y_size": y_size, "z_size": z_size,
    }))


@mcp.tool()
def create_cylinder(base: list[float], radius: float, height: float) -> str:
    """원기둥(cylinder)을 만듭니다. base 는 바닥 중심 [x, y, z]."""
    return _ok(rhino_request("create_cylinder",
                             {"base": base, "radius": radius, "height": height}))


@mcp.tool()
def create_cone(base: list[float], radius: float, height: float) -> str:
    """원뿔(cone)을 만듭니다. base 는 바닥 중심 [x, y, z]."""
    return _ok(rhino_request("create_cone",
                             {"base": base, "radius": radius, "height": height}))


@mcp.tool()
def add_text_dot(text: str, point: list[float]) -> str:
    """지정 위치에 항상 화면을 향하는 텍스트 점(주석)을 추가합니다."""
    return _ok(rhino_request("add_text_dot", {"text": text, "point": point}))


# --------------------------------------------------------------------------
# 변형 도구
# --------------------------------------------------------------------------
@mcp.tool()
def move_object(object_id: str, translation: list[float]) -> str:
    """객체를 translation([dx, dy, dz]) 만큼 이동합니다."""
    return _ok(rhino_request("move_object",
                             {"id": object_id, "translation": translation}))


@mcp.tool()
def copy_object(object_id: str, translation: list[float]) -> str:
    """객체를 translation([dx, dy, dz]) 만큼 이동한 위치에 복사합니다."""
    return _ok(rhino_request("copy_object",
                             {"id": object_id, "translation": translation}))


@mcp.tool()
def rotate_object(object_id: str, angle_degrees: float,
                  center: list[float] = [0.0, 0.0, 0.0]) -> str:
    """객체를 center 를 기준으로 Z축(위) 방향으로 angle_degrees 만큼 회전합니다."""
    return _ok(rhino_request("rotate_object", {
        "id": object_id, "angle_degrees": angle_degrees, "center": center,
    }))


@mcp.tool()
def scale_object(object_id: str, scale: list[float],
                 origin: list[float] = [0.0, 0.0, 0.0]) -> str:
    """객체를 origin 기준으로 scale([sx, sy, sz]) 배율만큼 확대/축소합니다."""
    return _ok(rhino_request("scale_object",
                             {"id": object_id, "scale": scale, "origin": origin}))


@mcp.tool()
def delete_object(object_id: str) -> str:
    """객체를 삭제합니다."""
    return _ok(rhino_request("delete_object", {"id": object_id}))


# --------------------------------------------------------------------------
# 외형 / 레이어 도구
# --------------------------------------------------------------------------
@mcp.tool()
def set_object_color(object_id: str, r: int, g: int, b: int) -> str:
    """객체 색상을 RGB(0~255)로 지정합니다."""
    return _ok(rhino_request("set_object_color",
                             {"id": object_id, "color": [r, g, b]}))


@mcp.tool()
def set_object_name(object_id: str, name: str) -> str:
    """객체 이름을 지정합니다."""
    return _ok(rhino_request("set_object_name", {"id": object_id, "name": name}))


@mcp.tool()
def set_object_layer(object_id: str, layer: str) -> str:
    """객체를 지정한 레이어로 옮깁니다(레이어가 있어야 함)."""
    return _ok(rhino_request("set_object_layer", {"id": object_id, "layer": layer}))


@mcp.tool()
def create_layer(name: str, r: int = -1, g: int = -1, b: int = -1) -> str:
    """새 레이어를 만듭니다. r/g/b 를 0~255 로 주면 레이어 색상도 지정합니다
    (기본 -1 이면 색상 미지정)."""
    params = {"name": name}
    if r >= 0 and g >= 0 and b >= 0:
        params["color"] = [r, g, b]
    return _ok(rhino_request("create_layer", params))


# --------------------------------------------------------------------------
# 선택 / 뷰 도구
# --------------------------------------------------------------------------
@mcp.tool()
def select_objects(object_ids: list[str]) -> str:
    """지정한 id 들의 객체를 선택합니다(기존 선택은 해제)."""
    return _ok(rhino_request("select_objects", {"ids": object_ids}))


@mcp.tool()
def clear_selection() -> str:
    """모든 선택을 해제합니다."""
    return _ok(rhino_request("clear_selection"))


@mcp.tool()
def zoom_extents() -> str:
    """모든 뷰포트를 전체 객체가 보이도록 확대/축소합니다."""
    return _ok(rhino_request("zoom_extents"))


@mcp.tool()
def capture_viewport(width: int = 800, height: int = 600) -> Image:
    """활성 뷰포트를 PNG 이미지로 캡처해서 반환합니다.

    Claude 가 현재 모델의 모습을 '눈으로 보고' 다음 작업을 판단할 때 사용합니다.
    """
    result = rhino_request("capture_viewport", {"width": width, "height": height})
    data = base64.b64decode(result["image_base64"])
    return Image(data=data, format="png")


# --------------------------------------------------------------------------
# 고급 도구 (임의 코드 실행)
# --------------------------------------------------------------------------
@mcp.tool()
def execute_rhinoscript(code: str) -> str:
    """임의의 Python(rhinoscriptsyntax) 코드를 Rhino 안에서 실행합니다.

    미리 만들어진 도구로 표현하기 어려운 복잡한 작업(불리언 연산, 배열 복사,
    로프트, 스윕 등)에 사용하세요. 코드 스코프에는 rs, sc, Rhino, System 이
    주입되어 있습니다. 변수 `result` 에 값을 넣으면 문자열로 반환됩니다.
    print() 출력도 함께 돌아옵니다.

    ⚠️ 이 도구는 Rhino 프로세스 안에서 임의 코드를 실행하므로 강력합니다.
    신뢰할 수 있는 작업에만 사용하세요.

    예)
        pts = [[0,0,0],[10,0,0],[10,10,0]]
        crv = rs.AddPolyline(pts + [pts[0]])
        result = rs.ExtrudeCurveStraight(crv, [0,0,0], [0,0,5])
    """
    return _ok(rhino_request("execute_python", {"code": code}))


if __name__ == "__main__":
    # 기본 전송 방식은 stdio (Claude Desktop / Claude Code 표준).
    mcp.run()
