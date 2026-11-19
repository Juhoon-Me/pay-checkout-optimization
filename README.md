# 支付环节体验优化 · 结账链路实验

基于 [django-oscar](https://github.com/django-oscar/django-oscar) 的可运行电商沙箱，用来研究一个具体问题：

> **用户走到了支付这一步，为什么会放弃？**

Oscar 默认的结账链路里，支付方式节点是一个直接重定向的视图——用户在页面上从来没有见过它，支付详情页则是一段占位文案。这意味着「支付环节的体验」在这套系统里根本不存在，也就无从优化。

这个仓库把这一环补成了真实可交互的页面，并建立了一条从埋点到看板的完整观测链路。

---

## 关键发现

| | 改版前 | 改版后 |
|---|---|---|
| 支付方式节点：到达 → 渲染 | 28 次到达 / **0 次渲染** | 100% 渲染 |
| 可选支付方式 | 0 | 4 |
| 支付失败的可归因性 | 统一文案「支付失败」 | 4 类原因 × 2 个可行动作 |

**28 次到达、0 次渲染** —— 这个数字说明的不是转化率低，而是这一环节在页面上不存在。做漏斗分析时如果只统计「到达」，会把它误判成「用户不想选」。

---

## 我改了什么

| 模块 | 内容 | 解决的问题 |
|---|---|---|
| `sandbox/apps/tracking/` | 7 节点行为埋点、中间件、`funnel_report` 命令 | 原来没有任何行为数据可看 |
| `sandbox/apps/payext/` | 支付方式选择页、支付详情页、4 类失败文案 | 原来这一步不存在 / 是占位页 |
| `sandbox/apps/dashboard/` | 漏斗看板 `/dashboard/`、CSV 导出 | 数据只有开发者能看，业务方看不了 |
| `sandbox/templates/oscar/checkout/` | 4 个模板覆盖 | 不改核心代码即可扩展，能跟随上游升级 |
| `scripts/simulate_users.py` | 状态机式下单脚本 | 用于口径校准与链路回归，与真人数据分开统计 |

### 埋点记了什么

`CheckoutEvent` 每条记录包含：节点（7 个）、事件（进入 / 离开 / 提交 / 校验失败 / 支付成功）、停留时长、HTTP 状态码、失败字段、支付方式、失败原因、设备、**是否脚本模拟**、**版本（phase）**。

后两个字段是关键：没有它们，脚本跑出来的接近 100% 的完成率会污染结论。

---

## 快速开始

环境已在 Python 3.13 + Django 6.1 下跑通。

```bash
git clone https://github.com/Juhoon-Me/pay-checkout-optimization.git
cd pay-checkout-optimization
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install Whoosh

cd sandbox
python manage.py migrate
python manage.py runserver
```

- 商城：http://127.0.0.1:8000/en-gb/
- 数据看板：http://127.0.0.1:8000/dashboard/?phase=after&source=human

**商品列表为空**说明搜索索引没建，执行 `python manage.py rebuild_index` 即可。

**手机真机测试**：`python manage.py runserver 0.0.0.0:8000`，手机连同一 Wi-Fi 访问 `http://<本机局域网 IP>:8000/en-gb/`，无需安装任何东西。

---

## 看板与报表

```bash
# 终端里出漏斗（只统计真人数据）
python manage.py funnel_report --source human

# 导出明细 CSV
open http://127.0.0.1:8000/dashboard/export.csv
```

看板支持两个筛选维度：

- `phase=baseline|after|all` —— 改版前 / 改版后 / 全部
- `source=human|sim|all` —— 真人 / 脚本 / 全部

---

## 数据与结论的边界

这一节比上面的数字更重要：

- **真人样本只有 4 人（1 人完整下单）**。不足以做显著性判断，所有比率只作方向性参考。
- **改版前后完成率不可比**。改版前的链路里没有支付环节，「支付失败」在物理上不可能发生；分母定义不同，两个数不能放在一起比。
- **4 类失败是按设计注入的**（`?fail=insufficient_balance` 等），用于验证失败页的归因与动作是否成立，不代表真实失败率。
- 因此本项目的结论限定为**结构性**：这一环从不可交互变为可交互，从 0 个选项变为 4 个选项。**不作提升幅度声明。**

---

## 已知未完成

- 中断恢复：关掉页面后无法回到原步骤续付（属跨会话能力，本期未做）
- 分期引导：数据里看到高客单场景的机会，只留了入口，未做策略
- 埋点在 `validation_error` 与「支付失败」的判定上有过一版错误分类，已在中间件里通过标记位修正，见 `middleware.py` 注释

---

## 关于上游

本项目 fork 自 [django-oscar](https://github.com/django-oscar/django-oscar)（BSD 许可证），保留了其完整代码与许可证。上游说明见 [`README.rst`](README.rst)。

改动集中在 `sandbox/apps/` 与 `sandbox/templates/oscar/` 下，未修改 Oscar 核心包 `src/`，因此可以跟随上游版本升级。
