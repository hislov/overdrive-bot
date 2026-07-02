# Rhino 8 × Claude 연동 (MCP)

Rhino3D 8 을 **Claude**(Claude Desktop / Claude Code)와 연결해서, 자연어로 3D
모델을 만들고 편집할 수 있게 해 주는 MCP(Model Context Protocol) 통합입니다.

> 예) "원점에 반지름 10짜리 구를 만들고, 그 위 20만큼 위치에 반지름 5 구를 하나 더 올려줘.
> 그리고 뷰포트를 캡처해서 보여줘." → Claude 가 알아서 Rhino 에 도형을 만들고 화면을 확인합니다.

---

## 1. 구조 (Architecture)

```
   ┌──────────────────────────────┐
   │  Claude Desktop / Claude Code │   ← 사용자가 자연어로 지시
   └───────────────┬──────────────┘
                   │  MCP (stdio)
                   ▼
   ┌──────────────────────────────┐
   │      rhino_mcp_server.py      │   ← Claude 가 실행하는 MCP 서버 (이 폴더)
   └───────────────┬──────────────┘
                   │  TCP JSON  127.0.0.1:1999
                   ▼
   ┌──────────────────────────────┐
   │       rhino_listener.py       │   ← Rhino 8 ScriptEditor 안에서 실행
   └───────────────┬──────────────┘
                   │  RhinoCommon / rhinoscriptsyntax
                   ▼
            ┌───────────────┐
            │  Rhino 8 문서  │
            └───────────────┘
```

- **`rhino_listener.py`** — Rhino 8 내부에서 도는 소켓 서버. 문서 조작은 Rhino
  메인 스레드(`RhinoApp.Idle` 이벤트)에서 안전하게 실행합니다.
- **`rhino_mcp_server.py`** — Claude 가 실행하는 MCP 서버. Claude 의 자연어 요청을
  도구 호출로 받아 리스너로 전달합니다.

---

## 2. 준비물 (Prerequisites)

| 항목 | 버전/비고 |
| --- | --- |
| Rhino 3D | **8** (새 ScriptEditor 의 Python 3 사용) |
| Python | **3.10 이상** (MCP 서버 실행용, Rhino 와 별개) |
| Claude | **Claude Desktop** 또는 **Claude Code** (MCP 지원) |

---

## 3. 설치 (Setup)

### 3-1. MCP 서버 의존성 설치

```bash
cd rhino_claude
pip install -r requirements.txt
```

### 3-2. Rhino 리스너 실행 (Rhino 안에서)

1. Rhino 8 을 실행합니다.
2. 명령창에 `_ScriptEditor` 를 입력해 스크립트 에디터를 엽니다.
3. `rhino_listener.py` 파일을 열고 **실행(Run ▶)** 합니다.
4. Rhino 명령 히스토리에 다음이 보이면 성공입니다:

   ```
   [Claude Bridge] listening on 127.0.0.1:1999
   ```

5. **Rhino 를 켜 둔 상태로** 두세요. (리스너가 계속 대기해야 합니다.)

> 중지하려면 ScriptEditor 에서 아래를 실행하거나 Rhino 를 종료합니다.
> ```python
> import rhino_listener
> rhino_listener.stop_bridge()
> ```

### 3-3. Claude 에 MCP 서버 등록

#### (A) Claude Desktop

`claude_desktop_config.example.json` 을 참고해 설정 파일에 추가합니다.

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "rhino": {
      "command": "python",
      "args": ["/절대/경로/rhino_claude/rhino_mcp_server.py"],
      "env": { "RHINO_HOST": "127.0.0.1", "RHINO_PORT": "1999" }
    }
  }
}
```

`args` 의 경로를 실제 절대경로로 바꾼 뒤 Claude Desktop 을 **재시작**합니다.
채팅창 도구 아이콘에 `rhino` 서버가 보이면 준비 완료입니다.

#### (B) Claude Code (CLI)

```bash
claude mcp add rhino -- python /절대/경로/rhino_claude/rhino_mcp_server.py
```

`uv` 를 쓴다면:

```bash
claude mcp add rhino -- uv run --with "mcp[cli]" python /절대/경로/rhino_claude/rhino_mcp_server.py
```

---

## 4. 사용 예시 (Usage)

Claude 에게 자연어로 요청하면 됩니다.

- "지금 Rhino 문서 상태 알려줘." → `get_document_info`
- "원점에 한 변 10인 정육면체를 만들어줘." → `create_box`
- "반지름 5인 구 3개를 x축으로 15씩 띄워서 나란히 만들어줘."
- "방금 만든 구들을 빨간색으로 바꾸고 'balls' 레이어로 옮겨줘."
- "뷰 전체가 보이게 줌하고 뷰포트를 캡처해서 보여줘." → `zoom_extents` + `capture_viewport`
- "두 박스를 불리언 유니온 해줘." → 필요하면 Claude 가 `execute_rhinoscript` 로 처리합니다.

---

## 5. 제공 도구 (Tools)

| 도구 | 설명 |
| --- | --- |
| `get_document_info` | 문서 이름/경로/객체 수/단위/레이어 조회 |
| `list_objects` | 모든 객체(id·종류·레이어·이름) 나열 |
| `get_selected_objects` | 현재 선택된 객체 조회 |
| `create_point` / `create_line` / `create_polyline` / `create_curve` | 점·선·폴리라인·곡선 |
| `create_circle` / `create_rectangle` | 원·사각형 |
| `create_sphere` / `create_box` / `create_cylinder` / `create_cone` | 구·박스·원기둥·원뿔 |
| `add_text_dot` | 텍스트 주석 점 |
| `move_object` / `copy_object` / `rotate_object` / `scale_object` | 이동·복사·회전·스케일 |
| `delete_object` | 삭제 |
| `set_object_color` / `set_object_name` / `set_object_layer` | 색·이름·레이어 지정 |
| `create_layer` | 레이어 생성 |
| `select_objects` / `clear_selection` | 선택 / 선택 해제 |
| `zoom_extents` | 전체 보기 |
| `capture_viewport` | 뷰포트 PNG 캡처 (Claude 가 결과를 눈으로 확인) |
| `execute_rhinoscript` | 임의 Python(rhinoscriptsyntax) 코드 실행 (고급) |

좌표는 모두 `[x, y, z]`, 색은 `0~255` RGB, 각도는 도(degree) 단위입니다.

---

## 6. 문제 해결 (Troubleshooting)

- **"Rhino 리스너에 연결할 수 없습니다"**
  → Rhino 8 이 켜져 있고 `rhino_listener.py` 를 실행했는지, 명령창에
  `listening on 127.0.0.1:1999` 가 떴는지 확인하세요.

- **포트 충돌 / 다른 포트 사용**
  → `rhino_listener.py` 상단의 `PORT` 값과, MCP 서버의 환경변수 `RHINO_PORT`
  를 같은 값으로 맞추세요.

- **명령이 반응이 느리거나 멈춤**
  → 문서 조작은 Rhino 가 유휴(idle) 상태일 때 처리됩니다. Rhino 에서 다른
  명령이 실행 중이면(예: 진행 중인 드래그) 끝난 뒤 처리됩니다.

- **Claude Desktop 에 rhino 서버가 안 보임**
  → `args` 경로가 절대경로인지, `python` 이 PATH 에 있는지 확인하고 Claude
  Desktop 을 재시작하세요.

---

## 7. 보안 주의 (Security)

`execute_rhinoscript` / `execute_python` 도구는 **Rhino 프로세스 안에서 임의의
Python 코드를 실행**합니다. 매우 강력하지만 그만큼 위험할 수 있으니, 신뢰할 수
있는 작업에만 사용하세요. 리스너는 기본적으로 `127.0.0.1`(로컬)에서만
연결을 받습니다 — 외부에 노출하지 마세요.

---

## 8. English summary

This is an **MCP integration that connects Rhino 3D 8 to Claude**. Run
`rhino_listener.py` inside Rhino 8's ScriptEditor (Python 3) to start a local
socket server on `127.0.0.1:1999`; document edits are marshalled to Rhino's main
thread via the `RhinoApp.Idle` event. Register `rhino_mcp_server.py` as an MCP
server in Claude Desktop or Claude Code (`pip install -r requirements.txt`
first). Claude can then create/edit geometry, manage layers, and capture the
viewport by natural language. `execute_rhinoscript` runs arbitrary
rhinoscriptsyntax code for anything the dedicated tools don't cover — use it
only with trusted input. The listener binds to localhost only; don't expose it.
