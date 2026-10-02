import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Confirm, ErrorBox, Spinner, useToast } from "../components/ui";
import { get, post } from "../lib/api";
import { clock, dateOnly, dateTime, ORDER_STATUS, vnd } from "../lib/format";
import type { Order } from "../lib/types";

function Copy({ text }: { text: string }) {
  const toast = useToast();
  return (
    <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(text).then(() => toast("Đã sao chép", "ok"))}>
      Sao chép
    </button>
  );
}

export default function Checkout() {
  const { code = "" } = useParams();
  const nav = useNavigate();
  const [order, setOrder] = useState<Order | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [now, setNow] = useState(Date.now());
  const [cancel, setCancel] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const o = await get<Order>(`/api/orders/${code}`);
        if (!alive) return;
        setOrder((prev) => (prev && prev.qr_svg && !o.qr_svg && o.status === "pending" ? { ...o, qr_svg: prev.qr_svg } : o));
        if (o.status === "pending") timer = setTimeout(poll, 4000);
      } catch (e) {
        if (alive) setError(e);
      }
    };
    poll();
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => { alive = false; clearTimeout(timer); clearInterval(t); };
  }, [code]);

  if (error) return <div className="container page narrow"><ErrorBox error={error} /></div>;
  if (!order) return <Spinner />;
  const [label, cls] = ORDER_STATUS[order.status] || [order.status, ""];
  const left = (new Date(order.expires_at).getTime() - now) / 1000;

  return (
    <div className="container narrow page">
      <div className="row between mb">
        <h1 style={{ margin: 0 }}>Thanh toán</h1>
        <span className={"badge " + cls} style={{ fontSize: ".9rem" }}>{label}</span>
      </div>

      {order.status === "paid" && (
        <div className="card pad-lg center">
          <div style={{ fontSize: "2.6rem" }}>🎉</div>
          <h2>Thanh toán thành công</h2>
          {order.kind === "pro" ? <>
            <p className="muted">Gói Pro đã được kích hoạt{order.subscription ? <> – hiệu lực từ {dateOnly(order.subscription.starts_at)} đến <strong>{dateOnly(order.subscription.expires_at)}</strong></> : null}.
              Bạn có thể luyện tập toàn bộ ngân hàng câu hỏi đủ điều kiện.</p>
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn lg" onClick={() => nav("/luyen-tap")}>Bắt đầu luyện tập</button>
            </div>
          </> : <>
            <p className="muted">{order.name} đã được kích hoạt cho tài khoản của bạn.</p>
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn lg" onClick={() => nav("/de-thi")}>Vào làm bài</button>
            </div>
          </>}
        </div>
      )}

      {order.status === "pending" && (
        <div className="card pad-lg">
          <p>Quét mã QR bằng ứng dụng ngân hàng bất kỳ hoặc chuyển khoản theo thông tin bên dưới. Đơn được xác nhận khi giao dịch về tài khoản
            (tự động nếu đã kết nối, nếu không quản trị viên sẽ đối soát thủ công) – hiển thị mã QR không có nghĩa là đã thanh toán.</p>
          {order.bank ? (
            <div className="qr-box mt">
              {order.qr_svg ? <div className="qr" aria-label="Mã QR VietQR" dangerouslySetInnerHTML={{ __html: order.qr_svg }} />
                : <div className="qr muted small center" style={{ padding: 16 }}>Chuyển khoản theo thông tin bên cạnh.</div>}
              <dl className="kv">
                <dt>Sản phẩm</dt><dd>{order.name}</dd>
                <dt>Số tiền</dt><dd style={{ fontSize: "1.2rem", color: "var(--brand-700)" }} className="row" >
                  {vnd(order.amount_vnd)}<Copy text={String(order.amount_vnd)} />
                  {order.list_price_vnd && order.list_price_vnd > order.amount_vnd ? <s className="muted small">{vnd(order.list_price_vnd)}</s> : null}
                </dd>
                <dt>Ngân hàng</dt><dd>{order.bank.bank_name || order.bank.bin}</dd>
                <dt>Số tài khoản</dt><dd className="row" style={{ gap: 4 }}>{order.bank.account_number}<Copy text={order.bank.account_number} /></dd>
                <dt>Chủ tài khoản</dt><dd>{order.bank.account_name || <span className="muted small">Ứng dụng ngân hàng hiển thị tên chủ tài khoản khi bạn nhập số tài khoản – vui lòng kiểm tra trước khi chuyển.</span>}</dd>
                <dt>Nội dung CK</dt><dd className="row" style={{ gap: 4 }}><span className="mono" style={{ fontSize: "1.05rem" }}>{order.transfer_content}</span><Copy text={order.transfer_content} /></dd>
                <dt>Hết hạn sau</dt><dd>{left > 0 ? clock(left) : "đã hết hạn"}</dd>
              </dl>
            </div>
          ) : (
            <div className="alert warn">Tài khoản nhận tiền chưa được cấu hình. Vui lòng liên hệ quản trị viên và cung cấp mã đơn <strong className="mono">{order.code}</strong>.</div>
          )}
          {order.instructions && <div className="alert mt" style={{ whiteSpace: "pre-line" }}>{order.instructions}</div>}
          <div className="alert info mt">
            Vui lòng giữ nguyên nội dung chuyển khoản <strong className="mono">{order.transfer_content}</strong> để hệ thống nhận diện đơn hàng.
            Nếu đã chuyển mà chưa được xác nhận sau vài phút, hãy liên hệ hỗ trợ kèm mã đơn.
          </div>
          <div className="row between mt">
            <span className="muted small"><span className="spinner" style={{ width: 14, height: 14, borderWidth: 2, display: "inline-block", verticalAlign: -2 }} /> Đang chờ xác nhận thanh toán…</span>
            <button className="btn ghost sm" onClick={() => setCancel(true)}>Huỷ đơn</button>
          </div>
        </div>
      )}

      {["expired", "cancelled", "failed", "refunded"].includes(order.status) && (
        <div className="card pad-lg">
          <p>Đơn hàng <span className="mono">{order.code}</span> {{ expired: "đã hết hạn", cancelled: "đã bị huỷ", failed: "không thành công", refunded: "đã được hoàn tiền" }[order.status as "expired"]}.</p>
          {order.status !== "refunded" && <p className="muted small">Nếu bạn đã chuyển khoản cho đơn này, hãy liên hệ hỗ trợ kèm mã đơn <span className="mono">{order.code}</span>: giao dịch sẽ được đối soát và quyền lợi được kích hoạt.</p>}
          <Link to={order.kind === "pro" ? "/nang-cap" : "/de-thi"} className="btn">{order.kind === "pro" ? "Tạo đơn nâng cấp mới" : "Quay lại danh sách đề"}</Link>
        </div>
      )}

      <p className="muted small mt">Mã đơn: <span className="mono">{order.code}</span> · Tạo lúc {dateTime(order.created_at)}</p>
      {cancel && (
        <Confirm title="Huỷ đơn hàng?" danger confirmText="Huỷ đơn" onClose={() => setCancel(false)} onConfirm={async () => {
          const o = await post<Order>(`/api/orders/${code}/cancel`);
          setOrder(o);
          setCancel(false);
        }}>
          <p>Chỉ huỷ nếu bạn <strong>chưa chuyển khoản</strong>.</p>
        </Confirm>
      )}
    </div>
  );
}
