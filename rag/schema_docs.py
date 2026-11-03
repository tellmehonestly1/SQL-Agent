"""RAG 的索引源：把每张表的「结构 + 业务语义」写成文档块。

为什么不用 db.get_schema() 那种纯结构文本直接做 RAG：
纯结构只有「列名 + 类型」，没有业务含义（比如 amount 到底是金额还是数量、
单位是什么、要统计总额得 JOIN 哪张表）。把这些语义写进文档块，检索时才能把
「每个用户的订单总额」正确关联到「orders 表 + SUM(amount) GROUP BY user_id」。

真实工程里，这些语义通常来自：数据字典、表/列的 COMMENT、业务文档。
这里用小数据源演示，手动写清楚即可。
"""

# 手写的业务语义文档（表多的时候，这部分会来自数据字典/COMMENT）
STATIC_DOCS: list[dict[str, str]] = [
    {
        "id": "users",
        "title": "users 表",
        "content": (
            "users 表存储用户信息。字段：id（自增主键）、name（用户名）、"
            "created_at（创建时间）。查询用户总数、列出所有用户用这张表。"
        ),
    },
    {
        "id": "orders",
        "title": "orders 表",
        "content": (
            "orders 表存储订单。字段：id（自增主键）、user_id（外键，关联 users.id）、"
            "amount（订单金额，NUMERIC 类型，单位元）、created_at（创建时间）。"
        ),
    },
    {
        "id": "order-total",
        "title": "每个用户的订单总额怎么算",
        "content": (
            "计算每个用户的订单总额：把 orders 和 users 按 user_id 关联，"
            "对 orders.amount 做 SUM，并按 user_id（或 users.name）分组。"
            "SQL 示例：SELECT u.name, SUM(o.amount) AS total FROM orders o "
            "JOIN users u ON u.id = o.user_id GROUP BY u.name;"
        ),
    },
    {
        "id": "order-count",
        "title": "订单数量统计",
        "content": (
            "统计订单数量用 COUNT。每个用户的订单数："
            "SELECT user_id, COUNT(*) FROM orders GROUP BY user_id;"
        ),
    },
]


def build_schema_documents(schema_text: str) -> list[dict]:
    """One structural document per table, with stable identity and join metadata."""
    docs = []
    for item in STATIC_DOCS:
        doc = dict(item)
        doc["tables"] = (
            ["users", "orders"] if item["id"].startswith("order-") else [item["id"]]
        )
        docs.append(doc)
    for line in schema_text.splitlines():
        table = line.removeprefix("- ").split("(", 1)[0]
        if not table:
            continue
        docs.append(
            {
                "id": f"schema:{table}",
                "title": f"{table} schema",
                "content": line,
                "tables": [table],
            }
        )
    return docs
