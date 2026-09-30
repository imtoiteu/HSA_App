import { NavLink, Route, Routes } from "react-router-dom";
import Overview from "./Overview";
import Questions from "./Questions";
import QuestionDetail from "./QuestionDetail";
import Reports from "./Reports";
import { Banks, Corrections, Subjects } from "./Content";
import { BlueprintEditor, Blueprints } from "./Blueprints";
import { Orders, Products, Transactions } from "./Commerce";
import { UserDetail, Users } from "./Users";
import Settings from "./Settings";
import Audit from "./Audit";

const NAV: ([string, string] | string)[] = [
  ["/admin", "Tổng quan"],
  "Nội dung",
  ["/admin/cau-hoi", "Câu hỏi"],
  ["/admin/bao-loi", "Báo lỗi"],
  ["/admin/de-xuat-sua", "Đề xuất sửa"],
  ["/admin/ngan-hang", "Ngân hàng & đồng bộ"],
  ["/admin/mon-hoc", "Môn học"],
  "Đề thi",
  ["/admin/de-thi", "Cấu trúc đề"],
  "Kinh doanh",
  ["/admin/goi-ban", "Gói bán"],
  ["/admin/don-hang", "Đơn hàng"],
  ["/admin/giao-dich", "Giao dịch"],
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
