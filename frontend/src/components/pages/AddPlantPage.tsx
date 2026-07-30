import { useEffect, useState, type FormEvent } from "react";
import {
  createGarden,
  createPlant,
  listGardens,
  searchPlantCatalog,
  storagePathToPublicUrl,
  updatePlant,
  uploadPlantPhoto
} from "../../api";
import type { DesignPage } from "../../lib/constants";
import { setSelectedPlantId } from "../../lib/storage";
import type { Garden, PlantCatalogItem } from "../../types";

interface AddPlantPageProps {
  onNavigate: (page: DesignPage) => void;
  onAuthError: (error: unknown) => boolean;
}

export function AddPlantPage({ onNavigate, onAuthError }: AddPlantPageProps) {
  const [name, setName] = useState("");
  const [species, setSpecies] = useState("");
  const [location, setLocation] = useState("");
  const [sunlight, setSunlight] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [catalogItems, setCatalogItems] = useState<PlantCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [gardens, setGardens] = useState<Garden[]>([]);
  const [gardenChoice, setGardenChoice] = useState(""); // "" = 없음, gardenId, "__new__"
  const [newGardenName, setNewGardenName] = useState("");

  useEffect(() => {
    if (!photo) {
      setPreviewUrl("");
      return;
    }
    const objectUrl = URL.createObjectURL(photo);
    setPreviewUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [photo]);

  useEffect(() => {
    let active = true;
    listGardens()
      .then((rows) => {
        if (active) setGardens(rows);
      })
      .catch(() => {
        /* 텃밭을 불러오지 못해도 식물 등록은 계속 진행 가능 */
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (species.trim().length < 2) {
      setCatalogItems([]);
      setCatalogError("");
      return;
    }

    let active = true;
    const timer = window.setTimeout(() => {
      searchPlantCatalog(species.trim(), 6)
        .then((items) => {
          if (!active) return;
          setCatalogItems(items);
          setCatalogError("");
        })
        .catch(() => {
          if (!active) return;
          setCatalogItems([]);
          setCatalogError("식물 도감 검색을 사용할 수 없습니다. 직접 입력은 가능합니다.");
        });
    }, 250);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [species]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      let gardenId: string | undefined;
      if (gardenChoice === "__new__") {
        const trimmedGarden = newGardenName.trim();
        if (trimmedGarden) {
          const garden = await createGarden({
            name: trimmedGarden,
            location: location.trim() || undefined,
            sunlight: sunlight.trim() || undefined
          });
          gardenId = garden.id;
        }
      } else if (gardenChoice) {
        gardenId = gardenChoice;
      }

      const plant = await createPlant({
        name: name.trim(),
        species: species.trim() || undefined,
        location: location.trim() || undefined,
        sunlight: sunlight.trim() || undefined,
        gardenId
      });

      if (photo) {
        const uploadedPhoto = await uploadPlantPhoto(plant.id, photo, "등록 시 추가한 첫 관찰 사진");
        const imageUrl = storagePathToPublicUrl(uploadedPhoto.storagePath);
        if (!imageUrl) {
          throw new Error("업로드한 사진의 대표 이미지 URL을 생성하지 못했습니다.");
        }
        await updatePlant(plant.id, { imageUrl });
      }

      setSelectedPlantId(plant.id);
      onNavigate("detail");
    } catch (caughtError) {
      if (onAuthError(caughtError)) return;
      setError(caughtError instanceof Error ? caughtError.message : "식물을 등록하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page-container page-container-narrow">
      <header className="page-heading">
        <button className="back-button" type="button" onClick={() => onNavigate("dashboard")}><span className="material-symbols-outlined" aria-hidden="true">arrow_back</span>내 식물</button>
        <span className="eyebrow">ADD A NEW PLANT</span>
        <h1>새로운 초록이를 소개해 주세요</h1>
        <p>아는 만큼만 입력해도 괜찮아요. 나중에 언제든 수정할 수 있습니다.</p>
      </header>

      <div className="add-layout">
        <aside className="add-guide" aria-label="식물 등록 안내">
          <div className="add-guide-visual">
            {previewUrl ? <img src={previewUrl} alt="등록할 식물 미리보기" /> : <span className="material-symbols-outlined" aria-hidden="true">potted_plant</span>}
          </div>
          <h2>{name || "우리 집 새 식물"}</h2>
          <p>{species || "식물 종류를 입력하면 여기에 표시돼요."}</p>
          <ol className="register-steps">
            <li className="is-current"><span>1</span><div><strong>기본 정보</strong><small>이름과 식물 종류</small></div></li>
            <li><span>2</span><div><strong>재배 환경</strong><small>위치와 빛 조건</small></div></li>
            <li><span>3</span><div><strong>첫 사진</strong><small>선택 사항</small></div></li>
          </ol>
        </aside>

        <form className="form-card" onSubmit={handleSubmit}>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
          <div className="form-section-heading"><span className="material-symbols-outlined" aria-hidden="true">badge</span><div><h2>기본 정보</h2><p>식물을 구분할 수 있는 이름을 지어주세요.</p></div></div>

          <label className="field">
            <span>식물 이름 <b aria-label="필수">*</b></span>
            <input autoFocus maxLength={60} onChange={(event) => setName(event.target.value)} placeholder="예: 초록이, 몬티" required value={name} />
            <small className="field-hint">집에서 부르는 애칭도 좋아요.</small>
          </label>

          <div className="field catalog-field">
            <label htmlFor="plant-species">식물 종류</label>
            <span className="input-with-icon"><span className="material-symbols-outlined" aria-hidden="true">search</span><input aria-describedby={catalogError ? "catalog-message" : undefined} autoComplete="off" id="plant-species" onChange={(event) => setSpecies(event.target.value)} placeholder="예: 몬스테라 델리시오사" value={species} /></span>
            {catalogError && <small className="field-message" id="catalog-message">{catalogError}</small>}
            {catalogItems.length > 0 && (
              <ul className="catalog-results" aria-label="식물 종류 검색 결과">
                {catalogItems.map((item) => (
                  <li key={item.id}><button type="button" onClick={() => { setSpecies(item.name); setCatalogItems([]); }}><span className="catalog-icon material-symbols-outlined" aria-hidden="true">eco</span><span><strong>{item.name}</strong><small>{item.species}{item.familyName ? ` · ${item.familyName}` : ""}</small></span><span className="material-symbols-outlined" aria-hidden="true">north_west</span></button></li>
                ))}
              </ul>
            )}
          </div>

          <div className="form-divider" />
          <div className="form-section-heading"><span className="material-symbols-outlined" aria-hidden="true">light_mode</span><div><h2>재배 환경</h2><p>현재 환경을 알면 더 알맞은 안내를 받을 수 있어요.</p></div></div>

          <div className="form-grid">
            <label className="field"><span>식물을 둔 위치</span><input maxLength={80} onChange={(event) => setLocation(event.target.value)} placeholder="예: 거실 남향 창가, 베란다 선반" value={location} /></label>
            <label className="field"><span>빛 환경</span><select onChange={(event) => setSunlight(event.target.value)} value={sunlight}><option value="">선택하지 않음</option><option value="밝은 간접광">밝은 간접광</option><option value="오전 직사광">오전 직사광</option><option value="하루 종일 직사광">하루 종일 직사광</option><option value="빛이 적은 실내">빛이 적은 실내</option></select></label>
          </div>

          <label className="field">
            <span>텃밭 <small className="field-optional">(선택)</small></span>
            <select onChange={(event) => setGardenChoice(event.target.value)} value={gardenChoice}>
              <option value="">텃밭에 넣지 않음</option>
              {gardens.map((garden) => <option key={garden.id} value={garden.id}>{garden.name}</option>)}
              <option value="__new__">+ 새 텃밭 만들기</option>
            </select>
            <small className="field-hint">여러 작물을 한 구획으로 묶어 함께 관리할 수 있어요.</small>
          </label>
          {gardenChoice === "__new__" && (
            <label className="field">
              <span>새 텃밭 이름 <b aria-label="필수">*</b></span>
              <input maxLength={40} onChange={(event) => setNewGardenName(event.target.value)} placeholder="예: 베란다 텃밭" value={newGardenName} />
            </label>
          )}

          <div className="form-divider" />
          <div className="form-section-heading"><span className="material-symbols-outlined" aria-hidden="true">photo_camera</span><div><h2>첫 관찰 사진</h2><p>지금 모습을 남겨두면 성장 변화를 비교하기 쉬워요.</p></div></div>
          <label className={photo ? "file-field has-file" : "file-field"}>
            <span className="file-field-icon material-symbols-outlined" aria-hidden="true">add_photo_alternate</span>
            <span><strong>{photo ? "다른 사진 선택" : "사진을 끌어놓거나 선택하세요"}</strong><small>JPG, PNG 또는 WEBP · 최대 8MB</small></span>
            <input accept="image/jpeg,image/png,image/webp" onChange={(event) => setPhoto(event.target.files?.[0] || null)} type="file" />
            {photo && <em>{photo.name}</em>}
          </label>

          <div className="form-actions">
            <button className="button button-secondary" type="button" onClick={() => onNavigate("dashboard")}>취소</button>
            <button className="button button-primary" disabled={submitting} type="submit">{submitting ? "등록 중…" : "식물 등록"}<span className="material-symbols-outlined" aria-hidden="true">arrow_forward</span></button>
          </div>
        </form>
      </div>
    </div>
  );
}
