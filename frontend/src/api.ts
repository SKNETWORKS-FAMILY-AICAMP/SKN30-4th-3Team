import { mockChatResponse, mockGardens, mockPlants } from "./mockData";
import type {
  CareLog,
  ChatFeedbackItem,
  ChatFeedbackRating,
  ChatLLMProvider,
  ChatMessage,
  ChatMemoryMessage,
  ChatModelInfo,
  ChatProgressEvent,
  ChatResponseMode,
  ChatSession,
  ChecklistTask,
  Garden,
  GardenPhoto,
  Plant,
  PlantCareChatResponse,
  PlantCatalogItem,
  PlantPhoto,
  OpenAIChatModel,
  SessionFeedbackStats,
  UploadSignedUrlResponse,
  WateringReminder
} from "./types";

const API_BASE_URL =
  import.meta.env.VITE_BACKEND_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "http://localhost:8000" : "");
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || "";
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || "";
const SUPABASE_STORAGE_BUCKET = import.meta.env.VITE_SUPABASE_STORAGE_BUCKET || "plant-photos";
const ENABLE_DEVELOPMENT_MOCKS = import.meta.env.MODE === "development" && import.meta.env.VITE_ENABLE_MOCKS === "true";

const ACCESS_TOKEN_KEY = "farmhani_access_token";
const REFRESH_TOKEN_KEY = "farmhani_refresh_token";
const LOCAL_PLANTS_KEY = "farmhani_local_plants";
const LOCAL_CARE_LOGS_KEY = "farmhani_local_care_logs";
const LOCAL_PLANT_PHOTOS_KEY = "farmhani_local_plant_photos";
const LOCAL_GARDENS_KEY = "farmhani_local_gardens";
const MAX_PHOTO_UPLOAD_BYTES = 8 * 1024 * 1024;
let refreshSessionPromise: Promise<string | undefined> | undefined;
const plantDisplaySpeciesCache = new Map<string, Promise<string | undefined>>();
const HANGUL_PATTERN = /[가-힣]/;

type RequestOptions = RequestInit & {
  auth?: boolean;
};

type AuthResponse = {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  user?: {
    id: string;
    email?: string;
  };
};

export class AuthRequiredError extends Error {
  constructor(message = "로그인이 필요합니다.") {
    super(message);
    this.name = "AuthRequiredError";
  }
}

export class FrontendConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "FrontendConfigError";
  }
}

export function isAuthRequiredError(error: unknown) {
  return error instanceof AuthRequiredError || (error instanceof Error && error.name === "AuthRequiredError");
}

export function hasSupabaseAuthConfig() {
  return Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
}

export function isDevelopmentMockMode() {
  return ENABLE_DEVELOPMENT_MOCKS;
}

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function hasAuthSession() {
  return ENABLE_DEVELOPMENT_MOCKS || Boolean(getAccessToken());
}

export function storagePathToPublicUrl(storagePath?: string | null) {
  if (!storagePath) return undefined;
  if (/^(https?:|blob:|data:)/.test(storagePath)) return storagePath;
  if (!SUPABASE_URL) return undefined;

  const bucketPrefix = `${SUPABASE_STORAGE_BUCKET}/`;
  const cleanPath = storagePath.replace(/^\/+/, "");
  const pathWithoutBucket = cleanPath.startsWith(bucketPrefix) ? cleanPath.slice(bucketPrefix.length) : cleanPath;
  const encodedPath = pathWithoutBucket
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");

  return `${SUPABASE_URL.replace(/\/$/, "")}/storage/v1/object/public/${encodeURIComponent(SUPABASE_STORAGE_BUCKET)}/${encodedPath}`;
}

function saveAuthSession(data: AuthResponse) {
  if (data.access_token) {
    localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
  }
  if (data.refresh_token) {
    localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token);
  }
}

async function refreshAuthSession(): Promise<string | undefined> {
  if (refreshSessionPromise) return refreshSessionPromise;

  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken || !hasSupabaseAuthConfig()) return undefined;

  const pendingRefresh = (async () => {
    let response: Response;
    try {
      response = await fetch(`${SUPABASE_URL.replace(/\/$/, "")}/auth/v1/token?grant_type=refresh_token`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: SUPABASE_ANON_KEY
        },
        body: JSON.stringify({ refresh_token: refreshToken })
      });
    } catch {
      throw new Error("인증 서버 연결이 원활하지 않습니다. 잠시 후 다시 시도해 주세요.");
    }

    if (response.status === 400 || response.status === 401) return undefined;
    if (!response.ok) {
      throw new Error("로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }

    const data = (await response.json()) as AuthResponse;
    if (!data.access_token) return undefined;
    saveAuthSession(data);
    return data.access_token;
  })();

  refreshSessionPromise = pendingRefresh;
  try {
    return await pendingRefresh;
  } finally {
    if (refreshSessionPromise === pendingRefresh) refreshSessionPromise = undefined;
  }
}

export function clearAuthSession() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

async function supabaseAuthRequest(path: string, body: unknown): Promise<AuthResponse> {
  if (!hasSupabaseAuthConfig()) {
    throw new FrontendConfigError("로그인 서비스 설정이 없습니다. 관리자에게 환경변수 설정을 요청해 주세요.");
  }

  const requestUrl = `${SUPABASE_URL.replace(/\/$/, "")}/auth/v1/${path}`;
  const requestInit: RequestInit = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: SUPABASE_ANON_KEY
    },
    body: JSON.stringify(body)
  };
  let response: Response;
  try {
    response = await fetch(requestUrl, requestInit);
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 250));
    response = await fetch(requestUrl, requestInit);
  }

  if (!response.ok) {
    const text = await response.text();
    let authMessage = text;
    try {
      const payload = JSON.parse(text) as { error_description?: string; msg?: string; message?: string };
      authMessage = payload.error_description || payload.msg || payload.message || text;
    } catch {
      // Plain-text errors are already suitable for the fallback below.
    }

    const normalizedMessage = authMessage.toLowerCase();
    if (normalizedMessage.includes("invalid login credentials")) {
      throw new Error("이메일 또는 비밀번호가 올바르지 않습니다.");
    }
    if (normalizedMessage.includes("email not confirmed")) {
      throw new Error("이메일 인증이 아직 완료되지 않았습니다. 받은 편지함을 확인해 주세요.");
    }
    if (response.status === 429) {
      throw new Error("로그인 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.");
    }
    throw new Error(authMessage || `로그인 요청에 실패했습니다. (${response.status})`);
  }

  const data = (await response.json()) as AuthResponse;
  saveAuthSession(data);
  return data;
}

export async function signInWithPassword(email: string, password: string) {
  const data = await supabaseAuthRequest("token?grant_type=password", { email, password });
  if (!data.access_token) {
    throw new AuthRequiredError("로그인 토큰을 받지 못했습니다. 이메일 인증 상태를 확인한 뒤 다시 로그인해 주세요.");
  }
  return data;
}

export async function signUpWithPassword(email: string, password: string) {
  return supabaseAuthRequest("signup", { email, password });
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!API_BASE_URL) {
    throw new FrontendConfigError("백엔드 연결 주소가 설정되지 않았습니다. VITE_BACKEND_URL을 확인해 주세요.");
  }
  const headers = new Headers(options.headers);
  const bodyIsFormData = options.body instanceof FormData;

  if (!bodyIsFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (options.auth !== false) {
    const token = getAccessToken();
    if (!token) {
      throw new AuthRequiredError();
    }
    headers.set("Authorization", `Bearer ${token}`);
  }

  const requestUrl = `${API_BASE_URL}${path}`;
  const requestInit = { ...options, headers };
  const method = (options.method || "GET").toUpperCase();

  async function sendWithTransientRetry() {
    let response: Response;
    try {
      response = await fetch(requestUrl, requestInit);
    } catch (error) {
      if (method !== "GET") throw error;
      await new Promise((resolve) => setTimeout(resolve, 220));
      response = await fetch(requestUrl, requestInit);
    }

    if (method === "GET" && response.status === 503) {
      await new Promise((resolve) => setTimeout(resolve, 320));
      response = await fetch(requestUrl, requestInit);
    }
    return response;
  }

  let response = await sendWithTransientRetry();
  if (options.auth !== false && response.status === 401) {
    const refreshedAccessToken = await refreshAuthSession();
    if (refreshedAccessToken) {
      headers.set("Authorization", `Bearer ${refreshedAccessToken}`);
      response = await sendWithTransientRetry();
    }
  }

  if (response.status === 401) {
    throw new AuthRequiredError("세션이 만료되었거나 로그인이 필요합니다.");
  }
  if (response.status === 403) {
    throw new Error("이 작업을 수행할 권한이 없습니다.");
  }

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `API request failed: ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function loadLocalPlants(): Plant[] {
  const stored = localStorage.getItem(LOCAL_PLANTS_KEY);
  if (!stored) return mockPlants;
  try {
    return JSON.parse(stored) as Plant[];
  } catch {
    return mockPlants;
  }
}

function saveLocalPlants(plants: Plant[]) {
  localStorage.setItem(LOCAL_PLANTS_KEY, JSON.stringify(plants));
}

function loadLocalGardens(): Garden[] {
  const stored = localStorage.getItem(LOCAL_GARDENS_KEY);
  if (!stored) return mockGardens;
  try {
    return JSON.parse(stored) as Garden[];
  } catch {
    return mockGardens;
  }
}

function saveLocalGardens(gardens: Garden[]) {
  localStorage.setItem(LOCAL_GARDENS_KEY, JSON.stringify(gardens));
}

function loadLocalCareLogs(): CareLog[] {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_CARE_LOGS_KEY) || "[]") as CareLog[];
  } catch {
    return [];
  }
}

function saveLocalCareLogs(logs: CareLog[]) {
  localStorage.setItem(LOCAL_CARE_LOGS_KEY, JSON.stringify(logs));
}

function loadLocalPlantPhotos(): PlantPhoto[] {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_PLANT_PHOTOS_KEY) || "[]") as PlantPhoto[];
  } catch {
    return [];
  }
}

function saveLocalPlantPhotos(photos: PlantPhoto[]) {
  localStorage.setItem(LOCAL_PLANT_PHOTOS_KEY, JSON.stringify(photos));
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")), { once: true });
    reader.addEventListener("error", () => reject(new Error("사진을 읽지 못했습니다.")), { once: true });
    reader.readAsDataURL(file);
  });
}

async function resolvePlantDisplaySpecies(species?: string | null): Promise<string | undefined> {
  const trimmedSpecies = species?.trim();
  if (!trimmedSpecies) return undefined;
  if (HANGUL_PATTERN.test(trimmedSpecies)) return trimmedSpecies;

  const cacheKey = trimmedSpecies.toLowerCase();
  const cached = plantDisplaySpeciesCache.get(cacheKey);
  if (cached) return cached;

  const pendingName = searchPlantCatalog(trimmedSpecies, 6)
    .then((items) => {
      const exactMatch = items.find((item) => item.species.trim().toLowerCase() === cacheKey);
      const matchedItem = exactMatch ?? items.find((item) => HANGUL_PATTERN.test(item.name));
      return matchedItem && HANGUL_PATTERN.test(matchedItem.name) ? matchedItem.name : undefined;
    })
    .catch(() => undefined);
  plantDisplaySpeciesCache.set(cacheKey, pendingName);
  return pendingName;
}

async function addPlantDisplaySpecies<T extends Plant>(plants: T[]): Promise<T[]> {
  return Promise.all(
    plants.map(async (plant) => ({
      ...plant,
      displaySpecies: await resolvePlantDisplaySpecies(plant.species)
    }))
  );
}

export async function getPlants(): Promise<Plant[]> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    return addPlantDisplaySpecies(loadLocalPlants());
  }
  const plants = await request<Plant[]>("/api/v1/plants");
  return addPlantDisplaySpecies(plants);
}

// 텃밭(구획) 목록 — 도메인 확장(백엔드 미구현, mock/계약 우선)
export async function listGardens(): Promise<Garden[]> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    return loadLocalGardens();
  }
  return request<Garden[]>("/api/v1/gardens");
}

export async function createGarden(
  input: Pick<Garden, "name"> & Partial<Pick<Garden, "location" | "description" | "sunlight" | "soilType" | "cultivationType" | "representativeCrop">>
): Promise<Garden> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const garden: Garden = {
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
      cultivationType: input.cultivationType || "mixed",
      ...input
    };
    saveLocalGardens([garden, ...loadLocalGardens()]);
    return garden;
  }
  return request<Garden>("/api/v1/gardens", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function updateGarden(
  gardenId: string,
  input: Partial<Pick<Garden, "name" | "location" | "description" | "sunlight" | "soilType" | "imageUrl" | "cultivationType" | "representativeCrop">>
): Promise<Garden> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const gardens = loadLocalGardens();
    const nextGardens = gardens.map((garden) => (garden.id === gardenId ? { ...garden, ...input } : garden));
    saveLocalGardens(nextGardens);
    const updated = nextGardens.find((garden) => garden.id === gardenId);
    if (!updated) throw new Error("수정할 텃밭을 찾지 못했습니다.");
    return updated;
  }
  return request<Garden>(`/api/v1/gardens/${gardenId}`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export async function deleteGarden(gardenId: string): Promise<void> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    saveLocalGardens(loadLocalGardens().filter((garden) => garden.id !== gardenId));
    // 담긴 작물은 삭제하지 않고 텃밭 배정만 해제한다
    saveLocalPlants(loadLocalPlants().map((plant) => (plant.gardenId === gardenId ? { ...plant, gardenId: null } : plant)));
    return;
  }
  await request<void>(`/api/v1/gardens/${gardenId}`, { method: "DELETE" });
}

export async function getPlant(plantId: string) {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const plant = loadLocalPlants().find((item) => item.id === plantId);
    if (!plant) throw new Error("식물 정보를 찾지 못했습니다.");
    const [plantWithDisplaySpecies] = await addPlantDisplaySpecies([{
      ...plant,
      careLogs: loadLocalCareLogs().filter((item) => item.plantId === plantId),
      photos: loadLocalPlantPhotos().filter((item) => item.plantId === plantId)
    }]);
    return plantWithDisplaySpecies;
  }
  const plant = await request<Plant & { careLogs: CareLog[]; photos: PlantPhoto[] }>(`/api/v1/plants/${plantId}`);
  const [plantWithDisplaySpecies] = await addPlantDisplaySpecies([plant]);
  return plantWithDisplaySpecies;
}

export async function updatePlant(
  plantId: string,
  input: Partial<Pick<Plant, "name" | "species" | "location" | "sunlight" | "imageUrl" | "gardenId">>
): Promise<Plant> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const plants = loadLocalPlants();
    const nextPlants = plants.map((plant) => (plant.id === plantId ? { ...plant, ...input } : plant));
    saveLocalPlants(nextPlants);
    const updatedPlant = nextPlants.find((plant) => plant.id === plantId) ?? plants[0];
    if (!updatedPlant) throw new Error("수정할 식물을 찾지 못했습니다.");
    return updatedPlant;
  }

  return request<Plant>(`/api/v1/plants/${plantId}`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export async function deletePlant(plantId: string): Promise<void> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    saveLocalPlants(loadLocalPlants().filter((plant) => plant.id !== plantId));
    saveLocalCareLogs(loadLocalCareLogs().filter((log) => log.plantId !== plantId));
    saveLocalPlantPhotos(loadLocalPlantPhotos().filter((photo) => photo.plantId !== plantId));
    return;
  }

  await request<void>(`/api/v1/plants/${plantId}`, {
    method: "DELETE"
  });
}

export async function createPlant(input: Pick<Plant, "name" | "species" | "location" | "sunlight"> & Partial<Pick<Plant, "gardenId">>): Promise<Plant> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const plant: Plant = {
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
      moisture: "기록 대기",
      nextTask: "첫 관찰 기록 작성",
      ...input
    };
    saveLocalPlants([plant, ...loadLocalPlants()]);
    return plant;
  }

  return request<Plant>("/api/v1/plants", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function createCareLog(
  plantId: string,
  input: Pick<CareLog, "wateredAt" | "leafCondition" | "soilCondition" | "memo">
) {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const log: CareLog = {
      id: crypto.randomUUID(),
      plantId,
      createdAt: new Date().toISOString(),
      ...input
    };
    saveLocalCareLogs([log, ...loadLocalCareLogs()]);
    return log;
  }
  return request<CareLog>(`/api/v1/plants/${plantId}/care-logs`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export async function searchPlantCatalog(q: string, limit = 6): Promise<PlantCatalogItem[]> {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("limit", String(limit));

  try {
    return await request<PlantCatalogItem[]>(`/api/v1/plant-catalog?${params.toString()}`, { auth: false });
  } catch (error) {
    if (!ENABLE_DEVELOPMENT_MOCKS) throw error;
    console.warn("[Farmhani] Falling back to local plant catalog:", error);
    const term = q.trim().toLowerCase();
    return [
      { id: "monstera-deliciosa", name: "몬스테라 델리시오사", species: "Monstera deliciosa", familyName: "천남성과" },
      { id: "ficus-elastica", name: "인도고무나무", species: "Ficus elastica", familyName: "뽕나무과" },
      { id: "sansevieria", name: "스투키", species: "Dracaena angolensis", familyName: "아스파라거스과" },
      { id: "spathiphyllum", name: "스파티필럼", species: "Spathiphyllum wallisii", familyName: "천남성과" },
      { id: "rose", name: "장미", species: "Rosa spp.", familyName: "장미과" },
      { id: "strawberry", name: "딸기", species: "Fragaria x ananassa", familyName: "장미과" },
      { id: "potato", name: "감자", species: "Solanum tuberosum", familyName: "가지과" },
      { id: "sweet-potato", name: "고구마", species: "Ipomoea batatas", familyName: "메꽃과" },
      { id: "orchid", name: "난", species: "Orchidaceae", familyName: "난초과" }
    ].filter((item) => !term || item.name.toLowerCase().includes(term) || item.species.toLowerCase().includes(term));
  }
}

export type RagSearchResult = {
  sourceId: string;
  title: string;
  url?: string | null;
  publisher?: string | null;
  excerpt: string;
  score?: number | null;
};

export async function searchRagDocuments(q: string, limit = 5): Promise<RagSearchResult[]> {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("limit", String(limit));
  return request<RagSearchResult[]>(`/api/v1/rag/search?${params.toString()}`, { auth: false });
}

export async function getUploadSignedUrl(file: File): Promise<UploadSignedUrlResponse> {
  return request<UploadSignedUrlResponse>("/api/v1/uploads/signed-url", {
    method: "POST",
    body: JSON.stringify({
      fileName: file.name,
      mimeType: file.type,
      fileSize: file.size
    })
  });
}

async function uploadFileToSignedUrl(signedUrl: string, file: File) {
  const headers = file.type ? { "Content-Type": file.type } : undefined;
  let lastError: Error | undefined;

  for (const method of ["PUT", "POST"]) {
    try {
      const response = await fetch(signedUrl, {
        method,
        headers,
        body: file
      });
      if (response.ok) return;
      lastError = new Error(`Signed upload failed with ${method}: ${response.status}`);
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  throw lastError ?? new Error("Signed upload failed.");
}

export async function createPlantPhoto(
  plantId: string,
  input: Pick<PlantPhoto, "storagePath" | "capturedAt" | "note">
) {
  return request<PlantPhoto>(`/api/v1/plants/${plantId}/photos`, {
    method: "POST",
    body: JSON.stringify(input)
  });
}

async function uploadPlantPhotoViaBackend(plantId: string, file: File, note?: string) {
  const form = new FormData();
  form.append("plantId", plantId);
  form.append("file", file);
  form.append("capturedAt", new Date().toISOString());
  if (note) form.append("note", note);

  return request<PlantPhoto>("/api/v1/uploads/plant-photo", {
    method: "POST",
    body: form
  });
}

export async function uploadPlantPhoto(plantId: string, file: File, note?: string): Promise<PlantPhoto> {
  if (file.size > MAX_PHOTO_UPLOAD_BYTES) {
    throw new Error("사진 파일이 너무 큽니다. 8MB 이하로 업로드해주세요.");
  }

  if (ENABLE_DEVELOPMENT_MOCKS) {
    const photo: PlantPhoto = {
      id: crypto.randomUUID(),
      plantId,
      storagePath: await fileToDataUrl(file),
      capturedAt: new Date().toISOString(),
      note,
      createdAt: new Date().toISOString()
    };
    saveLocalPlantPhotos([photo, ...loadLocalPlantPhotos()]);
    return photo;
  }

  try {
    const signed = await getUploadSignedUrl(file);
    await uploadFileToSignedUrl(signed.signedUrl, file);
    return await createPlantPhoto(plantId, {
      storagePath: signed.storagePath,
      capturedAt: new Date().toISOString(),
      note
    });
  } catch (error) {
    if (isAuthRequiredError(error)) throw error;
    console.warn("[Farmhani] Signed upload failed, trying backend upload:", error);
    return uploadPlantPhotoViaBackend(plantId, file, note);
  }
}

export async function uploadGardenPhoto(gardenId: string, file: File, note?: string): Promise<GardenPhoto> {
  if (file.size > MAX_PHOTO_UPLOAD_BYTES) {
    throw new Error("사진 파일이 너무 큽니다. 8MB 이하로 업로드해주세요.");
  }

  if (ENABLE_DEVELOPMENT_MOCKS) {
    return {
      id: crypto.randomUUID(),
      gardenId,
      storagePath: await fileToDataUrl(file),
      capturedAt: new Date().toISOString(),
      note,
      createdAt: new Date().toISOString()
    };
  }

  try {
    const signed = await getUploadSignedUrl(file);
    await uploadFileToSignedUrl(signed.signedUrl, file);
    return await request<GardenPhoto>(`/api/v1/gardens/${gardenId}/photos`, {
      method: "POST",
      body: JSON.stringify({
        storagePath: signed.storagePath,
        capturedAt: new Date().toISOString(),
        note
      })
    });
  } catch (error) {
    if (isAuthRequiredError(error)) throw error;
    console.warn("[Farmhani] Signed garden upload failed, trying backend upload:", error);
    const form = new FormData();
    form.append("gardenId", gardenId);
    form.append("file", file);
    form.append("capturedAt", new Date().toISOString());
    if (note) form.append("note", note);
    return request<GardenPhoto>("/api/v1/uploads/garden-photo", {
      method: "POST",
      body: form
    });
  }
}

export async function askPlantCare(
  question: string,
  plantId: string | undefined,
  options: {
    gardenId?: string;
    careLogId?: string;
    photoId?: string;
    sessionId?: string;
    newSession?: boolean;
    responseMode?: ChatResponseMode;
    llmProvider?: ChatLLMProvider;
    llmModel?: OpenAIChatModel;
    recentMessages?: ChatMemoryMessage[];
  } = {}
): Promise<PlantCareChatResponse> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    return mockChatResponse;
  }

  return request<PlantCareChatResponse>("/api/v1/chat/plant-care", {
    method: "POST",
    body: JSON.stringify({
      plantId,
      gardenId: options.gardenId,
      careLogId: options.careLogId,
      photoId: options.photoId,
      sessionId: options.sessionId,
      newSession: options.newSession ?? false,
      responseMode: options.responseMode ?? "expert",
      llmProvider: options.llmProvider,
      llmModel: options.llmModel,
      recentMessages: options.recentMessages ?? [],
      question
    })
  });
}

export async function askPlantCareStream(
  question: string,
  plantId: string | undefined,
  options: {
    gardenId?: string;
    careLogId?: string;
    photoId?: string;
    sessionId?: string;
    newSession?: boolean;
    responseMode?: ChatResponseMode;
    llmProvider?: ChatLLMProvider;
    llmModel?: OpenAIChatModel;
    recentMessages?: ChatMemoryMessage[];
  } = {},
  onProgress?: (progress: ChatProgressEvent) => void
): Promise<PlantCareChatResponse> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    return mockChatResponse;
  }

  const token = getAccessToken();
  if (!token) {
    throw new AuthRequiredError();
  }

  if (!API_BASE_URL) {
    throw new FrontendConfigError("백엔드 연결 주소가 설정되지 않았습니다. VITE_BACKEND_URL을 확인해 주세요.");
  }

  const requestBody = JSON.stringify({
      plantId,
      gardenId: options.gardenId,
      careLogId: options.careLogId,
      photoId: options.photoId,
      sessionId: options.sessionId,
      newSession: options.newSession ?? false,
      responseMode: options.responseMode ?? "expert",
      llmProvider: options.llmProvider,
      llmModel: options.llmModel,
      recentMessages: options.recentMessages ?? [],
      question
  });
  const sendStreamRequest = (accessToken: string) => fetch(`${API_BASE_URL}/api/v1/chat/plant-care/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`
    },
    body: requestBody
  });

  let response = await sendStreamRequest(token);
  if (response.status === 401) {
    const refreshedAccessToken = await refreshAuthSession();
    if (refreshedAccessToken) response = await sendStreamRequest(refreshedAccessToken);
  }

  if (response.status === 401) {
    throw new AuthRequiredError("세션이 만료되었거나 로그인이 필요합니다.");
  }
  if (response.status === 403) {
    throw new Error("이 상담 기록에 접근할 권한이 없습니다.");
  }
  // 구버전 백엔드(스트림 미지원) 등에서는 기존 방식으로 자동 전환
  if (response.status === 404 || response.status === 405 || !response.body) {
    return askPlantCare(question, plantId, options);
  }
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `API request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");

      const dataLine = rawEvent.split("\n").find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      let payload: { type: string; [key: string]: unknown };
      try {
        payload = JSON.parse(dataLine.slice(6));
      } catch {
        continue;
      }

      if (payload.type === "progress" && onProgress) {
        onProgress(payload as unknown as ChatProgressEvent);
      } else if (payload.type === "result") {
        return payload.data as PlantCareChatResponse;
      } else if (payload.type === "error") {
        throw new Error(String(payload.detail || "상담 처리 중 오류가 발생했습니다."));
      }
    }
  }
  throw new Error("상담 응답 스트림이 완료되지 않았습니다.");
}

// mock 물주기 주기(일) — 백엔드 watering_interval_days의 경량 재현(도감 대신 키워드 규칙)
function mockWateringIntervalDays(plant: Plant): number {
  const hay = `${plant.name || ""} ${plant.species || ""}`.toLowerCase();
  if (/선인장|다육|스투키|산세|금전수|cactus|succulent|sansevieria|zamioculcas|aloe/.test(hay)) return 14;
  if (/바질|민트|상추|깻잎|시금치|부추|토마토|오이|고추|파프리카|딸기|가지|허브|basil|mint|lettuce|tomato|cucumber|strawberry/.test(hay)) return 3;
  return 7;
}

// mock: 최근 care_logs.watered_at에서 물주기 상태를 파생한다 ("물 줬어요" 완료 루프의 근거)
function computeMockReminder(plant: Plant, logs: CareLog[]): WateringReminder {
  const lastWateredAt = logs
    .filter((log) => log.plantId === plant.id && log.wateredAt)
    .map((log) => log.wateredAt as string)
    .sort()
    .pop() || null;
  const intervalDays = mockWateringIntervalDays(plant);
  if (!lastWateredAt) {
    return { plantId: plant.id, name: plant.name, species: plant.species, lastWateredAt: null, daysSinceWatered: null, intervalDays, status: "unknown" };
  }
  const daysSinceWatered = Math.max(0, Math.floor((Date.now() - new Date(lastWateredAt).getTime()) / 86400000));
  const remaining = intervalDays - daysSinceWatered;
  const status: WateringReminder["status"] = remaining <= 0 ? "due" : remaining <= 1 ? "upcoming" : "ok";
  return { plantId: plant.id, name: plant.name, species: plant.species, lastWateredAt, daysSinceWatered, intervalDays, status };
}

export async function getWateringReminders(): Promise<WateringReminder[]> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const logs = loadLocalCareLogs();
    return loadLocalPlants().map((plant) => computeMockReminder(plant, logs));
  }
  return request<WateringReminder[]>("/api/v1/plants/reminders");
}

export async function getTodayChecklist(): Promise<ChecklistTask[]> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    const logs = loadLocalCareLogs();
    const plants = loadLocalPlants();
    const tasks: ChecklistTask[] = [];
    for (const plant of plants) {
      const reminder = computeMockReminder(plant, logs);
      if (reminder.status === "due") {
        const over = (reminder.daysSinceWatered ?? 0) - reminder.intervalDays;
        tasks.push({
          id: `water-${plant.id}`,
          plantId: plant.id,
          plantName: plant.name,
          taskType: "water",
          title: "물주기 확인",
          description: over > 0
            ? `권장 주기(${reminder.intervalDays}일)를 ${over}일 지났어요. 겉흙 2~3cm 아래를 확인하고 필요하면 물을 주세요.`
            : "오늘이 물주기 예정일이에요. 겉흙 2~3cm 아래를 확인하고 필요하면 물을 주세요.",
          done: false
        });
      } else if (reminder.status === "unknown") {
        tasks.push({
          id: `water-${plant.id}`,
          plantId: plant.id,
          plantName: plant.name,
          taskType: "water",
          title: "첫 물주기 기록하기",
          description: "아직 물주기 기록이 없어요. 오늘 흙 상태를 확인하고 물을 줬다면 기록해 주세요.",
          done: false
        });
      }
    }
    // 물주기 할 일이 없으면 관찰 유도 1건
    if (tasks.length === 0 && plants[0]) {
      tasks.push({
        id: `observe-${plants[0].id}`,
        plantId: plants[0].id,
        plantName: plants[0].name,
        taskType: "observe",
        title: "새 잎과 흙 상태 확인",
        description: "지난 사진과 비교해 잎 색·흙 상태의 변화를 기록해 보세요.",
        done: false
      });
    }
    return tasks;
  }
  return request<ChecklistTask[]>("/api/v1/plants/checklist");
}

export async function getChatModelInfo(): Promise<ChatModelInfo> {
  if (ENABLE_DEVELOPMENT_MOCKS) {
    return {
      chatModel: "gpt-5.4",
      visionModel: "gpt-5.4",
      fallbackEnabled: true,
      localChatModel: "qwen3-vl:4b-instruct",
      localVisionModel: "qwen3-vl:4b-instruct",
      localAuxiliaryEnabled: false,
      primaryCircuit: { state: "closed", failureCount: 0, retryAfterSeconds: 0 },
      primaryConfigured: true,
      primaryAvailable: true,
      localAvailable: true,
      availableOpenAIModels: ["gpt-5.4", "gpt-5.5", "gpt-5.6-sol"]
    };
  }
  return request<ChatModelInfo>("/api/v1/chat/model-info", { auth: false });
}

export async function submitChatFeedback(
  messageId: string,
  rating: ChatFeedbackRating,
  comment?: string
): Promise<{ messageId: string; rating: string; saved: boolean }> {
  return request<{ messageId: string; rating: string; saved: boolean }>(`/api/v1/chat/messages/${messageId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating, comment })
  });
}

export async function getSessionFeedback(sessionId: string) {
  return request<ChatFeedbackItem[]>(`/api/v1/chat/sessions/${sessionId}/feedback`);
}

export async function getFeedbackSummary(plantId?: string) {
  const params = new URLSearchParams();
  if (plantId) params.set("plantId", plantId);
  const query = params.toString();
  return request<SessionFeedbackStats[]>(`/api/v1/chat/feedback/summary${query ? `?${query}` : ""}`);
}

export async function listChatSessions(plantId?: string, responseMode?: ChatResponseMode, gardenId?: string) {
  const params = new URLSearchParams();
  if (plantId) params.set("plantId", plantId);
  if (gardenId) params.set("gardenId", gardenId);
  if (responseMode) params.set("responseMode", responseMode);
  const query = params.toString();
  return request<ChatSession[]>(`/api/v1/chat/sessions${query ? `?${query}` : ""}`);
}

export async function deleteChatSession(sessionId: string) {
  return request<void>(`/api/v1/chat/sessions/${sessionId}`, { method: "DELETE" });
}

export async function listChatMessages(sessionId: string) {
  return request<ChatMessage[]>(`/api/v1/chat/sessions/${sessionId}/messages`);
}
