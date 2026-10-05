"""Run the MCP server over stdio: `python -m mtg_deck_advisor.mcp_server [--client NAME]`.

An MCP client (Claude Code, Claude Desktop) starts this as a subprocess and
speaks JSON-RPC on its stdin and stdout, so logs go to stderr. The server
uses the same database, embedder and reranker as the API, from the same
settings (.env). `--client` names the client in the run records (`mcp:NAME`).
"""

import argparse
import sys
from typing import Any

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mtg_deck_advisor.config import get_settings
from mtg_deck_advisor.db.connection import connect
from mtg_deck_advisor.llm.factory import build_embedder
from mtg_deck_advisor.mcp_server.server import McpSession, build_server
from mtg_deck_advisor.observability.logging import configure_logging
from mtg_deck_advisor.retrieval.rerank import build_reranker


async def serve(server: Server[Any]) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default="client", help="the client's name, for run records")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings, stream=sys.stderr)
    with connect(settings) as conn:
        session = McpSession(
            conn=conn,
            embedder=build_embedder(settings),
            reranker=build_reranker(settings),
            client_name=args.client,
        )
        try:
            anyio.run(serve, build_server(session))
        finally:
            session.close()


if __name__ == "__main__":
    main()
