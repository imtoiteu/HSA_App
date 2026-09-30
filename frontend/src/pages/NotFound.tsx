import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="container page center">
      <div style={{ fontSize: "3rem" }} aria-hidden>🧭</div>
      <h1>Không tìm thấy trang</h1>
      <p className="muted">Đường dẫn có thể đã thay đổi hoặc không tồn tại.</p>
      <Link to="/" className="btn">Về trang chủ</Link>
    </div>
  );
}
