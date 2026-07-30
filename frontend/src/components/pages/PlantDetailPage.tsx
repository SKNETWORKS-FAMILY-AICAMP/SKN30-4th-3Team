import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { createCareLog, deletePlant, getPlant, storagePathToPublicUrl, updatePlant, uploadPlantPhoto } from "../../api";
import dashboardPlantImage from "../../assets/dashboard-plant.webp";
import type { DesignPage } from "../../lib/constants";
import { getSelectedPlantId } from "../../lib/storage";
import type { CareLog, Plant, PlantPhoto } from "../../types";
import { PageState } from "../PageState";

interface PlantDetailPageProps {
  onNavigate: (page: DesignPage) => void;
  onAuthError: (error: unknown) => boolean;
}

type PlantDetail = Plant & { careLogs: CareLog[]; photos: PlantPhoto[] };

export function PlantDetailPage({ onNavigate, onAuthError }: PlantDetailPageProps) {
  const plantId = getSelectedPlantId();
  const [plant, setPlant] = useState<PlantDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [memo, setMemo] = useState("");
  const [leafCondition, setLeafCondition] = useState("");
  const [soilCondition, setSoilCondition] = useState("");
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editSpecies, setEditSpecies] = useState("");
  const [editLocation, setEditLocation] = useState("");
  const [editSunlight, setEditSunlight] = useState("");
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!plantId) {
      setLoading(false);
      return;
    }

    let active = true;
    setLoading(true);
    setError("");
    getPlant(plantId)
      .then((row) => {
        if (active) setPlant(row);
      })
      .catch((caughtError: unknown) => {
        if (!active || onAuthError(caughtError)) return;
        setError(caughtError instanceof Error ? caughtError.message : "식물 상세 정보를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [onAuthError, plantId, reloadKey]);

  async function handleCareLogSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plantId) return;
    setSaving(true);
    setError("");
    try {
      await createCareLog(plantId, {
        wateredAt: new Date().toISOString().slice(0, 10),
        leafCondition: leafCondition || undefined,
        soilCondition: soilCondition || undefined,
        memo: memo.trim() || undefined
      });
      setMemo("");
      setLeafCondition("");
      setSoilCondition("");
      setReloadKey((value) => value + 1);
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "관리 기록을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!plantId || !plant) return;
    if (!window.confirm(`${plant.name}와 연결된 관리 기록을 삭제할까요? 이 작업은 되돌릴 수 없습니다.`)) return;
    try {
      await deletePlant(plantId);
      onNavigate("dashboard");
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "식물을 삭제하지 못했습니다.");
    }
  }

  function startEditing() {
    if (!plant) return;
    setEditName(plant.name);
    setEditSpecies(plant.species || "");
    setEditLocation(plant.location || "");
    setEditSunlight(plant.sunlight || "");
    setEditing(true);
  }

  async function handleEditSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plantId || !editName.trim()) return;
    setSaving(true);
    setError("");
    try {
      const updated = await updatePlant(plantId, {
        name: editName.trim(),
        species: editSpecies.trim() || undefined,
        location: editLocation.trim() || undefined,
        sunlight: editSunlight || undefined
      });
      setPlant((current) => current ? { ...current, ...updated } : current);
      setEditing(false);
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "식물 정보를 수정하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  async function handlePhotoUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !plantId) return;
    setUploadingPhoto(true);
    setError("");
    try {
      await uploadPlantPhoto(plantId, file, "상세 화면에서 추가한 관찰 사진");
      setReloadKey((value) => value + 1);
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "사진을 추가하지 못했습니다.");
    } finally {
      setUploadingPhoto(false);
      event.target.value = "";
    }
  }

  if (!plantId) {
    return <PageState kind="empty" title="선택된 식물이 없어요" actionLabel="내 식물로 이동" onAction={() => onNavigate("dashboard")} />;
  }
  if (loading) return <PageState kind="loading" title="식물 기록을 불러오고 있어요" />;
  if (error && !plant) {
    return <PageState kind="error" title="식물 기록을 불러오지 못했어요" description={error} actionLabel="다시 시도" onAction={() => setReloadKey((value) => value + 1)} />;
  }
  if (!plant) return <PageState kind="empty" title="식물 정보를 찾지 못했어요" actionLabel="내 식물로 이동" onAction={() => onNavigate("dashboard")} />;

  const latestPhoto = plant.photos[0];
  const latestPhotoUrl = storagePathToPublicUrl(latestPhoto?.storagePath) || plant.imageUrl;
  const displaySpecies = plant.displaySpecies || plant.species;
  const showScientificName = Boolean(plant.displaySpecies && plant.species && plant.displaySpecies !== plant.species);

  return (
    <div className="page-container">
      <div className="detail-toolbar">
        <button className="back-button" type="button" onClick={() => onNavigate("dashboard")}><span className="material-symbols-outlined" aria-hidden="true">arrow_back</span>내 식물</button>
        <div><button className="button button-secondary button-small" type="button" onClick={startEditing}><span className="material-symbols-outlined" aria-hidden="true">edit</span>정보 수정</button></div>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      <section className="detail-hero">
        <div className="detail-photo">
          {latestPhotoUrl ? <img alt={`${plant.name}의 최근 관찰 사진`} src={latestPhotoUrl} onError={(event) => { event.currentTarget.src = dashboardPlantImage; }} /> : <span className="material-symbols-outlined" aria-hidden="true">potted_plant</span>}
          <label className="photo-upload-button">
            <span className="material-symbols-outlined" aria-hidden="true">photo_camera</span>{uploadingPhoto ? "업로드 중" : "새 사진"}
            <input accept="image/jpeg,image/png,image/webp" disabled={uploadingPhoto} onChange={handlePhotoUpload} type="file" />
          </label>
        </div>
        <div className="detail-copy">
          <span className="eyebrow">PLANT PROFILE</span>
          <h1>{plant.name}</h1>
          <p className="species-name">{displaySpecies || "식물 종류 미등록"}</p>
          {showScientificName && <p className="scientific-name">학명 {plant.species}</p>}
          <dl className="detail-facts">
            <div><span className="material-symbols-outlined" aria-hidden="true">location_on</span><div><dt>위치</dt><dd>{plant.location || "미등록"}</dd></div></div>
            <div><span className="material-symbols-outlined" aria-hidden="true">light_mode</span><div><dt>빛 환경</dt><dd>{plant.sunlight || "미등록"}</dd></div></div>
            <div><span className="material-symbols-outlined" aria-hidden="true">edit_calendar</span><div><dt>관리 기록</dt><dd>{plant.careLogs.length}개</dd></div></div>
            <div><span className="material-symbols-outlined" aria-hidden="true">photo_library</span><div><dt>성장 사진</dt><dd>{plant.photos.length}장</dd></div></div>
          </dl>
          <div className="hero-actions hero-actions-light">
            <button className="button button-primary" type="button" onClick={() => onNavigate("chat")}><span className="material-symbols-outlined" aria-hidden="true">temp_preferences_eco</span>AI에게 물어보기</button>
            <button className="button button-secondary" type="button" onClick={() => document.getElementById("care-form-title")?.scrollIntoView({ behavior: "smooth" })}><span className="material-symbols-outlined" aria-hidden="true">add_task</span>오늘 기록 남기기</button>
          </div>
        </div>
      </section>

      {editing && (
        <section className="edit-panel" aria-labelledby="edit-plant-title">
          <div className="section-heading"><div><span className="eyebrow">EDIT PROFILE</span><h2 id="edit-plant-title">식물 정보 수정</h2></div><button className="icon-button" type="button" onClick={() => setEditing(false)} aria-label="수정 닫기"><span className="material-symbols-outlined" aria-hidden="true">close</span></button></div>
          <form className="stack-form" onSubmit={handleEditSubmit}>
            <div className="form-grid"><label className="field"><span>식물 이름</span><input required maxLength={60} value={editName} onChange={(event) => setEditName(event.target.value)} /></label><label className="field"><span>식물 종류</span><input maxLength={100} value={editSpecies} onChange={(event) => setEditSpecies(event.target.value)} /></label></div>
            <div className="form-grid"><label className="field"><span>위치</span><input maxLength={80} value={editLocation} onChange={(event) => setEditLocation(event.target.value)} /></label><label className="field"><span>빛 환경</span><select value={editSunlight} onChange={(event) => setEditSunlight(event.target.value)}><option value="">선택하지 않음</option><option value="밝은 간접광">밝은 간접광</option><option value="오전 직사광">오전 직사광</option><option value="하루 종일 직사광">하루 종일 직사광</option><option value="빛이 적은 실내">빛이 적은 실내</option></select></label></div>
            <div className="form-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(false)}>취소</button><button className="button button-primary" disabled={saving} type="submit">{saving ? "저장 중…" : "변경사항 저장"}</button></div>
          </form>
        </section>
      )}

      <div className="detail-grid">
        <section className="content-section" aria-labelledby="care-form-title">
          <div className="section-heading">
            <div><span className="eyebrow">TODAY'S NOTE</span><h2 id="care-form-title">오늘의 관리 기록</h2><p>직접 확인한 상태만 간단히 남겨도 충분해요.</p></div>
          </div>
          <form className="stack-form" onSubmit={handleCareLogSubmit}>
            <div className="form-grid">
              <label className="field"><span>잎 상태</span><select onChange={(event) => setLeafCondition(event.target.value)} value={leafCondition}><option value="">선택하지 않음</option><option value="변화 없음">변화 없음</option><option value="잎 처짐 관찰">잎 처짐 관찰</option><option value="황화 관찰">황화 관찰</option><option value="반점 관찰">반점 관찰</option></select></label>
              <label className="field"><span>흙 상태</span><select onChange={(event) => setSoilCondition(event.target.value)} value={soilCondition}><option value="">선택하지 않음</option><option value="겉흙 마름">겉흙 마름</option><option value="약간 촉촉함">약간 촉촉함</option><option value="매우 젖어 있음">매우 젖어 있음</option></select></label>
            </div>
            <label className="field"><span>메모</span><textarea maxLength={500} onChange={(event) => setMemo(event.target.value)} placeholder="예: 새잎은 잘 펴졌고, 아래쪽 잎 끝이 조금 마른 상태예요." rows={4} value={memo} /></label>
            <button className="button button-primary" disabled={saving} type="submit">{saving ? "저장 중…" : "오늘 기록 저장"}<span className="material-symbols-outlined" aria-hidden="true">check</span></button>
          </form>
        </section>

        <section className="content-section" aria-labelledby="history-title">
          <div className="section-heading"><div><span className="eyebrow">CARE HISTORY</span><h2 id="history-title">최근 기록</h2><p>시간순으로 쌓인 관리 이력을 확인해요.</p></div></div>
          {plant.careLogs.length === 0 ? <div className="inline-empty"><span className="material-symbols-outlined" aria-hidden="true">edit_calendar</span><div><strong>아직 관리 기록이 없어요</strong><p>첫 기록을 남기면 물주기와 잎·흙 상태의 변화를 시간순으로 비교할 수 있어요.</p></div></div> : (
            <ol className="timeline">
              {plant.careLogs.map((log) => (
                <li key={log.id}>
                  <time dateTime={log.createdAt}>{new Date(log.createdAt).toLocaleDateString("ko-KR")}</time>
                  <strong>{log.wateredAt ? "물주기 및 관찰 기록" : "관찰 기록"}</strong>
                  <p>{[log.leafCondition, log.soilCondition, log.memo].filter(Boolean).join(" · ") || "메모 없음"}</p>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
      <section className="danger-zone" aria-labelledby="danger-zone-title"><div><h2 id="danger-zone-title">이 식물을 더 이상 관리하지 않나요?</h2><p>식물과 연결된 사진 및 관리 기록이 함께 삭제되며 되돌릴 수 없습니다.</p></div><button className="button button-danger-ghost" type="button" onClick={handleDelete}>식물 삭제</button></section>
    </div>
  );
}
