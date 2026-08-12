# Architecture

## Phase 1: mirror

```text
Cursor local data (read only)
  ├─ conversation-search.db ─┐
  ├─ state.vscdb ────────────┼─> CursorStore ─> HTTP JSON API ─> Web UI
  └─ agent transcripts ──────┘                  └─ SSE change events

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

미러링 범위:

- 로컬 transcript에 보존된 사용자/assistant 텍스트와 도구 호출 입력은 순서대로 렌더링합니다.
- Cursor 내부 transport의 `timestamp`, `user_query`, `image_files` 래퍼는 Agent Window에 맞게 정리합니다.
- 첨부 파일은 파일명 chip으로 표시하되 임의 로컬 파일을 HTTP로 제공하지 않습니다.
- Cursor가 transcript에 보존하지 않은 일시적 streaming delta나 확장된 도구 stdout은 복원하지 않습니다.
- `cloud-cache` 항목에 로컬 transcript가 없으면 이를 빈 대화로 가장하지 않고 명시합니다.

## Phase 2: control bridge

Phase 2는 Phase 1 프로세스와 분리하는 것이 안전합니다.

```text
Browser composer
  └─ authenticated command API
       └─ version adapter
            └─ loopback-only CDP
                 └─ Cursor Agent Window UI
```

필수 불변 조건:

1. Cursor SQLite 또는 transcript에 메시지를 직접 쓰지 않습니다.
2. CDP 포트를 LAN이나 tailnet에 노출하지 않습니다.
3. 요청받은 session ID와 실제 활성 UI session ID가 다르면 전송을 중단합니다.
4. 한 세션에는 한 번에 하나의 전송만 허용합니다.
5. 지원하지 않는 Cursor 버전에서는 읽기 전용으로 자동 강등합니다.
6. Tailscale 연결만으로 쓰기 권한을 추론하지 않고 명시적 사용자 allowlist를 적용합니다.

Phase 2의 완료 기준은 “메시지를 한 번 보낼 수 있음”이 아니라, 세션 오발송과 중복 전송을 자동 테스트로 막고 Cursor 업데이트 시 안전하게 비활성화되는 것입니다.
