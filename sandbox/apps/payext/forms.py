from django import forms
from django.utils.translation import gettext_lazy as _

from .methods import DEFAULT_METHOD

FAIL_CHOICES = [
    ("", "正常支付"),
    ("insufficient_balance", "余额不足"),
    ("limit_exceeded", "单笔限额"),
    ("network_timeout", "网络超时"),
    ("risk_blocked", "风控拦截"),
]

# 错误码 → 用户语言 → 可行动作。这是支付失败体验的核心映射表。
FAIL_MESSAGES = {
    "insufficient_balance": {
        "title": "支付失败：余额不足",
        "detail": "账户余额不足以支付本单，还差一部分金额。",
        "actions": ["换一种支付方式", "先充值再回来付"],
    },
    "limit_exceeded": {
        "title": "支付失败：超过单笔限额",
        "detail": "当前支付渠道的单笔限额低于本单金额，可以换方式或拆分支付。",
        "actions": ["换一种支付方式", "分两笔支付"],
    },
    "network_timeout": {
        "title": "支付未完成：网络超时",
        "detail": "本次未扣款，订单已为你保留，可以直接重试。",
        "actions": ["重试支付", "换一种支付方式"],
    },
    "risk_blocked": {
        "title": "支付被拦截：需要验证身份",
        "detail": "为保障资金安全，本笔交易需要额外验证，验证后即可继续。",
        "actions": ["去验证身份", "换一种支付方式"],
    },
}


class PaymentForm(forms.Form):
    payment_method = forms.CharField(widget=forms.HiddenInput, initial=DEFAULT_METHOD)
    card_number = forms.CharField(
        label=_("卡号"), required=False, max_length=32,
        widget=forms.TextInput(attrs={"placeholder": "6222 0000 0000 0000", "autocomplete": "cc-number"}),
    )
    card_expiry = forms.CharField(
        label=_("有效期"), required=False, max_length=7,
        widget=forms.TextInput(attrs={"placeholder": "MM/YY"}),
    )
    card_cvv = forms.CharField(
        label=_("安全码"), required=False, max_length=4,
        widget=forms.TextInput(attrs={"placeholder": "CVV", "autocomplete": "cc-csc"}),
    )
    fail = forms.ChoiceField(choices=FAIL_CHOICES, required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("payment_method") or DEFAULT_METHOD
        if method == "card":
            number = (cleaned.get("card_number") or "").replace(" ", "")
            if len(number) < 12 or not number.isdigit():
                self.add_error("card_number", _("请输入有效的银行卡号"))
            if not cleaned.get("card_expiry"):
                self.add_error("card_expiry", _("请填写有效期"))
            if not cleaned.get("card_cvv"):
                self.add_error("card_cvv", _("请填写安全码"))
        return cleaned
