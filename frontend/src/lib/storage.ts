// 선택 식물/세션/채팅 메모리/프로필 사진 로컬스토리지 유틸리티
import type { ChatMemoryMessage, ChatModelSelection, ChatResponseMode } from "../types";
import {
  CHAT_MEMORY_KEY,
  CHAT_MODEL_SELECTION_KEY,
  CHAT_RESPONSE_MODE_KEY,
  LAST_SESSION_ID_KEY,
  NOTIFICATION_ENABLED_KEY,
  SELECTED_GARDEN_ID_KEY,
  SELECTED_PLANT_ID_KEY,
  USER_PROFILE_PHOTO_KEY,
  WATERING_NOTIFIED_DATE_KEY
} from "./constants";

const CHAT_MODEL_SELECTIONS: ChatModelSelection[] = ["gpt-5.4", "gpt-5.5", "gpt-5.6-sol", "local"];

export function setSelectedPlantId(plantId: string) {
  localStorage.setItem(SELECTED_PLANT_ID_KEY, plantId);
  localStorage.removeItem(SELECTED_GARDEN_ID_KEY);
}

export function getSelectedPlantId() {
  return localStorage.getItem(SELECTED_PLANT_ID_KEY);
}

export function setSelectedGardenId(gardenId: string) {
  localStorage.setItem(SELECTED_GARDEN_ID_KEY, gardenId);
  localStorage.removeItem(SELECTED_PLANT_ID_KEY);
}

export function getSelectedGardenId() {
  return localStorage.getItem(SELECTED_GARDEN_ID_KEY);
}

export function clearSelectedGardenId() {
  localStorage.removeItem(SELECTED_GARDEN_ID_KEY);
}

export function setLastSessionId(sessionId?: string) {
  if (sessionId) localStorage.setItem(getLastSessionStorageKey(), sessionId);
}

export function getStoredChatResponseMode(): ChatResponseMode {
  const value = localStorage.getItem(CHAT_RESPONSE_MODE_KEY);
  return value === "companion" ? "companion" : "expert";
}

export function getStoredChatModelSelection(): ChatModelSelection | null {
  const value = localStorage.getItem(CHAT_MODEL_SELECTION_KEY) as ChatModelSelection | null;
  return value && CHAT_MODEL_SELECTIONS.includes(value) ? value : null;
}

export function setStoredChatModelSelection(value: ChatModelSelection) {
  localStorage.setItem(CHAT_MODEL_SELECTION_KEY, value);
}

export function getLastSessionStorageKey(plantId = getSelectedPlantId(), mode = getStoredChatResponseMode()) {
  return `${LAST_SESSION_ID_KEY}:${plantId || "none"}:${mode}`;
}

export function getLastSessionId(plantId = getSelectedPlantId(), mode = getStoredChatResponseMode()) {
  return localStorage.getItem(getLastSessionStorageKey(plantId, mode));
}

export function clearLastSessionId(plantId = getSelectedPlantId(), mode = getStoredChatResponseMode()) {
  localStorage.removeItem(getLastSessionStorageKey(plantId, mode));
}

export function getChatMemoryStorageKey(plantId = getSelectedPlantId(), mode = getStoredChatResponseMode()) {
  return `${CHAT_MEMORY_KEY}:${plantId || "none"}:${mode}`;
}

export function loadLocalChatMemory(plantId = getSelectedPlantId(), mode = getStoredChatResponseMode()): ChatMemoryMessage[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(getChatMemoryStorageKey(plantId, mode)) || "[]") as ChatMemoryMessage[];
    return parsed
      .filter((item) => (item.role === "user" || item.role === "assistant") && item.content?.trim())
      .slice(-12);
  } catch {
    return [];
  }
}

export function saveLocalChatMemory(plantId: string, mode: ChatResponseMode, messages: ChatMemoryMessage[]) {
  localStorage.setItem(getChatMemoryStorageKey(plantId, mode), JSON.stringify(messages.slice(-12)));
}

export function appendLocalChatMemory(plantId: string, mode: ChatResponseMode, ...messages: ChatMemoryMessage[]) {
  const next = [...loadLocalChatMemory(plantId, mode), ...messages].filter((item) => item.content.trim()).slice(-12);
  saveLocalChatMemory(plantId, mode, next);
}

export function getUserProfilePhoto() {
  return localStorage.getItem(USER_PROFILE_PHOTO_KEY) || "";
}

export function setUserProfilePhoto(value: string) {
  if (!value) return;
  try {
    localStorage.setItem(USER_PROFILE_PHOTO_KEY, value);
  } catch (error) {
    // 대용량 data URL은 localStorage 5MB 한도를 넘길 수 있다 — 가입 흐름을 막지 않는다
    console.warn("[Farmhani] 프로필 사진 저장 실패(용량 초과 가능):", error);
  }
}

export function isNotificationsEnabled() {
  return localStorage.getItem(NOTIFICATION_ENABLED_KEY) === "true";
}

export function setNotificationsEnabled(enabled: boolean) {
  localStorage.setItem(NOTIFICATION_ENABLED_KEY, String(enabled));
}

export function getWateringNotifiedDate() {
  return localStorage.getItem(WATERING_NOTIFIED_DATE_KEY);
}

export function setWateringNotifiedDate(date: string) {
  localStorage.setItem(WATERING_NOTIFIED_DATE_KEY, date);
}
