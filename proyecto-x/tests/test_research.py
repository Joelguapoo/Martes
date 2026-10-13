import pytest

from app.research.web import UnsafeUrlError, assert_safe_public_url


def test_research_blocks_plain_http():
    with pytest.raises(UnsafeUrlError):
        assert_safe_public_url("http://example.com")
