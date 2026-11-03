"""数据库访问层：只读、超时、行数上限，全部在这里兜底。

防御纵深（4 层，任何一层失效还有下一层）：
1. 只读事务：default_transaction_read_only=on，数据库自己拒绝写
2. 超时：statement_timeout，防止 pg_sleep 之类拖死连接
3. 行数上限：fetchmany 只取前 N 行，防止百万行打爆内存
4. 关键字黑名单：在 Pydantic 校验层（main.py 的 RunSQLArgs）

注意：agent 场景的 SQL 是模型【生成】的，没法像传统 web 后端那样用参数化查询防注入，
所以这里的防线是「只读 + 限制 + 校验」的纵深防御，而不是参数化。
"""
import os

MAX_ROWS = 1000
STATEMENT_TIMEOUT_MS = 5000


def get_connection():
    import psycopg  # 延迟导入：离线跑 Mock 时不需要装 psycopg

    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5433/sqlagent",
    )
    # 把「只读 + 超时」钉在连接层，任何走这个连接的代码都无法绕过
    return psycopg.connect(
        dsn,
        # 连接层超时：连不上（比如数据库容器没起）就 5 秒报错，而不是无限挂起
        connect_timeout=5,
        options=(
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"
        ),
    )


def run_sql(sql: str) -> str:
    """执行一条查询，返回格式化文本结果。安全限制都在这里。"""
    sql = sql.strip().rstrip(";")

    # 把「可能出错的操作」整个包进 try，except 接住后返回友好提示给模型
    try:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
            # 多取 1 行，用来判断是否被截断
            rows = cur.fetchmany(MAX_ROWS + 1)
            cols = [d.name for d in cur.description] if cur.description else []
    except Exception as e:
        return f"查询失败：{e}"

    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    return _format_rows(cols, rows, truncated=truncated)


def _format_rows(cols: list[str], rows: list, truncated: bool) -> str:
    if not cols:
        return "查询成功，但没有返回列（可能不是 SELECT 语句）"

    header = " | ".join(cols)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(" | ".join(str(v) for v in row))

    lines.append(f"（共 {len(rows)} 行）")
    if truncated:
        lines.append(f"[!] 结果超过 {MAX_ROWS} 行已截断，请用更精确的 WHERE 条件缩小范围。")
    return "\n".join(lines)


def get_schema() -> str:
    """从 information_schema 动态读取表结构，返回给模型看的文本。

    为什么动态读：表结构会变，硬编码会过时、让模型查错列；
    表多了手写也不现实。启动时读一次，保证 prompt 里的 schema 永远最新。
    """
    sql = (
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position"
    )
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    tables: dict[str, list[str]] = {}
    for table, column, dtype in rows:
        tables.setdefault(table, []).append(f"{column} ({dtype})")

    return "\n".join(f"- {t}({', '.join(cols)})" for t, cols in tables.items())


def execute_raw(sql: str) -> list[tuple]:
    """执行查询，返回原始行（不格式化），供评测比对结果集用。"""
    sql = sql.strip().rstrip(";")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()
