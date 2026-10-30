"""Regression tests for the independent Fliggy H5 fallback session."""
import logging

from crawlers.fliggy import FliggyCrawler


def test_h5_fallback_without_saved_session_reports_login(tmp_path):
    crawler = FliggyCrawler(
        {"user_data_dir": str(tmp_path)},
        logging.getLogger("test-fliggy-h5-fallback"),
    )
    crawler.path_reset()
    crawler.path_note("PC", "login", date="2027-01-02")

    rows = crawler._h5_fallback("LAX", "CTU", ["2027-01-02"])

    assert rows == []
    assert crawler.paths[-1] == {
        "path": "H5", "status": "login", "rows": 0,
        "date": "", "detail": "未保存淘宝 H5 会话", "ms": 0,
    }
