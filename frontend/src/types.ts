export type View = "dashboard" | "add" | "detail" | "chat";

export type Plant = {
  id: string;
  name: string;
  species?: string | null;
  displaySpecies?: string;
  location?: string | null;
  sunlight?: string | null;
  createdAt: string;
  imageUrl?: string;
  healthScore?: number;
  moisture?: string;
  nextTask?: string;
  gardenId?: string | null; // 소속 텃밭 (텃밭 확장)
};

// 텃밭(여러 작물을 담는 구획) — 단일 식물 관리에서 확장
export type Garden = {
  id: string;
  name: string;
  location?: string | null; // 텃밭 위치 (베란다, 옥상 등)
  description?: string | null;
  sunlight?: string | null; // 구획 공유 일조 환경
  soilType?: string | null; // 토양 종류
  plantCount?: number; // 소속 작물 수
  createdAt: string;
  imageUrl?: string | null;
  cultivationType: "single" | "mixed";
  representativeCrop?: string | null;
};

export type CareLog = {
  id: string;
  plantId: string;
  wateredAt?: string | null;
  leafCondition?: string | null;
  soilCondition?: string | null;
  memo?: string | null;
  createdAt: string;
};

export type PlantPhoto = {
  id: string;
  plantId: string;
  storagePath: string;
  capturedAt?: string | null;
  note?: string | null;
  createdAt: string;
};

export type GardenPhoto = {
  id: string;
  gardenId: string;
  storagePath: string;
  capturedAt?: string | null;
  note?: string | null;
  createdAt: string;
};

export type PlantCatalogItem = {
  id: string;
  name: string;
  species: string;
  familyName?: string | null;
  description?: string | null;
};

export type UploadSignedUrlResponse = {
  signedUrl: string;
  storagePath: string;
};

export type ChatResponseMode = "expert" | "companion";
export type ChatFeedbackRating = "helpful" | "not_helpful" | "unsafe" | "irrelevant";

export type ChatMemoryMessage = {
  role: "user" | "assistant";
  content: string;
};

// 병해충 진단 후보 (도메인 확장: 이미지/증상 기반, "가능성" 톤 유지)
export type PestDiagnosis = {
  candidate: string; // 병해충 후보명 (예: 역병, 진딧물)
  kind?: "disease" | "pest"; // 병 / 해충
  confidence: number; // 0~1 확신도
  rationale?: string; // 판단 근거 요약
  sourceId?: string; // 근거 문서 id (citations와 연결)
};

// 농약 안전사용 안내 (도메인 확장: 반드시 공식 출처 근거 기반)
export type PesticideGuidance = {
  name: string; // 약제/성분명
  targetPests?: string[]; // 대상 병해충
  targetCrops?: string[]; // 적용 작물
  dilution?: string; // 희석배수 (예: 1000배)
  phiDays?: number; // 수확 전 마지막 사용 가능일수 (PHI)
  maxApplications?: number; // 최대 사용 횟수
  regNo?: string; // 농약 등록번호
  safetyNote?: string; // 안전 사용 주의
  sourceId?: string; // 근거 문서 id (근거 필수 원칙)
};

export type PlantCareChatResponse = {
  summary: string;
  possibleCauses: string[];
  todayActions: string[];
  observationChecklist: string[];
  citations: {
    sourceId: string;
    title: string;
    url?: string;
    publisher?: string;
    excerpt?: string;
    section?: string;
  }[];
  safetyNotice?: string;
  sessionId?: string;
  messageId?: string;
  // 도메인 확장 — 선택적(하위호환). 백엔드가 채우면 프론트가 자동 렌더.
  pestDiagnosis?: PestDiagnosis[];
  pesticideGuidance?: PesticideGuidance[];
};

export type ChatProgressEvent = {
  step: number;
  total: number;
  node: string;
  label: string;
};

export type ChecklistTask = {
  id: string;
  plantId: string;
  plantName: string;
  taskType: "water" | "observe" | "photo";
  title: string;
  description: string;
  done: boolean;
};

export type WateringReminder = {
  plantId: string;
  name: string;
  species?: string | null;
  lastWateredAt?: string | null;
  daysSinceWatered?: number | null;
  intervalDays: number;
  status: "due" | "upcoming" | "ok" | "unknown";
};

export type ChatModelInfo = {
  chatModel: string;
  visionModel: string;
};

export type ChatSession = {
  id: string;
  userId: string;
  plantId?: string | null;
  gardenId?: string | null;
  title?: string | null;
  createdAt: string;
};

export type ChatFeedbackItem = {
  messageId: string;
  rating: ChatFeedbackRating;
  comment?: string | null;
};

export type SessionFeedbackStats = {
  sessionId: string;
  title?: string | null;
  helpful: number;
  notHelpful: number;
  unsafe: number;
  irrelevant: number;
  total: number;
};

export type ChatMessage = {
  id: string;
  sessionId: string;
  sender: "user" | "assistant";
  content: string;
  citations?: PlantCareChatResponse["citations"];
  createdAt: string;
};
