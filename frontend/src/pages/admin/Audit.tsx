import { get, qs } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { Empty, ErrorBox, Pager, Spinner, useAsync } from "../../components/ui";
import { PageHead, useUrlState } from "./common";

interface Entry { id: number; actor: string | null; action: string; entity: string; entity_id: string | null; data: Record<string, unknown>; created_at: string }
const SIZE = 50;

export default function Audit() {
  const u = useUrlState();
  const page = Number(u.get("page") || 1);
  const list = useAsync(() => get<{ total: number; items: Entry[] }>("/api/admin/audit" + qs({ page, size: SIZE })), [page]);
  const items = list.data?.items || [];
  return (
    <div>
      <PageHead title="Nhật ký quản trị" />
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !items.length ? <Empty title="Chưa có nhật ký." /> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Thời gian</th><th>Người thực hiện</th><th>Hành động</th><th>Đối tượng</th><th>Dữ liệu</th></tr></thead>
          <tbody>{items.map((a) => (
            <tr key={a.id}>
              <td className="small nowrap">{dateTime(a.created_at)}</td>
              <td className="small">{a.actor || "hệ thống"}</td>
              <td className="mono small">{a.action}</td>
              <td className="small">{a.entity}{a.entity_id ? ` · ${a.entity_id}` : ""}</td>
              <td className="mono small" style={{ maxWidth: 420, wordBreak: "break-all" }}>{Object.keys(a.data || {}).length ? JSON.stringify(a.data) : ""}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <Pager page={page} size={SIZE} total={list.data?.total || 0} onPage={(p) => u.set({ page: p }, false)} />
    </div>
  );
}
