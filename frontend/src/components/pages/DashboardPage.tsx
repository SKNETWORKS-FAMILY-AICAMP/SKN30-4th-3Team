import { useEffect, useState } from "react";
import { createCareLog, deletePlant, getPlants, getTodayChecklist, getWateringReminders } from "../../api";
import { PageState } from "../PageState";
import { defaultPlantImages, type DesignPage } from "../../lib/constants";
import { setSelectedPlantId } from "../../lib/storage";
import type { ChecklistTask, Plant, WateringReminder } from "../../types";
import dashboardPlantImage from "../../assets/dashboard-plant.webp";

interface DashboardPageProps {
  onNavigate: (page: DesignPage) => void;
  onAuthError: (error: unknown) => boolean;
}

// 물주기 D-day 라벨 — 다음 물주기까지 남은 일수(음수면 지남)
function wateringDdayLabel(reminder?: WateringReminder): string | null {
  if (!reminder) return null;
  if (reminder.status === "unknown" || reminder.daysSinceWatered === null || reminder.daysSinceWatered === undefined) return "물주기 기록 없음";
  const remaining = reminder.intervalDays - reminder.daysSinceWatered;
  if (remaining > 0) return `다음 물주기 D-${remaining}`;
  if (remaining === 0) return "오늘 물주기";
  return `물주기 ${-remaining}일 지남`;
}

export function DashboardPage({ onNavigate, onAuthError }: DashboardPageProps) {
  const [plants, setPlants] = useState<Plant[]>([]);
  const [checklist, setChecklist] = useState<ChecklistTask[]>([]);
  const [reminders, setReminders] = useState<WateringReminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [checklistExpanded, setChecklistExpanded] = useState(true);
  const [wateringId, setWateringId] = useState<string>();
  const [selectedPlantIds, setSelectedPlantIds] = useState<Set<string>>(() => new Set());
  const [deletingPlants, setDeletingPlants] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");

    Promise.all([getPlants(), getTodayChecklist(), getWateringReminders()])
      .then(([plantRows, taskRows, reminderRows]) => {
        if (!active) return;
        setPlants(plantRows);
        setChecklist(taskRows);
        setReminders(reminderRows);
        const currentPlantIds = new Set(plantRows.map((plant) => plant.id));
        setSelectedPlantIds((selectedIds) => new Set([...selectedIds].filter((id) => currentPlantIds.has(id))));
      })
      .catch((caughtError: unknown) => {
        if (!active || onAuthError(caughtError)) return;
        setError(caughtError instanceof Error ? caughtError.message : "대시보드 데이터를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [onAuthError, reloadKey]);

  function selectPlant(plantId: string, page: "detail" | "chat") {
    setSelectedPlantId(plantId);
    onNavigate(page);
  }

  async function handleWater(plantId: string) {
    if (wateringId) return;
    setWateringId(plantId);
    setError("");
    try {
      await createCareLog(plantId, { wateredAt: new Date().toISOString().slice(0, 10) });
      const [taskRows, reminderRows] = await Promise.all([getTodayChecklist(), getWateringReminders()]);
      setChecklist(taskRows);
      setReminders(reminderRows);
    } catch (caughtError) {
      if (!onAuthError(caughtError)) {
        setError(caughtError instanceof Error ? caughtError.message : "물주기 기록을 저장하지 못했습니다.");
      }
    } finally {
      setWateringId(undefined);
    }
  }

  function showChecklist() {
    setChecklistExpanded(true);
    window.requestAnimationFrame(() => {
      document.getElementById("checklist-section")?.scrollIntoView({ behavior: "smooth" });
    });
  }

  function togglePlantSelection(plantId: string) {
    setSelectedPlantIds((selectedIds) => {
      const nextIds = new Set(selectedIds);
      if (nextIds.has(plantId)) nextIds.delete(plantId);
      else nextIds.add(plantId);
      return nextIds;
    });
  }

  function toggleAllPlants() {
    setSelectedPlantIds((selectedIds) => selectedIds.size === plants.length ? new Set() : new Set(plants.map((plant) => plant.id)));
  }

  async function handleDeleteSelectedPlants() {
    const plantIds = [...selectedPlantIds];
    if (plantIds.length === 0 || deletingPlants) return;
    if (!window.confirm(`선택한 식물 ${plantIds.length}개와 연결된 관리 기록을 모두 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return;

    setDeletingPlants(true);
    setDeleteError("");
    try {
      const results = await Promise.allSettled(plantIds.map((plantId) => deletePlant(plantId)));
      const deletedIds = new Set(plantIds.filter((_, index) => results[index].status === "fulfilled"));
      const failures = results.filter((result): result is PromiseRejectedResult => result.status === "rejected");
      const authFailure = failures.find((failure) => onAuthError(failure.reason));
      if (authFailure) return;

      setPlants((currentPlants) => currentPlants.filter((plant) => !deletedIds.has(plant.id)));
      setSelectedPlantIds(new Set());
      if (failures.length > 0) {
        setDeleteError(`${deletedIds.size}개는 삭제했지만 ${failures.length}개는 삭제하지 못했습니다. 다시 선택해 주세요.`);
      } else {
        setReloadKey((value) => value + 1);
      }
    } finally {
      setDeletingPlants(false);
    }
  }

  if (loading) return <PageState kind="loading" title="내 식물 기록을 불러오고 있어요" />;
  if (error) {
    return (
      <PageState
        kind="error"
        title="대시보드를 불러오지 못했어요"
        description={error}
        actionLabel="다시 시도"
        onAction={() => setReloadKey((value) => value + 1)}
      />
    );
  }

  const dueReminders = reminders.filter((reminder) => reminder.status === "due");
  const openTasks = checklist.filter((task) => !task.done);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredPlants = plants.filter((plant) =>
    [plant.name, plant.displaySpecies, plant.species, plant.location].some((value) => value?.toLowerCase().includes(normalizedQuery))
  );
  const allPlantsSelected = plants.length > 0 && selectedPlantIds.size === plants.length;

  return (
    <div className="page-container">
      <section className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <span className="hero-pill"><span className="material-symbols-outlined" aria-hidden="true">sunny</span> 오늘의 식물 생활</span>
          <h1>{plants.length > 0 ? "오늘도 초록이들과 좋은 하루예요" : "첫 식물과의 기록을 시작해요"}</h1>
          <p>{plants.length > 0 ? `${plants.length}개의 식물 기록과 오늘 필요한 케어를 한곳에서 확인하세요.` : "이름만 등록해도 성장 기록과 관리 루틴을 만들 수 있어요."}</p>
          <div className="hero-actions">
            <button className="button button-on-dark" type="button" onClick={() => openTasks.length ? showChecklist() : document.getElementById("plants-section")?.scrollIntoView({ behavior: "smooth" })}>
              <span className="material-symbols-outlined" aria-hidden="true">{openTasks.length ? "checklist" : "potted_plant"}</span>{openTasks.length ? `오늘 할 일 ${openTasks.length}건` : "내 식물 보기"}
            </button>
          </div>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <img className="hero-plant-image" src={dashboardPlantImage} alt="" />
          <span className="floating-note floating-note-top"><i className="material-symbols-outlined">water_drop</i> 물주기 기록</span>
          <span className="floating-note floating-note-bottom"><i className="material-symbols-outlined">eco</i> 성장 관찰</span>
        </div>
      </section>

      <section className="summary-grid" aria-label="오늘의 관리 요약">
        <article className="summary-card">
          <span className="summary-icon summary-icon-mint"><span className="material-symbols-outlined" aria-hidden="true">potted_plant</span></span>
          <div><span>함께하는 식물</span><strong>{plants.length}<small>개</small></strong><p>현재 등록된 식물</p></div>
        </article>
        <button className="summary-card summary-card-button" type="button" onClick={showChecklist}>
          <span className="summary-icon summary-icon-blue"><span className="material-symbols-outlined" aria-hidden="true">task_alt</span></span>
          <div><span>오늘의 케어</span><strong>{openTasks.length}<small>건</small></strong><p>{openTasks.length ? "천천히 하나씩 확인해요" : "오늘 할 일을 모두 확인했어요"}</p></div>
          <span className="summary-arrow material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </button>
        <article className={dueReminders.length > 0 ? "summary-card summary-card-warning" : "summary-card"}>
          <span className="summary-icon summary-icon-coral"><span className="material-symbols-outlined" aria-hidden="true">water_drop</span></span>
          <div><span>물주기 확인</span><strong>{dueReminders.length}<small>개</small></strong><p>{dueReminders.length > 0 ? "흙 상태부터 살펴봐 주세요" : "지금 확인할 알림이 없어요"}</p></div>
        </article>
      </section>

      {plants.length === 0 ? (
        <PageState
          kind="empty"
          title="아직 등록된 식물이 없어요"
          description="식물 이름과 종류를 등록하면 관리 기록과 AI 상태 점검을 시작할 수 있습니다."
          actionLabel="첫 식물 등록"
          onAction={() => onNavigate("add")}
        />
      ) : (
        <section className="content-section" id="plants-section" aria-labelledby="plants-title">
          <div className="section-heading">
            <div>
              <span className="eyebrow">MY GREEN FAMILY</span>
              <h2 id="plants-title">내 식물</h2><p>식물을 선택하면 관리 기록과 상세 정보를 볼 수 있어요.</p>
            </div>
            <div className="plant-section-tools">
              <div className="plant-selection-tools">
                <label className="selection-checkbox">
                  <input type="checkbox" checked={allPlantsSelected} onChange={toggleAllPlants} />
                  <span>{allPlantsSelected ? "전체 해제" : "전체 선택"}</span>
                </label>
                <button className="button button-danger-ghost button-small" type="button" disabled={selectedPlantIds.size === 0 || deletingPlants} onClick={handleDeleteSelectedPlants}>
                  {deletingPlants ? "삭제 중…" : `선택 삭제${selectedPlantIds.size > 0 ? ` (${selectedPlantIds.size})` : ""}`}
                  <svg aria-hidden="true" fill="none" height="18" viewBox="0 0 24 24" width="18">
                    <path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
                  </svg>
                </button>
              </div>
              <label className="search-field">
                <span className="material-symbols-outlined" aria-hidden="true">search</span>
                <span className="sr-only">내 식물 검색</span>
                <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이름, 종류, 위치 검색" />
              </label>
            </div>
          </div>
          {deleteError && <div className="alert alert-error" role="alert">{deleteError}</div>}
          <div className="plant-grid">
            {filteredPlants.map((plant, index) => {
              const imageUrl = plant.imageUrl || defaultPlantImages[index % defaultPlantImages.length];
              const reminder = reminders.find((item) => item.plantId === plant.id);
              return (
                <article className={selectedPlantIds.has(plant.id) ? "plant-card is-selected" : "plant-card"} key={plant.id}>
                  <label className="plant-card-selector" onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedPlantIds.has(plant.id)}
                      onChange={() => togglePlantSelection(plant.id)}
                    />
                    <span className="material-symbols-outlined" aria-hidden="true">check</span>
                    <span className="sr-only">{plant.name} 선택</span>
                  </label>
                  <button className="plant-card-main" type="button" onClick={() => selectPlant(plant.id, "detail")}>
                    <span className="plant-card-image">
                      <img alt={`${plant.name} 식물`} loading="lazy" src={imageUrl} onError={(event) => { event.currentTarget.src = dashboardPlantImage; }} />
                      <span className={`status-chip status-${reminder?.status || "unknown"}`}>
                        {reminder?.status === "due" ? "확인 필요" : reminder?.status === "upcoming" ? "곧 물주기" : reminder?.status === "unknown" || !reminder ? "기록 없음" : "잘 지내는 중"}
                      </span>
                    </span>
                    <span className="plant-card-copy">
                      <strong>{plant.name}</strong>
                      <span>{plant.displaySpecies || plant.species || "종류를 알려주세요"}</span>
                      {plant.displaySpecies && plant.species && plant.displaySpecies !== plant.species && <em className="plant-scientific-name">{plant.species}</em>}
                      <small><span className="material-symbols-outlined" aria-hidden="true">location_on</span>{plant.location || "위치 미등록"}</small>
                    </span>
                  </button>
                  <div className="plant-card-footer">
                    <button className="card-action" type="button" onClick={() => selectPlant(plant.id, "detail")}><span className="material-symbols-outlined" aria-hidden="true">history</span>기록 보기</button>
                    <button className="card-action card-action-primary" type="button" onClick={() => selectPlant(plant.id, "chat")}><span className="material-symbols-outlined" aria-hidden="true">temp_preferences_eco</span>이 식물 상담</button>
                  </div>
                </article>
              );
            })}
          </div>
          {filteredPlants.length === 0 && <p className="search-empty">검색 결과가 없어요. 다른 이름이나 위치로 찾아보세요.</p>}
        </section>
      )}

        <section
          className={checklistExpanded ? "content-section checklist-section" : "content-section checklist-section is-collapsed"}
          id="checklist-section"
          aria-labelledby="tasks-title"
          tabIndex={-1}
          onFocus={(event) => {
            if (event.target === event.currentTarget) setChecklistExpanded(true);
          }}
        >
          <div className="section-heading">
            <div>
              <span className="eyebrow">TODAY'S CARE</span>
              <h2 id="tasks-title">오늘 확인할 항목</h2><p>식물의 실제 상태를 확인한 뒤 기록으로 남겨주세요.</p>
            </div>
            <button
              className="section-toggle"
              type="button"
              aria-controls="today-checklist-content"
              aria-expanded={checklistExpanded}
              onClick={() => setChecklistExpanded((expanded) => !expanded)}
            >
              <span>{checklistExpanded ? "접기" : `펼치기 · ${openTasks.length}건`}</span>
              <span className="section-toggle-chevron" aria-hidden="true" />
            </button>
          </div>
          <div id="today-checklist-content" hidden={!checklistExpanded}>
            {openTasks.length > 0 ? <ul className="task-list">
              {openTasks.map((task) => {
                const reminder = reminders.find((item) => item.plantId === task.plantId);
                const dday = task.taskType === "water" ? wateringDdayLabel(reminder) : null;
                return (
                  <li key={task.id}>
                    <span className={`task-marker task-marker-${task.taskType}`} aria-hidden="true"><span className="material-symbols-outlined">{task.taskType === "water" ? "water_drop" : task.taskType === "photo" ? "photo_camera" : "check"}</span></span>
                    <span className="task-copy"><strong>{task.plantName}</strong>{task.title}<small>{task.description}</small>{dday && <span className={reminder?.status === "due" ? "task-dday is-due" : "task-dday"}>{dday}</span>}</span>
                    <div className="task-actions">
                      {task.taskType === "water" && (
                        <button className="button button-primary button-small" type="button" disabled={wateringId === task.plantId} onClick={() => handleWater(task.plantId)}>
                          <span className="material-symbols-outlined" aria-hidden="true">water_drop</span>{wateringId === task.plantId ? "기록 중…" : "물 줬어요"}
                        </button>
                      )}
                      <button className="text-button" type="button" onClick={() => selectPlant(task.plantId, "detail")}>기록 열기</button>
                    </div>
                  </li>
                );
              })}
            </ul> : <div className="checklist-empty"><span className="material-symbols-outlined" aria-hidden="true">task_alt</span><div><strong>오늘의 체크리스트를 모두 확인했어요</strong><p>새로운 관리 항목이 생기면 여기에 표시됩니다.</p></div></div>}
          </div>
          {!checklistExpanded && <p className="checklist-collapsed-note">{openTasks.length > 0 ? `아직 확인할 관리 항목이 ${openTasks.length}건 있어요.` : "오늘의 관리 항목을 모두 확인했어요."}</p>}
        </section>
    </div>
  );
}
