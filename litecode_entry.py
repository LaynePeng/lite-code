"""PyInstaller 打包入口：以绝对导入启动 lite-code CLI。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from litecode.cli import main  # noqa: E402

if __name__ == "__main__":
    main()