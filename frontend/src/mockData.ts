import type { Garden, Plant, PlantCareChatResponse } from "./types";

export const mockPlants: Plant[] = [
  {
    id: "d3b07384-d113-49c3-a558-1ec114a84d41",
    name: "몬티",
    species: "Monstera deliciosa",
    location: "거실 창가",
    sunlight: "밝은 간접광",
    createdAt: "2026-06-01T12:00:00Z",
    healthScore: 92,
    moisture: "적정",
    nextTask: "내일 오전 흙 수분 확인",
    imageUrl:
      "https://images.unsplash.com/photo-1614594975525-e45190c55d0b?auto=format&fit=crop&w=1200&q=80"
  },
  {
    id: "e4c18495-e224-5aa4-b669-2fd225b95e52",
    name: "상추 텃밭",
    species: "Lactuca sativa",
    location: "베란다 화분",
    sunlight: "오전 직사광선",
    createdAt: "2026-06-10T09:30:00Z",
    healthScore: 78,
    moisture: "건조 주의",
    nextTask: "오늘 저녁 관수",
    gardenId: "garden-veranda",
    imageUrl:
      "https://images.unsplash.com/photo-1622205313162-be1d5712a43d?auto=format&fit=crop&w=1200&q=80"
  },
  {
    id: "mock-ficus",
    name: "홍바오",
    species: "Ficus elastica",
    location: "거실 안쪽",
    sunlight: "반음지",
    createdAt: "2026-06-16T08:00:00Z",
    healthScore: 84,
    moisture: "약간 건조",
    nextTask: "잎 먼지 닦기",
    imageUrl:
      "https://images.unsplash.com/photo-1598880940080-ff9a29891b85?auto=format&fit=crop&w=1200&q=80"
  }
];

export const mockChatResponse: PlantCareChatResponse = {
  summary: "잎 끝 마름과 하엽 황화가 관찰됩니다. 현재는 과습 확정보다 수분 부족, 강한 빛, 통풍 부족 가능성을 함께 점검해야 합니다.",
  possibleCauses: [
    "최근 관수 간격이 길어져 토양 하부 수분이 부족했을 가능성",
    "오후 직사광선에 의한 잎끝 스트레스",
    "환기 부족으로 인한 잎 표면 증산 균형 저하"
  ],
  todayActions: [
    "흙 표면 2~3cm 아래 수분을 손가락으로 확인",
    "흙이 말랐다면 배수구로 물이 빠질 정도로 충분히 관수",
    "오후 직사광선은 피하고 창가에서 40~60cm 안쪽으로 이동",
    "노랗게 마른 하엽은 깨끗한 가위로 제거"
  ],
  observationChecklist: [
    "새 잎까지 노랗게 변하는지",
    "줄기 밑동이 물러지거나 냄새가 나는지",
    "잎 뒷면에 해충 흔적이 있는지"
  ],
  citations: [
    {
      sourceId: "nongsaro_indoor_water",
      title: "농사로 실내식물 물관리 자료",
      publisher: "농촌진흥청/농사로",
      url: "https://www.nongsaro.go.kr/"
    },
    {
      sourceId: "nihhs_indoor_garden",
      title: "실내정원 유지관리 자료",
      publisher: "국립원예특작과학원"
    },
    {
      sourceId: "ncpms_late_blight",
      title: "감자·토마토 역병 발생생태 및 방제",
      publisher: "국가농작물병해충관리시스템(NCPMS)",
      url: "https://ncpms.rda.go.kr/"
    },
    {
      sourceId: "psis_mancozeb",
      title: "만코제브 수화제 안전사용기준",
      publisher: "농약안전정보시스템(PSIS)",
      url: "https://psis.rda.go.kr/"
    }
  ],
  safetyNotice: "이 결과는 공식 자료 기반 관리 가이드이며 병해충 확정 진단이 아닙니다. 증상이 악화되면 전문가 확인이 필요합니다.",
  pestDiagnosis: [
    {
      candidate: "역병(疫病)",
      kind: "disease",
      confidence: 0.62,
      rationale: "잎 뒷면 수침상 병반과 흰 곰팡이 흔적이 관찰됩니다. 가지과에서 습할 때 흔한 증상입니다.",
      sourceId: "ncpms_late_blight"
    },
    {
      candidate: "진딧물",
      kind: "pest",
      confidence: 0.28,
      rationale: "잎 뒷면 끈적임 가능성 — 추가 관찰이 필요합니다.",
      sourceId: "ncpms_late_blight"
    }
  ],
  pesticideGuidance: [
    {
      name: "만코제브 수화제",
      targetPests: ["역병"],
      targetCrops: ["감자", "토마토"],
      dilution: "500배",
      phiDays: 7,
      maxApplications: 3,
      regNo: "PIS-2024-0000",
      safetyNote: "수확 7일 전까지만 사용하고 방제복·마스크를 착용하세요. 표시사항의 적용대상·사용시기를 반드시 확인하세요.",
      sourceId: "psis_mancozeb"
    }
  ]
};

// 텃밭(구획) mock — 단일 식물 관리에서 텃밭 단위로 확장하기 위한 데모 데이터
export const mockGardens: Garden[] = [
  {
    id: "garden-veranda",
    name: "베란다 텃밭",
    location: "남향 베란다",
    description: "상추·방울토마토 위주의 소규모 텃밭",
    sunlight: "오전 직사광선",
    soilType: "상토 + 마사토",
    plantCount: 3,
    createdAt: "2026-06-05T09:00:00Z",
    imageUrl: null,
    cultivationType: "mixed",
    representativeCrop: "방울토마토"
  },
  {
    id: "garden-rooftop",
    name: "옥상 텃밭",
    location: "옥상",
    description: "고추·가지 등 여름 작물 재배",
    sunlight: "종일 직사광선",
    soilType: "밭흙",
    plantCount: 5,
    createdAt: "2026-06-20T08:00:00Z",
    imageUrl: null,
    cultivationType: "mixed",
    representativeCrop: "고추"
  }
];
