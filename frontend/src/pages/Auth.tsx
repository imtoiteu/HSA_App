import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ErrorBox } from "../components/ui";
import { post } from "../lib/api";
import { useAuth } from "../lib/auth";

function AuthCard({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="container page" style={{ maxWidth: 460 }}>
      <div className="card pad-lg">
        <h1 style={{ fontSize: "1.6rem" }}>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
        {children}
      </div>
    </div>
  );
}

function safeNext(p: string | null) {
  return p && p.startsWith("/") && !p.startsWith("//") ? p : "/tong-quan";
}

export function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const u = await login(email, password);
      nav(u.role === "admin" && !params.get("next") ? "/admin" : safeNext(params.get("next")), { replace: true });
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Đăng nhập" subtitle="Tiếp tục hành trình luyện thi của bạn.">
      <form className="stack" onSubmit={submit}>
        <div className="field"><label htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div className="field"><label htmlFor="pw">Mật khẩu</label>
          <input id="pw" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <ErrorBox error={err} />
        <button className="btn block lg" disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}</button>
        <div className="row between small">
          <Link to="/quen-mat-khau">Quên mật khẩu?</Link>
          <span>Chưa có tài khoản? <Link to={`/dang-ky${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`}>Đăng ký</Link></span>
        </div>
      </form>
    </AuthCard>
  );
}

export function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await register(email, password, name);
      nav(safeNext(params.get("next")), { replace: true });
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };
  return (
    <AuthCard title="Tạo tài khoản" subtitle="Miễn phí luyện tập với ngân hàng câu hỏi đã kiểm duyệt.">
      <form className="stack" onSubmit={submit}>
        <div className="field"><label htmlFor="name">Họ và tên</label>
          <input id="name" className="input" autoComplete="name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="field"><label htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
        <div className="field"><label htmlFor="pw">Mật khẩu</label>
          <input id="pw" className="input" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="hint">Ít nhất 8 ký tự.</span></div>
        <ErrorBox error={err} />
        <button className="btn block lg" disabled={busy}>{busy ? "Đang tạo tài khoản…" : "Đăng ký"}</button>
        <div className="small center">Đã có tài khoản? <Link to="/dang-nhap">Đăng nhập</Link></div>
      </form>
    </AuthCard>
  );
}

export function Forgot() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await post<{ message: string }>("/api/auth/forgot-password", { email });
      setDone(r.message);
    } catch (ex) {
      setErr(ex);
    }
  };
  return (
    <AuthCard title="Quên mật khẩu" subtitle="Nhập email đã đăng ký, chúng tôi sẽ gửi hướng dẫn đặt lại mật khẩu.">
      {done ? <div className="alert ok">{done} Nếu không nhận được email, hãy liên hệ quản trị viên để được cấp liên kết đặt lại.</div> : (
        <form className="stack" onSubmit={submit}>
          <div className="field"><label htmlFor="email">Email</label>
            <input id="email" className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          <ErrorBox error={err} />
          <button className="btn block">Gửi hướng dẫn</button>
        </form>
      )}
      <div className="small mt"><Link to="/dang-nhap">← Quay lại đăng nhập</Link></div>
    </AuthCard>
  );
}

export function Reset() {
  const [params] = useSearchParams();
  const { setUser } = useAuth();
  const nav = useNavigate();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await post<{ user: any }>("/api/auth/reset-password", { token: params.get("token") || "", password });
      setUser(r.user);
      nav("/tong-quan", { replace: true });
    } catch (ex) {
      setErr(ex);
    }
  };
  return (
    <AuthCard title="Đặt lại mật khẩu">
      <form className="stack" onSubmit={submit}>
        <div className="field"><label htmlFor="pw">Mật khẩu mới</label>
          <input id="pw" className="input" type="password" autoComplete="new-password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></div>
        <ErrorBox error={err} />
        <button className="btn block">Đặt lại mật khẩu</button>
      </form>
    </AuthCard>
  );
}
