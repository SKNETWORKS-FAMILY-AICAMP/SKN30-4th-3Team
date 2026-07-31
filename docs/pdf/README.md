# Farm하니 v2 제출 문서 PDF 변환

`docs/` 아래 5개 v2 산출물 마크다운을 제출용 PDF로 변환하는 스크립트와 결과물을 보관합니다.

## 결과물 (A4 가로 · 한글 폰트 · 표지 · 페이지 번호)

| PDF | 원본 |
|---|---|
| `Farm하니_v2_요구사항_정의서.pdf` | `../Farm하니_v2_요구사항_정의서.md` |
| `Farm하니_v2_화면설계서.pdf` | `../Farm하니_v2_화면설계서.md` |
| `Farm하니_v2_시스템_구성도.pdf` | `../Farm하니_v2_시스템_구성도.md` |
| `Farm하니_v2_통합테스트_계획서.pdf` | `../Farm하니_v2_통합테스트_계획서.md` |
| `Farm하니_v2_통합테스트_결과보고서.pdf` | `../Farm하니_v2_통합테스트_결과보고서.md` |

## 재생성 방법

```bash
# docs/pdf 폴더에서
npm install markdown-it puppeteer-core
node build_pdf.mjs
```

- **필요 조건**: Node.js 18+, 그리고 Chrome 또는 Edge(헤드리스 렌더링에 사용). 자동 탐지하며, 경로가 다르면 `CHROME_PATH` 환경변수로 지정합니다.
- **폰트**: 본문 폰트 스택은 `Pretendard → Noto Sans KR → Malgun Gothic` 순서입니다. Pretendard/Noto가 설치돼 있지 않으면 Windows 기본 한글 폰트인 **Malgun Gothic**으로 렌더링됩니다(현재 결과물 기준).
- **레이아웃**: A4 가로, 여백 상 15mm / 하 14mm / 좌·우 13mm, 표는 행 중간 분할 방지, 이미지는 본문 폭 이내로 자동 축소, 첫 메타데이터 표까지가 표지 페이지, 이후 각 대단원(`##`)이 새 페이지에서 시작합니다.
- **주의**: `npm install`로 생성되는 `node_modules/`, `package-lock.json`은 저장소에 커밋하지 않는 것을 권장합니다.

## 변환 시 확인 사항

- [ ] 모든 이미지(SVG 구성도·PNG 화면 캡처)가 누락 없이 렌더링됨
- [ ] 넓은 표의 마지막 열이 페이지 밖으로 잘리지 않음
- [ ] 한글이 깨지지 않고 출력됨
- [ ] 배포 URL·출처 URL이 클릭 가능한 링크로 유지됨
- [ ] 실제 이메일·토큰·API Key 등 민감정보가 본문·이미지에 없음
