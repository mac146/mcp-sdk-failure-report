import os, pathlib, sys
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

mcp = MCPServer("Orders")
LOG = pathlib.Path(os.environ.get("ORDERS_LOG", "orders.log"))


@mcp.tool()
def create_order(item: str) -> str:
    """Create an order. Writes one line to the log."""
    with LOG.open("a") as f:
        f.write(item + "\n")
    return f"created {item}"


@mcp.tool()
def big(n: int) -> str:
    """Return n bytes of text."""
    return "x" * n


@mcp.tool()
async def big_with_progress(n: int, ctx: Context) -> str:
    """Report progress once, then return n bytes."""
    await ctx.report_progress(1, total=2, message="working")
    return "x" * n


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8001)
