import { useState, type ChangeEvent, type ReactNode } from "react";
import type { DesignPage } from "../lib/constants";
import { getUserProfilePhoto, setUserProfilePhoto } from "../lib/storage";
import { Brand } from "./Brand";
import { NotificationMenu } from "./NotificationMenu";

interface AppShellProps {
  children: ReactNode;
  currentPage: DesignPage;
  onNavigate: (page: DesignPage) => void;
  onNavigateToDashboardSection: (sectionId: string) => void;
  onLogout: () => void;
}

export function AppShell({ children, currentPage, onNavigate, onNavigateToDashboardSection, onLogout }: AppShellProps) {
  const [profilePhoto, setProfilePhoto] = useState(getUserProfilePhoto);
  const [profileMessage, setProfileMessage] = useState("");

  function handleProfilePhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      setProfileMessage("프로필 사진은 2MB 이하로 선택해 주세요.");
      event.target.value = "";
      return;
    }

    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const value = String(reader.result || "");
      setUserProfilePhoto(value);
      setProfilePhoto(value);
      setProfileMessage("프로필 사진을 변경했습니다.");
    }, { once: true });
    reader.addEventListener("error", () => setProfileMessage("사진을 불러오지 못했습니다."), { once: true });
    reader.readAsDataURL(file);
    event.target.value = "";
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <button className="brand-button" type="button" onClick={() => onNavigate("dashboard")} aria-label="Farm하니 홈"><Brand /></button>

        <nav className="desktop-nav" aria-label="주요 메뉴">
          <button className={currentPage === "dashboard" ? "nav-button is-active" : "nav-button"} type="button" onClick={() => onNavigate("dashboard")} aria-current={currentPage === "dashboard" ? "page" : undefined}>홈</button>
          <button className="nav-button" type="button" onClick={() => onNavigateToDashboardSection("checklist-section")}>체크리스트</button>
          <button className={currentPage === "garden" ? "nav-button is-active" : "nav-button"} type="button" onClick={() => onNavigate("garden")} aria-current={currentPage === "garden" ? "page" : undefined}>내 텃밭</button>
          <button className={currentPage === "chat" ? "nav-button is-active" : "nav-button"} type="button" onClick={() => onNavigate("chat")} aria-current={currentPage === "chat" ? "page" : undefined}>AI 상담</button>
        </nav>

        <div className="header-actions">
          <button className={currentPage === "add" ? "button button-primary button-small is-current" : "button button-primary button-small"} type="button" onClick={() => onNavigate("add")}>
            <span className="material-symbols-outlined" aria-hidden="true">add</span>식물 등록
          </button>
          <NotificationMenu onOpenChecklist={() => onNavigateToDashboardSection("checklist-section")} onNavigate={onNavigate} />
          <details className="profile-menu">
            <summary aria-label="회원 프로필 메뉴">
              {profilePhoto ? <img src={profilePhoto} alt="회원 프로필" /> : <span className="material-symbols-outlined" aria-hidden="true">person</span>}
            </summary>
            <div className="profile-popover">
              <div className="profile-popover-heading">
                <span className="profile-avatar-large">{profilePhoto ? <img src={profilePhoto} alt="" /> : <span className="material-symbols-outlined" aria-hidden="true">person</span>}</span>
                <div><strong>내 프로필</strong><small>사진과 계정 관리</small></div>
              </div>
              <label className="profile-upload"><span className="material-symbols-outlined" aria-hidden="true">photo_camera</span>프로필 사진 변경<input accept="image/jpeg,image/png,image/webp" onChange={handleProfilePhoto} type="file" /></label>
              {profileMessage && <p className="profile-message" role="status">{profileMessage}</p>}
              <button className="profile-logout" type="button" onClick={onLogout}><span className="material-symbols-outlined" aria-hidden="true">logout</span>로그아웃</button>
            </div>
          </details>
        </div>
      </header>

      <main className="app-main" id="main-content">{children}</main>
      <footer className="app-footer"><p>Farm하니의 AI 안내는 식물 상태를 관찰하기 위한 참고 정보이며 확정 진단이 아닙니다.</p></footer>

      <nav className="mobile-nav" aria-label="모바일 주요 메뉴">
        <button className={currentPage === "dashboard" ? "mobile-nav-button is-active" : "mobile-nav-button"} type="button" onClick={() => onNavigate("dashboard")} aria-current={currentPage === "dashboard" ? "page" : undefined}><span className="material-symbols-outlined" aria-hidden="true">home</span>홈</button>
        <button className={currentPage === "garden" ? "mobile-nav-button is-active" : "mobile-nav-button"} type="button" onClick={() => onNavigate("garden")} aria-current={currentPage === "garden" ? "page" : undefined}><span className="material-symbols-outlined" aria-hidden="true">potted_plant</span>텃밭</button>
        <button className={currentPage === "add" ? "mobile-nav-button is-active" : "mobile-nav-button"} type="button" onClick={() => onNavigate("add")} aria-current={currentPage === "add" ? "page" : undefined}><span className="material-symbols-outlined" aria-hidden="true">add_circle</span>식물 등록</button>
        <button className={currentPage === "chat" ? "mobile-nav-button is-active" : "mobile-nav-button"} type="button" onClick={() => onNavigate("chat")} aria-current={currentPage === "chat" ? "page" : undefined}><span className="material-symbols-outlined" aria-hidden="true">temp_preferences_eco</span>AI 상담</button>
      </nav>
    </div>
  );
}
