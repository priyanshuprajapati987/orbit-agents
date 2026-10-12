# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Tool Modules

This package contains the core tool modules for system interaction:

- ComputerTool: Process management, file operations, clipboard control
- NewsTool: RSS news feed aggregation
- SecurityTool: Port scanning, firewall management, threat detection
- N8nTool: n8n automation server integration

Usage:
    from orbit.tools import ComputerTool, NewsTool

    computer = ComputerTool()
    processes = computer.run("list processes")

    news = NewsTool()
    headlines = news.run("get tech news")
"""

from .computer import ComputerTool  # noqa: F401
from .n8n import N8nTool  # noqa: F401
from .news import NewsTool  # noqa: F401
from .security import SecurityTool  # noqa: F401
from .mcp_tool import MCPManager, MCPClient, ORBITMCPServer, get_manager  # noqa: F401
from .cua_computer import CuaComputerTool  # noqa: F401
from .browser_tool import BrowserTool  # noqa: F401
from .desktop import DesktopAutomationTool  # noqa: F401
from .github_tool import GitHubTool  # noqa: F401
from .seo_tool import SEOTool  # noqa: F401
from .file_tools import FileTools  # noqa: F401
from .web_search import WebSearchTool  # noqa: F401
from .code_completion import CodeCompletionEngine  # noqa: F401
from .smart_actions import SmartActions  # noqa: F401
from .email_tool import EmailTool  # noqa: F401
from .calendar_tool import CalendarTool  # noqa: F401
