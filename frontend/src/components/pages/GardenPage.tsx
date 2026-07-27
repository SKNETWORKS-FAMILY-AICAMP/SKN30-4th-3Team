import { useEffect, useState, type FormEvent } from "react";
import { createGarden, deleteGarden, getPlants, listGardens, updateGarden, updatePlant } from "../../api";
import type { DesignPage } from "../../lib/constants";
import { setSelectedGardenId } from "../../lib/storage";
import type { Garden, Plant } from "../../types";
import { PageState } from "../PageState";

interface GardenPageProps {
  onNavigate: (page: DesignPage) => void;
  onAuthError: (error: unknown) => boolean;
}

const emptyForm = { name: "", location: "", sunlight: "", soilType: "" };
const emptyEditForm = { name: "", location: "", sunlight: "", soilType: "", description: "" };

export function GardenPage({ onNavigate, onAuthError }: GardenPageProps) {
  const [gardens, setGardens] = useState<Garden[]>([]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState(emptyEditForm);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  async function refresh() {
    const [gardenRows, plantRows] = await Promise.all([listGardens(), getPlants()]);
    setGardens(gardenRows);
    setPlants(plantRows);
  }

  useEffect(() => {
    let active = true;
    Promise.all([listGardens(), getPlants()])
      .then(([gardenRows, plantRows]) => {
        if (!active) return;
        setGardens(gardenRows);
        setPlants(plantRows);
      })
      .catch((caughtError: unknown) => {
        if (!active || onAuthError(caughtError)) return;
        setError(caughtError instanceof Error ? caughtError.message : "텃밭 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onAuthError]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = form.name.trim();
    if (!name || busy) return;
    setBusy(true);
    setError("");
    try {
      await createGarden({
        name,
        location: form.location.trim() || undefined,
        sunlight: form.sunlight.trim() || undefined,
        soilType: form.soilType.trim() || undefined
      });
      setForm(emptyForm);
      setCreating(false);
      await refresh();
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "텃밭을 만들지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function assignPlant(plantId: string, gardenId: string | null) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await updatePlant(plantId, { gardenId });
      await refresh();
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "식물을 옮기지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function startEdit(garden: Garden) {
    setPendingDeleteId(null);
    setCreating(false);
    setEditingId(garden.id);
    setEditForm({
      name: garden.name || "",
      location: garden.location || "",
      sunlight: garden.sunlight || "",
      soilType: garden.soilType || "",
      description: garden.description || ""
    });
  }

  async function handleUpdate(event: FormEvent<HTMLFormElement>, gardenId: string) {
    event.preventDefault();
    const name = editForm.name.trim();
    if (!name || busy) return;
    setBusy(true);
    setError("");
    try {
      await updateGarden(gardenId, {
        name,
        location: editForm.location.trim() || undefined,
        sunlight: editForm.sunlight.trim() || undefined,
        soilType: editForm.soilType.trim() || undefined,
        description: editForm.description.trim() || undefined
      });
      setEditingId(null);
      await refresh();
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "텃밭을 수정하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(gardenId: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await deleteGarden(gardenId);
      setPendingDeleteId(null);
      await refresh();
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "텃밭을 삭제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <PageState kind="loading" title="텃밭을 불러오고 있어요" />;

  const membersOf = (gardenId: string) => plants.filter((plant) => plant.gardenId === gardenId);
  const assignableTo = (gardenId: string) => plants.filter((plant) => plant.gardenId !== gardenId);

  return (
    <div className="garden-page">
      <header className="garden-heading">
        <div>
          <span className="eyebrow">MY GARDEN</span>
          <h1>내 텃밭</h1>
          <p>여러 작물을 구획으로 묶어, 같은 밭의 흙·일조 환경과 함께 관리해요.</p>
        </div>
        <button className="button button-primary button-small" type="button" onClick={() => setCreating((value) => !value)}>
          <span className="material-symbols-outlined" aria-hidden="true">add</span>새 텃밭 만들기
        </button>
      </header>

      {error && <div className="alert alert-error" role="alert">{error}</div>}

      {creating && (
        <form className="garden-create form-card" onSubmit={handleCreate}>
          <div className="form-grid">
            <label className="field"><span>텃밭 이름 <b aria-label="필수">*</b></span><input autoFocus maxLength={40} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="예: 베란다 텃밭" required value={form.name} /></label>
            <label className="field"><span>위치</span><input maxLength={60} onChange={(event) => setForm({ ...form, location: event.target.value })} placeholder="예: 남향 베란다" value={form.location} /></label>
            <label className="field"><span>일조 환경</span><input maxLength={40} onChange={(event) => setForm({ ...form, sunlight: event.target.value })} placeholder="예: 오전 직사광선" value={form.sunlight} /></label>
            <label className="field"><span>토양</span><input maxLength={40} onChange={(event) => setForm({ ...form, soilType: event.target.value })} placeholder="예: 상토 + 마사토" value={form.soilType} /></label>
          </div>
          <div className="form-actions">
            <button className="button button-secondary" type="button" onClick={() => { setCreating(false); setForm(emptyForm); }}>취소</button>
            <button className="button button-primary" type="submit" disabled={!form.name.trim() || busy}>{busy ? "만드는 중…" : "텃밭 만들기"}</button>
          </div>
        </form>
      )}

      {gardens.length === 0 ? (
        <p className="garden-empty">아직 텃밭이 없어요. 위의 <strong>새 텃밭 만들기</strong>로 첫 구획을 만들어 보세요.</p>
      ) : (
        <div className="garden-grid">
          {gardens.map((garden) => {
            const members = membersOf(garden.id);
            const addable = assignableTo(garden.id);
            return (
              <article className="garden-card" key={garden.id}>
                <div className="garden-card-media" aria-hidden="true">
                  {garden.imageUrl ? <img src={garden.imageUrl} alt="" /> : <span className="material-symbols-outlined">potted_plant</span>}
                </div>
                <div className="garden-card-body">
                  {editingId === garden.id ? (
                    <form className="garden-edit" onSubmit={(event) => handleUpdate(event, garden.id)}>
                      <label className="field"><span>텃밭 이름 <b aria-label="필수">*</b></span><input autoFocus maxLength={40} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} required value={editForm.name} /></label>
                      <label className="field"><span>위치</span><input maxLength={60} onChange={(event) => setEditForm({ ...editForm, location: event.target.value })} value={editForm.location} /></label>
                      <label className="field"><span>일조 환경</span><input maxLength={40} onChange={(event) => setEditForm({ ...editForm, sunlight: event.target.value })} value={editForm.sunlight} /></label>
                      <label className="field"><span>토양</span><input maxLength={40} onChange={(event) => setEditForm({ ...editForm, soilType: event.target.value })} value={editForm.soilType} /></label>
                      <label className="field"><span>설명</span><input maxLength={80} onChange={(event) => setEditForm({ ...editForm, description: event.target.value })} value={editForm.description} /></label>
                      <div className="form-actions">
                        <button className="button button-secondary button-small" type="button" onClick={() => setEditingId(null)}>취소</button>
                        <button className="button button-primary button-small" type="submit" disabled={!editForm.name.trim() || busy}>{busy ? "저장 중…" : "저장"}</button>
                      </div>
                    </form>
                  ) : (
                  <>
                  <div className="garden-card-titlebar">
                    <h2>{garden.name}</h2>
                    <div className="garden-card-actions">
                      <button type="button" disabled={busy} onClick={() => startEdit(garden)}>수정</button>
                      <button className="is-danger" type="button" disabled={busy} onClick={() => { setPendingDeleteId(garden.id); setEditingId(null); }}>삭제</button>
                    </div>
                  </div>
                  <p className="garden-card-meta">{garden.location || "위치 미등록"} · 작물 {members.length}종</p>
                  {garden.description && <p className="garden-card-desc">{garden.description}</p>}
                  <dl className="garden-card-facts">
                    {garden.sunlight && <div><dt>일조</dt><dd>{garden.sunlight}</dd></div>}
                    {garden.soilType && <div><dt>토양</dt><dd>{garden.soilType}</dd></div>}
                  </dl>

                  {pendingDeleteId === garden.id && (
                    <div className="garden-delete-confirm" role="alert" aria-live="assertive">
                      <p>‘{garden.name}’ 텃밭을 삭제할까요? 담긴 작물은 삭제되지 않고 텃밭 배정만 해제돼요.</p>
                      <div>
                        <button type="button" disabled={busy} onClick={() => setPendingDeleteId(null)}>취소</button>
                        <button className="is-danger" type="button" disabled={busy} onClick={() => handleDelete(garden.id)}>{busy ? "삭제 중…" : "삭제"}</button>
                      </div>
                    </div>
                  )}

                  <div className="garden-members">
                    {members.length > 0 ? (
                      <ul className="garden-member-list">
                        {members.map((plant) => (
                          <li key={plant.id}>
                            <span className="material-symbols-outlined" aria-hidden="true">eco</span>
                            <span>{plant.name}</span>
                            <button type="button" aria-label={`${plant.name}을(를) 텃밭에서 빼기`} disabled={busy} onClick={() => assignPlant(plant.id, null)}>×</button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="garden-member-empty">아직 이 텃밭에 담긴 작물이 없어요.</p>
                    )}

                    {addable.length > 0 && (
                      <label className="garden-add-plant">
                        <span className="sr-only">이 텃밭에 기존 식물 추가</span>
                        <select
                          disabled={busy}
                          value=""
                          onChange={(event) => {
                            if (event.target.value) assignPlant(event.target.value, garden.id);
                          }}
                        >
                          <option value="">+ 기존 식물 추가…</option>
                          {addable.map((plant) => (
                            <option key={plant.id} value={plant.id}>
                              {plant.name}{plant.gardenId ? " (다른 텃밭)" : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>

                  <button
                    className="button button-secondary button-small garden-chat-button"
                    type="button"
                    onClick={() => {
                      setSelectedGardenId(garden.id);
                      onNavigate("chat");
                    }}
                  >
                    <span className="material-symbols-outlined" aria-hidden="true">chat_bubble</span>이 텃밭 상담
                  </button>
                  </>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
