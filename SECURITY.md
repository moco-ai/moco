# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in moco-agent, please report it responsibly.

### How to Report

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Send an email to: security@moco-agent.dev (or create a private security advisory on GitHub)
3. Include the following information:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt within 48 hours
- **Assessment**: We will assess the vulnerability and determine its severity
- **Updates**: We will keep you informed of our progress
- **Resolution**: We aim to resolve critical vulnerabilities within 7 days
- **Credit**: We will credit you in our security advisories (unless you prefer anonymity)

## Security Best Practices

When using moco-agent, please follow these security best practices:

### API Keys

- **Never commit API keys** to version control
- Use environment variables or `.env` files (which are gitignored)
- Rotate API keys regularly
- Use separate API keys for development and production

### Guardrails

moco-agent includes built-in guardrails to prevent dangerous operations:

```python
from moco.core.guardrails import Guardrails

guardrails = Guardrails(
    # Block dangerous shell commands
    enable_dangerous_pattern_check=True,
    
    # Limit input/output length
    max_input_length=50000,
    max_output_length=100000,
    
    # Limit tool calls per turn
    max_tool_calls_per_turn=10,
)
```

### Tool Permissions

Be cautious when enabling tools that can:
- Execute shell commands (`bash`)
- Write to the filesystem (`write_file`, `edit_file`)
- Make network requests (`webfetch`, `websearch`)

Consider using read-only profiles for untrusted inputs.

### MCP Servers

When using MCP (Model Context Protocol) servers:
- Only connect to trusted MCP servers
- Review the capabilities of each MCP server before enabling
- Use the principle of least privilege

## Known Security Considerations

### Shell Command Execution

The `bash` tool can execute arbitrary shell commands. While guardrails block known dangerous patterns (like `rm -rf /`), it's impossible to block all potentially harmful commands.

**Recommendation**: Disable the `bash` tool in production environments or use a sandboxed execution environment.

### File System Access

The `read_file` and `write_file` tools can access any file the process has permission to read/write.

**Recommendation**: Run moco-agent with minimal filesystem permissions and use chroot or containerization in production.

### LLM Prompt Injection

LLM-based agents are susceptible to prompt injection attacks where malicious input attempts to override the system prompt.

**Recommendation**: 
- Validate and sanitize user inputs
- Use the guardrails system to filter suspicious patterns
- Monitor agent behavior for anomalies

## Changelog

### Security Updates

- **0.1.0** (2026-01-08): Initial release with guardrails system
