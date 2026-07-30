// 페이지 라우팅 상수 및 로컬스토리지 키
export type DesignPage = "login" | "dashboard" | "add" | "detail" | "chat" | "garden";

const hashToPage: Record<string, DesignPage> = {
  "#login": "login",
  "#dashboard": "dashboard",
  "#add": "add",
  "#detail": "detail",
  "#chat": "chat",
  "#garden": "garden"
};

export const SELECTED_PLANT_ID_KEY = "farmhani_selected_plant_id";
export const SELECTED_GARDEN_ID_KEY = "farmhani_selected_garden_id";
export const LAST_SESSION_ID_KEY = "farmhani_last_session_id";
export const PENDING_DIAGNOSIS_QUESTION_KEY = "farmhani_pending_diagnosis_question";
export const CHAT_RESPONSE_MODE_KEY = "farmhani_chat_response_mode";
export const CHAT_MEMORY_KEY = "farmhani_chat_memory";
export const USER_PROFILE_PHOTO_KEY = "farmhani_user_profile_photo";
export const NOTIFICATION_ENABLED_KEY = "farmhani_notification_enabled";
export const WATERING_NOTIFIED_DATE_KEY = "farmhani_watering_notified_date";
export const TODAY_CHECKLIST_EXPANDED_KEY = "farmhani_today_checklist_expanded";
export const REFERENCE_PANE_HIDDEN_KEY = "farmhani_reference_pane_hidden";
export const REFERENCE_PANE_COMPACT_KEY = "farmhani_reference_pane_compact";

export const defaultPlantImages = [
  "https://images.unsplash.com/photo-1614594975525-e45190c55d0b?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1598880940080-ff9a29891b85?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1622205313162-be1d5712a43d?auto=format&fit=crop&w=1200&q=80",
  "https://images.unsplash.com/photo-1521334884684-d80222895322?auto=format&fit=crop&w=1200&q=80"
];

export function getInitialPage(): DesignPage {
  return hashToPage[window.location.hash] ?? "login";
}
