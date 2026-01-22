import os
import re
from typing import List, Dict, Any

class SecurityScanner:
    """Scanner for detecting potentially malicious patterns in skill code."""

    # RegEx patterns for dangerous interactions with severity
    DANGEROUS_PATTERNS = {
        "python": [
            (r"eval\(", "Use of eval() which can execute arbitrary code", "high"),
            (r"exec\(", "Use of exec() which can execute arbitrary code", "high"),
            (r"os\.system\(", "Direct OS system call", "high"),
            (r"subprocess\.(Popen|run|call)\(.*shell=True", "Subprocess execution with shell=True", "high"),
            (r"requests\.(post|put|delete|patch)", "Outbound network requests (potential data exfiltration)", "medium"),
            (r"urllib\.request\.urlopen", "Direct URL opening", "medium"),
            (r"pickle\.load", "Unsafe deserialization with pickle", "high"),
            (r"os\.environ", "Accessing environment variables (potential secret theft)", "medium"),
            (r"open\(['\"]\.env['\"]", "Directly reading .env files", "high"),
            (r"socket\.", "Low-level socket communication", "medium"),
            (r"base64\.b64decode\(", "Base64 decoding (often used to hide payloads)", "low"),
            (r"getattr\(.*__import__", "Dynamic import obfuscation", "high"),
            (r"__builtins__", "Accessing builtins for obfuscation", "high"),
            (r"pathlib\.Path\(.*\.chmod\(", "Modifying file permissions", "medium"),
        ],
        "javascript": [
            (r"eval\(", "Use of eval() which is dangerous", "high"),
            (r"new Function\(", "Function constructor which can execute arbitrary code", "high"),
            (r"child_process\.(exec|spawn)", "Executing external processes", "high"),
            (r"axios\.post", "Outbound network requests", "medium"),
            (r"fetch\(", "Outbound network requests", "medium"),
            (r"process\.env", "Accessing environment variables", "medium"),
            (r"fs\.readFileSync\(['\"]\.env['\"]", "Directly reading .env files", "high"),
            (r"Buffer\.from\(.*['\"]base64['\"]", "Base64 decoding", "low"),
            (r"XMLHttpRequest", "Outbound network requests", "medium"),
            (r"require\(['\"]net['\"]", "Low-level network access", "medium"),
            (r"fs\.chmod", "Modifying file permissions", "medium"),
        ]
    }

    def scan_directory(self, directory: str) -> List[Dict[str, Any]]:
        """Scan a directory recursively for dangerous patterns.
        
        Returns:
            List of findings: [{"file": str, "line": int, "type": str, "severity": str, "description": str, "snippet": str}]
        """
        findings = []
        for root, _, files in os.walk(directory):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                lang = None
                if ext == ".py":
                    lang = "python"
                elif ext in [".js", ".ts"]:
                    lang = "javascript"
                
                if lang:
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, directory)
                    file_findings = self.scan_file(file_path, lang, rel_path)
                    findings.extend(file_findings)
                    
        return findings

    def scan_file(self, file_path: str, lang: str, rel_path: str) -> List[Dict[str, Any]]:
        """Scan a single file for dangerous patterns."""
        findings = []
        patterns = self.DANGEROUS_PATTERNS.get(lang, [])
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    for pattern, description, severity in patterns:
                        if re.search(pattern, line):
                            findings.append({
                                "file": rel_path,
                                "line": line_num,
                                "type": lang,
                                "severity": severity,
                                "description": description,
                                "snippet": line.strip()
                            })
        except Exception:
            # Skip files that can't be read
            pass
            
        return findings

    def generate_report(self, findings: List[Dict[str, Any]]) -> str:
        """Generate a human-readable report from findings."""
        if not findings:
            return "✅ No suspicious patterns found."
            
        report = [f"⚠️ Found {len(findings)} potential security issues:\n"]
        # Sort by severity (high -> medium -> low)
        severity_map = {"high": 0, "medium": 1, "low": 2}
        sorted_findings = sorted(findings, key=lambda x: severity_map.get(x["severity"], 3))
        
        for f in sorted_findings:
            sev_icon = "🔴" if f['severity'] == "high" else "🟡" if f['severity'] == "medium" else "⚪"
            report.append(f"{sev_icon} [{f['file']}:{f['line']}] ({f['severity']}) {f['description']}")
            report.append(f"  Snippet: {f['snippet']}")
            
        return "\n".join(report)
