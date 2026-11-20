"""Tests for ReportGenerator (e.g. generate_agent_report with researcher output)."""

import pytest
from datetime import date

from src.utils.reporting.report_generator import ReportGenerator


@pytest.fixture
def report_generator():
    """ReportGenerator with no db (avoids DB/auditor for agent report formatting)."""
    return ReportGenerator(db=None)


class TestGenerateAgentReportResearcherMarketMoneyline:
    """Test that researcher report handles market.moneyline as both dict and string."""

    def test_researcher_report_moneyline_dict(self, report_generator):
        """When market.moneyline is a dict with away/home, report shows Away X / Home Y."""
        agent_output = {
            "games": [
                {
                    "game_id": "1",
                    "teams": {"away": "Team A", "home": "Team B"},
                    "market": {
                        "spread": "Team A +3.5",
                        "total": "145.5",
                        "moneyline": {"away": "+130", "home": "-150"},
                    },
                }
            ]
        }
        text = report_generator.generate_agent_report(
            "researcher", agent_output, date(2026, 3, 7), metadata={}
        )
        assert "Moneyline: Away +130 / Home -150" in text

    def test_researcher_report_moneyline_string(self, report_generator):
        """When market.moneyline is a string (strict schema), report shows it without .get() error."""
        agent_output = {
            "games": [
                {
                    "game_id": "2",
                    "teams": {"away": "Away", "home": "Home"},
                    "market": {
                        "spread": "Away +4",
                        "total": "150",
                        "moneyline": "Away +120 / Home -140",
                    },
                }
            ]
        }
        text = report_generator.generate_agent_report(
            "researcher", agent_output, date(2026, 3, 7), metadata={}
        )
        assert "Moneyline: Away +120 / Home -140" in text

    def test_researcher_report_moneyline_only_dict(self, report_generator):
        """Market with only moneyline as dict still formats correctly."""
        agent_output = {
            "games": [
                {
                    "game_id": "3",
                    "teams": {"away": "X", "home": "Y"},
                    "market": {"moneyline": {"away": "-110", "home": "-110"}},
                }
            ]
        }
        text = report_generator.generate_agent_report(
            "researcher", agent_output, date(2026, 3, 7)
        )
        assert "Moneyline: Away -110 / Home -110" in text
