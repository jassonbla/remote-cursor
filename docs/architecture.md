# Architecture

## Phase 1: mirror

```text
Cursor local data (read only)
  ├─ conversation-search.db ─┐
  ├─ state.vscdb ────────────┼─> CursorStore ─> HTTP JSON API ─> Web UI
  └─ agent transcripts ──────┘                  └─ OS watcher ─> Event broker ─> SSE change events

127.0.0.1:4310 <─ Tailscale Serve HTTPS <─ allowed tailnet device
```

보안 경계:

- 애플리케이션은 localhost에만 바인딩합니다.
- 조직 tailnet에서는 `REMOTE_CURSOR_ALLOWED_USERS`로 Serve identity header를 검증합니다.
- SQLite는 URI `mode=ro`로 열고 `query_only`를 설정합니다.
- transcript와 metadata는 파일 읽기만 수행합니다.
- 쓰기 HTTP 메서드는 모두 거절합니다.
- CSP는 자체 script/style/connect만 허용하고 iframe embedding을 차단합니다.
- 인터넷 CDN, analytics, telemetry를 사용하지 않습니다.

성능 경계:

- 브라우저별 polling은 사용하지 않습니다. 하나의 `CursorChangeMonitor`가 Cursor 파일 변경을 기다리고 모든 SSE 연결에 fan-out합니다.
- macOS에서는 `kqueue` vnode 이벤트를 사용하므로 Cursor가 유휴 상태일 때 주기적 transcript 순회가 없습니다. 다른 OS에서는 하나의 낮은 빈도 fallback watcher만 사용합니다.
- SSE에는 증가하는 event ID가 붙고, 브라우저는 `Last-Event-ID`로 짧은 연결 단절 이후의 이벤트를 replay할 수 있습니다.
- background 데이터 갱신은 skeleton을 표시하지 않습니다. 목록 또는 선택된 대화의 직렬화된 revision이 달라질 때만 해당 DOM을 교체합니다.
- 유지 heartbeat는 15초마다 전송되지만 UI 데이터 reload를 만들지 않습니다.

미러링 범위:

- 로컬 transcript에 보존된 사용자/assistant 텍스트와 도구 호출 입력은 순서대로 렌더링합니다.
- Cursor 내부 transport의 `timestamp`, `user_query`, `image_files` 래퍼는 Agent Window에 맞게 정리합니다.
- 첨부 파일은 파일명 chip으로 표시하되 임의 로컬 파일을 HTTP로 제공하지 않습니다.
- Cursor가 transcript에 보존하지 않은 일시적 streaming delta나 확장된 도구 stdout은 복원하지 않습니다.
- `cloud-cache` 항목에 로컬 transcript가 없으면 이를 빈 대화로 가장하지 않고 명시합니다.

## Phase 2: control bridge

Phase 2의 대상은 Cursor IDE 전체가 아니라 Cursor Agent Window에 열린 로컬 Cursor Agents 세션입니다. 에디터, 파일 탐색기, 터미널, diff, 확장 기능, 내장 브라우저 제어는 범위 밖입니다.

Phase 2는 Cursor의 로컬 Desktop Bridge를 우선 사용하고, 제어 경계는 Phase 1의 read-only data reader와 분리합니다.

```text
Browser composer
  └─ authenticated command API
       └─ DesktopBridgeClient
            └─ cursor desktop CLI
                 └─ local Unix socket / Windows named pipe
                      └─ Cursor Agent Window
```

Desktop Bridge를 우선하는 이유:

- Cursor 3.15.6에는 `composer.desktopBridge.listThreads`와 `composer.desktopBridge.sendMessage`가 포함되어 있고, 실제 Agent Window thread에 메시지를 전달합니다.
- Cursor가 discovery, 로컬 인증 token, 다중 window, 전송 상태를 관리하므로 DOM 클릭이나 foreground focus가 필요하지 않습니다.
- `cursor-agent --resume`는 Cursor CLI Agent의 별도 session store를 재개하므로 Agent Window의 동일 세션을 제어하는 수단으로 사용하지 않습니다.
- CDP 자동화는 Cursor의 내부 DOM과 focus에 의존하므로 기본 경로가 아닌 실험적 fallback으로만 고려합니다.

Desktop Bridge는 Cursor Settings의 Beta 기능이므로 사용자가 활성화하고 Cursor를 재시작해야 합니다. 기능이 없거나 비활성화됐거나 protocol/version이 지원되지 않으면 composer를 비활성화하고 Phase 1 read-only 상태로 자동 강등합니다.

### 구현 순서

1. `cursor desktop ls --json`으로 bridge capability를 확인합니다.
2. Phase 1 session ID와 Desktop Bridge thread ID가 완전히 일치하는 세션만 전송 가능 상태로 표시합니다.
3. `POST /api/conversations/{id}/messages`는 shell 없이 `cursor desktop send <exact-id> --stdin --json`을 실행합니다.
4. 응답의 `submitted`, `queued`, `unknown-thread`, `not-sendable`, `timeout` 상태를 UI에 명시적으로 반영합니다.
5. 전송 후 transcript mirror에서 사용자 메시지가 확인될 때까지 pending 상태를 유지합니다.

`listThreads`의 `running` 상태는 사이드바와 열린 대화의 응답 생성 표시에도 사용합니다. 상태는 별도의 read-only API로 짧게 cache하여 조회하고, transcript의 `turn_ended`는 완료 표시를 보조할 뿐 실행 상태의 기준으로 추측하지 않습니다.

필수 불변 조건:

1. Cursor SQLite 또는 transcript에 메시지를 직접 쓰지 않습니다.
2. Desktop Bridge의 socket, discovery token 또는 discovery file을 HTTP로 노출하지 않습니다.
3. 요청받은 session ID와 Desktop Bridge의 전체 thread ID가 다르면 전송을 중단합니다.
4. 한 세션에는 한 번에 하나의 전송만 허용하고 idempotency key로 중복 요청을 차단합니다.
5. `timeout`은 실제 전송 여부가 불명확하므로 자동 재시도하지 않습니다.
6. 지원하지 않는 Cursor 버전에서는 읽기 전용으로 자동 강등합니다.
7. Tailscale 연결만으로 쓰기 권한을 추론하지 않고 별도의 명시적 control allowlist를 적용합니다.
8. 초기 구현은 text follow-up만 지원하고 force interrupt, tool approval, 첨부 파일, 음성, model/mode 변경은 허용하지 않습니다.

Phase 2의 완료 기준은 “메시지를 한 번 보낼 수 있음”이 아니라, 세션 오발송과 중복 전송을 자동 테스트로 막고 Cursor 업데이트 시 안전하게 비활성화되는 것입니다.
