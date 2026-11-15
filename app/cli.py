"""관리 명령.

    uv run python -m app.cli create-admin --username admin
    uv run python -m app.cli reset-password --username admin

비밀번호는 인자로 받지 않고 프롬프트로 입력받는다(셸 기록에 남지 않도록).
"""

import argparse
import getpass
import sys

from app.config import get_settings
from app.db import create_db_engine, create_session_factory
from app.errors import DomainError
from app.services import auth as auth_service


def _prompt_password() -> str:
    first = getpass.getpass("비밀번호: ")
    second = getpass.getpass("비밀번호 확인: ")
    if first != second:
        raise SystemExit("비밀번호가 일치하지 않습니다")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("create-admin", "관리자 계정 생성"),
        ("reset-password", "비밀번호 재설정 (기존 세션 모두 로그아웃)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--username", required=True)
    args = parser.parse_args(argv)

    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    try:
        password = _prompt_password()
        with factory() as session:
            if args.command == "create-admin":
                auth_service.create_admin(session, args.username, password)
                print(f"관리자 생성: {args.username}")
            else:
                auth_service.reset_password(session, args.username, password)
                print(f"비밀번호 재설정 완료, 기존 세션 무효화: {args.username}")
    except DomainError as exc:
        print(f"오류: {exc.detail}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
