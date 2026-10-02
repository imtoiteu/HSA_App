import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Icon, Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Landing from "./pages/Landing";
import { Forgot, Login, Register, Reset } from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import Practice from "./pages/Practice";
import Upgrade from "./pages/Upgrade";
import Exams from "./pages/Exams";
import Runner from "./pages/Runner";
import Result from "./pages/Result";
import History from "./pages/History";
import Bookmarks from "./pages/Bookmarks";
import Checkout from "./pages/Checkout";
import Account from "./pages/Account";
import NotFound from "./pages/NotFound";

const Admin = lazy(() => import("./pages/admin/AdminApp"));

function RequireAuth({ children, admin }: { children: ReactNode; admin?: boolean }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) return <Spinner />;
  if (!user) return <Navigate to={`/dang-nhap?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  if (admin && user.role !== "admin") return <Navigate to="/tong-quan" replace />;
  return <>{children}</>;
}

function TopBar() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <header className="topbar">
      <div className="container">
        <Link to={user ? "/tong-quan" : "/"} className="brand" aria-label="Trang chủ">
          <span className="brand-mark">HSA</span>
          <span>Luyện thi HSA</span>
        </Link>
        {user && (
          <nav className={"nav" + (open ? " open" : "")} aria-label="Điều hướng chính">
            <NavLink to="/tong-quan">Tổng quan</NavLink>
            <NavLink to="/luyen-tap">Luyện tập</NavLink>
            <NavLink to="/de-thi">Đề thi thử</NavLink>
            <NavLink to="/lich-su">Lịch sử</NavLink>
            <NavLink to="/cau-hoi-da-luu">Câu đã lưu</NavLink>
            {user.role !== "admin" && <NavLink to="/nang-cap" className="nav-pro">Gói Pro</NavLink>}
            {user.role === "admin" && <NavLink to="/admin">Quản trị</NavLink>}
            <NavLink to="/tai-khoan" className="nav-account">Tài khoản</NavLink>
          </nav>
        )}
        <div className="spacer" />
        {user ? (
          <div className="row" style={{ gap: 6 }}>
            <NavLink to="/tai-khoan" className="btn ghost sm user-name" title="Tài khoản">{user.display_name}</NavLink>
            <button className="btn secondary sm" onClick={logout}>Đăng xuất</button>
            <button className="icon-btn menu-btn" aria-label="Mở menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              <Icon name="menu" />
            </button>
          </div>
        ) : (
          <div className="row" style={{ gap: 8 }}>
            <Link to="/dang-nhap" className="btn ghost sm">Đăng nhập</Link>
            <Link to="/dang-ky" className="btn sm">Đăng ký miễn phí</Link>
          </div>
        )}
      </div>
    </header>
  );
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <TopBar />
      <main className="grow">{children}</main>
      <footer className="footer">
        <div className="container row between">
          <span>© {new Date().getFullYear()} Luyện thi HSA · Ôn luyện Đánh giá năng lực</span>
          <span className="small">Câu hỏi được biên tập và kiểm duyệt trước khi phục vụ.</span>
        </div>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* distraction-free runner: no site chrome */}
        <Route path="/lam-bai/:id" element={<RequireAuth><Runner /></RequireAuth>} />
        <Route path="/admin/*" element={
          <RequireAuth admin>
            <div className="shell"><TopBar />
              <Suspense fallback={<Spinner />}><Admin /></Suspense>
            </div>
          </RequireAuth>} />
        <Route path="*" element={
          <Shell>
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/dang-nhap" element={<Login />} />
              <Route path="/dang-ky" element={<Register />} />
              <Route path="/quen-mat-khau" element={<Forgot />} />
              <Route path="/dat-lai-mat-khau" element={<Reset />} />
              <Route path="/tong-quan" element={<RequireAuth><Dashboard /></RequireAuth>} />
              <Route path="/luyen-tap" element={<RequireAuth><Practice /></RequireAuth>} />
              <Route path="/de-thi" element={<Exams />} />
              <Route path="/ket-qua/:id" element={<RequireAuth><Result /></RequireAuth>} />
              <Route path="/lich-su" element={<RequireAuth><History /></RequireAuth>} />
              <Route path="/cau-hoi-da-luu" element={<RequireAuth><Bookmarks /></RequireAuth>} />
              <Route path="/thanh-toan/:code" element={<RequireAuth><Checkout /></RequireAuth>} />
              <Route path="/tai-khoan" element={<RequireAuth><Account /></RequireAuth>} />
              <Route path="/nang-cap" element={<Upgrade />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Shell>} />
      </Routes>
    </BrowserRouter>
  );
}
