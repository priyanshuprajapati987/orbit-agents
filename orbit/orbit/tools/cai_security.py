# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
import asyncio
import json
import os

from ..config import DEFAULT_OLLAMA_MODEL
from ..logging_config import get_logger
from .security import SecurityTool

logger = get_logger(__name__)

_CAI_AVAILABLE = False

try:
    from cai.sdk.agents import (
        Agent,
        OpenAIChatCompletionsModel,
        RunConfig,
        Runner,
        function_tool,
    )
    from openai import AsyncOpenAI

    _CAI_AVAILABLE = True
    logger.info("CAI SDK loaded successfully")
except ImportError as e:
    logger.warning("CAI SDK not available", extra={"error": str(e)})
    Agent = Runner = OpenAIChatCompletionsModel = function_tool = RunConfig = AsyncOpenAI = None


_security_tool = SecurityTool()
_cai_agent = None
_client = None


CAI_TOOLS = []

if _CAI_AVAILABLE:

    @function_tool
    def scan_processes() -> str:
        data = _security_tool.scan_processes()
        return json.dumps(data, indent=2)

    @function_tool
    def scan_ports() -> str:
        ports = _security_tool.scan_ports()
        return "\n".join(ports)

    @function_tool
    def scan_network() -> str:
        return _security_tool.scan_network()

    @function_tool
    def firewall_status() -> str:
        return _security_tool.firewall_status()

    @function_tool
    def disk_usage() -> str:
        drives = _security_tool.disk_usage()
        return json.dumps(drives, indent=2)

    @function_tool
    def active_connections() -> str:
        return _security_tool.active_connections()

    @function_tool
    def defender_scan() -> str:
        return _security_tool.defender_scan()

    @function_tool
    def full_report() -> str:
        return _security_tool.full_report()

    CAI_TOOLS = [
        scan_processes,
        scan_ports,
        scan_network,
        firewall_status,
        disk_usage,
        active_connections,
        defender_scan,
        full_report,
    ]


def get_agent():
    global _cai_agent, _client
    if not _CAI_AVAILABLE:
        logger.warning("CAI agent requested but SDK not available")
        return None
    if _cai_agent is not None:
        return _cai_agent

    base_url = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
    model = os.getenv("CAI_MODEL", "ollama/" + DEFAULT_OLLAMA_MODEL)

    _client = AsyncOpenAI(base_url=base_url, api_key="ollama")
    _cai_agent = Agent(
        name="Security Analyst",
        instructions="""You are a cybersecurity AI assistant integrated into ORBIT.
Your role:
- Analyze system security using available tools
- Provide clear, actionable security insights
- Identify potential threats and vulnerabilities
- Explain findings in simple terms
- Suggest remediation steps when needed

Always use the tools to gather data before making claims.
Present findings in a structured, readable format.""",
        model=OpenAIChatCompletionsModel(model=model, openai_client=_client),
        tools=CAI_TOOLS,
    )
    logger.info("CAI agent initialized", extra={"model": model, "base_url": base_url})
    return _cai_agent


async def analyze(query: str) -> str:
    agent = get_agent()
    if not agent:
        return "CAI module unavailable — install with: pip install cai-framework"
    try:
        result = await Runner.run(agent, input=query)
        return result.final_output
    except Exception as e:
        error_msg = str(e)
        if "Cannot connect to host" in error_msg or "refused" in error_msg:
            return (
                "CAI Error: Cannot connect to Ollama. "
                "Make sure Ollama is running: `ollama serve` "
                "and the model is pulled: `ollama pull qwen2.5:14b`"
            )
        if "AuthenticationError" in error_msg or "api_key" in error_msg:
            return (
                "CAI Error: Authentication issue. "
                "For Ollama, set CAI_MODEL=ollama/qwen2.5:14b "
                "and ensure OLLAMA_URL is correct."
            )
        logger.error("CAI Analysis Error", extra={"error": error_msg})
        return f"CAI Analysis Error: {error_msg}"


async def security_audit() -> str:
    return await analyze(
        "Perform a comprehensive security audit. Check: firewall status, "
        "active network connections, listening ports, running processes, "
        "disk usage, and run a defender scan. Provide a security assessment "
        "with recommendations."
    )


async def threat_analysis() -> str:
    return await analyze(
        "Analyze active network connections and listening ports for potential "
        "security threats. Check for unusual services or connections. "
        "Provide a threat assessment report."
    )


def run_analysis(query: str) -> str:
    try:
        return asyncio.run(analyze(query))
    except Exception as e:
        logger.error("CAI Analysis Error", extra={"error": str(e)})
        return f"CAI Analysis Error: {str(e)}"


def run_audit() -> str:
    try:
        return asyncio.run(security_audit())
    except Exception as e:
        logger.error("CAI Audit Error", extra={"error": str(e)})
        return f"CAI Audit Error: {str(e)}"


def run_threat_analysis() -> str:
    try:
        return asyncio.run(threat_analysis())
    except Exception as e:
        logger.error("CAI Threat Analysis Error", extra={"error": str(e)})
        return f"CAI Threat Analysis Error: {str(e)}"


if __name__ == "__main__":
    logger.info(run_audit())
