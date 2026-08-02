from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from charity_donor_outreach.cli import main
from charity_donor_outreach.models import CampaignBrief, DonorRecord, OutreachResult

ROOT = Path(__file__).resolve().parents[1]
JLL_EXAMPLES = ROOT / "examples" / "jll-supplied"


def test_jll_supplied_fixture_is_provenance_locked() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_jll_fixture.py"), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "mode": "check",
        "records": 50,
        "source": "examples/jll-supplied/source-donors.csv",
        "status": "ok",
    }


def test_jll_supplied_operational_fixture_matches_golden_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "jll-results.jsonl"
    exit_code = main(
        [
            "generate",
            "--campaign",
            str(JLL_EXAMPLES / "campaign.json"),
            "--donors",
            str(JLL_EXAMPLES / "donors.jsonl"),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert output.read_bytes() == (JLL_EXAMPLES / "results.jsonl").read_bytes()
    assert json.loads(capsys.readouterr().out) == {
        "records": 50,
        "statuses": {"draft_ready": 33, "review_required": 17},
    }


def test_jll_supplied_operational_fixture_matches_runtime_contracts() -> None:
    CampaignBrief.model_validate_json((JLL_EXAMPLES / "campaign.json").read_text(encoding="utf-8"))
    donors = [
        DonorRecord.model_validate_json(line)
        for line in (JLL_EXAMPLES / "donors.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    results = [
        OutreachResult.model_validate_json(line)
        for line in (JLL_EXAMPLES / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(donors) == len(results) == 50
    assert [donor.donor_id for donor in donors] == [result.donor_id for result in results]


def test_one_command_reviewer_demo_runs_and_verifies_both_datasets(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_examples.py"),
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["fixture"]["status"] == "ok"
    assert [dataset["name"] for dataset in report["datasets"]] == [
        "realistic-controls",
        "jll-supplied-table",
    ]
    assert all(dataset["golden_match"] for dataset in report["datasets"])
    assert [dataset["records"] for dataset in report["datasets"]] == [6, 50]
    summary_path = Path(report["reviewer_summary"])
    if not summary_path.is_absolute():
        summary_path = ROOT / summary_path
    summary = summary_path.read_text(encoding="utf-8")
    assert "# Reviewer demo results" in summary
    assert "realistic-controls: representative draft" in summary
    assert "jll-supplied-table: representative draft" in summary
    assert "No outreach was sent." in summary
