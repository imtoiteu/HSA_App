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

const NAV: ([string, string] | string)[] = [
  ["/admin", "Tổng quan"],
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

export default function AdminApp() {
  return (
    <div className="admin">
      <nav className="admin-nav" aria-label="Quản trị">
        {NAV.map((n, i) => typeof n === "string"
          ? <div key={i} className="grp">{n}</div>
          : <NavLink key={i} to={n[0]} end={n[0] === "/admin"}>{n[1]}</NavLink>)}
      </nav>
      <div className="admin-main">
        <Routes>
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
      </div>
    </div>
  );
}
