"""lite-code CLI：无头 Core 服务启动入口。

用法:
  lite-code serve --host 127.0.0.1 --port 0 --token xxx --workspace /path
  lite-code serve --port 8787          # 默认端口
  lite-code --version

启动成功后输出一行机器可读的就绪标记供 Electron 解析:
  LITECODE_CORE_READY port=8787 workspace=/path
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import uvicorn

from .app import AgentApp
from .server.app import create_app

VERSION = "0.6.3rc0"


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lite-code",
        description="lite-code Core：手写的 Code 开发 Agent 服务",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 Core 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--token", default=None, help="远程访问令牌（可选）")
    serve.add_argument("--workspace", default=None, help="工作区目录（默认当前目录）")
    serve.add_argument("--config-dir", default=".lite-code", help="配置/会话目录（默认 .lite-code）")
    serve.add_argument("--api-key", default=None, help="LLM API Key（默认读 DEEPSEEK_API_KEY）")
    serve.add_argument("--base-url", default=None, help="OpenAI 兼容 base_url（默认 DeepSeek）")
    serve.add_argument("--model", default=None, help="模型名（默认 deepseek-v4-flash）")
    serve.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])

    parser.add_argument("--version", action="store_true", help="显示版本")

    return parser.parse_args(argv)


def main(argv: list = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.version:
        print(f"lite-code {VERSION}")
        return

    if args.command != "serve":
        print("用法: lite-code serve [--port N] [--token xxx] [--workspace /path]")
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = AgentApp(
        workspace=args.workspace,
        config_dir=args.config_dir,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )
    fast_app = create_app(app, token=args.token)

    class _Server(uvicorn.Server):
        async def startup(self, sockets=None) -> None:
            await super().startup(sockets=sockets)
            port = args.port
            try:
                if self.servers and self.servers[0].sockets:
                    port = self.servers[0].sockets[0].getsockname()[1]
            except Exception:
                pass
            # 机器可读就绪标记：Electron 主进程据此拿到实际端口
            print(f"LITECODE_CORE_READY port={port} workspace={app.workspace}", flush=True)
            print(f"lite-code Core 已启动 → http://{args.host}:{port}", flush=True)

    server = _Server(uvicorn.Config(fast_app, host=args.host, port=args.port, log_level=args.log_level))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        import asyncio

        try:
            asyncio.run(app.close())
        except RuntimeError:
            pass


if __name__ == "__main__":
    main()