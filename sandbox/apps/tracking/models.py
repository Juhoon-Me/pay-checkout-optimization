"""结账链路行为埋点。

设计原则：
1. 只记录，不参与业务逻辑——埋点出错不能影响下单。
2. 字段为「分析用」而非「日志用」：每个字段都要能回答一个产品问题。
3. 一次会话（session_key）串起完整路径，才能算漏斗。
"""

from django.db import models


class CheckoutEvent(models.Model):
    """结账链路上的一次行为。"""

    # 链路节点
    STEP_GATEWAY = "gateway"            # 身份识别
    STEP_ADDRESS = "address"            # 收货地址
    STEP_SHIPPING = "shipping"          # 配送方式
    STEP_PAYMENT_METHOD = "payment_method"      # 支付方式选择（本项目新增）
    STEP_PAYMENT_DETAILS = "payment_details"    # 支付详情
    STEP_PREVIEW = "preview"            # 订单确认
    STEP_DONE = "done"                  # 下单成功
    STEP_OTHER = "other"

    # 行为类型
    EVENT_ENTER = "enter"                      # 进入页面
    EVENT_LEAVE = "leave"                      # 离开页面（带停留时长）
    EVENT_SUBMIT = "submit"                    # 提交表单
    EVENT_VALIDATION_ERROR = "validation_error"  # 表单校验失败
    EVENT_PAYMENT_FAIL = "payment_fail"        # 支付失败
    EVENT_PAYMENT_SUCCESS = "payment_success"  # 支付成功
    EVENT_METHOD_SELECT = "method_select"      # 选择支付方式
    EVENT_ABANDON = "abandon"                  # 离开结账域（放弃信号）

    # 支付失败四类归因
    FAIL_INSUFFICIENT = "insufficient_balance"   # 余额不足（用户可修复）
    FAIL_LIMIT = "limit_exceeded"                # 单笔限额（用户可修复）
    FAIL_TIMEOUT = "network_timeout"             # 网络超时（系统侧）
    FAIL_RISK = "risk_blocked"                   # 风控拦截（需人工）
    FAIL_CANCELLED = "user_cancelled"            # 用户主动取消

    session_key = models.CharField("会话", max_length=40, db_index=True)
    step = models.CharField("节点", max_length=24, db_index=True)
    event = models.CharField("行为", max_length=24, db_index=True)

    duration_ms = models.PositiveIntegerField("停留时长(ms)", null=True, blank=True)
    status_code = models.PositiveSmallIntegerField("HTTP状态", null=True, blank=True)

    error_field = models.CharField("出错字段", max_length=64, blank=True, default="")
    error_count = models.PositiveSmallIntegerField("出错字段数", default=0)

    payment_method = models.CharField("支付方式", max_length=32, blank=True, default="")
    fail_reason = models.CharField("失败原因", max_length=32, blank=True, default="")

    user_type = models.CharField("用户类型", max_length=16, blank=True, default="")  # guest / auth
    device = models.CharField("设备", max_length=16, blank=True, default="")        # mobile / desktop
    is_simulated = models.BooleanField("模拟流量", default=False, db_index=True)
    phase = models.CharField("阶段", max_length=16, blank=True, default="", db_index=True)

    created_at = models.DateTimeField("时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "结账行为"
        verbose_name_plural = "结账行为"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_key", "created_at"]),
            models.Index(fields=["step", "event"]),
        ]

    def __str__(self):
        return f"{self.session_key[:8]} {self.step}/{self.event}"
