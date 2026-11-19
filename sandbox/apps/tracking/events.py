"""供视图调用的埋点接口。

中间件负责「页面级」行为（进入/离开/校验失败），
支付成功/失败这类「业务级」行为需要视图显式上报。
"""

from .middleware import _detect_device
from .models import CheckoutEvent


def log_payment_event(request, step, event, payment_method="", fail_reason=""):
    """记录支付相关事件。任何异常都吞掉，不影响主流程。"""
    try:
        if request.session.session_key is None:
            request.session.save()
        CheckoutEvent.objects.create(
            session_key=request.session.session_key or "anonymous",
            step=step,
            event=event,
            payment_method=payment_method or "",
            fail_reason=fail_reason or "",
            user_type="auth" if getattr(request.user, "is_authenticated", False) else "guest",
            device=_detect_device(request.META.get("HTTP_USER_AGENT", "")),
            is_simulated=bool(request.session.get("trk_simulated")),
            phase=request.session.get("trk_phase", ""),
        )
    except Exception:
        pass
