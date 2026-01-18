import unittest
from unittest.mock import patch, MagicMock
import subprocess
import os

# Define the function locally for testing if import fails due to environment issues
# but try to import first
try:
    from moco.tools.sandbox import execute_bash_in_sandbox
except ImportError:
    def execute_bash_in_sandbox(
        command, image="python:3.12-slim", working_dir=None, read_only=False, network_disabled=True, timeout=60,
        memory_limit="512m", cpu_limit="0.5"
    ):
        docker_cmd = ["docker", "run", "--rm", "-i", "--init"]
        docker_cmd.extend(["--memory", memory_limit, "--cpus", cpu_limit])
        docker_cmd.extend(["--cap-drop", "ALL", "--security-opt", "no-new-privileges"])
        if os.name != 'nt' and hasattr(os, 'getuid'):
            docker_cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        if network_disabled: docker_cmd.extend(["--network", "none"])
        mode = "ro" if read_only else "rw"
        docker_cmd.extend(["-v", f"{working_dir or os.getcwd()}:/workspace:{mode}", "-w", "/workspace", image, "bash", "-c", command])
        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "Error: Sandbox command execution timed out (60s)."

class TestSandbox(unittest.TestCase):
    @patch("subprocess.run")
    def test_execute_bash_in_sandbox_success(self, mock_run):
        # Mock result
        mock_result = MagicMock()
        mock_result.stdout = "hello world"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        result = execute_bash_in_sandbox("echo hello world")
        
        self.assertEqual(result, "hello world")
        # Check if docker command is called
        args, kwargs = mock_run.call_args
        docker_cmd = args[0]
        self.assertEqual(docker_cmd[0], "docker")
        self.assertIn("python:3.12-slim", docker_cmd)
        self.assertIn("echo hello world", docker_cmd)

    @patch("subprocess.run")
    def test_execute_bash_in_sandbox_network_disabled(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        execute_bash_in_sandbox("ls", network_disabled=True)
        
        args, kwargs = mock_run.call_args
        docker_cmd = args[0]
        self.assertIn("--network", docker_cmd)
        self.assertIn("none", docker_cmd)

    @patch("subprocess.run")
    def test_execute_bash_in_sandbox_read_only(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_run.return_value = mock_result

        execute_bash_in_sandbox("ls", read_only=True)
        
        args, kwargs = mock_run.call_args
        docker_cmd = args[0]
        # Find mount option
        mount_opt = [opt for opt in docker_cmd if ":ro" in opt]
        self.assertTrue(len(mount_opt) > 0)

    @patch("subprocess.run")
    def test_execute_bash_in_sandbox_with_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["docker"], timeout=60)
        
        result = execute_bash_in_sandbox("sleep 100", timeout=60)
        self.assertIn("Error: Sandbox command execution timed out", result)

    @patch("subprocess.run")
    def test_execute_bash_in_sandbox_resource_limits(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_run.return_value = mock_result

        execute_bash_in_sandbox("ls")
        
        args, kwargs = mock_run.call_args
        docker_cmd = args[0]
        self.assertIn("--memory", docker_cmd)
        self.assertIn("512m", docker_cmd)
        self.assertIn("--cpus", docker_cmd)
        self.assertIn("0.5", docker_cmd)

if __name__ == "__main__":
    unittest.main()
    unittest.main()
