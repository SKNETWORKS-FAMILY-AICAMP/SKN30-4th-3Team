import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import {
  askPlantCareStream,
  deleteChatSession,
  getPlants,
  listChatMessages,
  listChatSessions,
  submitChatFeedback,
  uploadPlantPhoto
} from "../../api";
import dashboardPlantImage from "../../assets/dashboard-plant.webp";
import { defaultPlantImages, type DesignPage } from "../../lib/constants";
import { getSelectedPlantId, setSelectedPlantId } from "../../lib/storage";
import type {
  ChatFeedbackRating,
  ChatMessage,
  ChatProgressEvent,
  ChatResponseMode,
  ChatSession,
  PestDiagnosis,
  PesticideGuidance,
  Plant,
  PlantCareChatResponse
} from "../../types";
import { PageState } from "../PageState";

interface ChatPageProps {
  onNavigate: (page: DesignPage) => void;
  onAuthError: (error: unknown) => boolean;
}

type ConversationItem =
  | { id: string; role: "user"; text: string; imageUrl?: string }
  | { id: string; role: "assistant"; response: PlantCareChatResponse; feedback?: ChatFeedbackRating; saved?: boolean };

const quickQuestions = [
  "잎이 처진 원인을 확인하고 싶어요.",
  "최근 물주기 기록을 바탕으로 관찰할 점을 알려주세요.",
  "잎 색이 달라졌는데 어떤 정보를 더 확인해야 하나요?"
] as const;

const acceptedPhotoTypes = ["image/jpeg", "image/png", "image/webp"];
const maxPhotoBytes = 8 * 1024 * 1024;

function pickImageFile(files?: FileList | null) {
  if (!files) return null;
  return Array.from(files).find((file) => file.type.startsWith("image/")) || null;
}

function hasDraggedFiles(transfer: DataTransfer | null) {
  return Boolean(transfer) && Array.from(transfer!.types).includes("Files");
}

function formatFileSize(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

function sessionTitle(title?: string | null) {
  return (title || "새 식물 상담").replace(/^\[(전문가|내 식물)\]\s*/, "");
}

function sessionDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric" }).format(new Date(value));
}

function parseSavedAnswer(content: string) {
  const normalized = content.replace(/\\n/g, "\n").trim();
  const parts = normalized.split(/\[(요약|의심 원인|가능한 원인|오늘 할 일|추가 관찰)\]/);
  const sections = new Map<string, string>();
  for (let index = 1; index < parts.length; index += 2) {
    sections.set(parts[index], (parts[index + 1] || "").trim());
  }
  const toItems = (value?: string) => (value || "")
    .split("\n")
    .map((item) => item.replace(/^[-•]\s*/, "").trim())
    .filter(Boolean);

  return {
    summary: sections.get("요약") || normalized,
    possibleCauses: toItems(sections.get("의심 원인") || sections.get("가능한 원인")),
    todayActions: toItems(sections.get("오늘 할 일")),
    observationChecklist: toItems(sections.get("추가 관찰"))
  };
}

function messagesToConversation(messages: ChatMessage[]): ConversationItem[] {
  return messages.map((message) => {
    if (message.sender === "user") {
      return { id: message.id, role: "user", text: message.content };
    }
    const parsedAnswer = parseSavedAnswer(message.content);
    return {
      id: message.id,
      role: "assistant",
      saved: true,
      response: {
        ...parsedAnswer,
        citations: message.citations || [],
        sessionId: message.sessionId,
        messageId: message.id
      }
    };
  });
}

export function ChatPage({ onNavigate, onAuthError }: ChatPageProps) {
  const conversationRef = useRef<HTMLDivElement>(null);
  const activeQuestionRef = useRef<HTMLElement>(null);
  const latestAnswerRef = useRef<HTMLElement>(null);
  const deleteConfirmRef = useRef<HTMLDivElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const messagePhotoUrlsRef = useRef<string[]>([]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [selectedPlantId, setSelectedPlant] = useState(getSelectedPlantId() || "");
  const [question, setQuestion] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState("");
  const [draggingPhoto, setDraggingPhoto] = useState(false);
  const [mode, setMode] = useState<ChatResponseMode>("expert");
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>();
  const [startingNewSession, setStartingNewSession] = useState(true);
  const [progress, setProgress] = useState<ChatProgressEvent>();
  const [loadingPlants, setLoadingPlants] = useState(true);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [pendingDeleteSession, setPendingDeleteSession] = useState<ChatSession | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string>();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [activeQuestionId, setActiveQuestionId] = useState<string>();
  const [revealingMessageId, setRevealingMessageId] = useState<string>();

  useEffect(() => {
    let active = true;
    getPlants()
      .then((rows) => {
        if (!active) return;
        setPlants(rows);
        const storedPlantId = getSelectedPlantId();
        const initialPlantId = rows.some((plant) => plant.id === storedPlantId) ? storedPlantId || "" : rows[0]?.id || "";
        setSelectedPlant(initialPlantId);
        if (initialPlantId) setSelectedPlantId(initialPlantId);
      })
      .catch((caughtError: unknown) => {
        if (!active || onAuthError(caughtError)) return;
        setError(caughtError instanceof Error ? caughtError.message : "식물 목록을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setLoadingPlants(false);
      });

    return () => {
      active = false;
    };
  }, [onAuthError]);

  useEffect(() => {
    if (!selectedPlantId) return;
    let active = true;
    setLoadingSessions(true);
    setLoadingConversation(true);
    setConversation([]);
    setSessionId(undefined);
    setPendingDeleteSession(null);
    setStartingNewSession(true);
    setError("");

    listChatSessions(selectedPlantId, mode)
      .then(async (rows) => {
        if (!active) return;
        setSessions(rows);
        const latestSession = rows[0];
        if (!latestSession) return;
        const messages = await listChatMessages(latestSession.id);
        if (!active) return;
        setSessionId(latestSession.id);
        setStartingNewSession(false);
        setConversation(messagesToConversation(messages));
        window.requestAnimationFrame(() => conversationRef.current?.scrollTo({ top: 0, behavior: "auto" }));
      })
      .catch((caughtError: unknown) => {
        if (!active || onAuthError(caughtError)) return;
        setError(caughtError instanceof Error ? caughtError.message : "상담 기록을 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!active) return;
        setLoadingSessions(false);
        setLoadingConversation(false);
      });

    return () => {
      active = false;
    };
  }, [mode, onAuthError, selectedPlantId]);

  useEffect(() => {
    if (!photo) {
      setPhotoPreview("");
      return;
    }
    const previewUrl = URL.createObjectURL(photo);
    setPhotoPreview(previewUrl);
    return () => URL.revokeObjectURL(previewUrl);
  }, [photo]);

  useEffect(() => () => {
    messagePhotoUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    messagePhotoUrlsRef.current = [];
  }, []);

  useEffect(() => {
    if (!pendingDeleteSession) return;
    const frameId = window.requestAnimationFrame(() => deleteConfirmRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    return () => window.cancelAnimationFrame(frameId);
  }, [pendingDeleteSession]);

  useEffect(() => {
    if (!activeQuestionId) return;
    const frameId = window.requestAnimationFrame(() => scrollMessageToTop(conversationRef.current, activeQuestionRef.current));
    return () => window.cancelAnimationFrame(frameId);
  }, [activeQuestionId]);

  useEffect(() => {
    if (!revealingMessageId) return;
    const frameId = window.requestAnimationFrame(() => scrollMessageToTop(conversationRef.current, latestAnswerRef.current));
    const timerId = window.setTimeout(() => setRevealingMessageId(undefined), 2200);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timerId);
    };
  }, [revealingMessageId]);

  async function openSession(nextSessionId: string) {
    if (nextSessionId === sessionId || loadingConversation) return;
    setError("");
    setLoadingConversation(true);
    try {
      const messages = await listChatMessages(nextSessionId);
      setSessionId(nextSessionId);
      setStartingNewSession(false);
      setConversation(messagesToConversation(messages));
      window.requestAnimationFrame(() => conversationRef.current?.scrollTo({ top: 0, behavior: "auto" }));
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "상담 내용을 불러오지 못했습니다.");
    } finally {
      setLoadingConversation(false);
    }
  }

  function startNewChat() {
    setPendingDeleteSession(null);
    setSessionId(undefined);
    setStartingNewSession(true);
    setConversation([]);
    setQuestion("");
    clearPhoto();
    setError("");
    setActiveQuestionId(undefined);
    setRevealingMessageId(undefined);
    conversationRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }

  async function refreshSessions(activeSessionId?: string) {
    try {
      const rows = await listChatSessions(selectedPlantId, mode);
      setSessions(rows);
      if (activeSessionId) setSessionId(activeSessionId);
    } catch (caughtError) {
      if (!onAuthError(caughtError)) {
        setError(caughtError instanceof Error ? caughtError.message : "상담방 목록을 갱신하지 못했습니다.");
      }
    }
  }

  async function confirmDeleteSession() {
    if (!pendingDeleteSession || deletingSessionId || submitting || loadingConversation) return;

    const deletingId = pendingDeleteSession.id;
    setDeletingSessionId(deletingId);
    setError("");
    try {
      await deleteChatSession(deletingId);
      const remainingSessions = sessions.filter((session) => session.id !== deletingId);
      setSessions(remainingSessions);
      setPendingDeleteSession(null);

      if (deletingId === sessionId) {
        const nextSession = remainingSessions[0];
        if (nextSession) {
          setSessionId(undefined);
          setActiveQuestionId(undefined);
          setRevealingMessageId(undefined);
          await openSession(nextSession.id);
        } else {
          startNewChat();
        }
      }
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "상담방을 삭제하지 못했습니다.");
    } finally {
      setDeletingSessionId(undefined);
    }
  }

  function applyPhotoSelection(file: File | null) {
    if (!file) return;
    if (!acceptedPhotoTypes.includes(file.type)) {
      setError("JPG, PNG, WebP 이미지만 첨부할 수 있어요.");
      return;
    }
    if (file.size > maxPhotoBytes) {
      setError(`사진이 너무 큽니다(${formatFileSize(file.size)}). 8MB 이하로 첨부해 주세요.`);
      return;
    }
    setError("");
    setPhoto(file);
  }

  function clearPhoto() {
    setPhoto(null);
    if (photoInputRef.current) photoInputRef.current.value = "";
  }

  function handlePhotoDragOver(event: DragEvent<HTMLElement>) {
    if (!hasDraggedFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDraggingPhoto(true);
  }

  function handlePhotoDragLeave(event: DragEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDraggingPhoto(false);
  }

  function handlePhotoDrop(event: DragEvent<HTMLElement>) {
    if (!hasDraggedFiles(event.dataTransfer)) return;
    event.preventDefault();
    setDraggingPhoto(false);
    applyPhotoSelection(pickImageFile(event.dataTransfer.files));
  }

  function handlePhotoPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const imageItem = Array.from(event.clipboardData.items).find((item) => item.kind === "file" && item.type.startsWith("image/"));
    if (!imageItem) return;
    const pastedFile = imageItem.getAsFile();
    if (!pastedFile) return;
    event.preventDefault();
    applyPhotoSelection(pastedFile);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || !selectedPlantId || submitting) return;

    const sentPhoto = photo;
    let sentPhotoUrl: string | undefined;
    if (sentPhoto) {
      sentPhotoUrl = URL.createObjectURL(sentPhoto);
      messagePhotoUrlsRef.current.push(sentPhotoUrl);
    }

    const userItem: ConversationItem = { id: crypto.randomUUID(), role: "user", text: trimmedQuestion, imageUrl: sentPhotoUrl };
    setConversation((items) => [...items, userItem]);
    setActiveQuestionId(userItem.id);
    setRevealingMessageId(undefined);
    setQuestion("");
    setError("");
    setSubmitting(true);
    setProgress({ step: 1, total: 10, node: "upload", label: photo ? "사진을 안전하게 등록하고 있어요" : "질문을 확인하고 있어요" });

    try {
      let photoId: string | undefined;
      if (sentPhoto) {
        const uploadedPhoto = await uploadPlantPhoto(selectedPlantId, sentPhoto, trimmedQuestion);
        photoId = uploadedPhoto.id;
      }

      const recentMessages = conversation.slice(-8).map((item) => ({
        role: item.role,
        content: item.role === "user" ? item.text : item.response.summary
      }));

      const response = await askPlantCareStream(
        trimmedQuestion,
        selectedPlantId,
        {
          photoId,
          sessionId,
          newSession: startingNewSession || !sessionId,
          responseMode: mode,
          recentMessages
        },
        setProgress
      );

      const nextSessionId = response.sessionId || sessionId;
      setSessionId(nextSessionId);
      setStartingNewSession(false);
      const answerId = response.messageId || crypto.randomUUID();
      setConversation((items) => [...items, { id: answerId, role: "assistant", response }]);
      setActiveQuestionId(undefined);
      setRevealingMessageId(answerId);
      clearPhoto();
      await refreshSessions(nextSessionId);
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "상태 점검 답변을 받지 못했습니다.");
    } finally {
      setSubmitting(false);
      setProgress(undefined);
    }
  }

  function handleQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!question.trim() || !selectedPlantId || submitting) return;
    event.currentTarget.form?.requestSubmit();
  }

  async function handleFeedback(itemId: string, rating: ChatFeedbackRating, messageId?: string) {
    if (!messageId) return;
    try {
      await submitChatFeedback(messageId, rating);
      setConversation((items) => items.map((item) => item.id === itemId && item.role === "assistant" ? { ...item, feedback: rating } : item));
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "피드백을 저장하지 못했습니다.");
    }
  }

  if (loadingPlants) return <PageState kind="loading" title="AI 상담 화면을 준비하고 있어요" />;
  if (plants.length === 0) {
    return <PageState kind="empty" title="먼저 식물을 등록해 주세요" description="AI 상담은 등록한 식물의 관리 이력과 함께 진행됩니다." actionLabel="식물 등록" onAction={() => onNavigate("add")} />;
  }

  const selectedPlant = plants.find((plant) => plant.id === selectedPlantId);
  const selectedPlantImage = selectedPlant?.imageUrl && !defaultPlantImages.includes(selectedPlant.imageUrl)
    ? selectedPlant.imageUrl
    : dashboardPlantImage;

  return (
    <div className="chat-page">
      <aside className="chat-context" aria-labelledby="chat-context-title">
        <span className="chat-context-kicker"><span className="material-symbols-outlined" aria-hidden="true">auto_awesome</span>{mode === "expert" ? " PLANT CARE AI" : ` ${selectedPlant?.name || "내 식물"}와 대화`}</span>
        <h1 id="chat-context-title">{mode === "expert" ? <>식물 고민,<br />함께 살펴봐요</> : <>{selectedPlant?.name || "내 식물"}의<br />이야기를 들어봐요</>}</h1>
        <p>{mode === "expert" ? "사진과 관리 기록을 바탕으로 가능한 원인과 다음 관찰 항목을 안내해 드려요." : "등록된 기록을 바탕으로 내 식물이 말하듯 쉽고 다정하게 설명해 드려요."}</p>

        {selectedPlant && <div className="selected-plant-card"><img src={selectedPlantImage} alt="" onError={(event) => { event.currentTarget.src = dashboardPlantImage; }} /><div><small>지금 상담할 식물</small><strong>{selectedPlant.name}</strong><span>{selectedPlant.species || "종류 미등록"}</span></div></div>}

        <label className="field field-on-dark">
          <span>점검할 식물</span>
          <select onChange={(event) => { setSelectedPlant(event.target.value); setSelectedPlantId(event.target.value); }} value={selectedPlantId}>
            {plants.map((plant) => <option key={plant.id} value={plant.id}>{plant.name} · {plant.species || "종류 미등록"}</option>)}
          </select>
        </label>

        <div className="mode-selector" role="group" aria-label="상담 모드">
          <button className={mode === "expert" ? "is-active" : ""} type="button" onClick={() => setMode("expert")}><span className="material-symbols-outlined" aria-hidden="true">format_list_bulleted</span><span><strong>식물 관리 상담</strong><small>근거와 행동 중심</small></span></button>
          <button className={mode === "companion" ? "is-active" : ""} type="button" onClick={() => setMode("companion")}><span className="material-symbols-outlined" aria-hidden="true">chat_bubble</span><span><strong>내 식물과 대화하기</strong><small>식물이 말하듯 쉽게</small></span></button>
        </div>

        <section className="chat-session-panel" aria-labelledby="chat-session-title">
          <div className="chat-session-heading">
            <div><small>CONVERSATIONS</small><h2 id="chat-session-title">상담 기록</h2></div>
            <button type="button" onClick={startNewChat} aria-label="새 상담 시작"><span className="material-symbols-outlined" aria-hidden="true">add</span>새 상담</button>
          </div>
          {loadingSessions ? <p className="chat-session-state">상담방을 불러오는 중…</p> : sessions.length > 0 ? (
            <div className="chat-session-list">
              {sessions.map((session) => {
                const isConfirming = pendingDeleteSession?.id === session.id;
                const itemClass = ["chat-session-item", session.id === sessionId ? "is-active" : "", isConfirming ? "is-confirming" : ""].filter(Boolean).join(" ");
                return (
                  <div className={itemClass} key={session.id}>
                    <div className="chat-session-row">
                      <button className="chat-session-open" type="button" onClick={() => openSession(session.id)} aria-current={session.id === sessionId ? "true" : undefined}>
                        <span className="material-symbols-outlined" aria-hidden="true">chat_bubble</span>
                        <span><strong>{sessionTitle(session.title)}</strong><small>{sessionDate(session.createdAt)}</small></span>
                      </button>
                      <button
                        className="chat-session-delete"
                        type="button"
                        aria-label={isConfirming ? "삭제 취소" : `${sessionTitle(session.title)} 상담방 삭제`}
                        aria-expanded={isConfirming}
                        disabled={Boolean(deletingSessionId) || submitting || loadingConversation}
                        onClick={() => setPendingDeleteSession(isConfirming ? null : session)}
                      >
                        <TrashIcon />
                      </button>
                    </div>
                    {isConfirming && (
                      <div className="chat-session-delete-confirm" role="alert" aria-live="assertive" ref={deleteConfirmRef}>
                        <p>대화 내용도 함께 사라져요. 삭제할까요?</p>
                        <div>
                          <button type="button" onClick={() => setPendingDeleteSession(null)} disabled={Boolean(deletingSessionId)}>취소</button>
                          <button className="is-danger" type="button" onClick={confirmDeleteSession} disabled={Boolean(deletingSessionId)}>
                            {deletingSessionId ? "삭제 중…" : "삭제"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : <p className="chat-session-state">첫 질문을 보내면 상담방이 만들어져요.</p>}
        </section>
      </aside>

      <section
        className={draggingPhoto ? "chat-workspace is-dragging" : "chat-workspace"}
        aria-labelledby="conversation-title"
        onDragOver={handlePhotoDragOver}
        onDragLeave={handlePhotoDragLeave}
        onDrop={handlePhotoDrop}
      >
        {draggingPhoto && (
          <div className="chat-dropzone" aria-hidden="true">
            <span className="material-symbols-outlined">add_photo_alternate</span>
            여기에 사진을 놓으면 첨부돼요
          </div>
        )}
        <header className="chat-heading">
          <div>
            <span className="eyebrow">{startingNewSession ? "새 상담" : `${selectedPlant?.name || "선택한 식물"}와 상담 중`}</span>
            <h2 id="conversation-title">{mode === "expert" ? "어떤 점이 궁금한가요?" : `${selectedPlant?.name || "내 식물"}에게 말을 걸어보세요`}</h2>
          </div>
          <button className="button button-secondary button-small" type="button" onClick={() => onNavigate("detail")}><span className="material-symbols-outlined" aria-hidden="true">history</span>식물 기록</button>
        </header>

        {error && <div className="alert alert-error" role="alert">{error}</div>}

        <div className="conversation" aria-live="polite" ref={conversationRef}>
          {loadingConversation ? (
            <div className="conversation-loading" role="status"><span className="spinner" aria-hidden="true" /><strong>상담 내용을 불러오고 있어요</strong></div>
          ) : conversation.length === 0 && (
            <div className="conversation-empty">
              <span className="conversation-empty-icon material-symbols-outlined" aria-hidden="true">temp_preferences_eco</span>
              <h3>{mode === "expert" ? "관찰한 변화를 편하게 알려주세요" : `${selectedPlant?.name || "내 식물"}의 오늘을 들려주세요`}</h3>
              <p>{mode === "expert" ? "변화가 시작된 시점과 위치, 최근 물주기와 빛 환경을 함께 알려주면 가능한 원인을 더 정확히 좁힐 수 있어요." : "오늘 보인 모습과 최근 달라진 점을 말해 주세요. 기록을 바탕으로 내 식물이 말하듯 설명해 드릴게요."}</p>
              <div className="quick-question-list">
                {quickQuestions.map((item) => <button key={item} type="button" onClick={() => setQuestion(item)}><span className="material-symbols-outlined" aria-hidden="true">north_west</span>{item}</button>)}
              </div>
            </div>
          )}

          {!loadingConversation && conversation.map((item) => item.role === "user" ? (
            <article className="message message-user" key={item.id} ref={item.id === activeQuestionId ? activeQuestionRef : undefined}>
              <span className="message-label">나</span>
              <div className="message-user-content">
                {item.imageUrl && <img className="message-photo" src={item.imageUrl} alt="함께 보낸 관찰 사진" />}
                <p>{item.text}</p>
              </div>
            </article>
          ) : (
            <article className={item.id === revealingMessageId ? "message message-assistant is-revealing" : "message message-assistant"} key={item.id} ref={item.id === revealingMessageId ? latestAnswerRef : undefined}>
              <span className="message-label">{mode === "expert" ? "Farm하니" : selectedPlant?.name || "내 식물"}</span>
              {item.saved && item.response.possibleCauses.length === 0 && item.response.todayActions.length === 0 ? <p className="saved-answer">{item.response.summary}</p> : (
                <>
                  <div className="answer-summary"><span>관찰 요약</span><p>{item.response.summary}</p></div>
                  <AnswerList icon="search_insights" title="가능한 원인" items={item.response.possibleCauses} emptyText="현재 근거만으로 원인을 좁히기 어렵습니다." />
                  <AnswerList icon="check_circle" title="오늘 할 일" items={item.response.todayActions} emptyText="즉시 권장할 관리 행동은 없습니다." />
                  <AnswerList icon="visibility" title="추가 관찰" items={item.response.observationChecklist} emptyText="추가 사진이나 관리 기록을 남겨주세요." />
                </>
              )}
              {item.response.pestDiagnosis && item.response.pestDiagnosis.length > 0 && <PestDiagnosisList items={item.response.pestDiagnosis} />}
              {item.response.pesticideGuidance && item.response.pesticideGuidance.length > 0 && <PesticideGuidanceList items={item.response.pesticideGuidance} />}
              <CitationList citations={item.response.citations} />
              {item.response.safetyNotice && <div className="safety-notice">{item.response.safetyNotice}</div>}
              {item.response.messageId && !item.saved && (
                <div className="feedback-row" aria-label="답변 평가">
                  <span>{item.feedback ? "평가를 저장했습니다." : "이 답변이 도움이 되었나요?"}</span>
                  {!item.feedback && <><button type="button" onClick={() => handleFeedback(item.id, "helpful", item.response.messageId)}>도움됨</button><button type="button" onClick={() => handleFeedback(item.id, "not_helpful", item.response.messageId)}>개선 필요</button></>}
                </div>
              )}
            </article>
          ))}

          {submitting && (
            <div className="progress-card" role="status">
              <span className="spinner" aria-hidden="true" />
              <div><strong>{progress?.label || "관련 문서와 기록을 확인하고 있어요"}</strong><small>{progress ? `${progress.step}/${progress.total} 단계 · 답변을 구성하는 중` : "잠시만 기다려 주세요"}</small><progress max={progress?.total || 10} value={progress?.step || 1} /></div>
            </div>
          )}
        </div>

        <form className="chat-composer" onSubmit={handleSubmit}>
          {photo && (
            <div className="attached-photo">
              {photoPreview && <img src={photoPreview} alt="첨부한 사진 미리보기" />}
              <div><strong>{photo.name}</strong><small>{formatFileSize(photo.size)} · 함께 분석돼요</small></div>
              <button aria-label="첨부 사진 제거" type="button" onClick={clearPhoto}>×</button>
            </div>
          )}
          <label className="photo-attach" title="관찰 사진 첨부">
            <span className="material-symbols-outlined" aria-hidden="true">add_photo_alternate</span>
            <span className="sr-only">관찰 사진 첨부</span>
            <input
              accept="image/jpeg,image/png,image/webp"
              onChange={(event) => { applyPhotoSelection(event.target.files?.[0] || null); event.target.value = ""; }}
              ref={photoInputRef}
              type="file"
            />
          </label>
          <label className="sr-only" htmlFor="chat-question">식물 관찰 내용</label>
          <textarea aria-keyshortcuts="Enter" id="chat-question" onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleQuestionKeyDown} onPaste={handlePhotoPaste} placeholder="예: 3일 전부터 아래 잎이 노랗고, 일주일 전에 물을 줬어요." rows={1} title="Enter로 전송 · Shift+Enter로 줄바꿈 · 사진은 끌어다 놓거나 붙여넣기(Ctrl+V)" value={question} />
          <button className="send-button" disabled={!question.trim() || !selectedPlantId || submitting || loadingConversation} type="submit" aria-label="질문 보내기"><span className="material-symbols-outlined" aria-hidden="true">arrow_upward</span></button>
        </form>
      </section>
    </div>
  );
}

function TrashIcon() {
  return (
    <svg
      className="trash-icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 7h16" />
      <path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7" />
      <path d="M6.5 7l.7 11.2A2 2 0 0 0 9.2 20h5.6a2 2 0 0 0 2-1.8L17.5 7" />
      <path d="M10 11v5M14 11v5" />
    </svg>
  );
}

function scrollMessageToTop(container: HTMLDivElement | null, target: HTMLElement | null) {
  if (!container || !target) return;
  const containerRect = container.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const top = container.scrollTop + targetRect.top - containerRect.top - 18;
  container.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

interface AnswerListProps {
  icon: string;
  title: string;
  items: string[];
  emptyText: string;
}

function AnswerList({ icon, title, items, emptyText }: AnswerListProps) {
  const uniqueItems = Array.from(new Set(items));
  return (
    <section className="answer-section">
      <h3><span className="material-symbols-outlined" aria-hidden="true">{icon}</span>{title}</h3>
      {uniqueItems.length > 0 ? <ul>{uniqueItems.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="muted-copy">{emptyText}</p>}
    </section>
  );
}

function PestDiagnosisList({ items }: { items: PestDiagnosis[] }) {
  return (
    <section className="answer-section pest-section">
      <h3><span className="material-symbols-outlined" aria-hidden="true">search_insights</span>병해충 가능성</h3>
      <ul className="pest-list">
        {items.map((item) => {
          const pct = Math.round(Math.min(1, Math.max(0, item.confidence)) * 100);
          const kindLabel = item.kind === "pest" ? "해충" : item.kind === "disease" ? "병" : "관찰";
          return (
            <li className="pest-item" key={`${item.candidate}-${pct}`}>
              <div className="pest-item-head">
                <span className={`pest-badge pest-badge-${item.kind || "unknown"}`}>{kindLabel}</span>
                <strong>{item.candidate}</strong>
                <span className="pest-confidence">{pct}%</span>
              </div>
              <div className="pest-meter" aria-hidden="true"><span style={{ width: `${pct}%` }} /></div>
              {item.rationale && <p>{item.rationale}</p>}
            </li>
          );
        })}
      </ul>
      <p className="muted-copy pest-note">확정 진단이 아닌 가능성입니다. 잎 뒷면·병반을 추가로 관찰해 주세요.</p>
    </section>
  );
}

function PesticideGuidanceList({ items }: { items: PesticideGuidance[] }) {
  return (
    <section className="answer-section pesticide-section">
      <h3><span className="material-symbols-outlined" aria-hidden="true">verified</span>농약 안전사용 안내</h3>
      <ul className="pesticide-list">
        {items.map((item) => (
          <li className="pesticide-item" key={item.name}>
            <div className="pesticide-head">
              <strong>{item.name}</strong>
              {item.regNo && <span className="pesticide-regno">등록 {item.regNo}</span>}
            </div>
            {Boolean(item.targetCrops?.length || item.targetPests?.length) && (
              <p className="pesticide-target">
                {item.targetCrops?.length ? `적용작물: ${item.targetCrops.join(", ")}` : ""}
                {item.targetCrops?.length && item.targetPests?.length ? " · " : ""}
                {item.targetPests?.length ? `대상: ${item.targetPests.join(", ")}` : ""}
              </p>
            )}
            <dl className="pesticide-facts">
              {typeof item.phiDays === "number" && <div className="is-phi"><dt>수확 전 사용</dt><dd>{item.phiDays}일 전까지</dd></div>}
              {item.dilution && <div><dt>희석배수</dt><dd>{item.dilution}</dd></div>}
              {typeof item.maxApplications === "number" && <div><dt>최대 사용</dt><dd>{item.maxApplications}회</dd></div>}
            </dl>
            {item.safetyNote && <p className="pesticide-safety">{item.safetyNote}</p>}
          </li>
        ))}
      </ul>
      <p className="muted-copy pesticide-note">공식 안전사용기준(농약안전정보시스템 등) 기반 안내입니다. 실제 사용 전 제품 표시사항의 적용대상·사용시기를 반드시 확인하세요.</p>
    </section>
  );
}

interface CitationListProps {
  citations: PlantCareChatResponse["citations"];
}

function CitationList({ citations }: CitationListProps) {
  return (
    <section className="citation-section">
      <h3>근거 문서</h3>
      {citations.length === 0 ? (
        <div className="citation-empty"><strong>표시할 근거 문서가 없습니다.</strong><span>추가 사진이나 환경 정보를 알려주면 다시 검색할 수 있어요.</span></div>
      ) : (
        <ol>
          {citations.map((citation) => (
            <li key={`${citation.sourceId}-${citation.title}`}>
              <div><strong>{citation.title}</strong><span>{citation.publisher || "발행기관 미상"} · {citation.sourceId}</span>{citation.excerpt && <p>{citation.excerpt}</p>}</div>
              {citation.url ? <a href={citation.url} rel="noreferrer" target="_blank">원문 보기<span className="sr-only">: {citation.title}</span></a> : <span className="citation-no-link">원문 링크 없음</span>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
