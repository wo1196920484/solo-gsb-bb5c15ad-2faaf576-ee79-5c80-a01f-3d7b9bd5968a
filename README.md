# 批次资金流水核销 API

基于 **Python 3.12 + FastAPI** 的批次资金流水核销服务。接收左右两侧各最多 12 笔流水，
按规则枚举全部合法核销组，并用位掩码记忆化 DP 求出**核销金额绝对值总和最大**的互斥集合；
最优解并列时返回字典序最小的组编码列表。

## 一条命令启动

前置要求：已安装 Docker 与 Docker Compose（`docker compose` 子命令）。

```bash
docker compose up --build -d
```

服务启动后监听 **8000 端口**：

| 地址 | 说明 |
| --- | --- |
| <http://localhost:8000> | 服务根地址 |
| <http://localhost:8000/health> | 健康检查 |
| <http://localhost:8000/docs> | Swagger UI（可在线发请求） |
| <http://localhost:8000/redoc> | ReDoc 文档 |
| <http://localhost:8000/openapi.json> | OpenAPI 3 描述文件 |

停止并清理容器：

```bash
docker compose down
```

## 接口：`POST /reconcile`

### 请求体

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `left` | `Entry[1..12]` | 左侧流水，最多 12 笔 |
| `right` | `Entry[1..12]` | 右侧流水，最多 12 笔 |

`Entry`：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | string | 非空；在**整个批次（含两侧）**内唯一，重复返回 `400` |
| `time` | string (ISO 8601) | **必须带时区**的 UTC 时间，如 `2026-09-17T08:00:00Z`、`2026-09-17T16:00:00+08:00`（自动归一化为 UTC）；不带时区返回 `422` |
| `amount` | integer | 非零、带符号整数，单位**分**；为 0 返回 `422` |

### 核销规则

一个核销组只能是以下两种形状之一：

- **一对一**：左侧 1 笔、右侧 1 笔；
- **一对多**：一侧 1 笔，另一侧 **2～3** 笔（不允许 2:2、3:3）。

成组必须同时满足：

1. **同号**：组内全部金额同为正或同为负（允许正负两组各自核销）；
2. **48 小时时间窗**：组内最早与最晚流水时间差 `≤ 48h`（恰好 48h 合法）；
3. **金额和相等**：左侧金额之和 == 右侧金额之和。

每笔流水**最多进入一个核销组**。在全部合法候选中选择核销金额绝对值总和
`total_amount = Σ |组金额和|` 最大的互斥集合；若有多个最优解，取
**排序后组编码列表字典序最小**的一个。组编码为
`(升序左侧 ID 列表, 升序右侧 ID)`，组列表本身也按组编码升序输出。

### 响应体

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `groups` | `Group[]` | 核销组（按组编码升序），每组含 `left_ids` / `right_ids`（各自升序）/ `amount`（带符号金额，单位分） |
| `total_amount` | integer | 核销金额**绝对值**总和，单位分 |
| `unmatched_left_ids` | string[] | 左侧未匹配流水 ID（升序） |
| `unmatched_right_ids` | string[] | 右侧未匹配流水 ID（升序） |

### 示例

```bash
curl -s http://localhost:8000/reconcile \
  -H 'Content-Type: application/json' \
  -d '{
    "left": [
      {"id": "L1", "time": "2026-09-17T00:00:00Z", "amount": 100},
      {"id": "L2", "time": "2026-09-17T00:00:00Z", "amount": -100}
    ],
    "right": [
      {"id": "R1", "time": "2026-09-18T01:00:00Z", "amount": 40},
      {"id": "R2", "time": "2026-09-18T02:00:00Z", "amount": 60},
      {"id": "R3", "time": "2026-09-18T03:00:00Z", "amount": -100}
    ]
  }'
```

响应：

```json
{
  "groups": [
    {"left_ids": ["L1"], "right_ids": ["R1", "R2"], "amount": 100},
    {"left_ids": ["L2"], "right_ids": ["R3"], "amount": -100}
  ],
  "total_amount": 200,
  "unmatched_left_ids": [],
  "unmatched_right_ids": []
}
```

### 错误响应

- `400`：批次内存在重复 ID，`detail` 中列出重复 ID；
- `422`：请求体不满足约束（缺字段、超过 12 笔、金额为 0、时间不带时区等）。

## 算法说明

1. 枚举两侧大小 1/2/3 的所有子集，按四种组形状（1:1、1:2、1:3、2:1、3:1）
   配对，过滤掉异号、金额和不等、时间跨度 > 48h 的组合（每侧 ≤ 12 笔，候选至多约 7k 个）；
2. 将问题视为**候选组上的带权最大独立集**（共享流水即冲突，权重为金额绝对值），
   用 24 位位掩码做记忆化 DP；按流水在候选中的静态冲突度对掩码位重排以压缩状态数；
   正、负流水天然不相交，分两个宇宙独立求解；
3. 解重建时按组编码升序贪心选取仍可属于某个最优解的最小组，
   保证并列时返回字典序最小的组编码列表。

典型满规模（12+12）请求耗时约 10ms 量级；人工构造的最密集重叠实例（全部小额金额）
约数百毫秒。

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试（单元测试、400 组随机实例对拍暴力求解器、HTTP 集成测试）：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── engine.py      # 核销引擎：候选枚举 + 位掩码 DP + 字典序重建
│   ├── schemas.py     # Pydantic 请求/响应模型与入参校验
│   └── main.py        # FastAPI 路由
├── tests/             # pytest：引擎单测、暴力对拍、API 集成测试
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── requirements-dev.txt
```
