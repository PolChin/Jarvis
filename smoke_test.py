"""
Phase 0 smoke test — proves the two foundations work before we build anything:
  1. an MCP server (T-800) can be launched and called as a tool
  2. (optionally) the LLM client can reach Gemini/Claude

Run:  python smoke_test.py
(from the phoenix-demo/ root, with deps installed — see README)
"""

import asyncio
import os
from dotenv import load_dotenv

from fastmcp import Client

# Load environment variables from .env file if present
load_dotenv()



async def test_mcp():
    print("=" * 60)
    print("TEST 1 — MCP: launch T-800 and call can_commit")
    print("=" * 60)

    # FastMCP can spawn the server from its python file and talk over stdio
    client = Client("mcp_servers/t800.py")
    async with client:
        tools = await client.list_tools()
        print("tools exposed by T-800:", [t.name for t in tools])

        # happy case — small qty, stock on hand
        r1 = await client.call_tool("can_commit",
                                    {"grade": "D777C", "qty": 100, "load_date": "2026-03-18"})
        print("\n[OK case] can_commit(D777C, 100, 2026-03-18):")
        print("   ", r1.data)

        # shortfall case — qty exceeds on-hand, recovers next production
        r2 = await client.call_tool("can_commit",
                                    {"grade": "D388C", "qty": 600, "load_date": "2026-03-15"})
        print("\n[recovery case] can_commit(D388C, 600, 2026-03-15):")
        print("   ", r2.data)


def test_llm():
    print("\n" + "=" * 60)
    print("TEST 2 — LLM client (optional, needs API key)")
    print("=" * 60)
    provider = os.environ.get("PHOENIX_LLM", "gemini")
    keyvar = "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"
    key = os.environ.get(keyvar)
    if not key or "your_" in key:
        print(f"skipped — set {keyvar} (and PHOENIX_LLM={provider}) to test the LLM")
        return

    from common.llm_client import get_llm
    llm = get_llm("fast")
    r = llm.complete(
        system="You are terse. Reply in 5 words or fewer.",
        messages=[{"role": "user", "content": "Greet Project Phoenix."}],
    )
    print(f"provider={provider} model={llm.model}")
    print("response:", r.text)


if __name__ == "__main__":
    asyncio.run(test_mcp())
    test_llm()
    print("\nPhase 0 OK — foundations work. Ready for Phase 1 (workflow engine).")
