"""支付环节的两个页面。

设计原则：
1. 不改 Oscar 核心文件——通过「视图继承 + 模板覆盖」扩展，保证能跟随上游升级
2. 失败必须可归因、可行动——错误码翻译成用户语言，并给出下一步
3. 每个决策都留数据——选择、失败原因、停留时长都进埋点表
"""

from django.shortcuts import redirect
from django.urls import reverse
from django.views import generic

from apps.tracking.events import log_payment_event
from apps.tracking.models import CheckoutEvent
from oscar.apps.checkout.views import PaymentMethodView as OscarPaymentMethodView

from .forms import FAIL_MESSAGES, PaymentForm
from .methods import DEFAULT_METHOD, PAYMENT_METHODS, get_method

# 演示用余额，用于触发「余额不足」这类真实失败场景
DEMO_BALANCE = 86.00


class PaymentMethodSelectView(OscarPaymentMethodView):
    """支付方式选择页。

    Oscar 原实现 get() 直接重定向，导致这一步从未渲染。
    这里改为真实页面：用户能看到、能选、能被记住。
    """

    template_name = "oscar/checkout/payment_methods.html"

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        context["payment_methods"] = PAYMENT_METHODS
        context["selected"] = request.session.get("payment_method", DEFAULT_METHOD)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        code = request.POST.get("payment_method") or DEFAULT_METHOD
        method = get_method(code)
        request.session["payment_method"] = method["code"]
        log_payment_event(
            request,
            step=CheckoutEvent.STEP_PAYMENT_METHOD,
            event=CheckoutEvent.EVENT_METHOD_SELECT,
            payment_method=method["code"],
        )
        return redirect(reverse("checkout:payment-details"))


class PaymentDetailsView(generic.FormView):
    """支付详情页（替换 Oscar 的占位符）。

    失败时停在原页，给出原因和可点的下一步；
    成功后才进入确认页。
    """

    template_name = "oscar/checkout/payment_details.html"
    form_class = PaymentForm

    def dispatch(self, request, *args, **kwargs):
        # 购物车为空时不要停在支付页，回购物车
        if request.basket is None or request.basket.is_empty:
            return redirect(reverse("basket:summary"))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        initial["payment_method"] = self.request.session.get("payment_method", DEFAULT_METHOD)
        # 故障注入：?fail=insufficient_balance，用于演示与测试四类失败
        initial["fail"] = self.request.GET.get("fail", "")
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        code = context["form"]["payment_method"].value() or self.request.session.get("payment_method", DEFAULT_METHOD)
        context["method"] = get_method(code)
        context["balance"] = DEMO_BALANCE
        basket = self.request.basket
        context["amount"] = basket.total_incl_tax if basket else 0
        context["currency"] = basket.currency if basket else "CNY"
        context["failure"] = getattr(self, "_failure", None)
        context["fail_choices"] = [
            ("", "正常支付（演示用）"),
            ("insufficient_balance", "余额不足"),
            ("limit_exceeded", "单笔限额"),
            ("network_timeout", "网络超时"),
            ("risk_blocked", "风控拦截"),
        ]
        return context

    def form_invalid(self, form):
        # 卡信息填错：属于「用户可修复」，页内就地提示，不走失败页
        log_payment_event(
            self.request,
            step=CheckoutEvent.STEP_PAYMENT_DETAILS,
            event=CheckoutEvent.EVENT_VALIDATION_ERROR,
            payment_method=self.request.POST.get("payment_method", ""),
        )
        return super().form_invalid(form)

    def form_valid(self, form):
        code = form.cleaned_data["payment_method"]
        method = get_method(code)
        basket = self.request.basket
        amount = float(basket.total_incl_tax) if basket else 0.0
        reason = form.cleaned_data.get("fail") or ""

        if not reason and code == "balance" and amount > DEMO_BALANCE:
            reason = "insufficient_balance"

        if reason:
            failure = dict(FAIL_MESSAGES[reason])
            failure["reason"] = reason
            failure["method"] = method["name"]
            self._failure = failure
            log_payment_event(
                self.request,
                step=CheckoutEvent.STEP_PAYMENT_DETAILS,
                event=CheckoutEvent.EVENT_PAYMENT_FAIL,
                payment_method=code,
                fail_reason=reason,
            )
            response = self.render_to_response(self.get_context_data(form=form))
            # 支付失败不是表单校验失败，别混进同一类指标
            response.is_payment_failure = True
            return response

        log_payment_event(
            self.request,
            step=CheckoutEvent.STEP_PAYMENT_DETAILS,
            event=CheckoutEvent.EVENT_PAYMENT_SUCCESS,
            payment_method=code,
        )
        self.request.session["payment_ok"] = True
        return redirect(reverse("checkout:preview"))
