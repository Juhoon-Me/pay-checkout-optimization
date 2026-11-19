"""模拟用户下单脚本。

用途（必须说清楚，否则就是造假）：
1. 验证改版前后「链路是否可跑通」——这是功能正确性，模拟是有效的
2. 生成可重复的埋点数据，检查埋点是否完整、支付失败是否被正确归类
3. 采集「结构性指标」：步骤数、必填字段数、可挽回路径数

不能用于：
- 证明「用户体验变好了」。脚本知道下一步点哪，真人不知道，
  所以机器人跑出的转化率永远接近 100%，与体验好坏无关。

用法：
    python scripts/simulate_users.py --count 20 --variant baseline
    python scripts/simulate_users.py --count 20 --variant new --fail-rate 0.3
"""

import argparse
import json
import os
import random
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = os.environ.get("OSCAR_BASE", "http://127.0.0.1:8000")
LANG = "/en-gb"

ADDRESS = {
    "first_name": "Sim",
    "last_name": "User",
    "line1": "南山区科技园路 1 号",
    "line2": "",
    "line3": "",
    "line4": "深圳",
    "state": "广东",
    "postcode": "518000",
    "country": "CN",
    "phone_number": "13800138000",
    "notes": "",
}


def new_session(phase="after"):
    s = requests.Session()
    s.trust_env = False  # 本机代理会改写 Host，必须绕过
    s.headers.update({"X-Simulated": "1", "X-Phase": phase, "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"})
    return s


def parse(html):
    return BeautifulSoup(html, "html.parser")


EXCLUDE_ACTIONS = ("search", "i18n", "language", "newsletter", "login", "register")


def checkout_form(soup):
    """只挑结账/购物车相关的表单，避开页头的搜索框和语言切换。"""
    candidates = []
    for f in soup.find_all("form"):
        action = (f.get("action") or "").lower()
        if any(k in action for k in EXCLUDE_ACTIONS):
            continue
        candidates.append(f)
    for f in candidates:
        action = (f.get("action") or "").lower()
        if "checkout" in action or "basket" in action or action == "":
            return f
    return candidates[0] if candidates else None


def form_payload(soup, overrides=None):
    """把页面上的结账表单填成「合理值」。"""
    overrides = overrides or {}
    form = checkout_form(soup)
    if form is None:
        return None, {}
    data = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name:
            continue
        etype = (el.get("type") or "").lower()
        if etype in ("submit", "button", "file"):
            continue
        if name in overrides:
            data[name] = overrides[name]
        elif etype in ("radio", "checkbox"):
            data.setdefault(name, el.get("value", ""))
        elif el.name == "select":
            options = [o.get("value") for o in el.find_all("option") if o.get("value")]
            data[name] = options[0] if options else ""
        else:
            data[name] = el.get("value", "")
    data.update(overrides)
    return form.get("action") or "", data


def classify(url):
    for key, name in (
        ("thank-you", "done"),
        ("preview", "preview"),
        ("payment-method", "payment_method"),
        ("payment-details", "payment_details"),
        ("shipping-method", "shipping"),
        ("shipping-address", "address"),
    ):
        if key in url:
            return name
    if "/checkout/" in url:
        return "gateway"
    return "other"


def continue_link(soup):
    """占位页没有表单，只有一个「Continue」链接指向下一步。"""
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        if "continue" in text or "继续" in text:
            return a["href"]
    for a in soup.find_all("a", href=True):
        if "/checkout/" in a["href"]:
            return a["href"]
    return None


def run_one(idx, product_url, fail_reason=None, method="wechat", max_steps=14, phase="after"):
    """状态机式下单：不预设步骤数，按页面实际状态推进。

    这一点很关键：Oscar 会跳过配送方式页，硬编码步骤必然失败。
    """
    s = new_session(phase=phase)
    email = f"sim{phase[:3]}{idx:03d}@example.com"
    trail = []

    r = s.get(urljoin(BASE, product_url), timeout=20)
    soup = parse(r.text)
    form = soup.find("form", action=re.compile("basket"))
    if form is None:
        return {"ok": False, "error": "商品页无加购表单", "trail": trail}
    s.post(urljoin(BASE, form["action"]),
           data={el.get("name"): el.get("value", "") for el in form.find_all("input") if el.get("name")},
           timeout=20)

    r = s.get(urljoin(BASE, f"{LANG}/checkout/"), timeout=20)
    injected = False

    for _ in range(max_steps):
        page = classify(r.url)
        trail.append((page, r.status_code))
        soup = parse(r.text)

        if page == "done":
            return {"ok": True, "url": r.url, "trail": trail, "pages": len(trail)}

        form = checkout_form(soup)

        if form is None:
            href = continue_link(soup)
            if not href:
                return {"ok": False, "error": f"{page} 无表单也无继续链接", "trail": trail}
            r = s.get(urljoin(BASE, href), timeout=20)
            continue

        overrides = {}
        if page == "gateway":
            overrides = {"username": email, "options": "anonymous", "password": ""}
        elif page == "address":
            overrides = dict(ADDRESS)
        elif page in ("payment_method", "payment_details"):
            overrides = {"payment_method": method}
            if method == "card":
                overrides.update({"card_number": "6222021234567890", "card_expiry": "12/28", "card_cvv": "123"})
            if fail_reason and page == "payment_details" and not injected:
                overrides["fail"] = fail_reason

        action, data = form_payload(soup, overrides)

        # 确认页要点「提交订单」，否则只是重新渲染
        if page == "preview":
            data["action"] = "place_order"

        target = urljoin(BASE, action) if action else r.url
        r = s.post(target, data=data, timeout=30)

        if page == "payment_details" and "payment-details" in r.url:
            # 支付未通过：停在原页 = 失败（真实用户此时会退出或换方式）
            reason = fail_reason or "insufficient_balance"
            return {"ok": False, "error": reason, "trail": trail, "pages": len(trail)}

    return {"ok": False, "error": "超过最大步数", "trail": trail}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--fail-rate", type=float, default=0.0)
    ap.add_argument("--product", default="/en-gb/catalogue/the-shellcoders-handbook_198/")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    random.seed(args.seed)
    reasons = ["insufficient_balance", "limit_exceeded", "network_timeout", "risk_blocked"]
    results = []

    for i in range(args.count):
        fail = None
        if args.fail_rate and random.random() < args.fail_rate:
            fail = random.choice(reasons)
        started = time.time()
        res = run_one(i, args.product, fail_reason=fail, phase=args.variant, method=random.choice(["wechat", "alipay", "card", "balance"]))
        res["index"] = i
        res["seconds"] = round(time.time() - started, 2)
        res["simulated_fail"] = fail
        results.append(res)
        print(f"[{i+1}/{args.count}] ok={res['ok']} fail={fail} {res.get('error','')} {res['seconds']}s")

    ok = sum(1 for r in results if r["ok"])
    print(f"\n完成 {ok}/{len(results)}；平均耗时 {sum(r['seconds'] for r in results)/len(results):.2f}s")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("结果已写入", args.out)


if __name__ == "__main__":
    main()
