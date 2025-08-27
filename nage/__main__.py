#!/usr/bin/env python3
import sys
import os

# 添加包路径以支持绝对导入
if __package__ is None:
    # 当作为脚本直接运行时
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nage.main import cli
else:
    # 当作为模块导入时
    from .main import cli

if __name__ == "__main__":
    cli()
