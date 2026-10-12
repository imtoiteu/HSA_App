"""On-demand AI assistant of the Review Studio.

* Never called automatically: only when a reviewer clicks an action for ONE question.
* Output is a suggestion stored in the review history; it changes nothing until a reviewer applies it, and it
  never confirms a QA dimension or makes a question READY.
* Pluggable providers; credentials come from environment variables only (HSA_AI_PROVIDER, HSA_AI_API_KEY,
  HSA_AI_MODEL). Without a provider every action answers "not configured".
"""
import base64
import json
import logging
import urllib.error
import urllib.request

from ..content.render import plain_text

log = logging.getLogger(__name__)

ACTIONS = {
    "fix_ocr": ("Gợi ý sửa lỗi OCR",
                "Sửa các lỗi nhận dạng ký tự (OCR), chính tả và dấu tiếng Việt trong đề và phương án. Không đổi nội dung "
                "hay đáp án. Trả về đề và phương án đã sửa, liệt kê từng chỗ sửa."),
    "suggest_formula": ("Gợi ý công thức",
                        "Viết lại các công thức toán học trong đề/phương án dưới dạng LaTeX chuẩn (KaTeX hiển thị được). "
                        "Liệt kê mỗi công thức gốc → LaTeX đề xuất."),
    "formula_from_image": ("Phục hồi công thức từ ảnh nguồn",
                           "Dựa vào ảnh trang tài liệu gốc kèm theo, chép lại chính xác các công thức của câu hỏi này "
                           "dưới dạng LaTeX. Chỉ chép những gì nhìn thấy; chỗ nào không đọc được thì ghi rõ."),
    "solve": ("Giải câu hỏi",
              "Giải câu hỏi từng bước ngắn gọn và nêu đáp án cuối cùng. Nếu đề thiếu dữ kiện thì nói rõ."),
    "check_answer": ("Kiểm tra đáp án",
                     "Kiểm tra đáp án đang lưu có đúng không. Trả lời: ĐÚNG / SAI / KHÔNG CHẮC, kèm lập luận ngắn và "
                     "đáp án bạn cho là đúng."),
    "suggest_subject": ("Gợi ý môn",
                        "Câu hỏi thuộc môn nào trong: math, physics, chemistry, biology, literature, english, history, "
                        "geography, general (tư duy/khoa học tổng hợp)? Trả lời mã môn và lý do ngắn."),
    "compare_source": ("So sánh với nguồn",
                       "So sánh câu hỏi đã trích xuất với ảnh trang tài liệu gốc kèm theo. Liệt kê mọi khác biệt "
                       "(chữ, công thức, phương án, đáp án, hình)."),
    "explain_disagreement": ("Giải thích bất đồng",
                             "Đáp án/lời giải của câu này có mâu thuẫn. Phân tích nguyên nhân có thể và đề xuất cách "
                             "người duyệt kiểm chứng với tài liệu gốc."),
}
NEEDS_IMAGE = {"formula_from_image", "compare_source"}


class AIError(Exception):
    def __init__(self, message, status=503, code="ai_unavailable"):
        super().__init__(message)
        self.status, self.code = status, code


def question_text(content: dict, answer: dict | None, subject: str | None) -> str:
    lines = [f"Môn hiện tại: {subject or 'chưa phân loại'}", f"Dạng: {content.get('type')}"]
    g = content.get("group")
    if g and g.get("passage"):
        lines.append("Đoạn dữ liệu chung:\n" + plain_text(g["passage"])[:6000])
    lines.append("Đề bài:\n" + plain_text(content.get("stem") or []))
    for o in content.get("options") or []:
        lines.append(f"{o['label']}. {plain_text(o['content'])}")
    lines.append("Đáp án đang lưu: " + (json.dumps(answer, ensure_ascii=False) if answer else "chưa có"))
    sol = plain_text(content.get("solution") or [])
    if sol:
        lines.append("Lời giải:\n" + sol[:6000])
    return "\n".join(lines)


class Provider:
    name = "none"

    def complete(self, system: str, text: str, image_png: bytes | None) -> str:
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str):
        self.key, self.model, self.base = api_key, model, base_url.rstrip("/")

    def complete(self, system: str, text: str, image_png: bytes | None) -> str:
        content = []
        if image_png:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": base64.b64encode(image_png).decode()}})
        content.append({"type": "text", "text": text})
        body = json.dumps({"model": self.model, "max_tokens": 1500, "system": system,
                           "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request(self.base + "/v1/messages", data=body, method="POST", headers={
            "content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as ex:
            log.warning("AI provider error %s", ex.code)
            raise AIError(f"Dịch vụ AI trả lỗi {ex.code}.", 502, "ai_error") from ex
        except (urllib.error.URLError, TimeoutError) as ex:
            raise AIError("Không kết nối được dịch vụ AI.", 502, "ai_error") from ex
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()


def provider() -> Provider | None:
    from ..config import get_settings
    s = get_settings()
    if s.ai_provider == "anthropic" and s.ai_api_key:
        return AnthropicProvider(s.ai_api_key, s.ai_model, s.ai_base_url)
    return None


def status() -> dict:
    p = provider()
    return {"configured": p is not None, "provider": p.name if p else None,
            "actions": [{"code": k, "label": v[0], "needs_source_image": k in NEEDS_IMAGE} for k, v in ACTIONS.items()]}


def run(action: str, content: dict, answer: dict | None, subject: str | None, image_png: bytes | None) -> str:
    if action not in ACTIONS:
        raise AIError("Thao tác AI không hợp lệ.", 422, "invalid")
    p = provider()
    if p is None:
        raise AIError("Trợ lý AI chưa được cấu hình (HSA_AI_PROVIDER / HSA_AI_API_KEY).", 503, "ai_not_configured")
    if action in NEEDS_IMAGE and not image_png:
        raise AIError("Cần ảnh trang nguồn (tài liệu gốc chưa sẵn sàng).", 409, "source_needed")
    system = ("Bạn là trợ lý biên tập ngân hàng câu hỏi thi ĐGNL (HSA). Bạn chỉ GỢI Ý; người duyệt sẽ quyết định. "
              "Viết công thức bằng LaTeX giữa $...$. Trả lời bằng tiếng Việt, ngắn gọn. " + ACTIONS[action][1])
    return p.complete(system, question_text(content, answer, subject), image_png)
