"""结账漏斗数据看板。

设计原则：
1. 浏览器直接看，不依赖命令行——产品同学要能自己反复看。
2. 每个数字都要能回答一个问题，不做「数据陈列」。
3. 只读，不写库，不改业务。
"""

import csv

from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import render

from apps.tracking.models import CheckoutEvent as E

# 链路顺序。注意 shipping 在 Oscar 里通常被 302 自动跳过，
# 所以它「到达数」正常，「有效渲染数」为 0 属于框架行为，不是缺陷。
FUNNEL_STEPS = [
    (E.STEP_GATEWAY, "① 身份识别"),
    (E.STEP_ADDRESS, "② 收货地址"),
    (E.STEP_SHIPPING, "③ 配送方式"),
    (E.STEP_PAYMENT_METHOD, "④ 支付方式"),
    (E.STEP_PAYMENT_DETAILS, "⑤ 支付详情"),
    (E.STEP_PREVIEW, "⑥ 订单确认"),
    (E.STEP_DONE, "⑦ 下单成功"),
]

FAIL_LABELS = {
    E.FAIL_INSUFFICIENT: ("余额不足", "用户可修复"),
    E.FAIL_LIMIT: ("单笔限额", "用户可修复"),
    E.FAIL_TIMEOUT: ("网络超时", "系统侧"),
    E.FAIL_RISK: ("风控拦截", "需人工介入"),
    E.FAIL_CANCELLED: ("用户取消", "不该打扰"),
}

METHOD_LABELS = {
    "wechat": "微信支付",
    "alipay": "支付宝",
    "card": "银行卡",
    "balance": "账户余额",
}


def _sessions(qs, step):
    return qs.filter(step=step, event=E.EVENT_ENTER).values("session_key").distinct().count()


def _valid_sessions(qs, step):
    return (
        qs.filter(step=step, event=E.EVENT_ENTER, status_code=200)
        .values("session_key")
        .distinct()
        .count()
    )


def _fmt_dwell(ms):
    """停留时长的展示格式。模拟请求间隔极短，会是毫秒级。"""
    if ms is None:
        return None
    if ms < 100:
        return "<0.1s"
    if ms < 1000:
        return f"{ms / 1000:.2f}s"
    return f"{ms / 1000:.1f}s"


def build_funnel(qs, dwell_map=None):
    dwell_map = dwell_map or {}
    rows = []
    prev = None
    peak = 1
    for step, label in FUNNEL_STEPS:
        arrived = _sessions(qs, step)
        rendered = _valid_sessions(qs, step)
        peak = max(peak, arrived)

        # 渲染数低于到达数时，要说清是「缺陷」还是「框架行为」
        note = ""
        if arrived and not rendered:
            if step == E.STEP_SHIPPING:
                note = "框架自动跳过"
            elif step == E.STEP_PAYMENT_METHOD:
                note = "页面不存在，被 302 跳过"
            else:
                note = "未渲染"

        rows.append(
            {
                "step": step,
                "label": label,
                "arrived": arrived,
                "rendered": rendered,
                "step_rate": (arrived / prev * 100) if prev else None,
                "dropped": (prev - arrived) if prev else 0,
                "dwell": dwell_map.get(step),
                "note": note,
            }
        )
        prev = arrived
    for r in rows:
        r["width"] = round(r["arrived"] / peak * 100, 1)
        r["rendered_width"] = round(r["rendered"] / peak * 100, 1)
    return rows


def build_report(qs):
    total_sessions = _sessions(qs, E.STEP_GATEWAY) or _sessions(qs, E.STEP_ADDRESS)
    done_sessions = _sessions(qs, E.STEP_DONE)
    checkout_started = _sessions(qs, E.STEP_ADDRESS)

    # 支付失败归因
    fails = (
        qs.filter(event=E.EVENT_PAYMENT_FAIL)
        .exclude(fail_reason="")
        .values("fail_reason")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    fail_total = sum(f["n"] for f in fails) or 1
    fail_rows = []
    for f in fails:
        name, kind = FAIL_LABELS.get(f["fail_reason"], (f["fail_reason"], "未分类"))
        fail_rows.append(
            {
                "reason": f["fail_reason"],
                "name": name,
                "kind": kind,
                "n": f["n"],
                "pct": round(f["n"] / fail_total * 100, 1),
                "width": round(f["n"] / fail_total * 100, 1),
            }
        )

    # 支付方式分布
    methods = (
        qs.filter(event=E.EVENT_METHOD_SELECT)
        .exclude(payment_method="")
        .values("payment_method")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    method_total = sum(m["n"] for m in methods) or 1
    method_rows = [
        {
            "name": METHOD_LABELS.get(m["payment_method"], m["payment_method"]),
            "n": m["n"],
            "pct": round(m["n"] / method_total * 100, 1),
            "width": round(m["n"] / method_total * 100, 1),
        }
        for m in methods
    ]

    # 每步平均停留时长（秒）——先算，漏斗行要用
    dwell = (
        qs.filter(event=E.EVENT_LEAVE, duration_ms__isnull=False)
        .values("step")
        .annotate(avg_ms=Avg("duration_ms"))
    )
    dwell_map = {d["step"]: _fmt_dwell(d["avg_ms"]) for d in dwell}

    # 校验失败 TOP
    errors = (
        qs.filter(event=E.EVENT_VALIDATION_ERROR)
        .exclude(error_field="")
        .values("step", "error_field")
        .annotate(n=Count("id"))
        .order_by("-n")[:8]
    )

    return {
        "total_sessions": total_sessions,
        "checkout_started": checkout_started,
        "done_sessions": done_sessions,
        "completion": round(done_sessions / checkout_started * 100, 1) if checkout_started else 0,
        "funnel": build_funnel(qs, dwell_map),
        "fail_rows": fail_rows,
        "fail_total": sum(f["n"] for f in fails),
        "method_rows": method_rows,
        "dwell_map": dwell_map,
        "errors": list(errors),
        "payment_success": qs.filter(event=E.EVENT_PAYMENT_SUCCESS).count(),
        "validation_errors": qs.filter(event=E.EVENT_VALIDATION_ERROR).count(),
    }


def _apply_filters(qs, phase, source):
    """按阶段和来源筛选。

    关键：真人在浏览器里访问时没有 X-Phase 头，phase 为空。
    而站点当前就是改版后的版本，所以「无阶段标记的真人数据」应当归入 after，
    否则明天收回来的真人数据会在看板里凭空消失。
    """
    if phase == "baseline":
        qs = qs.filter(phase="baseline")
    elif phase == "after":
        qs = qs.filter(Q(phase="after") | (Q(phase="") & Q(is_simulated=False)))
    if source == "human":
        qs = qs.filter(is_simulated=False)
    elif source == "sim":
        qs = qs.filter(is_simulated=True)
    return qs


def _filtered(request):
    phase = request.GET.get("phase", "after")
    source = request.GET.get("source", "all")
    return _apply_filters(E.objects.all(), phase, source), phase, source


def index(request):
    qs, phase, source = _filtered(request)

    # 两阶段并排对比，方便直接讲「改版前后」
    compare = {}
    for ph in ("baseline", "after"):
        sub = _apply_filters(E.objects.all(), ph, source)
        compare[ph] = {
            "method_arrived": _sessions(sub, E.STEP_PAYMENT_METHOD),
            "method_rendered": _valid_sessions(sub, E.STEP_PAYMENT_METHOD),
            "sessions": _sessions(sub, E.STEP_ADDRESS) or _sessions(sub, E.STEP_GATEWAY),
            "done": _sessions(sub, E.STEP_DONE),
            "fail": sub.filter(event=E.EVENT_PAYMENT_FAIL).count(),
        }

    ctx = {
        "report": build_report(qs),
        "phase": phase,
        "source": source,
        "compare": compare,
        "recent": qs.select_related()[:30],
        "total_all": E.objects.count(),
        "human_total": E.objects.filter(is_simulated=False).count(),
        "human_sessions": E.objects.filter(is_simulated=False).values("session_key").distinct().count(),
        "human_in_view": qs.filter(is_simulated=False).count(),
        "mobile_total": qs.filter(device="mobile", is_simulated=False).count(),
    }
    return render(request, "dashboard/index.html", ctx)


def export_csv(request):
    qs, phase, source = _filtered(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="checkout_events_{phase}_{source}.csv"'
    response.write("\ufeff")  # 让 Excel 正确识别中文
    writer = csv.writer(response)
    writer.writerow(
        ["时间", "会话", "阶段", "节点", "行为", "停留ms", "状态码", "出错字段",
         "支付方式", "失败原因", "用户类型", "设备", "模拟流量"]
    )
    for e in qs.order_by("-created_at")[:20000]:
        writer.writerow(
            [e.created_at.strftime("%Y-%m-%d %H:%M:%S"), e.session_key[:12], e.phase,
             e.step, e.event, e.duration_ms, e.status_code, e.error_field,
             e.payment_method, e.fail_reason, e.user_type, e.device,
             "是" if e.is_simulated else "否"]
        )
    return response
