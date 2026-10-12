import { NavLink, Route, Routes } from "react-router-dom";
import Overview from "./Overview";
import Questions from "./Questions";
import QuestionDetail from "./QuestionDetail";
import Reports from "./Reports";
import { Banks, Corrections, Subjects } from "./Content";
import { BlueprintEditor, Blueprints } from "./Blueprints";
import { Products, Transactions } from "./Commerce";
import { OrderDetail, Orders, Plans } from "./Payments";
import { QaQueues, SubjectReconciliation } from "./Reconcile";
import { UserDetail, Users } from "./Users";
import Settings from "./Settings";
import Audit from "./Audit";
import { lazy, Suspense } from "react";
import { Navigate } from "react-router-dom";
import { Spinner } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import ReviewDashboard from "./review/ReviewDashboard";
import ReviewQueue from "./review/ReviewQueue";

// the studio (editor, MathLive loader) is its own chunk
const ReviewStudio = lazy(() => import("./review/ReviewStudio"));

const NAV: ([string, string] | string)[] = [
  ["/admin", "Tổng quan"],
  "Review Studio",
  ["/admin/review", "Tổng quan duyệt"],
  ["/admin/review/queue", "Hàng đợi duyệt"],
  "Ngân hàng câu hỏi",
  ["/admin/cau-hoi", "Câu hỏi"],
  ["/admin/hang-doi-qa", "Hàng đợi QA"],
  ["/admin/doi-soat-mon", "Đối soát theo môn"],
  ["/admin/ngan-hang", "Ngân hàng & đồng bộ"],
  ["/admin/mon-hoc", "Môn học"],
  ["/admin/bao-loi", "Báo lỗi"],
  ["/admin/de-xuat-sua", "Đề xuất sửa"],
  "Đề thi thử",
  ["/admin/de-thi", "Đề thi & giá"],
  "Kinh doanh",
  ["/admin/goi", "Gói luyện tập & giá"],
  ["/admin/don-hang", "Thanh toán & đơn hàng"],
  ["/admin/giao-dich", "Giao dịch ngân hàng"],
  ["/admin/goi-ban", "Gói lượt thi"],
  "Người dùng",
  ["/admin/nguoi-dung", "Người dùng"],
  "Hệ thống",
  ["/admin/cai-dat", "Cài đặt"],
  ["/admin/nhat-ky", "Nhật ký"],
];

const REVIEW_NAV = NAV.slice(1, 4);

export default function AdminApp() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const review = (
    <>
      <Route path="review" element={<ReviewDashboard />} />
      <Route path="review/queue" element={<ReviewQueue />} />
      <Route path="review/q/:id" element={<Suspense fallback={<Spinner />}><ReviewStudio /></Suspense>} />
    </>
  );
  return (
    <div className="admin">
      <nav className="admin-nav" aria-label="Quản trị">
        {(isAdmin ? NAV : REVIEW_NAV).map((n, i) => typeof n === "string"
          ? <div key={i} className="grp">{n}</div>
          : <NavLink key={i} to={n[0]} end={n[0] === "/admin" || n[0] === "/admin/review"}>{n[1]}</NavLink>)}
      </nav>
      <div className="admin-main">
        {!isAdmin ? (
          <Routes>
            {review}
            <Route path="*" element={<Navigate to="/admin/review" replace />} />
          </Routes>
        ) : (
        <Routes>
          {review}
          <Route index element={<Overview />} />
          <Route path="cau-hoi" element={<Questions />} />
          <Route path="cau-hoi/:id" element={<QuestionDetail />} />
          <Route path="bao-loi" element={<Reports />} />
          <Route path="de-xuat-sua" element={<Corrections />} />
          <Route path="ngan-hang" element={<Banks />} />
          <Route path="mon-hoc" element={<Subjects />} />
          <Route path="de-thi" element={<Blueprints />} />
          <Route path="de-thi/moi" element={<BlueprintEditor />} />
          <Route path="de-thi/:id" element={<BlueprintEditor />} />
          <Route path="goi-ban" element={<Products />} />
          <Route path="don-hang" element={<Orders />} />
          <Route path="don-hang/:code" element={<OrderDetail />} />
          <Route path="goi" element={<Plans />} />
          <Route path="hang-doi-qa" element={<QaQueues />} />
          <Route path="doi-soat-mon" element={<SubjectReconciliation />} />
          <Route path="giao-dich" element={<Transactions />} />
          <Route path="nguoi-dung" element={<Users />} />
          <Route path="nguoi-dung/:id" element={<UserDetail />} />
          <Route path="cai-dat" element={<Settings />} />
          <Route path="nhat-ky" element={<Audit />} />
          <Route path="*" element={<div className="empty">Không tìm thấy trang quản trị.</div>} />
        </Routes>
        )}
      </div>
    </div>
  );
}
