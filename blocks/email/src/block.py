"""Email Block - SendGrid/AWS SES integration"""
from blocks.base import LegoBlock
from typing import Dict, Any, List

class EmailBlock(LegoBlock):
    """Email sending - SendGrid, AWS SES, SMTP"""
    name = "email"
    version = "1.0.0"
    requires = ["config"]
    layer = 5  # Integration layer
    tags = ["email", "notification", "integration"]
    default_config = {
        "provider": "smtp",
        "smtp_host": "localhost",
        "smtp_port": 587
    }
    
    PROVIDERS = {
        "sendgrid": {"url": "https://api.sendgrid.com/v3/mail/send"},
        "ses": {"region": "us-east-1"},
        "smtp": {}
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.provider = config.get("provider", "sendgrid")
        self.api_key = config.get("sendgrid_key") or config.get("ses_key")
        self.from_email = config.get("from_email", "noreply@cerebrum.io")
        
        # SMTP settings
        self.smtp_host = config.get("smtp_host")
        self.smtp_port = config.get("smtp_port", 587)
        self.smtp_user = config.get("smtp_user")
        self.smtp_pass = config.get("smtp_pass")
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "send":
            return await self._send_email(input_data)
        elif action == "send_template":
            return await self._send_template(input_data)
        elif action == "validate_address":
            return await self._validate_address(input_data)
        return {"error": "Unknown action"}
    
    async def _send_email(self, data: Dict) -> Dict:
        """Send email"""
        to = data.get("to")
        subject = data.get("subject")
        body = data.get("body")
        html = data.get("html")
        attachments = data.get("attachments", [])
        
        if self.provider == "sendgrid":
            return await self._send_sendgrid(to, subject, body, html, attachments)
        elif self.provider == "ses":
            return await self._send_ses(to, subject, body, html)
        elif self.provider == "smtp":
            return await self._send_smtp(to, subject, body, html, attachments)
        
        return {"error": f"Unknown provider: {self.provider}"}
    
    async def _send_sendgrid(self, to: str, subject: str, body: str, html: str = None, attachments: List = None) -> Dict:
        """Send via SendGrid"""
        if not self.api_key:
            return {"error": "SendGrid API key not configured"}
        
        try:
            import aiohttp
            
            payload = {
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": self.from_email},
                "subject": subject,
                "content": []
            }
            
            if html:
                payload["content"].append({"type": "text/html", "value": html})
            else:
                payload["content"].append({"type": "text/plain", "value": body})
            
            # Handle attachments
            if attachments:
                payload["attachments"] = []
                for att in attachments:
                    import base64
                    payload["attachments"].append({
                        "filename": att.get("filename"),
                        "content": base64.b64encode(att.get("content")).decode(),
                        "type": att.get("type", "application/octet-stream")
                    })
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.PROVIDERS["sendgrid"]["url"],
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as resp:
                    if resp.status == 202:
                        return {"sent": True, "provider": "sendgrid", "to": to}
                    else:
                        error = await resp.text()
                        return {"error": f"SendGrid error: {error}", "status": resp.status}
                        
        except ImportError:
            return {"error": "aiohttp not installed"}
        except Exception as e:
            return {"error": f"Send failed: {str(e)}"}
    
    async def _send_ses(self, to: str, subject: str, body: str, html: str = None) -> Dict:
        """Send via AWS SES"""
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            client = boto3.client('ses', region_name=self.PROVIDERS["ses"]["region"])
            
            msg = {
                "Source": self.from_email,
                "Destination": {"ToAddresses": [to]},
                "Message": {
                    "Subject": {"Data": subject},
                    "Body": {}
                }
            }
            
            if html:
                msg["Message"]["Body"]["Html"] = {"Data": html}
            else:
                msg["Message"]["Body"]["Text"] = {"Data": body}
            
            response = client.send_email(**msg)
            
            return {
                "sent": True,
                "provider": "ses",
                "message_id": response["MessageId"]
            }
            
        except ImportError:
            return {"error": "boto3 not installed. Run: pip install boto3"}
        except ClientError as e:
            return {"error": f"SES error: {str(e)}"}
    
    async def _send_smtp(self, to: str, subject: str, body: str, html: str = None, attachments: List = None) -> Dict:
        """Send via SMTP"""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            from email.mime.base import MIMEBase
            from email import encoders
            
            msg = MIMEMultipart()
            msg["From"] = self.from_email
            msg["To"] = to
            msg["Subject"] = subject
            
            if html:
                msg.attach(MIMEText(html, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))
            
            # Attachments
            for att in attachments or []:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att["content"])
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename= {att['filename']}"
                )
                msg.attach(part)
            
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_pass)
            server.send_message(msg)
            server.quit()
            
            return {"sent": True, "provider": "smtp", "to": to}
            
        except Exception as e:
            return {"error": f"SMTP error: {str(e)}"}
    
    async def _send_template(self, data: Dict) -> Dict:
        """Send using template"""
        template_id = data.get("template_id")
        to = data.get("to")
        variables = data.get("variables", {})
        
        # Template rendering
        templates = {
            "welcome": "Welcome {name}! Your API key is {api_key}",
            "alert": "Alert: {message}",
            "report": "Daily report for {date}: {summary}"
        }
        
        template = templates.get(template_id, "")
        body = template.format(**variables)
        
        return await self._send_email({
            "to": to,
            "subject": data.get("subject", "Notification"),
            "body": body
        })
    
    async def _validate_address(self, data: Dict) -> Dict:
        """Validate email address format"""
        import re
        email = data.get("email")
        
        pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
        is_valid = re.match(pattern, email) is not None
        
        return {
            "email": email,
            "valid": is_valid,
            "normalized": email.lower().strip() if is_valid else None
        }
    
    def health(self) -> Dict:
        h = super().health()
        h["provider"] = self.provider
        h["configured"] = self.api_key is not None or self.smtp_host is not None
        return h
