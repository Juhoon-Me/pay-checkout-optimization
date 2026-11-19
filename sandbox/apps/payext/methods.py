"""支付方式定义。

为什么把支付方式做成配置而不是硬编码在模板里：
- 新增渠道只需要加一条，不用改页面
- 排序即优先级：微信支付置顶是产品决策，应该显性化
"""

PAYMENT_METHODS = [
    {
        "code": "wechat",
        "name": "微信支付",
        "hint": "推荐 · 免手续费",
        "detail": "余额、零钱、绑定银行卡均可支付，实时到账",
        "icon": "fa-comment-dots",
        "default": True,
    },
    {
        "code": "alipay",
        "name": "支付宝",
        "hint": "",
        "detail": "余额宝、花呗可用，实时到账",
        "icon": "fa-wallet",
        "default": False,
    },
    {
        "code": "card",
        "name": "银行卡",
        "hint": "需填写卡信息",
        "detail": "支持储蓄卡与信用卡，1-2 个工作日到账",
        "icon": "fa-credit-card",
        "default": False,
    },
    {
        "code": "balance",
        "name": "账户余额",
        "hint": "小额可用",
        "detail": "余额不足时可用组合支付",
        "icon": "fa-coins",
        "default": False,
    },
]

METHOD_MAP = {m["code"]: m for m in PAYMENT_METHODS}
DEFAULT_METHOD = next(m["code"] for m in PAYMENT_METHODS if m["default"])


def get_method(code):
    return METHOD_MAP.get(code or "", METHOD_MAP[DEFAULT_METHOD])
