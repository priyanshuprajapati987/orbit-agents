# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
ORBIT Email Tool

Send and manage emails via SMTP.
Supports: Gmail, Outlook, Yahoo, custom SMTP servers.
"""

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Dict, List
from ..logging_config import get_logger

logger = get_logger(__name__)


class EmailTool:
    def __init__(self):
        self.name = "email"
        self.description = "Send and manage emails"
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        try:
            self.smtp_port = int(os.getenv("SMTP_PORT", "587") or 587)
        except (ValueError, TypeError):
            self.smtp_port = 587
        self.email_address = os.getenv("EMAIL_ADDRESS", "")
        # Read password lazily (not stored in memory)
        self._password_env = "EMAIL_PASSWORD"

    def _get_connection(self):
        email_password = os.getenv(self._password_env, "")
        if not self.email_address or not email_password:
            return None
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15)
            server.starttls()
            server.login(self.email_address, email_password)
            return server
        except Exception as e:
            logger.error("SMTP connection failed: %s", e)
            return None

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        cc: str = "",
        bcc: str = "",
        attachments: List[str] = None,
    ) -> Dict:
        if not self.email_address:
            return {"error": "Email not configured. Set EMAIL_ADDRESS and EMAIL_PASSWORD env vars."}
        if not to:
            return {"error": "Recipient 'to' address is required"}
        to = to.replace("\r", "").replace("\n", "")
        cc = cc.replace("\r", "").replace("\n", "") if cc else ""
        bcc = bcc.replace("\r", "").replace("\n", "") if bcc else ""
        subject = subject.replace("\r", "").replace("\n", "")
        try:
            msg = MIMEMultipart()
            msg["From"] = self.email_address
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type))
            if attachments:
                for filepath in attachments:
                    p = Path(filepath).resolve()
                    blocked = ["System32", "Windows", "Program Files", "ProgramData", ".ssh", ".env"]
                    if any(b.lower() in str(p).lower() for b in blocked):
                        continue
                    # ToolGate: check attachment path is safe
                    try:
                        from ..core.policy_engine import get_policy_engine

                        path_result = get_policy_engine().check_path(str(p), mutate=False)
                        if not path_result.allowed:
                            continue
                    except Exception:
                        pass
                    if p.exists() and p.is_file():
                        with open(p, "rb") as f:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(f.read())
                            encoders.encode_base64(part)
                            part.add_header("Content-Disposition", "attachment", filename=p.name)
                            msg.attach(part)
            recipients = [to]
            if cc:
                recipients.extend([c.strip() for c in cc.split(",") if c.strip()])
            if bcc:
                recipients.extend([b.strip() for b in bcc.split(",") if b.strip()])
            server = self._get_connection()
            if not server:
                return {"error": "Failed to connect to SMTP server"}
            try:
                server.sendmail(self.email_address, recipients, msg.as_string())
                return {"success": True, "to": to, "subject": subject}
            finally:
                server.quit()
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return {"error": str(e)}

    def send_bulk(self, recipients: List[str], subject: str, body: str, html: bool = False) -> Dict:
        results = []
        for recipient in recipients:
            result = self.send(recipient, subject, body, html)
            results.append({"to": recipient, "result": result})
        successful = sum(1 for r in results if r["result"].get("success"))
        return {"total": len(recipients), "successful": successful, "results": results}

    def check_config(self) -> Dict:
        password = os.getenv(self._password_env, "")
        return {
            "configured": bool(self.email_address and password),
            "smtp_server": self.smtp_server,
            "smtp_port": self.smtp_port,
            "email": self.email_address[:3] + "***" if self.email_address else "Not set",
        }
