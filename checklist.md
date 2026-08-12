# 심박존 체류 시간 코치 반영 — 체크리스트

## 배경
평균 페이스 한 줄(`10.0km @ 5:10/km`)로는 인터벌 자극이 뭉개져서 코치가 강도를 오판한다.
심박존 체류 시간을 붙여 자극의 실제 크기를 노출한다.

## 작업

- [x] 1. `garmin_client.py` — 존 조회·캐시 추가
  - [x] `GarminClient.fetch_hr_zones(activity_id)` — 실패 시 None
  - [x] `load_hr_zones()` / `save_hr_zones()` — `data/hr_zones.json`
  - verify: 로컬 문법 검사 통과, EC2에서 캐시 파일 생성 확인

- [x] 2. `scheduler.py` — 새 런 감지 시 존 데이터 1회 수집
  - [x] `check_new_runs`에서 미캐시 활동만 조회 후 저장
  - verify: 코치 분석 메시지 생성 전에 캐시가 채워지는 순서인지 확인

- [x] 3. `coach.py` — 컨텍스트에 존 요약 한 줄 추가
  - [x] `_zone_summary()` 헬퍼
  - [x] `build_fitness_context`의 러닝 목록에 하위 라인으로 삽입
  - [x] 시스템 프롬프트에 "존 체류 시간을 평균 페이스보다 우선" 원칙 추가
  - verify: 컨텍스트 출력에 `Z4 33.9분` 라인이 보이는지 확인

- [x] 4. `backfill_hr_zones.py` — 기존 활동 백필
  - [x] 최근 활동 한정, 호출 간 대기, 429 시 중단
  - verify: 실행 후 캐시 건수 증가 확인

- [x] 5. 배포·검증
  - [x] rsync → EC2, 봇 재시작
  - [x] `build_fitness_context` 출력에 존 라인 확인
  - [x] 커밋·푸시

## 남은 것
- 없음
