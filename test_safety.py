import sys
sys.path.insert(0, ".")          # 保证能 import 到同目录的 main
from pydantic import ValidationError
from main import RunSQLArgs


def test_no_write_ops() -> None:
    RunSQLArgs(sql="SELECT count(*) FROM users")       # 应该通过，不抛异常

    try:
        RunSQLArgs(sql="DELETE FROM users")            # 应该被拒
        assert False, "DELETE 居然通过了！"
    except ValidationError:
        pass

    try:
        RunSQLArgs(sql="SELECT " + "x" * 600)          # 超长应该被拒
        assert False, "超长 SQL 居然通过了！"
    except ValidationError:
        pass

    print("全部通过 ✅")


if __name__ == "__main__":
    test_no_write_ops()
