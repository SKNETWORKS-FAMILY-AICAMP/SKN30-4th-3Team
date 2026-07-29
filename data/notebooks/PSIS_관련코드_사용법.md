[PSIS 농약 데이터 전체 실행 순서]

■ 선행 조건

1. 저장소 루트의 .env에 PSIS API 키가 있어야 합니다.

   PSIS_API_KEY=발급받은_API_KEY

2. NCPMS 병·해충 수집 결과가 준비되어 있어야 합니다.

   data/interim/all_NCPMS/

3. Supabase에 이미 등록된 추가 식물 목록이 준비되어 있어야 합니다.

   data/notebooks/supabase_supplement_targets.jsonl

4. unmatched 식물의 검색 후보를 수정하려면 다음 파일을 사용합니다.

   data/notebooks/unmatched_alias_candidates.jsonl


■ 1단계: SVC01 농약 등록 목록 수집

NCPMS 병·해충 식물과 Supabase 추가 식물을 기준으로 농약 등록정보를
수집합니다.

PowerShell:

python data/notebooks/PSIS_from_NCPMS.py `
  --ncpms-only `
  --already-exist-in-supabase `
  --ncpms-discovery none `
  --page-size 99 `
  --delay 0 `
  --list-only

주요 결과:

data/interim/all_PSIS/pesticide_registration_list.jsonl
data/interim/all_PSIS/crop_pesticide_relations.jsonl
data/interim/all_PSIS/crops.jsonl
data/interim/all_PSIS/ncpms_psis_crop_mapping.jsonl

--list-only는 SVC01만 호출하며 SVC02는 호출하지 않습니다.


■ 2단계: unmatched 식물 후보 확인

python data/notebooks/PSIS_unmatched_candidates.py --build

python data/notebooks/PSIS_unmatched_candidates.py --list

검색 후보는 다음 파일에서 직접 수정할 수 있습니다.

data/notebooks/unmatched_alias_candidates.jsonl

형식:

{"name":"치콘","candidates":["치커리","엔다이브"]}


■ 3단계: unmatched 후보로 SVC01 재검색

python data/notebooks/PSIS_unmatched_candidates.py `
  --search `
  --page-size 99 `
  --delay 0

검색 결과가 발견된 식물은 기존 all_PSIS 결과에 자동으로 병합되고,
unmatched 후보 파일에서 제거됩니다.

필요하면 후보 이름을 수정한 뒤 같은 명령을 반복 실행합니다.

상태 확인:

python data/notebooks/PSIS_unmatched_candidates.py --list


■ 4단계: SVC02 실행 설정 확인

SVC02를 실제 호출하기 전에 입력·출력 경로를 확인합니다.

python data/notebooks/PSIS_from_NCPMS.py `
  --details-only `
  --resume `
  --delay 0.1 `
  --dry-run

확인할 값:

details_input:
data/interim/all_PSIS/pesticide_registration_list.jsonl

details_output:
data/interim/all_PSIS/pesticide_registration_details.jsonl

details_scope:
product


■ 5단계: SVC02 제품 상세·독성정보 수집

python data/notebooks/PSIS_from_NCPMS.py `
  --details-only `
  --resume `
  --delay 0.1

기본적으로 고유 pesti_code마다 대표 등록정보 하나를 이용하여 SVC02를
호출합니다.

수집 대상은 약 1,481개 제품입니다.

수집 정보:

- 유효성분 함량
- 독성 코드
- 독성 구분명
- 어독성 구분

결과:

data/interim/all_PSIS/pesticide_registration_details.jsonl
data/interim/all_PSIS/details_summary.json

중간에 종료되면 같은 명령을 다시 실행합니다. --resume을 사용하므로 이미
수집된 제품은 건너뜁니다.


■ 6단계: SVC01과 SVC02 최종 병합

python data/notebooks/PSIS_new_table.py

병합 방식:

- 사용방법은 SVC01 등록정보 사용
- 독성·어독성·성분 함량은 SVC02 제품정보 사용
- SVC02는 pesti_code 기준으로 병합
- 작물 코드 + 대상 병해충 + 용도 기준으로 그룹화
- 중복 등록정보 제거
- 식물 별칭은 crop_or_plant 리스트에 저장

최종 결과:

data/interim/all_PSIS/PSIS_result/psis_pesticide_groups.jsonl

통계:

data/interim/all_PSIS/PSIS_result/summary.json


■ 전체 명령 순서 요약

1. SVC01 기본 수집

python data/notebooks/PSIS_from_NCPMS.py `
  --ncpms-only `
  --already-exist-in-supabase `
  --ncpms-discovery none `
  --page-size 99 `
  --delay 0 `
  --list-only

2. unmatched 목록 생성·확인

python data/notebooks/PSIS_unmatched_candidates.py --build
python data/notebooks/PSIS_unmatched_candidates.py --list

3. unmatched 후보 검색

python data/notebooks/PSIS_unmatched_candidates.py `
  --search `
  --page-size 99 `
  --delay 0

4. SVC02 제품 상세 수집

python data/notebooks/PSIS_from_NCPMS.py `
  --details-only `
  --resume `
  --delay 0.1

5. 최종 JSONL 생성

python data/notebooks/PSIS_new_table.py


■ 작업 완료 후 최종 확인 파일

SVC01 목록:

data/interim/all_PSIS/pesticide_registration_list.jsonl

SVC02 상세:

data/interim/all_PSIS/pesticide_registration_details.jsonl

SVC02 진행 상태:

data/interim/all_PSIS/details_summary.json

최종 DB 적재 파일:

data/interim/all_PSIS/PSIS_result/psis_pesticide_groups.jsonl

최종 가공 통계:

data/interim/all_PSIS/PSIS_result/summary.json