"""Tests for github_ops.py — mocks `run_command_safe` since we can't
hit the real GitHub API in unit tests."""
import base64
import json
from unittest.mock import patch, MagicMock

import pytest

from github_ops import (
    get_changed_paths,
    fetch_upstream_digest,
)


class TestGetChangedPaths:
    def test_extracts_filenames_sorted(self):
        api_response = json.dumps([
            {"filename": "z/file.py", "status": "modified"},
            {"filename": "a/file.py", "status": "added"},
            {"filename": "m/file.py", "status": "removed"},
        ])
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(stdout=api_response, returncode=0)
            paths = get_changed_paths("owner/repo", 123)
        assert paths == ["a/file.py", "m/file.py", "z/file.py"]

    def test_handles_empty_response(self):
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(stdout="", returncode=0)
            paths = get_changed_paths("owner/repo", 123)
        assert paths == []

    def test_handles_unexpected_response_shape(self):
        # If GitHub API ever returns something other than a list, don't crash
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(stdout='{"error": "oops"}', returncode=0)
            paths = get_changed_paths("owner/repo", 123)
        assert paths == []


class TestFetchUpstreamDigest:
    def test_decodes_base64_content_from_api(self):
        original = "## scenario: pod\nscenario_type: pod_d_s\n"
        encoded = base64.b64encode(original.encode()).decode()
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(stdout=encoded, returncode=0)
            text = fetch_upstream_digest("owner/repo", "abc123")
        assert text == original

    def test_returns_empty_on_404(self):
        # File doesn't exist at this ref — common case for the FIRST PR
        # that introduces the digest.
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(
                stdout="",
                stderr="HTTP 404: Not Found",
                returncode=1,
            )
            text = fetch_upstream_digest("owner/repo", "abc")
        assert text == ""

    def test_raises_on_non_404_error(self):
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(
                stdout="",
                stderr="rate limit exceeded",
                returncode=1,
            )
            with pytest.raises(RuntimeError, match="failed to fetch"):
                fetch_upstream_digest("owner/repo", "abc")

    def test_handles_garbled_base64(self):
        # Don't crash if API returns something weird — return empty
        with patch("github_ops.run_command_safe") as mock:
            mock.return_value = MagicMock(stdout="@@@invalid@@@", returncode=0)
            text = fetch_upstream_digest("owner/repo", "abc")
        assert text == ""
