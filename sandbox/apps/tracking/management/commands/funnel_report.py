"""一次性输出结账漏斗统计，可直接贴进报告或简历。

用法：
    python sandbox/manage.py funnel_report                  # 全部数据
    python sandbox/manage.py funnel_report --source human    # 只看真人
    python sandbox/manage.py funnel_report --save reports/   # 同时存 markdown

和看板的区别：看板是交互式的，这个命令是为了「把数字固定下来」——
写报告时要的是某一刻的快照，而不是一个会变的页面。
"""

import os
from datetime import datetime

from django.core.management.base import BaseCommand

from apps.dashboard.views import build_report, _apply_filters
from apps.tracking.models import CheckoutEvent as E

PHASE_NAMES = {"baseline": "改版前", "after": "改版后", "all": "全部"}


class Command(BaseCommand):
    help = "输出结账漏斗统计（终端表格 + 可选 markdown）"

    def add_arguments(self, parser):
        parser.add_argument("--source", default="all", choices=["all", "human", "sim"])
        parser.add_argument("--save", default="", help="markdown 保存目录")

    def handle(self, *args, **opts):
        source = opts["source"]
        lines = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        title = f"结账漏斗统计 · 来源={source} · {now}"
        lines.append(f"# {title}\n")

        # 两阶段主体指标
        lines.append("## 1. 改版前后对比\n")
        lines.append("| 指标 | 改版前 | 改版后 |")
        lines.append("|---|---|---|")

        stats = {}
        for ph in ("baseline", "after"):
            qs = _apply_filters(E.objects.all(), ph, source)
            stats[ph] = build_report(qs)

        lines.append(
            f"| 结账会话 | {stats['baseline']['checkout_started']} "
            f"| {stats['after']['checkout_started']} |"
        )
        lines.append(
            f"| 完成下单 | {stats['baseline']['done_sessions']} "
            f"| {stats['after']['done_sessions']} |"
        )
        lines.append(
            f"| 完成率 | {stats['baseline']['completion']}% "
            f"| {stats['after']['completion']}% |"
        )
        lines.append(
            f"| 支付失败记录 | {stats['baseline']['fail_total']} "
            f"| {stats['after']['fail_total']} |"
        )
        lines.append(
            f"| 支付方式页渲染 | {self._rendered(stats['baseline'])} "
            f"| {self._rendered(stats['after'])} |"
        )
        lines.append("")
        lines.append(
            "> **读法**：改版前的完成率偏高是假象——该版本链路里没有支付环节"
            "（支付方式页 0 渲染），失败在物理上不可能发生。"
            "完成率必须和「支付方式页渲染数」一起读，单独看会得出反的结论。\n"
        )

        # 分阶段漏斗明细
        for ph in ("baseline", "after"):
            lines.append(f"## 2. {PHASE_NAMES[ph]}漏斗\n")
            lines.append("| 节点 | 到达 | 渲染 | 单步转化 | 本步流失 | 平均停留 |")
            lines.append("|---|---|---|---|---|---|")
            for r in stats[ph]["funnel"]:
                rate = f"{r['step_rate']:.1f}%" if r["step_rate"] is not None else "—"
                lines.append(
                    f"| {r['label']} | {r['arrived']} | {r['rendered']} | {rate} "
                    f"| {r['dropped'] if r['dropped'] else 0} | {r['dwell'] or '—'} |"
                )
            lines.append("")

        # 支付失败归因
        after = stats["after"]
        if after["fail_rows"]:
            lines.append("## 3. 支付失败归因（改版后）\n")
            lines.append("| 原因 | 性质 | 次数 | 占比 |")
            lines.append("|---|---|---|---|")
            for f in after["fail_rows"]:
                lines.append(f"| {f['name']} | {f['kind']} | {f['n']} | {f['pct']}% |")
            lines.append("")

        # 支付方式分布
        if after["method_rows"]:
            lines.append("## 4. 支付方式偏好（改版后）\n")
            lines.append("| 方式 | 次数 | 占比 |")
            lines.append("|---|---|---|")
            for m in after["method_rows"]:
                lines.append(f"| {m['name']} | {m['n']} | {m['pct']}% |")
            lines.append("")

        # 数据来源说明——面试一定会问
        human = E.objects.filter(is_simulated=False)
        sim = E.objects.filter(is_simulated=True)
        lines.append("## 5. 数据来源\n")
        lines.append(f"- 真人事件 {human.count()} 条 / "
                     f"{human.values('session_key').distinct().count()} 个会话")
        lines.append(f"- 模拟事件 {sim.count()} 条 / "
                     f"{sim.values('session_key').distinct().count()} 个会话")
        lines.append("- 模拟数据只用于验证链路与埋点正确性，不用于体验结论")
        lines.append("")

        text = "\n".join(lines)
        self.stdout.write(text)

        if opts["save"]:
            os.makedirs(opts["save"], exist_ok=True)
            path = os.path.join(opts["save"], f"funnel_{source}_{datetime.now():%Y%m%d_%H%M}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.stdout.write(self.style.SUCCESS(f"\n已保存：{path}"))

    @staticmethod
    def _rendered(report):
        for r in report["funnel"]:
            if r["step"] == E.STEP_PAYMENT_METHOD:
                return f"{r['rendered']}/{r['arrived']}"
        return "—"
