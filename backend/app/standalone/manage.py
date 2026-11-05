"""仅对显式配置的独立数据库执行初始化和账号管理。"""
import argparse
import getpass
import os

from sqlalchemy import create_engine

from .identity import Base, create_account
from ..plus.blockchain_supervision import models as _bs_models  # 注册审查表
from ..models import models as _knowledge_models  # 注册知识绑定与引用表
from ..services import token_service as _token_service  # 注册模型用量表


def main():
    parser = argparse.ArgumentParser(description='独立分包审查数据库管理')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init-db')
    create = commands.add_parser('create-user')
    create.add_argument('username')
    args = parser.parse_args()
    engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
    if args.command == 'init-db':
        Base.metadata.create_all(engine)
        print('已初始化缺失的表')
    else:
        first = getpass.getpass('密码（至少 12 字符）: ')
        second = getpass.getpass('再次输入密码: ')
        if first != second:
            raise SystemExit('两次密码不一致')
        create_account(engine, args.username, first)
        print('账号已创建')


if __name__ == '__main__':
    main()
