"""结账链路埋点中间件。

为什么用中间件而不是改视图：
- 不侵入业务代码，一行配置就能开关
- 每个 HTTP 请求都会被覆盖，不会漏掉某个入口
- 后续换框架、加渠道都不用重写
"""

import logging
import re
import time

from .models import CheckoutEvent

logger = logging.getLogger(__name__)

# 路径 → 链路节点。顺序重要：具体路径必须排在 /checkout/ 前面
STEP_PATTERNS = [
    (re.compile(r"/checkout/shipping-address/"), CheckoutEvent.STEP_ADDRESS),
    (re.compile(r"/checkout/shipping-method/"), CheckoutEvent.STEP_SHIPPING),
    (re.compile(r"/checkout/payment-method/"), CheckoutEvent.STEP_PAYMENT_METHOD),
    (re.compile(r"/checkout/payment-details/"), CheckoutEvent.STEP_PAYMENT_DETAILS),
    (re.compile(r"/checkout/preview/"), CheckoutEvent.STEP_PREVIEW),
    (re.compile(r"/checkout/thank-you/"), CheckoutEvent.STEP_DONE),
    (re.compile(r"/checkout/"), CheckoutEvent.STEP_GATEWAY),
]

# 离开结账域的路径（放弃信号）
EXIT_PATTERN = re.compile(r"/(basket|catalogue)/")

IGNORE_PREFIXES = ("/static/", "/media/", "/admin/", "/dashboard/", "/i18n/")

# 表单页：POST 返回 200 说明校验失败并重新渲染
FORM_STEPS = {
    CheckoutEvent.STEP_GATEWAY,
    CheckoutEvent.STEP_ADDRESS,
    CheckoutEvent.STEP_PAYMENT_METHOD,
    CheckoutEvent.STEP_PAYMENT_DETAILS,
}

STATE_KEY = "trk_state"


def _detect_step(path):
    for pattern, step in STEP_PATTERNS:
        if pattern.search(path):
            return step
    return None


def _detect_device(user_agent):
    ua = (user_agent or "").lower()
    if any(t in ua for t in ("iphone", "android", "mobile", "ipad")):
        return "mobile"
    return "desktop"


class CheckoutTrackingMiddleware:
    """记录每一步的进入、离开、停留时长与校验失败。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(IGNORE_PREFIXES):
            return self.get_response(request)

        step = _detect_step(path)

        # 模拟流量标记（脚本请求会带这个头），用于在报告中区分真人/模拟
        if request.headers.get("X-Simulated") == "1":
            request.session["trk_simulated"] = True
        phase = request.headers.get("X-Phase")
        if phase:
            request.session["trk_phase"] = phase[:16]

        if step is None:
            # 离开结账域 → 记录放弃信号，然后清空当前节点
            if EXIT_PATTERN.search(path):
                self._log_abandon(request)
            return self.get_response(request)

        started = time.monotonic()
        request._trk_step = step

        response = self.get_response(request)

        try:
            self._record(request, step, response, started)
        except Exception:  # 埋点绝不能影响下单
            logger.exception("埋点写入失败 step=%s", step)

        return response

    def _record(self, request, step, response, started):
        duration_ms = int((time.monotonic() - started) * 1000)
        state = request.session.get(STATE_KEY)

        if request.session.session_key is None:
            request.session.save()

        common = {
            "session_key": request.session.session_key or "anonymous",
            "user_type": "auth" if getattr(request.user, "is_authenticated", False) else "guest",
            "device": _detect_device(request.META.get("HTTP_USER_AGENT", "")),
            "is_simulated": bool(request.session.get("trk_simulated")),
            "phase": request.session.get("trk_phase", ""),
        }

        # 1. 节点切换 → 上一步记离开（带停留时长），新节点记进入
        if not state or state.get("step") != step:
            if state and state.get("step"):
                CheckoutEvent.objects.create(
                    step=state["step"],
                    event=CheckoutEvent.EVENT_LEAVE,
                    duration_ms=int((time.time() - state.get("ts", time.time())) * 1000),
                    status_code=state.get("status"),
                    payment_method=state.get("method", ""),
                    **common,
                )
            CheckoutEvent.objects.create(
                step=step,
                event=CheckoutEvent.EVENT_ENTER,
                status_code=response.status_code,
                **common,
            )

        # 2. 同节点 POST = 提交；返回 200 = 校验失败重渲染
        if request.method == "POST":
            CheckoutEvent.objects.create(
                step=step,
                event=CheckoutEvent.EVENT_SUBMIT,
                status_code=response.status_code,
                **common,
            )
            if response.status_code == 200 and step in FORM_STEPS and not getattr(response, "is_payment_failure", False):
                error_fields = self._extract_error_fields(response)
                CheckoutEvent.objects.create(
                    step=step,
                    event=CheckoutEvent.EVENT_VALIDATION_ERROR,
                    status_code=response.status_code,
                    error_field=",".join(error_fields)[:64],
                    error_count=len(error_fields),
                    **common,
                )

        request.session[STATE_KEY] = {
            "step": step,
            "ts": time.time(),
            "status": response.status_code,
            "method": request.POST.get("payment_method", "") if request.method == "POST" else state.get("method", "") if state else "",
        }

    def _log_abandon(self, request):
        state = request.session.get(STATE_KEY)
        if not state or not state.get("step"):
            return
        if state["step"] in (CheckoutEvent.STEP_DONE,):
            request.session.pop(STATE_KEY, None)
            return
        if request.session.session_key is None:
            return
        CheckoutEvent.objects.create(
            session_key=request.session.session_key,
            step=state["step"],
            event=CheckoutEvent.EVENT_ABANDON,
            duration_ms=int((time.time() - state.get("ts", time.time())) * 1000),
            user_type="auth" if getattr(request.user, "is_authenticated", False) else "guest",
            device=_detect_device(request.META.get("HTTP_USER_AGENT", "")),
            is_simulated=bool(request.session.get("trk_simulated")),
            phase=request.session.get("trk_phase", ""),
        )
        request.session.pop(STATE_KEY, None)

    @staticmethod
    def _extract_error_fields(response):
        """从渲染后的表单里提取出错字段（用于定位哪个字段最常卡人）。"""
        try:
            content = response.content.decode("utf-8", "ignore")
        except Exception:
            return []
        fields = set()
        for match in re.finditer(r'name="([^"]+)"[^>]*class="[^"]*(?:is-invalid|error)[^"]*"', content):
            fields.add(match.group(1))
        for match in re.finditer(r'id="id_([a-zA-Z0-9_-]+)-error"', content):
            fields.add(match.group(1))
        return sorted(fields)
